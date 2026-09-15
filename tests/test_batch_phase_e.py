"""Batch/Lot + Expiry tracking - Phase E (Stock Adjustment).

Builds on Phase A's schema foundation and Phases B/C/D's Purchase/Sale/
Sale-Return/Transfer wiring by extending batch awareness to the last
originally-scoped deferred item: Stock Adjustment.

Approved design (this phase's own instructions):
- StockAdjustment.batch_id: one nullable FK, no junction table (one
  adjustment row = one batch, mirroring TransferItem.batch_id's shape).
- OUT (Stock Out / Damage Write-off / Sample-Free-Issue / Count Correction
  Decrease): mandatory manual batch selection, no FEFO fallback - a
  write-off is a specific physical batch, not "whichever expires soonest".
- IN (Stock In / Count Correction Increase): an existing batch to top up,
  or a new named batch, or the item's Unknown Batch when blank - reusing
  get_or_create_batch() unchanged, valued at item.avg_cost (the same figure
  this route already computed for every non-batch "in" before this phase).
- Reversal (via /document/stock_adjustment/<id>/reverse) restores/removes
  the exact batch recorded on StockAdjustment.batch_id.

Nothing here touches Purchase, Sale, Sale Return, or Transfer code paths -
only StockAdjustment's own model/route/reversal branch.
"""
import json
from datetime import date
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, StockAdjustment, PostingError,
    Batch,
    get_or_create_batch, item_add_stock_batched, item_add_stock,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links,
)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, BatchStock, StockMovement,
    get_or_create_default_location, stock_at_location,
)
from salpurflask.models.business_config import BusinessCategory


# ─── helpers ─────────────────────────────────────────────────────────────────


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()


def _item(name="Medicine", batch_tracked=True, stock=0):
    bcat = BusinessCategory(name="Cat-" + name, slug="cat-" + name.lower(), is_enabled=True)
    db.session.add(bcat); db.session.flush()
    it = Item(name=name, business_category_id=bcat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             opening_stock=0, stock=0, inventory_value=Decimal("0"),
             batch_tracked=batch_tracked)
    db.session.add(it); db.session.commit()
    if stock:
        item_add_stock(it, stock, Decimal(str(stock * 10)),
                       location_id=get_or_create_default_location().id)
        db.session.commit()
    return it


def _receive_batch(item, batch_no, qty, unit_cost=10, expiry_date=None, location_id=None):
    loc_id = location_id or get_or_create_default_location().id
    batch = get_or_create_batch(item.id, batch_no, expiry_date, unit_cost,
                                source_type="test", source_id=0)
    item_add_stock_batched(item, qty, Decimal(str(unit_cost)) * qty,
                           location_id=loc_id, batch=batch,
                           movement_type="purchase", source_type="test", source_id=0)
    db.session.commit()
    return batch


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


def _adjust(client, item, adj_type, qty, batch_id="", batch_no="", expiry_date="",
            location_id=None, reason="test"):
    data = {
        "item_id": str(item.id), "adj_type": adj_type, "quantity": str(qty),
        "date": "2026-03-01", "reason": reason,
        "batch_id": str(batch_id) if batch_id else "",
        "batch_no": batch_no, "expiry_date": expiry_date,
    }
    if location_id is not None:
        data["location_id"] = str(location_id)
    else:
        data["location_id"] = str(get_or_create_default_location().id)
    return client.post("/stock_adjustment", data=data, follow_redirects=True)


def _latest_adjustment():
    return StockAdjustment.query.order_by(StockAdjustment.id.desc()).first()


def _reverse_document(client, kind, doc_id):
    return client.post(f"/document/{kind}/{doc_id}/reverse", data={}, follow_redirects=True)


# ═══════════════════════════════════════════════════════════════════════════
# STOCK IN
# ═══════════════════════════════════════════════════════════════════════════


def test_batch_in_to_new_named_batch(appctx):
    _books()
    item = _item()
    manager = _manager("m1@t.com")

    _adjust(manager, item, "Stock In", 10, batch_no="NEWB", expiry_date="2027-06-30")

    adj = _latest_adjustment()
    assert adj.batch_id is not None
    batch = db.session.get(Batch, adj.batch_id)
    assert batch.batch_no == "NEWB"
    assert batch.expiry_date == date(2027, 6, 30)
    db.session.refresh(item)
    assert item.stock == 10


def test_batch_in_to_existing_batch(appctx):
    _books()
    item = _item()
    existing = _receive_batch(item, "B001", 5, unit_cost=10, expiry_date=date(2027, 1, 1))
    manager = _manager("m2@t.com")

    _adjust(manager, item, "Stock In", 3, batch_id=existing.id)

    adj = _latest_adjustment()
    assert adj.batch_id == existing.id
    loc_id = get_or_create_default_location().id
    bstock = BatchStock.query.filter_by(batch_id=existing.id, location_id=loc_id).first()
    assert bstock.quantity == 8  # 5 + 3
    db.session.refresh(item)
    assert item.stock == 8


def test_batch_in_to_unknown_batch(appctx):
    _books()
    item = _item()
    manager = _manager("m3@t.com")

    _adjust(manager, item, "Count Correction (Increase)", 4)  # no batch_no, no batch_id

    adj = _latest_adjustment()
    assert adj.batch_id is not None
    batch = db.session.get(Batch, adj.batch_id)
    assert batch.batch_no is None  # the Unknown Batch
    db.session.refresh(item)
    assert item.stock == 4


def test_batch_in_costed_at_item_avg_cost_for_new_batch(appctx):
    _books()
    item = _item()
    # Seed a non-batch-tracked cost basis so avg_cost is non-zero and known.
    item.purchase_price = Decimal("15")
    db.session.commit()
    manager = _manager("m4@t.com")

    _adjust(manager, item, "Stock In", 2, batch_no="COST1")

    adj = _latest_adjustment()
    batch = db.session.get(Batch, adj.batch_id)
    assert batch.unit_cost == Decimal("15.0000")
    assert adj.cost_value == Decimal("30.0000")  # 2 * 15


def test_batch_in_same_batch_different_cost_rejected(appctx):
    """The existing get_or_create_batch() same-batch/different-cost
    protection must remain enforced when reused here. item.avg_cost is a
    blended company-wide average (inventory_value / stock) -- to make it
    genuinely diverge from batch B001's own unit_cost (10), mix in stock
    from a second, higher-cost batch so the blend no longer equals 10."""
    _books()
    item = _item()
    _receive_batch(item, "B001", 5, unit_cost=10, expiry_date=date(2027, 1, 1))
    _receive_batch(item, "B002", 5, unit_cost=200, expiry_date=date(2027, 6, 1))
    db.session.refresh(item)
    assert item.avg_cost != Decimal("10.0000")  # confirm the blend actually diverged
    manager = _manager("m5@t.com")

    adjustments_before = StockAdjustment.query.count()
    _adjust(manager, item, "Stock In", 3, batch_no="B001")  # same batch_no, different cost

    assert StockAdjustment.query.count() == adjustments_before  # refused, nothing created


# ═══════════════════════════════════════════════════════════════════════════
# STOCK OUT
# ═══════════════════════════════════════════════════════════════════════════


def test_batch_out_with_valid_selected_batch(appctx):
    _books()
    item = _item()
    batch = _receive_batch(item, "B001", 10, unit_cost=10, expiry_date=date(2027, 1, 1))
    manager = _manager("m6@t.com")

    _adjust(manager, item, "Damage Write-off", 4, batch_id=batch.id)

    adj = _latest_adjustment()
    assert adj.batch_id == batch.id
    loc_id = get_or_create_default_location().id
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert bstock.quantity == 6
    db.session.refresh(item)
    assert item.stock == 6
    assert adj.cost_value == Decimal("40.0000")  # 4 * batch.unit_cost(10)


def test_batch_out_without_batch_rejected(appctx):
    _books()
    item = _item()
    _receive_batch(item, "B001", 10, unit_cost=10, expiry_date=date(2027, 1, 1))
    manager = _manager("m7@t.com")

    adjustments_before = StockAdjustment.query.count()
    _adjust(manager, item, "Stock Out", 3)  # no batch_id supplied

    assert StockAdjustment.query.count() == adjustments_before


def test_batch_out_insufficient_batchstock_rejected(appctx):
    _books()
    item = _item()
    batch = _receive_batch(item, "B001", 3, unit_cost=10, expiry_date=date(2027, 1, 1))
    manager = _manager("m8@t.com")

    adjustments_before = StockAdjustment.query.count()
    _adjust(manager, item, "Sample / Free Issue", 5, batch_id=batch.id)  # only 3 available

    assert StockAdjustment.query.count() == adjustments_before
    loc_id = get_or_create_default_location().id
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert bstock.quantity == 3  # untouched


def test_batch_out_selected_batch_wrong_item_rejected(appctx):
    _books()
    item_a = _item(name="Med-A")
    item_b = _item(name="Med-B")
    batch_b = _receive_batch(item_b, "B001", 10, unit_cost=10, expiry_date=date(2027, 1, 1))
    manager = _manager("m9@t.com")

    adjustments_before = StockAdjustment.query.count()
    _adjust(manager, item_a, "Stock Out", 2, batch_id=batch_b.id)  # batch belongs to item_b

    assert StockAdjustment.query.count() == adjustments_before


# ═══════════════════════════════════════════════════════════════════════════
# REVERSAL
# ═══════════════════════════════════════════════════════════════════════════


def test_reversal_of_batch_in(appctx):
    _books()
    item = _item()
    manager = _manager("m10@t.com")
    admin = _admin("a10@t.com")

    _adjust(manager, item, "Stock In", 6, batch_no="RIN")
    adj = _latest_adjustment()
    batch = db.session.get(Batch, adj.batch_id)
    db.session.refresh(item)
    assert item.stock == 6

    _reverse_document(admin, "stock_adjustment", adj.id)

    db.session.refresh(item)
    assert item.stock == 0
    loc_id = get_or_create_default_location().id
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert bstock.quantity == 0


def test_reversal_of_batch_out(appctx):
    _books()
    item = _item()
    batch = _receive_batch(item, "B001", 10, unit_cost=10, expiry_date=date(2027, 1, 1))
    manager = _manager("m11@t.com")
    admin = _admin("a11@t.com")

    _adjust(manager, item, "Stock Out", 4, batch_id=batch.id)
    adj = _latest_adjustment()
    db.session.refresh(item)
    assert item.stock == 6

    _reverse_document(admin, "stock_adjustment", adj.id)

    db.session.refresh(item)
    assert item.stock == 10
    loc_id = get_or_create_default_location().id
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert bstock.quantity == 10


def test_reversal_stock_movement_tagged_with_batch_id(appctx):
    _books()
    item = _item()
    manager = _manager("m12@t.com")
    admin = _admin("a12@t.com")
    _adjust(manager, item, "Stock In", 5, batch_no="MVB")
    adj = _latest_adjustment()
    batch = db.session.get(Batch, adj.batch_id)

    _reverse_document(admin, "stock_adjustment", adj.id)

    movements = StockMovement.query.filter_by(source_type="stock_adjustment", source_id=adj.id).all()
    assert len(movements) >= 2  # original create + reversal
    assert all(m.batch_id == batch.id for m in movements)


# ═══════════════════════════════════════════════════════════════════════════
# ZERO-COST DELETE PATH
# ═══════════════════════════════════════════════════════════════════════════


def test_zero_cost_batch_delete_keeps_batchstock_itemstock_consistent(appctx):
    """delete_stock_adjustment() is reachable only when cost_value == 0
    (post_stock_adjustment() posts no JournalEntry in that case, so
    assert_not_posted's guard passes) -- confirmed by the Phase E audit.
    A batch whose unit_cost is 0 reaches exactly this path; the delete
    route must still move BatchStock together with ItemStock."""
    _books()
    item = _item()
    zero_cost_batch = get_or_create_batch(item.id, "ZERO", date(2027, 1, 1), 0,
                                          source_type="test", source_id=0)
    db.session.commit()
    manager = _manager("m13@t.com")
    admin = _admin("a13@t.com")

    _adjust(manager, item, "Stock In", 5, batch_id=zero_cost_batch.id)
    adj = _latest_adjustment()
    assert adj.cost_value == Decimal("0.0000")
    db.session.refresh(item)
    assert item.stock == 5

    resp = admin.post(f"/stock_adjustment/delete/{adj.id}", follow_redirects=True)

    db.session.refresh(item)
    assert item.stock == 0
    loc_id = get_or_create_default_location().id
    bstock = BatchStock.query.filter_by(batch_id=zero_cost_batch.id, location_id=loc_id).first()
    assert bstock.quantity == 0
    istock = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first()
    assert istock.quantity == 0
    assert db.session.get(StockAdjustment, adj.id) is None  # actually deleted


def test_nonzero_cost_delete_remains_unreachable_regression(appctx):
    """Confirms the existing dead-code shape is unchanged: a non-zero-cost
    adjustment posts a JournalEntry, so assert_not_posted refuses the
    delete route, exactly as it always has for Purchase/Sale."""
    _books()
    item = _item(batch_tracked=False)
    item.purchase_price = Decimal("10")
    db.session.commit()
    manager = _manager("m14@t.com")
    admin = _admin("a14@t.com")

    _adjust(manager, item, "Stock In", 5)
    adj = _latest_adjustment()
    assert adj.cost_value > 0

    resp = admin.post(f"/stock_adjustment/delete/{adj.id}", follow_redirects=True)

    # Refused -- the adjustment must still exist.
    assert db.session.get(StockAdjustment, adj.id) is not None


# ═══════════════════════════════════════════════════════════════════════════
# INVARIANTS / REGRESSION / LOCKING
# ═══════════════════════════════════════════════════════════════════════════


def test_batchstock_itemstock_invariant_after_in_and_out(appctx):
    _books()
    item = _item()
    manager = _manager("m15@t.com")
    _adjust(manager, item, "Stock In", 10, batch_no="INV1", expiry_date="2027-01-01")
    adj_in = _latest_adjustment()
    batch = db.session.get(Batch, adj_in.batch_id)

    _adjust(manager, item, "Damage Write-off", 4, batch_id=batch.id)

    loc_id = get_or_create_default_location().id
    istock = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first()
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert istock.quantity == bstock.quantity == 6


def test_non_batch_stock_adjustment_regression(appctx):
    _books()
    item = _item(batch_tracked=False)
    item.purchase_price = Decimal("10")
    db.session.commit()
    manager = _manager("m16@t.com")

    _adjust(manager, item, "Stock In", 8)
    db.session.refresh(item)
    assert item.stock == 8
    adj = _latest_adjustment()
    assert adj.batch_id is None

    _adjust(manager, item, "Stock Out", 3)
    db.session.refresh(item)
    assert item.stock == 5
    adj2 = _latest_adjustment()
    assert adj2.batch_id is None


def test_stock_adjustment_item_first_lock_order_static(appctx):
    """SQLite cannot demonstrate real concurrent locking (see Phase A-D's
    own precedent) -- proves the source-level ordering instead: the route
    calls get_item_locked() before any Batch/BatchStock access."""
    import inspect
    from salpurflask.inventory import routes as inv_routes

    source = inspect.getsource(inv_routes.stock_adjustment)
    lock_pos = source.index("get_item_locked(int(item_id))")
    batch_pos = source.index("item_obj.batch_tracked")
    assert lock_pos < batch_pos


def test_reversal_branch_locks_item_before_batch_lookup_static(appctx):
    import inspect
    from salpurflask.models import models as models_module

    source = inspect.getsource(models_module._unwind_stock_and_subledger)
    # The stock_adjustment branch reads db.session.get(Item, doc.item_id)
    # (the equivalent of the Item lock read on this document type -- see
    # every other kind's branch in this same function for the identical
    # pattern) before it reaches doc.batch_id.
    adj_branch_start = source.index('if kind == "stock_adjustment":')
    item_get_pos = source.index("db.session.get(Item, doc.item_id)", adj_branch_start)
    batch_id_pos = source.index("doc.batch_id", adj_branch_start)
    assert item_get_pos < batch_id_pos


def test_atomic_rollback_on_get_or_create_batch_failure_for_in(appctx):
    """A same-batch/different-cost PostingError during the IN path must
    roll back the whole request -- no StockAdjustment row, no stock
    movement, no partial BatchStock/ItemStock change."""
    _books()
    item = _item()
    _receive_batch(item, "CONFLICT", 5, unit_cost=10, expiry_date=date(2027, 1, 1))
    _receive_batch(item, "OTHER", 5, unit_cost=200, expiry_date=date(2027, 6, 1))
    db.session.refresh(item)  # avg_cost now conflicts with CONFLICT's own unit_cost (10)
    manager = _manager("m17@t.com")

    stock_before = item.stock
    adjustments_before = StockAdjustment.query.count()
    movements_before = StockMovement.query.filter_by(item_id=item.id).count()

    _adjust(manager, item, "Stock In", 3, batch_no="CONFLICT")

    assert StockAdjustment.query.count() == adjustments_before
    assert StockMovement.query.filter_by(item_id=item.id).count() == movements_before
    db.session.refresh(item)
    assert item.stock == stock_before
