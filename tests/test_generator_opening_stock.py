"""The test-data generator must record opening stock the way the Add Item screen does.

item_add_stock() alone moves stock and Item.inventory_value but posts nothing, so generated opening
stock used to sit in the stock valuation with no Dr Inventory / Cr Opening Equity: the Inventory
Subledger Reconciliation showed Inventory (1200) short by exactly the opening stock. The generator
now goes through post_item_opening() (helper _open_item_stock), which is what these tests pin.

Importing tools.generate_test_data is safe here: its import-time gate only checks that DATABASE_URL
is PostgreSQL (conftest points it at tradeflow_test) and nothing touches a database until main().
"""
import re
from decimal import Decimal

from app import (
    db, Item, Category, JournalEntry, gl_balances, get_account, natural_balance, ACC_INVENTORY,
    seed_chart_of_accounts, seed_fiscal_year,
)
from salpurflask.models.models import JournalLine, post_item_opening
from salpurflask.models.inventory_location import get_or_create_default_location


def _gen():
    import tools.generate_test_data as gen
    return gen


def _books():
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    db.session.commit()


def _items(*prices):
    cat = Category.query.first() or Category(name="Cat")
    db.session.add(cat)
    db.session.flush()
    out = []
    for n, price in enumerate(prices):
        it = Item(name=f"Gen item {n}", category_id=cat.id, stock=0, opening_stock=0,
                  purchase_price=price, sale_price=20, item_type="STOCK")
        db.session.add(it)
        out.append(it)
    db.session.commit()
    return out


def _ctx(items):
    from tools._data_common import make_rng
    gen = _gen()
    ctx = gen.Ctx(make_rng())
    ctx.items = list(items)
    ctx.locations.append(get_or_create_default_location())
    return gen, ctx


def _gl_inventory():
    acct = get_account(ACC_INVENTORY)
    return natural_balance(acct, gl_balances().get(acct.id, Decimal("0")))


def _stock_value():
    return Decimal(str(db.session.query(db.func.sum(Item.inventory_value)).scalar() or 0))


def _live_opening_entries(item):
    return JournalEntry.query.filter_by(source_type="item_opening", source_id=item.id,
                                        is_reversed=False, reversal_of_id=None).all()


def test_generated_opening_stock_is_posted_and_reconciles_with_1200(appctx):
    _books()
    items = _items(Decimal("12.50"), Decimal("200"), Decimal("3"))
    gen, ctx = _ctx(items)

    gen.stage3_opening_stock(ctx)
    db.session.expire_all()

    assert all(it.stock > 0 for it in items)
    for it in items:
        assert it.opening_stock == it.stock                       # what post_item_opening values
        entries = _live_opening_entries(it)
        assert len(entries) == 1
        lines = JournalLine.query.filter_by(entry_id=entries[0].id).all()
        debit, credit = sum(l.debit for l in lines), sum(l.credit for l in lines)
        assert debit == credit == (Decimal(str(it.purchase_price)) * it.stock).quantize(Decimal("0.01"))
    # the figure the reconciliation report compares
    assert _gl_inventory() == _stock_value() > 0


def test_opening_entry_is_dr_inventory_cr_opening_equity(appctx):
    _books()
    (it,) = _items(Decimal("50"))
    gen, ctx = _ctx([it])
    gen.stage3_opening_stock(ctx)
    entry = _live_opening_entries(it)[0]
    by_code = {l.account.code: (l.debit, l.credit) for l in JournalLine.query.filter_by(entry_id=entry.id)}
    value = Decimal("50") * it.stock
    assert by_code[ACC_INVENTORY] == (value, 0)
    assert sorted((d, c) for d, c in by_code.values()) == sorted([(value, 0), (0, value)])


def test_rerunning_the_stage_is_idempotent(appctx):
    _books()
    items = _items(Decimal("10"), Decimal("20"))
    gen, ctx = _ctx(items)
    gen.stage3_opening_stock(ctx)
    entries, stock, value, gl = (JournalEntry.query.count(), sum(i.stock for i in items),
                                 _stock_value(), _gl_inventory())

    gen.stage3_opening_stock(ctx)             # items already have stock: skipped
    db.session.expire_all()

    assert JournalEntry.query.count() == entries
    assert sum(i.stock for i in items) == stock
    assert _stock_value() == value and _gl_inventory() == gl


def test_reposting_keeps_one_live_entry_and_the_reconciliation(appctx):
    """post_item_opening()'s reverse-and-repost (what an item edit triggers) still works on
    generated items and leaves exactly one live entry, and 1200 still equals the stock value."""
    _books()
    (it,) = _items(Decimal("25"))
    gen, ctx = _ctx([it])
    gen.stage3_opening_stock(ctx)

    post_item_opening(it)
    db.session.commit()
    db.session.expire_all()

    assert len(_live_opening_entries(it)) == 1
    assert JournalEntry.query.filter_by(source_type="item_opening", source_id=it.id).count() == 3
    assert _gl_inventory() == _stock_value()


def test_item_without_a_price_gets_the_generators_default_cost_in_both_stock_and_books(appctx):
    _books()
    (it,) = _items(None)
    gen = _gen()
    gen._open_item_stock(it, 40, get_or_create_default_location().id)
    db.session.commit()
    db.session.refresh(it)

    assert Decimal(str(it.purchase_price)) == Decimal("10")
    assert Decimal(str(it.inventory_value)) == Decimal("400")
    assert _gl_inventory() == Decimal("400")


def test_no_generator_path_adds_opening_stock_without_the_helper():
    """Both opening-stock sites (stage 3 and the non-medical items in the batch section) go through
    _open_item_stock; the only raw opening item_add_stock call left is the helper's own."""
    with open("tools/generate_test_data.py", encoding="utf-8") as f:
        src = f.read()
    assert len(re.findall(r'movement_type="opening"', src)) == 1
    assert src.count("_open_item_stock(") == 3            # definition + the two call sites
