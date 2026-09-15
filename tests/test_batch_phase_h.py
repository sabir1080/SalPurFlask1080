"""Batch/Lot + Expiry tracking - Phase H (Batch-Aware Inventory
Reconciliation).

post_reconciliation() (salpurflask/services/inventory_reconciliation.py)
applies every line's variance through item_add_stock()/item_remove_stock()
- the only door into ItemStock. For a batch_tracked item this left
BatchStock silently unmirrored: ItemStock/Item.stock moved correctly, but
no BatchStock row was ever touched, an invisible divergence exactly like
the one Phase G closed at the "enable tracking" boundary, now reachable at
the "reconciliation posting" boundary instead.

Approved design (this phase's own instructions):
- Counting stays item-level only (no schema change, no counting-UI change)
  - a "found" quantity has no real batch identity to record beyond the
  item's single Unknown Batch (get_or_create_unknown_batch()), the same
  principle Phase G's enable-tracking migration already applies.
- Stock IN (positive variance) for a batch_tracked item goes through
  item_add_stock_batched() against the Unknown Batch.
- Stock OUT (negative variance) for a batch_tracked item goes through
  item_remove_stock_batched() against the Unknown Batch, UNMODIFIED - if
  that specific batch does not have enough BatchStock at this location, it
  raises PostingError exactly as it always has for every other batch-aware
  flow, never silently deducting from a differently-named batch and never
  inventing batch identity. That PostingError aborts posting entirely via
  the existing single-transaction/rollback shape (reconciliation_post() in
  the route module) - the reconciliation stays Approved, not a half-applied
  Posted.
- net_value/GL math is completely unaffected: item_add_stock_batched()/
  item_remove_stock_batched() are thin wrappers around the exact same
  item_add_stock()/item_remove_stock() calls, returning/consuming identical
  values.
- A non-batch-tracked item's line is untouched - the exact same code path
  as before this phase.

Nothing here touches Purchase, Sale, Sale Return, Transfer, Stock
Adjustment, or Phase G's enable-tracking code paths - only
post_reconciliation()'s own per-line branching.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from app import (
    app as flask_app, db, User, pwd_context, Category, Item, JournalEntry,
    Batch, PostingError,
    seed_chart_of_accounts, seed_fiscal_year,
    get_or_create_batch, item_add_stock_batched,
)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, BatchStock, StockMovement,
    InventoryReconciliation, InventoryReconciliationLine, UserLocationAccess,
    get_or_create_default_location, stock_at_location,
)
from salpurflask.models.models import item_add_stock
from salpurflask.services import inventory_reconciliation as svc


# ─── helpers (mirrors tests/test_inventory_reconciliation.py) ──────────────


def _world():
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    db.session.commit()


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
    return c


def _user(role, email=None):
    email = email or f"{role}{User.query.count()}@reconh.com"
    u = User(name=role.capitalize(), email=email, password=pwd_context.hash("secret123"),
            verified=True, role=role)
    db.session.add(u)
    db.session.commit()
    return u


def _admin(email=None):
    return _user("admin", email=email)


def _manager(email=None):
    return _user("manager", email=email)


def _grant(user, location):
    db.session.add(UserLocationAccess(user_id=user.id, location_id=location.id))
    db.session.commit()


def _second_location(name="Second WH-H"):
    branch = Branch.query.filter_by(is_default=True).first()
    loc = Location(name=name, kind="warehouse", branch_id=branch.id)
    db.session.add(loc)
    db.session.commit()
    return loc


def _item(stock=0, name=None, location=None, batch_tracked=False):
    cat = Category.query.first()
    if cat is None:
        cat = Category(name="Cat-H")
        db.session.add(cat)
        db.session.flush()
    it = Item(name=name or f"Item-H-{Item.query.count()}", category_id=cat.id,
             stock=0, purchase_price=10, sale_price=20, item_type="STOCK",
             batch_tracked=batch_tracked)
    db.session.add(it)
    db.session.commit()
    if stock:
        item_add_stock(it, stock, Decimal(str(stock * 10)),
                       location_id=(location or get_or_create_default_location()).id)
        db.session.commit()
    return it


def _batch_item_with_unknown_stock(stock, location=None, unit_cost=10, name=None):
    """A batch_tracked item whose existing stock already lives in its
    Unknown Batch - the state a Phase G enable-tracking migration would
    leave it in, or that item_add_stock_batched() against
    get_or_create_unknown_batch() produces directly."""
    it = _item(stock=0, name=name, batch_tracked=True)
    loc = location or get_or_create_default_location()
    if stock:
        from salpurflask.models.models import get_or_create_unknown_batch
        batch = get_or_create_unknown_batch(it.id)
        item_add_stock_batched(it, stock, Decimal(str(stock * unit_cost)),
                               location_id=loc.id, batch=batch,
                               movement_type="purchase", source_type="test", source_id=0)
        db.session.commit()
    return it


def _named_batch_stock(item, batch_no, qty, unit_cost=10, location=None):
    """Give a batch_tracked item some stock under a NAMED batch (not
    Unknown) - used to prove an OUT variance never touches it."""
    loc = location or get_or_create_default_location()
    batch = get_or_create_batch(item.id, batch_no, None, unit_cost,
                                source_type="test", source_id=0)
    item_add_stock_batched(item, qty, Decimal(str(qty * unit_cost)),
                           location_id=loc.id, batch=batch,
                           movement_type="purchase", source_type="test", source_id=0)
    db.session.commit()
    return batch


def _unknown_batch(item_id):
    return Batch.query.filter_by(item_id=item_id, batch_no=None).first()


def _draft(location, items, counted_by=None, counts=None):
    r = svc.create_reconciliation(
        location_id=location.id, item_ids=[i.id for i in items],
        date=datetime(2026, 3, 1), created_by_id=getattr(counted_by, "id", None))
    db.session.commit()
    if counts:
        svc.save_counts(r, counts)
        db.session.commit()
    return r


def _finalized(location, items, counts, counted_by):
    r = _draft(location, items, counted_by=counted_by, counts=counts)
    svc.finalize_count(r, counted_by_id=counted_by.id)
    db.session.commit()
    return r


def _approved(location, items, counts, counted_by, approved_by):
    r = _finalized(location, items, counts, counted_by)
    svc.approve_reconciliation(r, approved_by_id=approved_by.id)
    db.session.commit()
    return r


# ─── 1: batch-tracked positive variance ─────────────────────────────────────


def test_batch_tracked_positive_variance_increases_unknown_batch_batchstock(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _batch_item_with_unknown_stock(100, location=loc)
    r = _approved(loc, [item], {item.id: 105}, admin, _admin(email="h1@reconh.com"))

    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    batch = _unknown_batch(item.id)
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).first()
    assert bstock.quantity == 105  # 100 + 5 found


# ─── 2: batch-tracked negative variance ─────────────────────────────────────


def test_batch_tracked_negative_variance_decreases_unknown_batch_batchstock(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _batch_item_with_unknown_stock(100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="h2@reconh.com"))

    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    batch = _unknown_batch(item.id)
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).first()
    assert bstock.quantity == 97  # 100 - 3 lost


# ─── 3: Unknown Batch insufficient for OUT ─────────────────────────────────


def test_unknown_batch_insufficient_for_out_variance_rejects_entire_posting(appctx):
    """The user's own worked example: ItemStock=20 (Unknown=5, Named=15),
    physical count=10, variance=-10. Unknown Batch alone (5) cannot cover
    it -- posting must be refused outright, never deduct from Named."""
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _batch_item_with_unknown_stock(5, location=loc)  # Unknown Batch = 5
    named = _named_batch_stock(item, "NAMEDX", 15, location=loc)  # Named Batch A = 15
    db.session.refresh(item)
    assert item.stock == 20

    r = _approved(loc, [item], {item.id: 10}, admin, _admin(email="h3@reconh.com"))

    with pytest.raises(PostingError):
        svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.rollback()

    db.session.refresh(r)
    assert r.status == "Approved"  # never advanced to Posted

    db.session.refresh(item)
    assert item.stock == 20  # untouched
    assert stock_at_location(item.id, loc.id) == 20

    unknown = _unknown_batch(item.id)
    unknown_bstock = BatchStock.query.filter_by(batch_id=unknown.id, location_id=loc.id).first()
    assert unknown_bstock.quantity == 5  # untouched
    named_bstock = BatchStock.query.filter_by(batch_id=named.id, location_id=loc.id).first()
    assert named_bstock.quantity == 15  # untouched -- never silently raided

    assert JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).count() == 0
    assert StockMovement.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).count() == 0


# ─── 4: SUM(BatchStock) == ItemStock invariant ─────────────────────────────


def test_batchstock_matches_itemstock_after_successful_in_and_out(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()

    item_in = _batch_item_with_unknown_stock(50, location=loc, name="In-Item")
    r_in = _approved(loc, [item_in], {item_in.id: 55}, admin, _admin(email="h4a@reconh.com"))
    svc.post_reconciliation(r_in, posted_by_id=admin.id)
    db.session.commit()
    db.session.refresh(item_in)
    batch_in = _unknown_batch(item_in.id)
    total_in = sum(b.quantity for b in BatchStock.query.filter_by(batch_id=batch_in.id).all())
    assert total_in == item_in.stock == ItemStock.query.filter_by(
        item_id=item_in.id, location_id=loc.id).first().quantity == 55

    item_out = _batch_item_with_unknown_stock(50, location=loc, name="Out-Item")
    r_out = _approved(loc, [item_out], {item_out.id: 44}, admin, _admin(email="h4b@reconh.com"))
    svc.post_reconciliation(r_out, posted_by_id=admin.id)
    db.session.commit()
    db.session.refresh(item_out)
    batch_out = _unknown_batch(item_out.id)
    total_out = sum(b.quantity for b in BatchStock.query.filter_by(batch_id=batch_out.id).all())
    assert total_out == item_out.stock == ItemStock.query.filter_by(
        item_id=item_out.id, location_id=loc.id).first().quantity == 44


# ─── 5: non-batch item regression ──────────────────────────────────────────


def test_non_batch_item_reconciliation_unaffected(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc, batch_tracked=False)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="h5@reconh.com"))

    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    db.session.refresh(item)
    assert item.stock == 97
    assert stock_at_location(item.id, loc.id) == 97
    # No Batch/BatchStock rows exist at all for a non-batch-tracked item.
    assert Batch.query.filter_by(item_id=item.id).count() == 0


# ─── 6: mixed batch/non-batch reconciliation ───────────────────────────────


def test_mixed_batch_and_non_batch_reconciliation(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    batch_item = _batch_item_with_unknown_stock(30, location=loc, name="Mixed-Batch")
    plain_item = _item(stock=30, location=loc, name="Mixed-Plain", batch_tracked=False)

    r = _approved(loc, [batch_item, plain_item],
                 {batch_item.id: 33, plain_item.id: 28}, admin, _admin(email="h6@reconh.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    db.session.refresh(batch_item)
    db.session.refresh(plain_item)
    assert batch_item.stock == 33
    assert plain_item.stock == 28
    batch = _unknown_batch(batch_item.id)
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).first()
    assert bstock.quantity == 33
    assert Batch.query.filter_by(item_id=plain_item.id).count() == 0

    # One reconciliation, one net-valued JournalEntry covering both lines.
    entries = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).all()
    assert len(entries) == 1


def test_mixed_reconciliation_out_failure_rolls_back_the_batch_line_too(appctx):
    """A batch-tracked line's rejected OUT variance must roll back the
    WHOLE reconciliation, including a non-batch-tracked line that would
    otherwise have succeeded on its own."""
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    batch_item = _batch_item_with_unknown_stock(5, location=loc, name="MixFail-Batch")
    _named_batch_stock(batch_item, "MIXNAMED", 15, location=loc)
    plain_item = _item(stock=30, location=loc, name="MixFail-Plain", batch_tracked=False)
    db.session.refresh(batch_item)
    assert batch_item.stock == 20

    r = _approved(loc, [batch_item, plain_item],
                 {batch_item.id: 10, plain_item.id: 25}, admin, _admin(email="h6b@reconh.com"))

    with pytest.raises(PostingError):
        svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.rollback()

    db.session.refresh(r)
    assert r.status == "Approved"
    db.session.refresh(plain_item)
    assert plain_item.stock == 30  # the non-batch line's own would-be-successful move was rolled back too


# ─── 7: StockMovement.batch_id correctness ─────────────────────────────────


def test_batch_tracked_variance_stock_movement_carries_unknown_batch_id(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _batch_item_with_unknown_stock(20, location=loc)
    r = _approved(loc, [item], {item.id: 25}, admin, _admin(email="h7@reconh.com"))

    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    batch = _unknown_batch(item.id)
    mv = StockMovement.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).first()
    assert mv is not None
    assert mv.batch_id == batch.id


# ─── 8: GL/net_value amounts equivalent to the unbatched path ─────────────


def test_batch_tracked_positive_variance_posts_same_gl_amount_as_non_batch(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    batch_item = _batch_item_with_unknown_stock(100, location=loc, name="GL-Batch")
    r = _approved(loc, [batch_item], {batch_item.id: 105}, admin, _admin(email="h8a@reconh.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    entry = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).first()
    total_dr = sum(l.debit for l in entry.lines)
    total_cr = sum(l.credit for l in entry.lines)
    assert total_dr == total_cr == Decimal("50.0000")  # 5 units * avg_cost 10, same as the non-batch test


def test_batch_tracked_negative_variance_posts_same_gl_amount_as_non_batch(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    batch_item = _batch_item_with_unknown_stock(100, location=loc, name="GL-Batch-Out")
    r = _approved(loc, [batch_item], {batch_item.id: 97}, admin, _admin(email="h8b@reconh.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    entry = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).first()
    total_dr = sum(l.debit for l in entry.lines)
    total_cr = sum(l.credit for l in entry.lines)
    assert total_dr == total_cr == Decimal("30.0000")  # 3 units * avg_cost 10, same as the non-batch test


# ─── 9: already-Posted guard still protects a batch-tracked reconciliation ─


def test_already_posted_batch_reconciliation_cannot_repost(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _batch_item_with_unknown_stock(50, location=loc)
    r = _approved(loc, [item], {item.id: 55}, admin, _admin(email="h9@reconh.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    with pytest.raises(PostingError):
        svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.rollback()

    entries = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).all()
    assert len(entries) == 1  # no duplicate


# ─── 10: non-admin permission regression (route level) ────────────────────


def test_manager_cannot_post_batch_tracked_reconciliation_via_route(appctx):
    _world()
    admin = _admin()
    manager = _manager(email="h10mgr@reconh.com")
    loc = get_or_create_default_location()
    item = _batch_item_with_unknown_stock(20, location=loc)
    r = _approved(loc, [item], {item.id: 22}, admin, _admin(email="h10@reconh.com"))

    c = _login(manager)
    resp = c.post(f"/reconciliations/{r.id}/post", follow_redirects=False)
    assert resp.status_code == 302  # admin_required redirects a non-admin

    db.session.refresh(r)
    assert r.status == "Approved"


# ─── 11: location-security regression ──────────────────────────────────────


def test_restricted_manager_cannot_view_batch_reconciliation_at_inaccessible_location(appctx):
    """Posting itself is @admin_required, and an admin is never location-
    restricted in this codebase's model (accessible_location_ids() returns
    None -- unrestricted -- for any admin, confirmed by every existing
    Phase 5 test in test_inventory_reconciliation.py, which only ever
    exercises location-restriction against a manager). The genuinely
    enforced 403 for a batch-tracked reconciliation is the same
    _get_reconciliation_or_403()/require_location_access() gate every other
    reconciliation action already goes through -- reached here via the
    manager-only reconciliation_detail() view, exactly like the existing
    (non-batch) Phase 5 location tests in that file."""
    _world()
    admin = _admin()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    mgr = _manager(email="h11mgr@reconh.com")
    _grant(mgr, loc_a)  # access to A only, not B

    item = _batch_item_with_unknown_stock(20, location=loc_b)
    r = _draft(loc_b, [item])

    c = _login(mgr)
    resp = c.get(f"/reconciliations/{r.id}")
    assert resp.status_code == 403

    db.session.refresh(r)
    assert r.status == "Draft"  # untouched


# ─── 12: existing reconciliation suite unaffected ──────────────────────────
# Run separately: `pytest -q tests/test_inventory_reconciliation.py`
# (not duplicated here -- see the implementation report's test run for
# confirmation all 37 existing tests still pass unmodified).


# ─── 13: static test -- batch-tracked branch never uses the unbatched pair ─


def test_post_reconciliation_batch_branch_uses_batched_wrappers_static(appctx):
    """Source-level proof that a batch_tracked line's IN/OUT calls go
    through item_add_stock_batched()/item_remove_stock_batched(), inside an
    `if item.batch_tracked:` guard -- not the unbatched item_add_stock()/
    item_remove_stock() calls used for a non-batch-tracked line."""
    import inspect

    source = inspect.getsource(svc.post_reconciliation)
    assert "item_add_stock_batched(" in source
    assert "item_remove_stock_batched(" in source
    assert "get_or_create_unknown_batch(" in source

    # The batched calls must appear after an `if item.batch_tracked:` guard,
    # not unconditionally.
    guard_pos = source.index("if item.batch_tracked:")
    batched_in_pos = source.index("item_add_stock_batched(", guard_pos)
    assert guard_pos < batched_in_pos
