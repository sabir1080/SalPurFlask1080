"""Posted Purchase Correction for a batch-tracked item whose batch has
already been partially or fully consumed by a later Sale.

Bug this file guards against: correct_purchase_route()'s reversal step used
to call the same live, forward-looking batch-removal primitive a real-time
Sale/Adjustment/Transfer uses, unconditionally demanding the ORIGINAL
quantity back from the batch. Once a later Sale had consumed some or all of
that batch's stock, the correction was refused outright -- even for a
cost-only correction that never needed to touch quantity at all -- with a
generic "Batch X has only 0 available, but 5 requested" error.

Fix: correct_purchase_route() now unwinds a batch-tracked line through
_unwind_batch_tracked_purchase_for_correction() (salpurflask/purchase/
routes.py), which only removes whatever portion of the original receipt is
STILL physically sitting in BatchStock -- never re-removing what a later
Sale/Transfer/Adjustment already correctly took out, and never touching
SaleItemBatch/TransferItem/StockAdjustment. If the corrected quantity would
claim fewer units than have already been consumed from that same batch, the
whole correction is refused with a specific, actionable PostingError before
anything is written -- a genuine contradiction, not a clamping problem.

Nothing here touches Sale/POS/Transfer/Stock Adjustment code paths, or the
genuine full-reversal path (reverse_document()), which still correctly
refuses outright when a batch has moved on -- only correct_purchase_route()'s
own unwind step.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Supplier, Customer, Purchase, PurchaseItem,
    JournalEntry, StockMovement, PostingError,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, sync_customer_opening,
    Batch, PurchaseItemBatch, SaleItemBatch, Sale,
)
from salpurflask.models.inventory_location import BatchStock, ItemStock, get_or_create_default_location
from salpurflask.models.business_config import BusinessCategory


# ─── helpers (mirrors tests/test_batch_phase_b.py / test_batch_phase_c.py) ────


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()


def _item(name="Medicine", batch_tracked=True):
    bcat = BusinessCategory(name="Cat-" + name, slug="cat-" + name.lower(), is_enabled=True)
    db.session.add(bcat); db.session.flush()
    it = Item(name=name, business_category_id=bcat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             opening_stock=0, stock=0, inventory_value=Decimal("0"),
             batch_tracked=batch_tracked)
    db.session.add(it); db.session.commit()
    return it


def _supplier(name="Supplier A"):
    s = Supplier(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    from app import sync_supplier_opening
    sync_supplier_opening(s); db.session.commit()
    return s


def _customer(name="Customer A"):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


class _RoleClient:
    def __init__(self, client):
        self._client = client

    def _clear_g(self):
        from flask import g
        try:
            del g._login_user
        except AttributeError:
            pass

    def get(self, *a, **kw):
        self._clear_g(); return self._client.get(*a, **kw)

    def post(self, *a, **kw):
        self._clear_g(); return self._client.post(*a, **kw)


def _login(user):
    from flask import g
    try:
        del g._login_user
    except AttributeError:
        pass
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id); s["_fresh"] = True
    return _RoleClient(c)


def _manager(email="m@t.com"):
    u = User(name="M", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="manager")
    db.session.add(u); db.session.commit()
    return _login(u)


def _admin(email="a@t.com"):
    u = User(name="A", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="admin")
    db.session.add(u); db.session.commit()
    return _login(u)


def _purchase_via_form(client, supplier, item, qty, price, batch_no="", expiry_date="2027-06-30"):
    return client.post("/purchase", data={
        "supplier_id": str(supplier.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "purchase_price[]": str(price),
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
        "batch_no[]": batch_no, "expiry_date[]": expiry_date,
    }, follow_redirects=True)


def _latest_purchase():
    return Purchase.query.order_by(Purchase.id.desc()).first()


def _post(client, pur_id):
    return client.post(f"/purchase/{pur_id}/post", follow_redirects=True)


def _correct_via_form(client, pur, rows, reason="fix", confirm=False):
    """rows: list of (item, qty, price, batch_no, expiry_date) tuples."""
    data = {
        "supplier_id": str(pur.supplier_id), "notes": "", "reason": reason,
        "item_id[]": [str(r[0].id) for r in rows],
        "quantity[]": [str(r[1]) for r in rows],
        "purchase_price[]": [str(r[2]) for r in rows],
        "discount_type[]": ["percent"] * len(rows),
        "discount_value[]": ["0"] * len(rows),
        "tax_percent[]": ["0"] * len(rows),
        "batch_no[]": [r[3] for r in rows],
        "expiry_date[]": [r[4] for r in rows],
    }
    if confirm:
        data["confirm_correction"] = "1"
    return client.post(f"/purchase/correct/{pur.id}", data=data, follow_redirects=True)


def _sale_via_form(client, customer, item, qty, price):
    return client.post("/sale", data={
        "customer_id": str(customer.id), "date": "2026-01-02", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "sale_price[]": str(price),
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)


def _latest_sale():
    return Sale.query.order_by(Sale.id.desc()).first()


def _post_sale(client, sale_id):
    return client.post(f"/sale/{sale_id}/post", data={}, follow_redirects=True)


def _batchstock(item_id, batch_no, location_id=None):
    loc_id = location_id or get_or_create_default_location().id
    batch = Batch.query.filter_by(item_id=item_id, batch_no=batch_no).first()
    if batch is None:
        return None
    row = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    return row.quantity if row else 0


# ─── A: unchanged quantity/cost after full consumption ─────────────────────


def test_correction_unchanged_quantity_and_cost_after_full_sale_succeeds(appctx):
    """The reported bug, as a regression: Purchase +5 into B0145, Sale -5
    (batch fully consumed), then a correction that keeps quantity AND cost
    the same (e.g. only the notes/reason changed) must succeed -- it must
    never be blocked merely because the batch was subsequently sold."""
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)
    db.session.refresh(item)
    assert item.stock == 5

    _sale_via_form(manager, cust, item, 5, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    db.session.refresh(item)
    assert item.stock == 0
    assert _batchstock(item.id, "B0145") == 0

    resp = _correct_via_form(admin, pur, [(item, 5, 10, "B0145", "2027-06-30")],
                             reason="notes only")
    assert resp.status_code == 200
    assert b"corrected successfully" in resp.data or b"Cannot correct" not in resp.data

    db.session.refresh(pur)
    db.session.refresh(item)
    # Net physical effect: purchase still claims 5 received, sale still took
    # 5 -- item stock is unaffected by the correction (0 before, 0 after).
    assert item.stock == 0
    assert _batchstock(item.id, "B0145") == 0


# ─── B: quantity reduction below consumed amount is rejected ───────────────


def test_correction_reducing_quantity_below_consumed_amount_rejected(appctx):
    """Purchase +5 -> Sale -3 -> BatchStock=2. Reducing the corrected
    quantity to 2 would claim the purchase only ever received 2 units, but
    3 have already been demonstrably sold from that exact batch -- a
    contradiction. Must be refused, not silently accepted."""
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)

    _sale_via_form(manager, cust, item, 3, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    assert _batchstock(item.id, "B0145") == 2

    resp = _correct_via_form(admin, pur, [(item, 2, 10, "B0145", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" in resp.data
    assert b"already had 3 unit" in resp.data or b"already had 3" in resp.data

    db.session.refresh(pur)
    db.session.refresh(item)
    # Nothing changed -- the correction was fully rolled back.
    assert pur.quantity == 5
    assert item.stock == 2
    assert _batchstock(item.id, "B0145") == 2


def test_correction_reducing_quantity_to_exactly_consumed_amount_succeeds(appctx):
    """Purchase +5 -> Sale -3 -> BatchStock=2. Reducing the corrected
    quantity to 3 (exactly what was consumed) is the boundary-valid case:
    the batch ends with 0 available, matching physical reality exactly."""
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)

    _sale_via_form(manager, cust, item, 3, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    assert _batchstock(item.id, "B0145") == 2

    resp = _correct_via_form(admin, pur, [(item, 3, 10, "B0145", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" not in resp.data

    db.session.refresh(pur)
    db.session.refresh(item)
    assert pur.quantity == 3
    assert item.stock == 0
    assert _batchstock(item.id, "B0145") == 0


# ─── C: full consumption, reduce to 0 (drop the line) ──────────────────────


def test_correction_dropping_fully_consumed_line_rejected(appctx):
    """Purchase +5 -> Sale -5 -> BatchStock=0. Dropping the line entirely
    (the only way to represent "received 0" through this form, since
    quantity must be a positive whole number) is equivalent to claiming a
    corrected quantity of 0 for that batch -- but 5 units have already been
    demonstrably sold from it. Same contradiction rule as reducing to any
    other value below what's already consumed: must be rejected, not
    silently accepted as a no-op."""
    _books()
    sup = _supplier()
    cust = _customer()
    other_item = _item(name="Other")
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)

    _sale_via_form(manager, cust, item, 5, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    assert _batchstock(item.id, "B0145") == 0

    # Correct the purchase to a completely different item instead (the form
    # requires at least one row; this represents "drop the original line").
    resp = _correct_via_form(admin, pur, [(other_item, 5, 10, "", "")])
    assert resp.status_code == 200
    assert b"Cannot correct" in resp.data
    assert b"already had 5 unit" in resp.data

    db.session.refresh(pur)
    db.session.refresh(item)
    db.session.refresh(other_item)
    # Nothing changed -- fully rolled back.
    assert pur.quantity == 5
    assert item.stock == 0
    assert _batchstock(item.id, "B0145") == 0
    assert other_item.stock == 0
    assert Batch.query.filter_by(item_id=item.id, batch_no="B0145").count() == 1


# ─── D: batch number/expiry change after consumption ───────────────────────


def test_correction_changing_batch_number_after_partial_consumption_rejected(appctx):
    """Purchase +5 into B0145 -> Sale -3 -> BatchStock=2. Renaming the batch
    to B0146 would strand the 3-unit SaleItemBatch history against a batch
    number the corrected purchase no longer claims to have received into --
    the sale's own record permanently says B0145, and that can never
    retroactively become B0146. Renaming a batch is only safe while it is
    still fully untouched; once ANY of it has been consumed, the rename
    must be refused, using the exact same contradiction rule as a quantity
    reduction below the consumed amount (dropping the B0145 key from the
    corrected rows is indistinguishable from claiming 0 units for it)."""
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)

    _sale_via_form(manager, cust, item, 3, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    assert _batchstock(item.id, "B0145") == 2

    resp = _correct_via_form(admin, pur, [(item, 5, 10, "B0146", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" in resp.data
    assert b"already had 3 unit" in resp.data

    db.session.refresh(pur)
    db.session.refresh(item)
    # Nothing changed -- fully rolled back. B0145 still holds its 2
    # remaining units; B0146 was never created.
    assert pur.quantity == 5
    assert _batchstock(item.id, "B0145") == 2
    assert item.stock == 2
    assert Batch.query.filter_by(item_id=item.id, batch_no="B0146").count() == 0


def test_correction_changing_batch_number_before_any_consumption_succeeds(appctx):
    """Purchase +5 into B0145, nothing sold yet -- renaming to B0146 (same
    quantity) is the safe case: the batch is fully untouched, so the rename
    is a pure relabeling with no history to strand."""
    _books()
    sup = _supplier()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)

    resp = _correct_via_form(admin, pur, [(item, 5, 10, "B0146", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" not in resp.data

    db.session.refresh(item)
    assert _batchstock(item.id, "B0145") == 0
    assert _batchstock(item.id, "B0146") == 5
    assert item.stock == 5


# ─── E: cost correction after full consumption ─────────────────────────────


def test_cost_correction_after_full_consumption_with_different_cost_rejected(appctx):
    """Purchase +5 into B0145 @ cost 10 -> Sale -5 (fully consumed) ->
    correction keeps quantity at 5 but changes the unit price/cost to 12.
    get_or_create_batch()'s own existing cost-immutability guard refuses
    this (an established batch's unit_cost cannot silently change) -- the
    whole correction is rejected with a clear PostingError, not a corrupted
    valuation."""
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)

    _sale_via_form(manager, cust, item, 5, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    assert _batchstock(item.id, "B0145") == 0

    resp = _correct_via_form(admin, pur, [(item, 5, 12, "B0145", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" in resp.data or b"already exists at cost" in resp.data

    batch = Batch.query.filter_by(item_id=item.id, batch_no="B0145").first()
    assert Decimal(str(batch.unit_cost)) == Decimal("10.0000")


def test_cost_correction_after_full_consumption_with_same_cost_succeeds(appctx):
    """Same setup, but the corrected price matches the original unit cost
    exactly (e.g. only discount/tax metadata changed, net unit cost
    unchanged) -- this must succeed."""
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)

    _sale_via_form(manager, cust, item, 5, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)

    resp = _correct_via_form(admin, pur, [(item, 5, 10, "B0145", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" not in resp.data


# ─── F: multi-line purchase, one batch consumed -- atomic rollback ─────────


def test_multiline_purchase_one_consumed_batch_rolls_back_entire_correction(appctx):
    """Two items in one purchase: Item A's batch is fully consumed and the
    correction tries to reduce its quantity below what was sold (rejected);
    Item B's line would otherwise correct cleanly. The whole transaction
    must roll back -- Item B's stock/batch must be completely untouched,
    not partially corrected."""
    _books()
    sup = _supplier()
    cust = _customer()
    item_a = _item(name="Item-A")
    item_b = _item(name="Item-B")
    manager = _manager()
    admin = _admin()

    resp = manager.post("/purchase", data={
        "supplier_id": str(sup.id), "date": "2026-01-01", "notes": "",
        "item_id[]": [str(item_a.id), str(item_b.id)],
        "quantity[]": ["5", "5"], "purchase_price[]": ["10", "10"],
        "discount_type[]": ["percent", "percent"], "discount_value[]": ["0", "0"],
        "tax_percent[]": ["0", "0"],
        "batch_no[]": ["BA01", "BB01"], "expiry_date[]": ["2027-06-30", "2027-06-30"],
    }, follow_redirects=True)
    assert resp.status_code == 200
    pur = _latest_purchase()
    _post(manager, pur.id)
    db.session.refresh(item_a)
    db.session.refresh(item_b)
    assert item_a.stock == 5 and item_b.stock == 5

    _sale_via_form(manager, cust, item_a, 5, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    assert _batchstock(item_a.id, "BA01") == 0

    # Correction: Item A reduced to 0 units claimed (contradiction, since 5
    # were sold), Item B corrected to 7 (a normally-valid change).
    resp = _correct_via_form(admin, pur, [(item_b, 7, 10, "BB01", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" in resp.data

    db.session.refresh(pur)
    db.session.refresh(item_a)
    db.session.refresh(item_b)
    # Nothing changed for either item -- fully atomic.
    assert item_b.stock == 5
    assert _batchstock(item_b.id, "BB01") == 5
    assert item_a.stock == 0
    assert _batchstock(item_a.id, "BA01") == 0


# ─── G: no negative BatchStock ever ─────────────────────────────────────────


def test_no_negative_batchstock_across_all_scenarios(appctx):
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _sale_via_form(manager, cust, item, 5, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)

    _correct_via_form(admin, pur, [(item, 5, 10, "B0145", "2027-06-30")])

    batch = Batch.query.filter_by(item_id=item.id, batch_no="B0145").first()
    all_bstock = BatchStock.query.filter_by(batch_id=batch.id).all()
    assert all(b.quantity >= 0 for b in all_bstock)


# ─── H: ItemStock/BatchStock invariant ─────────────────────────────────────


def test_itemstock_batchstock_invariant_after_correction(appctx):
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _sale_via_form(manager, cust, item, 3, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)

    _correct_via_form(admin, pur, [(item, 3, 10, "B0145", "2027-06-30")])

    loc_id = get_or_create_default_location().id
    itemstock_qty = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first().quantity
    total_batchstock = sum(
        b.quantity for b in BatchStock.query.join(Batch).filter(Batch.item_id == item.id).all())
    assert itemstock_qty == total_batchstock


# ─── I: historical SaleItemBatch unchanged ─────────────────────────────────


def test_saleitembatch_unchanged_by_correction(appctx):
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item()
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _sale_via_form(manager, cust, item, 5, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)

    old_batch = Batch.query.filter_by(item_id=item.id, batch_no="B0145").first()
    before = [(a.sale_item_id, a.batch_id, a.quantity)
             for a in SaleItemBatch.query.filter_by(batch_id=old_batch.id).all()]
    assert before  # the sale really did allocate against B0145

    _correct_via_form(admin, pur, [(item, 5, 10, "B0145", "2027-06-30")])

    after = [(a.sale_item_id, a.batch_id, a.quantity)
             for a in SaleItemBatch.query.filter_by(batch_id=old_batch.id).all()]
    assert after == before
