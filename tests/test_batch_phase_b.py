"""Batch/Lot + Expiry tracking - Phase B (Purchase + Purchase Correction).

Builds on Phase A's schema foundation (tests/test_batch_phase_a.py) by wiring
batch-tracked Purchase lines through the real Draft -> Post -> Correction
lifecycle: a Draft's pending_batch_no/pending_expiry_date are plain text/date
until Post, Post turns them into a real Batch via get_or_create_batch() and
item_add_stock_batched(), and correction/reversal unwinds through
PurchaseItemBatch allocations instead of one aggregate item_remove_stock()
call. Nothing here touches Sale/POS batch logic, Purchase Returns, Transfers,
Stock Adjustment, or reports - those are out of scope for this phase (see
Stock Adjustment's own note in the Phase B report: it has no batch_id column
today and was deliberately left untouched here).
"""
from datetime import date
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Supplier, Purchase, PurchaseItem, SupplierPayment,
    JournalEntry, StockMovement, PostingError,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening, sync_supplier_opening,
    Batch, PurchaseItemBatch,
)
from salpurflask.models.models import STATUS_DRAFT, STATUS_POSTED
from salpurflask.models.inventory_location import BatchStock, ItemStock, get_or_create_default_location
from salpurflask.models.business_config import BusinessCategory


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()
    return FinancialAccount.query.filter_by(name="Cash").first().id


def _item(name="Medicine", stock=0, batch_tracked=True):
    bcat = BusinessCategory(name="Cat-" + name, slug="cat-" + name.lower(), is_enabled=True)
    db.session.add(bcat); db.session.flush()
    it = Item(name=name, business_category_id=bcat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             opening_stock=stock, stock=stock, inventory_value=Decimal(str(stock * 10)),
             batch_tracked=batch_tracked)
    db.session.add(it); db.session.flush()
    if stock:
        post_item_opening(it)
    db.session.commit()
    return it


def _supplier(name="Supplier A"):
    s = Supplier(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


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
        self._clear_g()
        return self._client.get(*a, **kw)

    def post(self, *a, **kw):
        self._clear_g()
        return self._client.post(*a, **kw)


def _login(user):
    from flask import g
    try:
        del g._login_user
    except AttributeError:
        pass
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
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


def _purchase_via_form(client, supplier, item, qty, price, batch_no="", expiry_date="",
                        disc_type="percent", disc_value="0", tax="0"):
    return client.post("/purchase", data={
        "supplier_id": str(supplier.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "purchase_price[]": str(price),
        "discount_type[]": disc_type, "discount_value[]": disc_value, "tax_percent[]": tax,
        "batch_no[]": batch_no, "expiry_date[]": expiry_date,
    }, follow_redirects=True)


def _latest_purchase():
    return Purchase.query.order_by(Purchase.id.desc()).first()


def _post(client, pur_id):
    return client.post(f"/purchase/{pur_id}/post", follow_redirects=True)


# ─── Draft: pending fields ──────────────────────────────────────────────────


def test_draft_purchase_saves_pending_batch_no_and_expiry_for_batch_tracked_item(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()

    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")

    pur = _latest_purchase()
    pi = pur.line_items[0]
    assert pi.pending_batch_no == "B001"
    assert pi.pending_expiry_date == date(2027, 6, 30)


def test_draft_purchase_ignores_submitted_batch_fields_for_non_batch_tracked_item(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=False)
    client = _manager()

    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")

    pur = _latest_purchase()
    pi = pur.line_items[0]
    assert pi.pending_batch_no is None
    assert pi.pending_expiry_date is None


def test_draft_purchase_with_blank_batch_no_leaves_pending_batch_no_null(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()

    _purchase_via_form(client, sup, item, 5, 10, batch_no="", expiry_date="")

    pi = _latest_purchase().line_items[0]
    assert pi.pending_batch_no is None
    assert pi.pending_expiry_date is None


def test_draft_purchase_creates_no_batch_or_batchstock_rows(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()

    batches_before = Batch.query.count()
    bstock_before = BatchStock.query.count()
    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")

    assert Batch.query.count() == batches_before
    assert BatchStock.query.count() == bstock_before


def test_draft_purchase_batch_tracked_item_has_no_stock_effect(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50, batch_tracked=True)
    client = _manager()

    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")

    db.session.refresh(item)
    assert item.stock == 50


# ─── Post: batch creation & stock effects ──────────────────────────────────


def test_post_creates_batch_with_correct_batch_no_and_expiry(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    pur = _latest_purchase()

    _post(client, pur.id)

    batch = Batch.query.filter_by(item_id=item.id, batch_no="B001").first()
    assert batch is not None
    assert batch.expiry_date == date(2027, 6, 30)


def test_post_creates_purchase_item_batch_allocation(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    pur = _latest_purchase()
    pi = pur.line_items[0]

    _post(client, pur.id)

    allocs = PurchaseItemBatch.query.filter_by(purchase_item_id=pi.id).all()
    assert len(allocs) == 1
    assert allocs[0].quantity == 5


def test_post_increases_item_stock_for_batch_tracked_item(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=0, batch_tracked=True)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    pur = _latest_purchase()

    _post(client, pur.id)

    db.session.refresh(item)
    assert item.stock == 5


def test_post_increases_batchstock_matching_itemstock(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=0, batch_tracked=True)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    pur = _latest_purchase()

    _post(client, pur.id)

    db.session.refresh(item)
    loc_id = get_or_create_default_location().id
    istock = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first()
    batch = Batch.query.filter_by(item_id=item.id, batch_no="B001").first()
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert istock.quantity == bstock.quantity == 5


def test_post_with_null_batch_no_uses_unknown_batch(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10, batch_no="", expiry_date="")
    pur = _latest_purchase()

    _post(client, pur.id)

    batch = Batch.query.filter_by(item_id=item.id, batch_no=None).first()
    assert batch is not None


def test_post_twice_with_same_batch_no_and_same_cost_reuses_batch(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()

    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    _post(client, _latest_purchase().id)
    batches_after_first = Batch.query.filter_by(item_id=item.id, batch_no="B001").count()

    _purchase_via_form(client, sup, item, 3, 10, batch_no="B001", expiry_date="2027-06-30")
    _post(client, _latest_purchase().id)
    batches_after_second = Batch.query.filter_by(item_id=item.id, batch_no="B001").count()

    assert batches_after_first == 1
    assert batches_after_second == 1
    db.session.refresh(item)
    assert item.stock == 8


def test_post_twice_with_same_batch_no_different_cost_is_refused(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()

    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    _post(client, _latest_purchase().id)

    _purchase_via_form(client, sup, item, 3, 25, batch_no="B001", expiry_date="2027-06-30")
    second = _latest_purchase()
    resp = _post(client, second.id)

    db.session.refresh(second)
    assert second.status == STATUS_DRAFT
    db.session.refresh(item)
    assert item.stock == 5


def test_post_non_batch_tracked_item_unaffected_uses_plain_stock_path(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=0, batch_tracked=False)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()

    _post(client, pur.id)

    db.session.refresh(item)
    assert item.stock == 5
    assert PurchaseItemBatch.query.join(PurchaseItem).filter(
        PurchaseItem.purchase_id == pur.id).count() == 0


def test_post_expired_batch_is_allowed_at_receiving(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10, batch_no="OLD1", expiry_date="2020-01-01")
    pur = _latest_purchase()

    _post(client, pur.id)

    db.session.refresh(pur)
    assert pur.status == STATUS_POSTED
    batch = Batch.query.filter_by(item_id=item.id, batch_no="OLD1").first()
    assert batch.expiry_date == date(2020, 1, 1)


def test_post_stock_movement_tagged_with_batch_id(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    pur = _latest_purchase()

    _post(client, pur.id)

    batch = Batch.query.filter_by(item_id=item.id, batch_no="B001").first()
    movement = StockMovement.query.filter_by(item_id=item.id, source_type="purchase",
                                              source_id=pur.id).first()
    assert movement is not None
    assert movement.batch_id == batch.id


# ─── Atomicity ───────────────────────────────────────────────────────────


def test_post_rollback_on_batch_conflict_leaves_no_partial_state_across_lines(appctx):
    """Two lines on one Draft: the first line's item has a fresh batch number,
    the second line's item reuses a batch number at a conflicting cost. The
    whole Post must fail and roll back -- including the first line's stock
    increase, which committed nothing because the whole route is one
    transaction."""
    _books()
    sup = _supplier()
    item_a = _item(name="Med-A", batch_tracked=True)
    item_b = _item(name="Med-B", batch_tracked=True)
    client = _manager()

    # Pre-seed item_b with a batch at cost 10 via a first Purchase.
    _purchase_via_form(client, sup, item_b, 2, 10, batch_no="CONFLICT", expiry_date="2027-01-01")
    _post(client, _latest_purchase().id)
    db.session.refresh(item_b)
    stock_b_before = item_b.stock

    # New Draft, two lines: item_a (fine) then item_b at a different cost for
    # the same batch number (must fail).
    resp = client.post("/purchase", data={
        "supplier_id": str(sup.id), "date": "2026-01-01", "notes": "",
        "item_id[]": [str(item_a.id), str(item_b.id)],
        "quantity[]": ["5", "3"],
        "purchase_price[]": ["10", "99"],
        "discount_type[]": ["percent", "percent"],
        "discount_value[]": ["0", "0"],
        "tax_percent[]": ["0", "0"],
        "batch_no[]": ["NEWBATCH", "CONFLICT"],
        "expiry_date[]": ["2027-01-01", "2027-01-01"],
    }, follow_redirects=True)
    pur = _latest_purchase()

    _post(client, pur.id)

    db.session.refresh(pur)
    db.session.refresh(item_a)
    db.session.refresh(item_b)
    assert pur.status == STATUS_DRAFT
    assert item_a.stock == 0
    assert item_b.stock == stock_b_before
    assert Batch.query.filter_by(item_id=item_a.id, batch_no="NEWBATCH").count() == 0


# ─── Correction / Reversal ──────────────────────────────────────────────


def _correct_via_form(client, pur, item, qty, price, batch_no, expiry_date, reason="fix"):
    return client.post(f"/purchase/correct/{pur.id}", data={
        "supplier_id": str(pur.supplier_id), "notes": "", "reason": reason,
        "item_id[]": str(item.id), "quantity[]": str(qty), "purchase_price[]": str(price),
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
        "batch_no[]": batch_no, "expiry_date[]": expiry_date,
    }, follow_redirects=True)


def test_correction_of_batch_tracked_purchase_reverses_old_batch_stock(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    manager = _manager()
    admin = _admin()
    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    pur = _latest_purchase()
    _post(manager, pur.id)
    db.session.refresh(item)
    assert item.stock == 5

    _correct_via_form(admin, pur, item, 3, 10, "B001", "2027-06-30")

    db.session.refresh(item)
    assert item.stock == 3


def test_correction_creates_new_purchase_item_batch_allocation(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    manager = _manager()
    admin = _admin()
    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    pur = _latest_purchase()
    _post(manager, pur.id)

    _correct_via_form(admin, pur, item, 7, 10, "B001", "2027-06-30")

    db.session.refresh(pur)
    pi = pur.line_items[0]
    allocs = PurchaseItemBatch.query.filter_by(purchase_item_id=pi.id).all()
    assert len(allocs) == 1
    assert allocs[0].quantity == 7


def test_correction_does_not_delete_batch_record(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    manager = _manager()
    admin = _admin()
    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B001", expiry_date="2027-06-30")
    pur = _latest_purchase()
    _post(manager, pur.id)
    batch_id = Batch.query.filter_by(item_id=item.id, batch_no="B001").first().id

    _correct_via_form(admin, pur, item, 3, 10, "B001", "2027-06-30")

    assert db.session.get(Batch, batch_id) is not None


def test_correction_of_non_batch_tracked_purchase_behaves_as_before(appctx):
    _books()
    sup = _supplier()
    item = _item(batch_tracked=False)
    manager = _manager()
    admin = _admin()
    _purchase_via_form(manager, sup, item, 5, 10)
    pur = _latest_purchase()
    _post(manager, pur.id)
    db.session.refresh(item)
    assert item.stock == 5

    admin.post(f"/purchase/correct/{pur.id}", data={
        "supplier_id": str(pur.supplier_id), "notes": "", "reason": "fix",
        "item_id[]": str(item.id), "quantity[]": "8", "purchase_price[]": "10",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)

    db.session.refresh(item)
    assert item.stock == 8
    assert PurchaseItemBatch.query.join(PurchaseItem).filter(
        PurchaseItem.purchase_id == pur.id).count() == 0
