"""Batch/Lot + Expiry tracking - Phase G (Enable Batch Tracking for Existing
Stock).

Turns batch_tracked on for an item that already has stock, without losing,
duplicating, or re-costing that stock. Existing stock has no real batch
history, so it is folded into the item's single Unknown Batch (batch_no=None)
- one BatchStock row per location the item already sits at, each set to
exactly that location's existing ItemStock.quantity (or Item.stock directly,
for a legacy pre-multi-warehouse item with no ItemStock rows yet).

Approved design (this phase's own instructions):
- enable_batch_tracking(item_id, *, created_by_id=None) in models.py: locks
  the Item first, is an idempotent no-op if already batch_tracked, validates
  Item.stock/ItemStock.quantity are never negative (PostingError otherwise),
  reuses get_or_create_unknown_batch()/_get_or_create_batch_stock(), never
  touches Item.stock/ItemStock.quantity/inventory_value, never writes a
  StockMovement or JournalEntry, does not commit (caller owns the
  transaction).
- enable_item_batch_tracking(id) route in inventory/routes.py: @admin_required
  (not manager_required - this migrates every location with no location
  filter, so a manager restricted to a subset of warehouses must not be able
  to trigger it), calls the model function, owns commit/rollback, redirects
  to edit_item.

Nothing here touches Purchase, Sale, Sale Return, Transfer, or Stock
Adjustment code paths - only the new enable-tracking function/route.
"""
from datetime import date
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, PostingError, Batch, JournalEntry,
    enable_batch_tracking, item_add_stock,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links,
)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, BatchStock, StockMovement,
    get_or_create_default_location,
)
from salpurflask.models.business_config import BusinessCategory


# ─── helpers (mirrors tests/test_batch_phase_e.py / test_batch_phase_f.py) ────


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()


def _item(name="Medicine", stock=0, batch_tracked=False):
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


def _admin(email="a@t.com"):
    u = User(name="A", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="admin")
    db.session.add(u); db.session.commit()
    return _login(u), u


def _manager(email="m@t.com"):
    u = User(name="M", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="manager")
    db.session.add(u); db.session.commit()
    return _login(u), u


def _enable_via_route(client, item):
    return client.post(f"/item/{item.id}/enable-batch-tracking", follow_redirects=True)


def _unknown_batch(item_id):
    return Batch.query.filter_by(item_id=item_id, batch_no=None).first()


# ─── 1-2: existing stock, single/multiple locations ────────────────────────


def test_enable_existing_stock_single_location(appctx):
    _books()
    item = _item(stock=10)
    db.session.commit()

    enable_batch_tracking(item.id)
    db.session.commit()

    db.session.refresh(item)
    assert item.batch_tracked is True
    batch = _unknown_batch(item.id)
    assert batch is not None
    loc_id = get_or_create_default_location().id
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert bstock.quantity == 10


def test_enable_existing_stock_multiple_locations(appctx):
    _books()
    item = _item(stock=0)
    branch = Branch(name="B1"); db.session.add(branch); db.session.flush()
    loc_a = Location(name="WH A", branch_id=branch.id, active=True)
    loc_b = Location(name="WH B", branch_id=branch.id, active=True)
    db.session.add_all([loc_a, loc_b]); db.session.commit()
    item_add_stock(item, 6, Decimal("60"), location_id=loc_a.id)
    item_add_stock(item, 4, Decimal("40"), location_id=loc_b.id)
    db.session.commit()

    enable_batch_tracking(item.id)
    db.session.commit()

    batch = _unknown_batch(item.id)
    bs_a = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_a.id).first()
    bs_b = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_b.id).first()
    assert bs_a.quantity == 6
    assert bs_b.quantity == 4


# ─── 3: legacy Item.stock with no ItemStock rows ───────────────────────────


def test_enable_legacy_item_stock_without_itemstock(appctx):
    _books()
    item = _item(stock=0)
    # Simulate a pre-multi-warehouse legacy item: real stock, zero ItemStock rows.
    item.stock = 15
    db.session.commit()
    assert ItemStock.query.filter_by(item_id=item.id).count() == 0

    enable_batch_tracking(item.id)
    db.session.commit()

    batch = _unknown_batch(item.id)
    loc_id = get_or_create_default_location().id
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert bstock.quantity == 15
    db.session.refresh(item)
    assert item.stock == 15  # untouched


# ─── 4: zero stock ──────────────────────────────────────────────────────────


def test_enable_zero_stock_creates_batch_no_batchstock(appctx):
    _books()
    item = _item(stock=0)

    enable_batch_tracking(item.id)
    db.session.commit()

    db.session.refresh(item)
    assert item.batch_tracked is True
    batch = _unknown_batch(item.id)
    assert batch is not None
    assert BatchStock.query.filter_by(batch_id=batch.id).count() == 0


# ─── 5-6: invalid stock rejection ──────────────────────────────────────────


def test_enable_rejects_negative_item_stock(appctx):
    _books()
    item = _item(stock=0)
    item.stock = -5
    db.session.commit()

    try:
        enable_batch_tracking(item.id)
        assert False, "expected PostingError"
    except PostingError:
        db.session.rollback()

    db.session.refresh(item)
    assert item.batch_tracked is False
    assert _unknown_batch(item.id) is None


def test_enable_rejects_negative_itemstock_row(appctx):
    _books()
    item = _item(stock=10)
    loc_id = get_or_create_default_location().id
    row = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first()
    row.quantity = -3
    db.session.commit()

    try:
        enable_batch_tracking(item.id)
        assert False, "expected PostingError"
    except PostingError:
        db.session.rollback()

    db.session.refresh(item)
    assert item.batch_tracked is False
    assert _unknown_batch(item.id) is None


# ─── 7-8: Unknown Batch singularity + exact quantities ─────────────────────


def test_enable_creates_exactly_one_unknown_batch(appctx):
    _books()
    item = _item(stock=8)

    enable_batch_tracking(item.id)
    db.session.commit()

    assert Batch.query.filter_by(item_id=item.id, batch_no=None).count() == 1


def test_enable_batchstock_quantity_matches_itemstock_exactly(appctx):
    _books()
    item = _item(stock=0)
    branch = Branch(name="B2"); db.session.add(branch); db.session.flush()
    loc = Location(name="WH X", branch_id=branch.id, active=True)
    db.session.add(loc); db.session.commit()
    item_add_stock(item, 17, Decimal("170"), location_id=loc.id)
    db.session.commit()
    expected = ItemStock.query.filter_by(item_id=item.id, location_id=loc.id).first().quantity

    enable_batch_tracking(item.id)
    db.session.commit()

    batch = _unknown_batch(item.id)
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).first()
    assert bstock.quantity == expected == 17


# ─── 9-12: existing data untouched ──────────────────────────────────────────


def test_enable_item_stock_unchanged(appctx):
    _books()
    item = _item(stock=12)
    before = item.stock

    enable_batch_tracking(item.id)
    db.session.commit()

    db.session.refresh(item)
    assert item.stock == before


def test_enable_itemstock_quantities_unchanged(appctx):
    _books()
    item = _item(stock=9)
    loc_id = get_or_create_default_location().id
    before = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first().quantity

    enable_batch_tracking(item.id)
    db.session.commit()

    after = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first().quantity
    assert after == before


def test_enable_inventory_value_unchanged(appctx):
    _books()
    item = _item(stock=5)
    before = Decimal(str(item.inventory_value))

    enable_batch_tracking(item.id)
    db.session.commit()

    db.session.refresh(item)
    assert Decimal(str(item.inventory_value)) == before


def test_enable_avg_cost_unchanged(appctx):
    _books()
    item = _item(stock=5)
    before = item.avg_cost

    enable_batch_tracking(item.id)
    db.session.commit()

    db.session.refresh(item)
    assert item.avg_cost == before
    # And the Unknown Batch is costed at exactly that figure.
    batch = _unknown_batch(item.id)
    assert Decimal(str(batch.unit_cost)) == before


# ─── 13-14: no StockMovement / no GL entry ─────────────────────────────────


def test_enable_creates_no_stock_movement(appctx):
    _books()
    item = _item(stock=7)
    before_count = StockMovement.query.filter_by(item_id=item.id).count()

    enable_batch_tracking(item.id)
    db.session.commit()

    after_count = StockMovement.query.filter_by(item_id=item.id).count()
    assert after_count == before_count


def test_enable_creates_no_journal_entry(appctx):
    _books()
    item = _item(stock=7)
    before_count = JournalEntry.query.count()

    enable_batch_tracking(item.id)
    db.session.commit()

    after_count = JournalEntry.query.count()
    assert after_count == before_count


# ─── 15: idempotent second call ────────────────────────────────────────────


def test_enable_idempotent_second_call(appctx):
    _books()
    item = _item(stock=10)

    enable_batch_tracking(item.id)
    db.session.commit()
    batch_id_first = _unknown_batch(item.id).id
    loc_id = get_or_create_default_location().id
    qty_first = BatchStock.query.filter_by(batch_id=batch_id_first, location_id=loc_id).first().quantity

    enable_batch_tracking(item.id)  # second call -- should be a no-op
    db.session.commit()

    assert Batch.query.filter_by(item_id=item.id, batch_no=None).count() == 1
    batch_id_second = _unknown_batch(item.id).id
    assert batch_id_second == batch_id_first
    qty_second = BatchStock.query.filter_by(batch_id=batch_id_second, location_id=loc_id).first().quantity
    assert qty_second == qty_first == 10


def test_enable_preserves_established_unit_cost_on_reenable(appctx):
    """If the Unknown Batch already exists (e.g. from a prior enable, or any
    other path that created it) with a real unit_cost, a repeat call must
    not silently overwrite it -- even if Item.avg_cost has since changed."""
    _books()
    item = _item(stock=10)
    enable_batch_tracking(item.id)
    db.session.commit()
    batch = _unknown_batch(item.id)
    batch.unit_cost = Decimal("99.9999")
    db.session.commit()

    enable_batch_tracking(item.id)  # already batch_tracked -> no-op path
    db.session.commit()

    db.session.refresh(batch)
    assert Decimal(str(batch.unit_cost)) == Decimal("99.9999")


# ─── 16: permission rejection ───────────────────────────────────────────────


def test_enable_route_requires_admin_manager_rejected(appctx):
    _books()
    item = _item(stock=10)
    manager, _ = _manager()

    resp = manager.post(f"/item/{item.id}/enable-batch-tracking", follow_redirects=False)
    assert resp.status_code == 302  # role_required redirects non-admins

    db.session.refresh(item)
    assert item.batch_tracked is False


def test_enable_route_admin_succeeds(appctx):
    _books()
    item = _item(stock=10)
    admin, _ = _admin()

    resp = _enable_via_route(admin, item)
    assert resp.status_code == 200

    db.session.refresh(item)
    assert item.batch_tracked is True


# ─── 17: atomic rollback ────────────────────────────────────────────────────


def test_enable_atomic_rollback_on_invalid_stock_via_route(appctx):
    _books()
    item = _item(stock=0)
    item.stock = -1
    db.session.commit()
    admin, _ = _admin()

    resp = _enable_via_route(admin, item)
    assert resp.status_code == 200  # flashed error, redirected back

    db.session.refresh(item)
    assert item.batch_tracked is False
    assert _unknown_batch(item.id) is None
    assert BatchStock.query.join(Batch).filter(Batch.item_id == item.id).count() == 0


# ─── 18: static Item-first lock-order test ─────────────────────────────────


def test_enable_batch_tracking_locks_item_first_static(appctx):
    """SQLite cannot demonstrate real concurrent locking (see Phase A-F's own
    precedent) -- proves the source-level ordering instead: get_item_locked()
    is called before any Batch/BatchStock access."""
    import inspect
    from salpurflask.models import models as models_module

    source = inspect.getsource(models_module.enable_batch_tracking)
    lock_pos = source.index("item = get_item_locked(item_id)")
    # search only the executable body after the lock call -- the function's
    # own docstring mentions get_or_create_unknown_batch() by name earlier,
    # which is not a real call and must not be matched here.
    batch_pos = source.index("get_or_create_unknown_batch(", lock_pos)
    assert lock_pos < batch_pos
