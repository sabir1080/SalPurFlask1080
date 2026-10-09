"""Converting a Purchase Order to a Purchase must value the stock at goods cost, without tax.

convert_po_to_purchase() used to hand item_add_stock() the line `amount`, which includes tax, while
post_document("purchase") books only the goods to Inventory (1200) and the tax to Tax Input (1300).
Every taxed conversion therefore left Item.inventory_value above account 1200 by the tax, which is
what the Inventory Subledger Reconciliation then reported. The PurchaseItem amount, the purchase
total and the journal entry are unchanged by the fix; only the cost given to the stock changes.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context, Item, Supplier, Purchase, PurchaseItem, FinancialAccount,
    JournalEntry, PurchaseOrder, PurchaseOrderItem, gl_balances, get_account, natural_balance, ACC_INVENTORY,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year, seed_financial_account_links,
    post_item_opening, sync_supplier_opening,
)
from salpurflask.models.models import JournalLine, enable_batch_tracking
from salpurflask.models.inventory_location import BatchStock, ItemStock, StockMovement
from app import Batch, PurchaseItemBatch
from salpurflask.models.business_config import BusinessCategory


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()


def _item(stock=20):
    bcat = BusinessCategory(name="Cat", slug="cat-w", is_enabled=True)
    db.session.add(bcat)
    db.session.flush()
    it = Item(name="Widget", business_category_id=bcat.id, unit="Pcs", purchase_price=Decimal("10"),
              sale_price=Decimal("20"), opening_stock=stock, stock=stock,
              inventory_value=Decimal(str(stock * 10)))
    db.session.add(it)
    db.session.flush()
    post_item_opening(it)
    db.session.commit()
    return it


def _admin():
    from flask import g
    try:
        del g._login_user
    except AttributeError:
        pass
    u = User(name="A", email="a@poconv.com", password=pwd_context.hash("secret123"), verified=True, role="admin")
    db.session.add(u)
    db.session.commit()
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True
    return c


def _convert(qty, price, discount_pct=0, discount_amt=0, tax_pct=0, tax_amt=0, batch_tracked=False):
    """A one-line PO, converted through the real route. Returns (item, purchase)."""
    _books()
    sup = Supplier(name="Sup", contact="0", address="X", opening_balance=0)
    db.session.add(sup)
    db.session.flush()
    sync_supplier_opening(sup)
    item = _item()
    if batch_tracked:                                  # 20 units already on hand, folded into the Unknown Batch
        enable_batch_tracking(item.id)
        db.session.commit()
    client = _admin()
    po = PurchaseOrder(supplier_id=sup.id, notes="", status="Draft")
    db.session.add(po)
    db.session.flush()
    db.session.add(PurchaseOrderItem(
        po_id=po.id, item_id=item.id, quantity=qty, purchase_price=price,
        discount_type="percent", discount_value=discount_pct, discount_amount=discount_amt,
        tax_percent=tax_pct, tax_amount=tax_amt, unit_name="Pcs", unit_factor=1))
    db.session.commit()
    value_before = Decimal(str(item.inventory_value))
    client.post(f"/purchase_orders/{po.id}/convert", data={"purchase_date": "2026-01-01"},
                follow_redirects=True)
    db.session.expire_all()
    item = db.session.get(Item, item.id)
    pur = Purchase.query.order_by(Purchase.id.desc()).first()
    return item, pur, Decimal(str(item.inventory_value)) - value_before


def _entry_lines(pur):
    entry = JournalEntry.query.filter_by(source_type="purchase", source_id=pur.id,
                                         reversal_of_id=None, is_reversed=False).one()
    return {l.account.code: (Decimal(str(l.debit)), Decimal(str(l.credit)))
            for l in JournalLine.query.filter_by(entry_id=entry.id)}


def _gl_inventory():
    acct = get_account(ACC_INVENTORY)
    return natural_balance(acct, gl_balances().get(acct.id, Decimal("0")))


def test_taxed_po_conversion_values_stock_without_tax(appctx):
    item, pur, added = _convert(qty=10, price=200, tax_pct=18, tax_amt=360)

    assert added == Decimal("2000")                                  # goods only, not 2,360
    assert item.stock == 30

    lines = _entry_lines(pur)
    assert lines["1200"] == (Decimal("2000"), Decimal("0"))          # Inventory debit = goods cost
    assert lines["1300"] == (Decimal("360"), Decimal("0"))           # tax to Tax Input
    assert lines["2100"] == (Decimal("0"), Decimal("2360"))          # payable = goods + tax

    pi = PurchaseItem.query.filter_by(purchase_id=pur.id).one()      # document figures unchanged
    assert Decimal(str(pi.amount)) == Decimal("2360")
    assert Decimal(str(pi.tax_amount)) == Decimal("360")
    assert Decimal(str(pi.tax_percent)) == Decimal("18")

    assert _gl_inventory() == Decimal(str(db.session.query(db.func.sum(Item.inventory_value)).scalar()))


def test_discounted_and_taxed_po_conversion(appctx):
    # 10 x 200 = 2,000, 5% discount = 100, 18% tax on 1,900 = 342, line amount 2,242
    item, pur, added = _convert(qty=10, price=200, discount_pct=5, discount_amt=100, tax_pct=18, tax_amt=342)

    assert added == Decimal("1900")
    lines = _entry_lines(pur)
    assert lines["1200"] == (Decimal("1900"), Decimal("0"))
    assert lines["1300"] == (Decimal("342"), Decimal("0"))
    assert Decimal(str(PurchaseItem.query.filter_by(purchase_id=pur.id).one().amount)) == Decimal("2242")
    assert _gl_inventory() == Decimal(str(db.session.query(db.func.sum(Item.inventory_value)).scalar()))


def test_zero_tax_po_conversion_is_unchanged(appctx):
    item, pur, added = _convert(qty=5, price=10)

    assert added == Decimal("50")
    assert item.stock == 25
    lines = _entry_lines(pur)
    assert lines["1200"] == (Decimal("50"), Decimal("0"))
    assert "1300" not in lines
    assert _gl_inventory() == Decimal(str(db.session.query(db.func.sum(Item.inventory_value)).scalar()))


def test_zero_tax_discounted_po_conversion_is_unchanged(appctx):
    item, pur, added = _convert(qty=10, price=20, discount_pct=10, discount_amt=20)

    assert added == Decimal("180")                                   # 200 less 10%
    assert _entry_lines(pur)["1200"] == (Decimal("180"), Decimal("0"))
    assert _gl_inventory() == Decimal(str(db.session.query(db.func.sum(Item.inventory_value)).scalar()))


# ── Batch-tracked items: a PO line has no batch number, so the stock goes to the Unknown Batch ──


def _quantities(item):
    db.session.expire_all()
    item = db.session.get(Item, item.id)
    batch_total = (db.session.query(db.func.coalesce(db.func.sum(BatchStock.quantity), 0))
                   .join(Batch, Batch.id == BatchStock.batch_id).filter(Batch.item_id == item.id).scalar())
    itemstock_total = (db.session.query(db.func.coalesce(db.func.sum(ItemStock.quantity), 0))
                       .filter(ItemStock.item_id == item.id).scalar())
    return item.stock, int(itemstock_total), int(batch_total)


def test_batch_tracked_po_conversion_keeps_item_warehouse_and_batch_stock_equal(appctx):
    item, pur, added = _convert(qty=10, price=200, tax_pct=18, tax_amt=360, batch_tracked=True)

    assert _quantities(item) == (30, 30, 30)                       # was 20 / 20 / 20 before the conversion
    unknown = Batch.query.filter_by(item_id=item.id, batch_no=None).one()
    alloc = PurchaseItemBatch.query.join(PurchaseItem, PurchaseItem.id == PurchaseItemBatch.purchase_item_id) \
        .filter(PurchaseItem.purchase_id == pur.id).one()
    assert alloc.batch_id == unknown.id and alloc.quantity == 10
    move = StockMovement.query.filter_by(item_id=item.id, source_type="purchase", source_id=pur.id).one()
    assert move.batch_id == unknown.id and move.quantity == 10
    # valuation and accounting are the tax-exclusive goods cost, exactly as for a non-batch item
    assert added == Decimal("2000")
    assert _entry_lines(pur)["1200"] == (Decimal("2000"), Decimal("0"))
    assert _entry_lines(pur)["1300"] == (Decimal("360"), Decimal("0"))


def test_reversing_a_converted_batch_tracked_purchase_restores_every_quantity(appctx):
    item, pur, _ = _convert(qty=10, price=200, batch_tracked=True)
    assert _quantities(item) == (30, 30, 30)

    from flask import g
    try:
        del g._login_user
    except AttributeError:
        pass
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(User.query.filter_by(email="a@poconv.com").one().id)
        s["_fresh"] = True
    c.post(f"/document/purchase/{pur.id}/reverse", follow_redirects=True)

    assert db.session.get(Purchase, pur.id).is_reversed is True
    assert _quantities(item) == (20, 20, 20)
    assert BatchStock.query.join(Batch, Batch.id == BatchStock.batch_id).filter(
        Batch.item_id == item.id, Batch.batch_no.is_(None)).one().quantity == 20


def test_non_batch_po_conversion_creates_no_batch_rows(appctx):
    item, pur, added = _convert(qty=10, price=200)

    assert _quantities(item) == (30, 30, 0)                        # no batches exist for a non-batch item
    assert Batch.query.filter_by(item_id=item.id).count() == 0
    assert PurchaseItemBatch.query.count() == 0
    move = StockMovement.query.filter_by(item_id=item.id, source_type="purchase", source_id=pur.id).one()
    assert move.batch_id is None and added == Decimal("2000")
