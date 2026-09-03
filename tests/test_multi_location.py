"""Multi-branch / multi-warehouse foundation — Phase 1.

Item.stock stays the company-wide total; ItemStock is the new per-location
truth. The two are kept equal by item_add_stock()/item_remove_stock() writing
both in the same transaction — the central invariant this file exists to
prove, from every angle: migration, mutation, insufficient stock, and
transaction rollback.
"""
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import app as flask_app, db, Item, Category, PostingError, User, pwd_context
from salpurflask.models.business_config import BusinessCategory
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, get_or_create_default_location,
    get_or_create_item_stock, backfill_item_stock_locations,
)
from salpurflask.models.models import item_add_stock, item_remove_stock


def _admin(email="admin@multiloc.com"):
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _enabled_category(name="Test Category"):
    bc = BusinessCategory(name=name, slug=name.lower().replace(" ", "-"), is_enabled=True)
    db.session.add(bc)
    db.session.commit()
    return bc


# ── helpers ───────────────────────────────────────────────────────────────────

def _item(stock=0, item_type="STOCK", value=0):
    cat = Category.query.first()
    if cat is None:
        cat = Category(name="Cat")
        db.session.add(cat)
        db.session.flush()
    it = Item(name=f"Item-{Item.query.count()}", category_id=cat.id, stock=stock,
             item_type=item_type, inventory_value=Decimal(str(value)))
    db.session.add(it)
    db.session.commit()
    return it


def _company_total():
    return sum(i.stock or 0 for i in Item.query.filter_by(item_type="STOCK").all())


def _itemstock_total():
    return sum(r.quantity for r in ItemStock.query.all())


# ── default warehouse / location creation ──────────────────────────────────────

def test_default_branch_and_location_created_on_first_call(appctx):
    assert Branch.query.count() == 0
    assert Location.query.count() == 0
    loc = get_or_create_default_location()
    assert Branch.query.count() == 1
    assert Location.query.count() == 1
    assert loc.is_default is True
    assert loc.branch.is_default is True


def test_get_or_create_default_location_is_idempotent(appctx):
    loc1 = get_or_create_default_location()
    loc2 = get_or_create_default_location()
    assert loc1.id == loc2.id
    assert Location.query.count() == 1


# ── ItemStock creation ──────────────────────────────────────────────────────────

def test_get_or_create_item_stock_creates_at_zero(appctx):
    it = _item(stock=0)
    loc = get_or_create_default_location()
    row = get_or_create_item_stock(it.id, loc.id)
    assert row.quantity == 0
    assert ItemStock.query.count() == 1


def test_get_or_create_item_stock_returns_existing_row(appctx):
    it = _item(stock=0)
    loc = get_or_create_default_location()
    row1 = get_or_create_item_stock(it.id, loc.id)
    row1.quantity = 5
    db.session.commit()
    row2 = get_or_create_item_stock(it.id, loc.id)
    assert row1.id == row2.id
    assert row2.quantity == 5
    assert ItemStock.query.count() == 1


def test_item_stock_unique_constraint_blocks_duplicate_pair(appctx):
    it = _item(stock=0)
    loc = get_or_create_default_location()
    db.session.add(ItemStock(item_id=it.id, location_id=loc.id, quantity=1))
    db.session.commit()
    db.session.add(ItemStock(item_id=it.id, location_id=loc.id, quantity=2))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


# ── migration of existing Item.stock ────────────────────────────────────────────

def test_migration_preserves_positive_stock(appctx):
    it = _item(stock=42, value=420)
    backfill_item_stock_locations()
    rows = ItemStock.query.filter_by(item_id=it.id).all()
    assert len(rows) == 1
    assert rows[0].quantity == 42
    assert it.stock == 42  # untouched by the backfill


def test_migration_creates_zero_stock_row(appctx):
    it = _item(stock=0)
    backfill_item_stock_locations()
    rows = ItemStock.query.filter_by(item_id=it.id).all()
    assert len(rows) == 1
    assert rows[0].quantity == 0


def test_migration_skips_service_items(appctx):
    it = _item(stock=0, item_type="SERVICE")
    backfill_item_stock_locations()
    assert ItemStock.query.filter_by(item_id=it.id).count() == 0


def test_migration_is_idempotent(appctx):
    _item(stock=10)
    _item(stock=20)
    backfill_item_stock_locations()
    n1 = ItemStock.query.count()
    backfill_item_stock_locations()
    n2 = ItemStock.query.count()
    assert n1 == n2 == 2


def test_migration_preserves_company_total_across_many_items(appctx):
    for qty in (0, 5, 17, 0, 100, 3):
        _item(stock=qty)
    total_before = _company_total()
    backfill_item_stock_locations()
    assert _itemstock_total() == total_before


def test_migration_does_not_reseed_an_item_already_stocked_elsewhere(appctx):
    """An item that already has an ItemStock row (e.g. created after Phase 1
    landed) must not get a second, conflicting default-location row from a
    later backfill run."""
    it = _item(stock=10)
    loc = get_or_create_default_location()
    db.session.add(ItemStock(item_id=it.id, location_id=loc.id, quantity=999))
    db.session.commit()
    backfill_item_stock_locations()
    rows = ItemStock.query.filter_by(item_id=it.id).all()
    assert len(rows) == 1
    assert rows[0].quantity == 999  # untouched, not overwritten to match Item.stock


# ── item_add_stock() / item_remove_stock() ──────────────────────────────────────

def test_add_stock_updates_both_item_and_itemstock(appctx):
    it = _item(stock=0)
    item_add_stock(it, 10, Decimal("100"))
    db.session.commit()
    assert it.stock == 10
    rows = ItemStock.query.filter_by(item_id=it.id).all()
    assert len(rows) == 1
    assert rows[0].quantity == 10


def test_remove_stock_updates_both_item_and_itemstock(appctx):
    it = _item(stock=10, value=100)
    item_remove_stock(it, 4)
    db.session.commit()
    assert it.stock == 6
    rows = ItemStock.query.filter_by(item_id=it.id).all()
    assert rows[0].quantity == 6


def test_repeated_mutations_keep_item_and_itemstock_equal(appctx):
    it = _item(stock=0)
    item_add_stock(it, 20, Decimal("200"))
    item_remove_stock(it, 5)
    item_add_stock(it, 3, Decimal("30"))
    item_remove_stock(it, 8)
    db.session.commit()
    row = ItemStock.query.filter_by(item_id=it.id).first()
    assert it.stock == 10
    assert row.quantity == 10


def test_add_stock_without_prior_itemstock_row_creates_one(appctx):
    """An item that predates this phase (no ItemStock row yet) still works
    correctly the first time it's touched — the lazy-create path."""
    it = _item(stock=0)
    assert ItemStock.query.filter_by(item_id=it.id).count() == 0
    item_add_stock(it, 7, Decimal("70"))
    db.session.commit()
    assert ItemStock.query.filter_by(item_id=it.id).count() == 1
    assert ItemStock.query.filter_by(item_id=it.id).first().quantity == 7


# ── insufficient / negative stock behaviour ─────────────────────────────────────

def test_insufficient_stock_raises_and_leaves_both_tables_unchanged(appctx):
    it = _item(stock=6, value=60)
    item_add_stock(it, 0, Decimal("0"))  # ensure an ItemStock row exists
    db.session.commit()
    with pytest.raises(PostingError):
        item_remove_stock(it, 999)
    db.session.rollback()
    db.session.refresh(it)
    assert it.stock == 6
    row = ItemStock.query.filter_by(item_id=it.id).first()
    assert row.quantity == 6


def test_current_rules_refuse_negative_stock(appctx):
    """TradeFlow's existing rule (unchanged by this phase): item_remove_stock
    refuses to take out more than is on hand — negative stock is not
    reachable through the choke point."""
    it = _item(stock=2, value=20)
    with pytest.raises(PostingError):
        item_remove_stock(it, 3)
    db.session.rollback()
    db.session.refresh(it)
    assert it.stock == 2


# ── transaction / rollback safety ───────────────────────────────────────────────

def test_rollback_after_add_stock_reverts_both_tables(appctx):
    it = _item(stock=5, value=50)
    item_add_stock(it, 10, Decimal("100"))
    db.session.rollback()
    db.session.refresh(it)
    assert it.stock == 5
    # the pre-existing ItemStock row (if any) is also reverted; none exists
    # yet for this item, so nothing should have been committed either
    assert ItemStock.query.filter_by(item_id=it.id).count() == 0


def test_rollback_mid_transaction_never_leaves_partial_stock_state(appctx):
    it = _item(stock=20, value=200)
    item_remove_stock(it, 5)  # in-session, not yet committed
    # simulate the rest of the caller's transaction failing before commit
    db.session.rollback()
    db.session.refresh(it)
    assert it.stock == 20
    assert ItemStock.query.filter_by(item_id=it.id).count() == 0


# ── the central invariant ────────────────────────────────────────────────────────

def test_item_stock_equals_sum_of_itemstock_after_mixed_activity(appctx):
    items = [_item(stock=q) for q in (0, 10, 25, 3)]
    backfill_item_stock_locations()
    item_add_stock(items[0], 5, Decimal("50"))
    item_remove_stock(items[1], 4)
    item_add_stock(items[2], 100, Decimal("1000"))
    item_remove_stock(items[3], 1)
    db.session.commit()

    for it in items:
        db.session.refresh(it)
        total_at_locations = sum(
            r.quantity for r in ItemStock.query.filter_by(item_id=it.id).all())
        assert it.stock == total_at_locations, (
            f"{it.name}: Item.stock={it.stock} != SUM(ItemStock)={total_at_locations}")


# ── existing inventory workflow regression (spot-check, not full replay) ───────

def test_existing_purchase_style_stock_increase_still_works(appctx):
    """Mirrors what purchase/routes.py does: item_add_stock, then commit.
    Confirms the new location_id=None default keeps this call site working
    exactly as it did before this phase, with no code change required there."""
    it = _item(stock=0)
    item_add_stock(it, 15, Decimal("150"))
    db.session.commit()
    assert it.stock == 15
    assert it.inventory_value == Decimal("150.0000") or it.inventory_value == Decimal("150")


def test_existing_sale_style_stock_decrease_still_works(appctx):
    it = _item(stock=15, value=150)
    cost = item_remove_stock(it, 5)
    db.session.commit()
    assert it.stock == 10
    assert cost > 0


def test_avg_cost_property_unaffected_by_location_tracking(appctx):
    it = _item(stock=0)
    item_add_stock(it, 10, Decimal("100"))
    db.session.commit()
    assert it.avg_cost == Decimal("10.0000")


# ── newly discovered direct-mutation paths, now routed to create ItemStock ─────

def test_item_creation_route_creates_matching_itemstock(appctx):
    from app import seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    cat = _enabled_category()
    c = _admin()
    resp = c.post("/item", data={
        "name": "Created Widget", "business_category_id": str(cat.id),
        "unit": "Pcs", "item_type": "STOCK", "opening_stock": "25",
        "reorder_level": "5", "purchase_price": "10", "sale_price": "20",
    }, follow_redirects=True)
    assert resp.status_code == 200
    it = Item.query.filter_by(name="Created Widget").first()
    assert it is not None
    assert it.stock == 25
    row = ItemStock.query.filter_by(item_id=it.id).first()
    assert row is not None
    assert row.quantity == 25


def test_item_creation_with_zero_opening_stock_still_creates_itemstock_row(appctx):
    cat = _enabled_category()
    c = _admin()
    c.post("/item", data={
        "name": "Zero Widget", "business_category_id": str(cat.id),
        "unit": "Pcs", "item_type": "STOCK", "opening_stock": "0",
        "reorder_level": "5",
    }, follow_redirects=True)
    it = Item.query.filter_by(name="Zero Widget").first()
    assert it is not None
    row = ItemStock.query.filter_by(item_id=it.id).first()
    assert row is not None
    assert row.quantity == 0


def test_service_item_creation_gets_no_itemstock_row(appctx):
    cat = _enabled_category()
    c = _admin()
    c.post("/item", data={
        "name": "Consulting", "business_category_id": str(cat.id),
        "unit": "Pcs", "item_type": "SERVICE", "opening_stock": "0",
    }, follow_redirects=True)
    it = Item.query.filter_by(name="Consulting").first()
    assert it is not None
    assert ItemStock.query.filter_by(item_id=it.id).count() == 0


def test_transaction_reset_reseeds_itemstock_consistently(appctx):
    """app.py's reset_transactions() zeroes Item.stock and wipes item_stock
    along with every other transaction table. Confirms the fix: after reset,
    Item.stock == SUM(ItemStock.quantity) still holds, not "0 rows vs a
    nonzero total" or the reverse."""
    from app import reset_transactions

    it = _item(stock=30, value=300)
    backfill_item_stock_locations()
    assert ItemStock.query.filter_by(item_id=it.id).first().quantity == 30

    reset_transactions()

    db.session.refresh(it)
    assert it.stock == 0
    row = ItemStock.query.filter_by(item_id=it.id).first()
    assert row is not None
    assert row.quantity == 0
