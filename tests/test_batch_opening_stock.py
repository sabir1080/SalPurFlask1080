"""Batch-wise opening stock at Add Item (batch-tracked items).

Scenario: Batch 101 = 30, Unknown = 20, Batch 102 = 50  ->  total 100.

The item is created empty and filled batch by batch through get_or_create_batch() +
item_add_stock_batched(), then post_item_opening() posts the GL entry. Every batch is received at
the item's purchase price. Non-batch creation is unchanged.
"""
from datetime import date
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context, Item, FinancialAccount, StockMovement, JournalEntry,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year, seed_financial_account_links,
    Batch, fefo_allocate_batches,
)
from salpurflask.models.models import JournalLine
from salpurflask.models.inventory_location import BatchStock, ItemStock, get_or_create_default_location
from salpurflask.models.business_config import BusinessCategory


def _setup():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()
    bcat = BusinessCategory(name="Medical", slug="cat-medical", is_enabled=True,
                            config_data={"batch_tracking": True, "stock_expiry": True})
    db.session.add(bcat)
    db.session.add(User(name="Admin", email="a@batchopen.com", password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": "a@batchopen.com", "password": "secret123"})
    return c, bcat


def _form(bcat, rows=None, name="Paracetamol", batch_tracked=True, opening="0", price="10"):
    data = {
        "name": name, "business_category_id": str(bcat.id), "unit": "Pcs", "item_type": "STOCK",
        "opening_stock": opening, "reorder_level": "5", "purchase_price": price, "sale_price": "20",
    }
    if batch_tracked:
        data["batch_tracked"] = "1"
    if rows is not None:
        data["opening_batch_no[]"] = [r[0] for r in rows]
        data["opening_batch_expiry[]"] = [r[1] for r in rows]
        data["opening_batch_qty[]"] = [r[2] for r in rows]
        data["opening_batch_rate[]"] = [r[3] if len(r) > 3 else "10" for r in rows]   # default = item price
    return data


GOOD_ROWS = [("101", "2027-03-01", "30"), ("", "", "20"), ("102", "2026-12-01", "50")]


def _create(c, bcat, rows=GOOD_ROWS, **kw):
    resp = c.post("/item", data=_form(bcat, rows, **kw), follow_redirects=True)
    return resp, Item.query.filter_by(name=kw.get("name", "Paracetamol")).first()


def test_batch_opening_creates_everything_consistently(appctx):
    c, bcat = _setup()
    resp, item = _create(c, bcat)
    assert item is not None and item.batch_tracked is True
    loc = get_or_create_default_location()

    batches = Batch.query.filter_by(item_id=item.id).all()
    assert len(batches) == 3
    bstocks = BatchStock.query.join(Batch).filter(Batch.item_id == item.id).all()
    assert len(bstocks) == 3
    assert sum(b.quantity for b in bstocks) == 100

    moves = StockMovement.query.filter_by(item_id=item.id, movement_type="opening").all()
    assert len(moves) == 3
    assert all(m.batch_id is not None for m in moves)
    assert all(m.source_type == "item" and m.source_id == item.id for m in moves)
    assert sorted(m.quantity for m in moves) == [20, 30, 50]

    rows = ItemStock.query.filter_by(item_id=item.id).all()
    assert len(rows) == 1 and rows[0].location_id == loc.id and rows[0].quantity == 100
    assert item.stock == 100 and item.opening_stock == 100


def test_valuation_and_gl_are_consistent_and_balanced(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat)
    assert Decimal(str(item.inventory_value)) == Decimal("1000")          # 100 x 10
    total_cost = sum(Decimal(str(b.unit_cost)) * bs.quantity
                     for b, bs in db.session.query(Batch, BatchStock)
                     .join(BatchStock, BatchStock.batch_id == Batch.id)
                     .filter(Batch.item_id == item.id))
    assert total_cost == Decimal(str(item.inventory_value))
    lines = JournalLine.query.all()
    assert lines and sum(l.debit for l in lines) == sum(l.credit for l in lines)
    assert sum(l.debit for l in lines) == Decimal("1000")


def test_unknown_batch_has_null_number_and_purchase_price_cost(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat)
    unknown = Batch.query.filter_by(item_id=item.id, batch_no=None).all()
    assert len(unknown) == 1
    assert Decimal(str(unknown[0].unit_cost)) == Decimal("10")
    assert unknown[0].expiry_date is None


def test_literal_unknown_word_is_the_unknown_batch(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat, rows=[("UNKNOWN", "", "5"), ("A", "", "5")])
    assert Batch.query.filter_by(item_id=item.id, batch_no=None).count() == 1
    assert Batch.query.filter_by(item_id=item.id, batch_no="UNKNOWN").count() == 0


def test_fefo_orders_by_expiry_with_unknown_last(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat)
    loc_id = get_or_create_default_location().id
    alloc = fefo_allocate_batches(item.id, loc_id, 100)
    assert [(b.batch_no, q) for b, q in alloc] == [("102", 50), ("101", 30), (None, 20)]


def _rejected(c, bcat, rows, **kw):
    resp = c.post("/item", data=_form(bcat, rows, **kw), follow_redirects=True)
    assert Item.query.filter_by(name="Paracetamol").count() == 0
    assert Batch.query.count() == 0 and StockMovement.query.count() == 0
    assert JournalEntry.query.count() == 0
    return resp


def test_duplicate_batch_number_rejected(appctx):
    c, bcat = _setup()
    resp = _rejected(c, bcat, [("101", "", "5"), ("101", "", "5")])
    assert b"more than once" in resp.data


def test_duplicate_batch_number_is_case_insensitive(appctx):
    c, bcat = _setup()
    _rejected(c, bcat, [("ab1", "", "5"), ("AB1", "", "5")])


def test_two_unknown_rows_rejected(appctx):
    c, bcat = _setup()
    resp = _rejected(c, bcat, [("", "", "5"), ("unknown", "", "5")])
    assert b"Only one Unknown" in resp.data


def test_bad_quantities_rejected(appctx):
    c, bcat = _setup()
    for bad in ("0", "-3", "abc", "2.5", ""):
        _rejected(c, bcat, [("101", "", bad)])


def test_invalid_expiry_rejected(appctx):
    c, bcat = _setup()
    resp = _rejected(c, bcat, [("101", "31-12-2027", "5")])
    assert b"invalid expiry" in resp.data


def test_no_rows_rejected(appctx):
    c, bcat = _setup()
    _rejected(c, bcat, [("", "", "")])


def test_item_purchase_price_not_needed_when_batches_carry_rates(appctx):
    """Each batch row has its own rate, so the item's general purchase price may be blank/0;
    it is left exactly as typed (never overwritten by a batch rate)."""
    c, bcat = _setup()
    _, item = _create(c, bcat, price="0")
    assert item is not None and item.stock == 100
    assert float(item.purchase_price or 0) == 0
    assert Decimal(str(item.inventory_value)) == Decimal("1000")


def test_total_comes_from_rows_not_the_typed_total(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat, opening="999")
    assert item.opening_stock == 100 and item.stock == 100


def test_form_rows_are_restored_after_validation_error(appctx):
    c, bcat = _setup()
    resp = c.post("/item", data=_form(bcat, [("101", "2027-03-01", "30"), ("101", "", "7")]),
                  follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert html.count('name="opening_batch_no[]"') == 2
    assert 'value="2027-03-01"' in html and 'value="30"' in html and 'value="7"' in html
    assert 'id="batch_tracked"' in html and "checked" in html.split('id="batch_tracked"')[1][:200]


def test_edit_guard_blocks_opening_stock_change_on_batch_item(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat)
    resp = c.post(f"/item/edit/{item.id}", data={
        "name": "Paracetamol", "business_category_id": str(bcat.id), "unit": "Pcs", "item_type": "STOCK",
        "opening_stock": "150", "reorder_level": "5", "purchase_price": "10", "sale_price": "20",
    }, follow_redirects=True)
    assert b"Stock Adjustment" in resp.data
    db.session.expire_all()
    item = db.session.get(Item, item.id)
    assert item.opening_stock == 100 and item.stock == 100
    assert sum(b.quantity for b in BatchStock.query.all()) == 100


def test_non_batch_item_creation_is_unchanged(appctx):
    c, bcat = _setup()
    resp = c.post("/item", data=_form(bcat, rows=None, batch_tracked=False, opening="40"),
                  follow_redirects=True)
    item = Item.query.filter_by(name="Paracetamol").first()
    assert item is not None and item.batch_tracked is False
    assert item.stock == 40 and item.opening_stock == 40
    assert Decimal(str(item.inventory_value)) == Decimal("400")
    moves = StockMovement.query.filter_by(item_id=item.id, movement_type="opening").all()
    assert len(moves) == 1 and moves[0].batch_id is None and moves[0].quantity == 40
    assert Batch.query.count() == 0
    assert ItemStock.query.filter_by(item_id=item.id).one().quantity == 40


# ── Category-level Batch No / Expiry Date (Medical Store EAV fields) ─────────


def _with_required_category_batch_fields(bcat):
    from salpurflask.models.business_config import ProductField
    db.session.add(ProductField(category_id=bcat.id, field_name="batch_no", field_label="Batch No.",
                                field_type="text", is_required=True, tab_name="Batch & Expiry"))
    db.session.add(ProductField(category_id=bcat.id, field_name="expiry_date", field_label="Expiry Date",
                                field_type="date", is_required=True, tab_name="Batch & Expiry"))
    db.session.commit()


def _category_values(item):
    from salpurflask.models.business_config import ProductCategoryData
    return {d.field_name: d.field_value for d in ProductCategoryData.query.filter_by(product_id=item.id)}


def test_batch_tracked_does_not_need_category_batch_fields(appctx):
    c, bcat = _setup()
    _with_required_category_batch_fields(bcat)
    resp, item = _create(c, bcat)            # no batch_no / expiry_date posted
    assert item is not None and item.stock == 100
    assert Batch.query.filter_by(item_id=item.id).count() == 3


def test_batch_tracked_saves_no_item_level_batch_or_expiry(appctx):
    c, bcat = _setup()
    _with_required_category_batch_fields(bcat)
    data = _form(bcat, GOOD_ROWS)
    data["batch_no"], data["expiry_date"] = "STRAY", "2030-01-01"     # a stale/bypassing client
    c.post("/item", data=data, follow_redirects=True)
    item = Item.query.filter_by(name="Paracetamol").first()
    assert item is not None
    vals = _category_values(item)
    assert "batch_no" not in vals and "expiry_date" not in vals
    assert not Batch.query.filter_by(item_id=item.id, batch_no="STRAY").count()


def test_not_batch_tracked_category_fields_still_required(appctx):
    c, bcat = _setup()
    _with_required_category_batch_fields(bcat)
    resp = c.post("/item", data=_form(bcat, rows=None, batch_tracked=False, opening="10"),
                  follow_redirects=True)
    assert Item.query.filter_by(name="Paracetamol").count() == 0
    assert b"Batch No. is required" in resp.data


def test_not_batch_tracked_category_fields_still_saved(appctx):
    c, bcat = _setup()
    _with_required_category_batch_fields(bcat)
    data = _form(bcat, rows=None, batch_tracked=False, opening="10")
    data["batch_no"], data["expiry_date"] = "B-7", "2030-01-01"
    c.post("/item", data=data, follow_redirects=True)
    item = Item.query.filter_by(name="Paracetamol").first()
    assert item is not None
    vals = _category_values(item)
    assert vals.get("batch_no") == "B-7" and vals.get("expiry_date") == "2030-01-01"
    assert Batch.query.count() == 0


def test_category_field_hiding_js_is_wired(appctx):
    c, bcat = _setup()
    html = c.get("/item").get_data(as_text=True)
    assert "function applyBatchTrackedCategoryFields" in html
    assert html.count("applyBatchTrackedCategoryFields();") >= 2     # redraw + toggle


# ── Per-batch Purchase Rate ──────────────────────────────────────────────────

RATE_ROWS = [("101", "2026-10-30", "30", "380"), ("", "", "20", "400"), ("102", "2026-11-30", "50", "420")]
RATE_VALUE = Decimal("40400")        # 30x380 + 20x400 + 50x420


def test_three_batches_with_different_rates(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat, rows=RATE_ROWS, price="999")     # general item price differs
    by_no = {b.batch_no: b for b in Batch.query.filter_by(item_id=item.id)}
    assert set(by_no) == {"101", None, "102"}
    assert {k: Decimal(str(b.unit_cost)) for k, b in by_no.items()} == {
        "101": Decimal("380"), None: Decimal("400"), "102": Decimal("420")}
    qty = {b.batch_no: BatchStock.query.filter_by(batch_id=b.id).one().quantity for b in by_no.values()}
    assert qty == {"101": 30, None: 20, "102": 50}
    assert item.stock == 100 and item.opening_stock == 100
    assert float(item.purchase_price) == 999          # general price untouched by batch rates


def test_stock_movements_point_at_their_batch_cost(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat, rows=RATE_ROWS)
    moves = StockMovement.query.filter_by(item_id=item.id, movement_type="opening").all()
    assert len(moves) == 3
    cost = {(m.quantity, db.session.get(Batch, m.batch_id).batch_no): Decimal(str(db.session.get(Batch, m.batch_id).unit_cost))
            for m in moves}
    assert cost == {(30, "101"): Decimal("380"), (20, None): Decimal("400"), (50, "102"): Decimal("420")}


def test_inventory_value_and_journal_use_batch_rates(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat, rows=RATE_ROWS)
    assert Decimal(str(item.inventory_value)) == RATE_VALUE
    entry = JournalEntry.query.filter_by(source_type="item_opening", source_id=item.id).one()
    lines = JournalLine.query.filter_by(entry_id=entry.id).all()
    assert sum(l.debit for l in lines) == RATE_VALUE == sum(l.credit for l in lines)
    assert len([l for l in lines if l.debit > 0]) == 1        # one Inventory debit
    assert len([l for l in lines if l.credit > 0]) == 1       # one Opening Equity credit


def test_unknown_batch_keeps_its_own_rate(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat, rows=RATE_ROWS)
    unknown = Batch.query.filter_by(item_id=item.id, batch_no=None).one()
    assert Decimal(str(unknown.unit_cost)) == Decimal("400") and unknown.expiry_date is None


def test_bad_rates_rejected(appctx):
    c, bcat = _setup()
    for bad in ("0", "-5", "abc", ""):
        resp = _rejected(c, bcat, [("101", "", "5", bad)])
        assert b"Purchase Rate must be greater than 0" in resp.data


def test_rates_are_restored_after_validation_error(appctx):
    c, bcat = _setup()
    resp = c.post("/item", data=_form(bcat, [("101", "", "30", "380"), ("101", "", "7", "415")]),
                  follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert 'value="380"' in html and 'value="415"' in html
    assert 'name="opening_batch_rate[]"' in html and 'id="batchOpeningValue"' in html


def test_batch_off_uses_item_purchase_price_for_the_gl(appctx):
    c, bcat = _setup()
    c.post("/item", data=_form(bcat, rows=None, batch_tracked=False, opening="40", price="12"),
           follow_redirects=True)
    item = Item.query.filter_by(name="Paracetamol").first()
    entry = JournalEntry.query.filter_by(source_type="item_opening", source_id=item.id).one()
    assert sum(l.debit for l in JournalLine.query.filter_by(entry_id=entry.id)) == Decimal("480")


# ── Item-level Purchase Price = weighted average of the batch rates (default) ─


def test_blank_purchase_price_defaults_to_weighted_average_incl_unknown(appctx):
    c, bcat = _setup()
    data = _form(bcat, RATE_ROWS)
    data["purchase_price"] = ""                          # (30x380 + 20x400 + 50x420) / 100
    c.post("/item", data=data, follow_redirects=True)
    item = Item.query.filter_by(name="Paracetamol").first()
    assert item is not None
    assert Decimal(str(item.purchase_price)) == Decimal("404.00")
    assert Decimal(str(item.inventory_value)) == RATE_VALUE       # still the batch rates


def test_user_edited_purchase_price_is_kept_and_does_not_touch_valuation(appctx):
    c, bcat = _setup()
    _, item = _create(c, bcat, rows=RATE_ROWS, price="450")
    assert Decimal(str(item.purchase_price)) == Decimal("450")
    assert Decimal(str(item.inventory_value)) == RATE_VALUE
    assert {Decimal(str(b.unit_cost)) for b in Batch.query.filter_by(item_id=item.id)} == {
        Decimal("380"), Decimal("400"), Decimal("420")}
    entry = JournalEntry.query.filter_by(source_type="item_opening", source_id=item.id).one()
    assert sum(l.debit for l in JournalLine.query.filter_by(entry_id=entry.id)) == RATE_VALUE


def test_batch_off_blank_purchase_price_behaviour_unchanged(appctx):
    c, bcat = _setup()
    # no stock: price stays blank (nothing is derived for a plain item)
    c.post("/item", data=_form(bcat, rows=None, batch_tracked=False, opening="0", price=""),
           follow_redirects=True)
    assert Item.query.filter_by(name="Paracetamol").one().purchase_price is None
    # with stock a price is still required
    resp = c.post("/item", data=_form(bcat, rows=None, batch_tracked=False, opening="5", price="",
                                      name="Other"), follow_redirects=True)
    assert b"purchase price greater than 0" in resp.data
    assert Item.query.filter_by(name="Other").count() == 0


def test_purchase_price_field_is_editable_with_average_hint(appctx):
    c, bcat = _setup()
    html = c.get("/item").get_data(as_text=True)
    field = html.split('id="purchase_price"')[1].split(">")[0]
    assert "readonly" not in field
    assert "Weighted Average" in html and "function syncAveragePurchasePrice" in html
