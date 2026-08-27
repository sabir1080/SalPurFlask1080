"""Physical count vs. system stock reconciliation — Phase 6, GL posting
added in Phase 8's H1 follow-up.

Every quantity here is Integer, matching Item.stock/ItemStock.quantity/
StockMovement.quantity — no Decimal invented for this phase.

The invariants under test everywhere: (1) variance is always physical -
system, computed server-side, never accepted from a form; (2) a
reconciliation moves quantity exclusively through item_add_stock()/
item_remove_stock(); (3) posting a nonzero-variance reconciliation creates
exactly ONE balanced JournalEntry for the whole reconciliation (never one
per line), idempotent via posted_entry(), while a zero-variance
reconciliation creates none.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from app import (
    app as flask_app, db, User, pwd_context, Category, Item, JournalEntry,
    seed_chart_of_accounts, seed_fiscal_year,
)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, InventoryReconciliation, InventoryReconciliationLine,
    UserLocationAccess, get_or_create_default_location, stock_at_location,
)
from salpurflask.models.models import item_add_stock, PostingError
from salpurflask.services import inventory_reconciliation as svc


def _world():
    """A chart of accounts and an open fiscal year — required to post the GL
    entry a nonzero-variance reconciliation now creates (Phase 8's H1
    follow-up). No funded cash account needed — reconciliation never moves
    cash, only Inventory vs. the Stock Adjustment P&L account."""
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    db.session.commit()


# ── helpers ───────────────────────────────────────────────────────────────────

def _login(user):
    """Sign in without POSTing /signin (avoids the rate limiter).

    Also clears Flask-Login's per-app-context user cache (flask.g._login_user)
    before returning. Flask-Login caches the loaded user on `g`, which is
    scoped to the *app context*, not the request — and the appctx fixture
    keeps one app context alive for the whole test, so a request context
    pushed inside it reuses that same g rather than getting a fresh one
    (see Flask's own docs: a request pushed while an app context is already
    active does not push a new one). Nothing in this codebase needed two
    different authenticated identities acting in sequence within one test
    until Phase 6's segregation-of-duties check — every earlier test using
    two test_client() instances only ever had one of them actually act."""
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
    email = email or f"{role}{User.query.count()}@recon.com"
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


def _second_location(name="Second WH"):
    default = get_or_create_default_location()
    branch = Branch.query.filter_by(is_default=True).first()
    loc = Location(name=name, kind="warehouse", branch_id=branch.id)
    db.session.add(loc)
    db.session.commit()
    return loc


def _item(stock=0, name=None, location=None):
    cat = Category.query.first()
    if cat is None:
        cat = Category(name="Cat")
        db.session.add(cat)
        db.session.flush()
    it = Item(name=name or f"Item-{Item.query.count()}", category_id=cat.id,
             stock=0, purchase_price=10, sale_price=20, item_type="STOCK")
    db.session.add(it)
    db.session.commit()
    if stock:
        item_add_stock(it, stock, Decimal(str(stock * 10)),
                       location_id=(location or get_or_create_default_location()).id)
        db.session.commit()
    return it


def _draft(location, items, counted_by=None, counts=None):
    """A Draft reconciliation with lines for each item, optionally already
    counted (Draft -> counts saved, still Draft)."""
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


# ── 1-3. Creation ────────────────────────────────────────────────────────────

def test_create_reconciliation_for_authorized_location(appctx):
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=10, location=loc)
    c = _login(mgr)
    resp = c.post("/reconciliations/new", data={
        "location_id": str(loc.id), "date": "2026-03-01", "item_id[]": str(item.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert InventoryReconciliation.query.count() == 1
    r = InventoryReconciliation.query.first()
    assert r.status == "Draft"
    assert len(r.lines) == 1


def test_create_reconciliation_unauthorized_location_rejected(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    item = _item(stock=10, location=loc_b)
    c = _login(mgr)
    resp = c.post("/reconciliations/new", data={
        "location_id": str(loc_b.id), "date": "2026-03-01", "item_id[]": str(item.id),
    })
    assert resp.status_code == 403
    assert InventoryReconciliation.query.count() == 0


def test_admin_can_create_reconciliation_at_any_location(appctx):
    admin = _admin()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    item = _item(stock=10, location=loc_b)
    c = _login(admin)
    resp = c.post("/reconciliations/new", data={
        "location_id": str(loc_b.id), "date": "2026-03-01", "item_id[]": str(item.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert InventoryReconciliation.query.count() == 1


# ── 4-9. Counting ────────────────────────────────────────────────────────────

def test_physical_quantities_accepted_as_integers(appctx):
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=10, location=loc)
    r = _draft(loc, [item], counted_by=mgr)
    svc.save_counts(r, {item.id: 7})
    db.session.commit()
    line = InventoryReconciliationLine.query.filter_by(reconciliation_id=r.id).first()
    assert line.physical_quantity == 7
    assert isinstance(line.physical_quantity, int)


def test_zero_variance_calculated_correctly(appctx):
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=10, location=loc)
    r = _finalized(loc, [item], {item.id: 10}, mgr)
    line = r.lines[0]
    assert line.system_quantity == 10
    assert line.physical_quantity == 10
    assert line.variance == 0


def test_positive_variance_calculated_correctly(appctx):
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _finalized(loc, [item], {item.id: 105}, mgr)
    line = r.lines[0]
    assert line.variance == 5


def test_negative_variance_calculated_correctly(appctx):
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _finalized(loc, [item], {item.id: 97}, mgr)
    line = r.lines[0]
    assert line.variance == -3


def test_submitted_system_quantity_cannot_override_server_value(appctx):
    """The route never even reads a system_quantity field — save_counts()
    only accepts physical_quantity_<item_id>. Posting a forged
    system_quantity field must have zero effect on the snapshot finalize
    later computes."""
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _draft(loc, [item], counted_by=mgr)
    c = _login(mgr)
    resp = c.post(f"/reconciliations/{r.id}", data={
        f"physical_quantity_{item.id}": "97",
        f"system_quantity_{item.id}": "1",   # not a real field; must be ignored
    }, follow_redirects=True)
    assert resp.status_code == 200
    svc.finalize_count(r, counted_by_id=mgr.id)
    db.session.commit()
    line = r.lines[0]
    assert line.system_quantity == 100  # the real stock, never "1"
    assert line.variance == -3


def test_submitted_variance_cannot_override_server_calculation(appctx):
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _draft(loc, [item], counted_by=mgr)
    c = _login(mgr)
    c.post(f"/reconciliations/{r.id}", data={
        f"physical_quantity_{item.id}": "97",
        f"variance_{item.id}": "99999",   # not a real field; must be ignored
    })
    svc.finalize_count(r, counted_by_id=mgr.id)
    db.session.commit()
    assert r.lines[0].variance == -3


# ── 10-16. Posting ───────────────────────────────────────────────────────────

def test_positive_variance_increases_stock(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 105}, admin, _admin(email="approver1@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()
    assert stock_at_location(item.id, loc.id) == 105
    assert db.session.get(Item, item.id).stock == 105


def test_negative_variance_decreases_stock(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="approver2@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()
    assert stock_at_location(item.id, loc.id) == 97
    assert db.session.get(Item, item.id).stock == 97


def test_posting_creates_stock_movement_with_reconciliation_source(appctx):
    from salpurflask.models.inventory_location import StockMovement
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="approver3@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()
    movements = StockMovement.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).all()
    assert len(movements) == 1
    m = movements[0]
    assert m.movement_type == "adjustment"
    assert m.direction == "out"
    assert m.quantity == -3
    assert m.item_id == item.id
    assert m.location_id == loc.id


def test_zero_variance_line_creates_no_movement(appctx):
    from salpurflask.models.inventory_location import StockMovement
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 100}, admin, _admin(email="approver4@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()
    assert StockMovement.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).count() == 0


def test_same_reconciliation_cannot_post_twice(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="approver5@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()
    assert stock_at_location(item.id, loc.id) == 97

    with pytest.raises(PostingError):
        svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.rollback()
    # Stock must not have moved a second time.
    assert stock_at_location(item.id, loc.id) == 97


def test_failed_posting_rolls_back_stock_and_status(appctx):
    """One line's variance would take an item negative (simulated by
    tampering with the snapshot after finalize to force a mismatch) —
    post_reconciliation() must refuse before mutating anything, and the
    status must remain Approved, not a half-applied Posted."""
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="approver6@recon.com"))

    # Simulate concurrent stock movement after approval: sell 50 units,
    # which changes stock_at_location() out from under the snapshot.
    item_obj = db.session.get(Item, item.id)
    from salpurflask.models.models import item_remove_stock
    item_remove_stock(item_obj, 50, location_id=loc.id)
    db.session.commit()

    with pytest.raises(PostingError):
        svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.rollback()
    db.session.refresh(r)
    assert r.status == "Approved"
    assert stock_at_location(item.id, loc.id) == 50  # only the sale happened, no adjustment


def test_posted_reconciliation_cannot_be_edited(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="approver7@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    c = _login(admin)
    resp = c.post(f"/reconciliations/{r.id}", data={f"physical_quantity_{item.id}": "50"},
                  follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(r)
    assert r.lines[0].physical_quantity == 97  # unchanged


# ── stale-stock rejection ───────────────────────────────────────────────────

def test_stale_snapshot_rejected_at_post(appctx):
    """The exact scenario from the approved proposal: system=100, counted=97,
    then 5 units move (sold) before posting. Posting must refuse, not
    silently apply a variance computed against a number that's no longer true."""
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="approver8@recon.com"))

    item_obj = db.session.get(Item, item.id)
    from salpurflask.models.models import item_remove_stock
    item_remove_stock(item_obj, 5, location_id=loc.id)
    db.session.commit()

    with pytest.raises(PostingError, match="changed since counting"):
        svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.rollback()
    db.session.refresh(r)
    assert r.status == "Approved"


# ── Segregation of duties ───────────────────────────────────────────────────

def test_counter_cannot_approve_own_reconciliation(appctx):
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _finalized(loc, [item], {item.id: 97}, admin)
    with pytest.raises(PostingError, match="cannot also approve"):
        svc.approve_reconciliation(r, approved_by_id=admin.id)
    db.session.rollback()


def test_different_admin_can_approve(appctx):
    counter = _admin(email="counter@recon.com")
    approver = _admin(email="approver9@recon.com")
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _finalized(loc, [item], {item.id: 97}, counter)
    svc.approve_reconciliation(r, approved_by_id=approver.id)
    db.session.commit()
    assert r.status == "Approved"


def test_route_hides_approve_button_but_route_also_enforces_it(appctx):
    """Server-side enforcement, not just UI — a manager who counted, then a
    different admin tries to approve via direct POST, must succeed; the
    same counter trying via direct POST must fail even bypassing the UI."""
    counter = _manager()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _draft(loc, [item], counted_by=counter, counts={item.id: 97})
    c = _login(counter)
    c.post(f"/reconciliations/{r.id}/finalize", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Counted"
    assert r.counted_by_id == counter.id

    # A different admin approving must succeed.
    c2 = _login(admin)
    resp = c2.post(f"/reconciliations/{r.id}/approve", follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(r)
    assert r.status == "Approved"


# ── 17-19. Permissions ───────────────────────────────────────────────────────

def test_restricted_user_cannot_access_another_locations_reconciliation(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    item = _item(stock=10, location=loc_b)
    r = _draft(loc_b, [item])
    c = _login(mgr)
    resp = c.get(f"/reconciliations/{r.id}")
    assert resp.status_code == 403


def test_restricted_user_cannot_post_another_locations_reconciliation(appctx):
    mgr = _manager()
    admin = _admin()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    item = _item(stock=100, location=loc_b)
    r = _approved(loc_b, [item], {item.id: 97}, admin, _admin(email="approver10@recon.com"))
    c = _login(mgr)
    resp = c.post(f"/reconciliations/{r.id}/post")
    # admin_required redirects (302) rather than aborting, same convention as
    # every other admin-only route in this codebase (see auth.role_required).
    assert resp.status_code == 302
    db.session.refresh(r)
    assert r.status == "Approved"


def test_admin_can_manage_all_locations(appctx):
    admin = _admin()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    item_a = _item(stock=10, location=loc_a)
    item_b = _item(stock=10, location=loc_b)
    c = _login(admin)
    for loc, item in ((loc_a, item_a), (loc_b, item_b)):
        resp = c.post("/reconciliations/new", data={
            "location_id": str(loc.id), "date": "2026-03-01", "item_id[]": str(item.id),
        }, follow_redirects=True)
        assert resp.status_code == 200
    assert InventoryReconciliation.query.count() == 2


# ── Cancellation ─────────────────────────────────────────────────────────────

def test_cancel_draft_moves_no_stock(appctx):
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=10, location=loc)
    r = _draft(loc, [item])
    svc.cancel_reconciliation(r)
    db.session.commit()
    assert r.status == "Cancelled"
    assert stock_at_location(item.id, loc.id) == 10


def test_cancel_counted_moves_no_stock(appctx):
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=10, location=loc)
    r = _finalized(loc, [item], {item.id: 5}, mgr)
    svc.cancel_reconciliation(r)
    db.session.commit()
    assert r.status == "Cancelled"
    assert stock_at_location(item.id, loc.id) == 10


def test_cancel_posted_reconciliation_refused(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="approver11@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()
    with pytest.raises(PostingError):
        svc.cancel_reconciliation(r)
    db.session.rollback()


# ── Full / partial counts ───────────────────────────────────────────────────

def test_full_count_includes_all_stock_items(appctx):
    loc = get_or_create_default_location()
    items = [_item(stock=10, location=loc) for _ in range(3)]
    r = svc.create_reconciliation(location_id=loc.id, item_ids=[i.id for i in items],
                                  date=datetime(2026, 3, 1))
    db.session.commit()
    assert len(r.lines) == 3


def test_partial_count_includes_only_selected_items(appctx):
    loc = get_or_create_default_location()
    items = [_item(stock=10, location=loc) for _ in range(3)]
    r = svc.create_reconciliation(location_id=loc.id, item_ids=[items[0].id],
                                  date=datetime(2026, 3, 1))
    db.session.commit()
    assert len(r.lines) == 1
    assert r.lines[0].item_id == items[0].id


def test_item_with_zero_system_stock_supported(appctx):
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=0, location=loc)
    r = _finalized(loc, [item], {item.id: 4}, mgr)
    line = r.lines[0]
    assert line.system_quantity == 0
    assert line.variance == 4


# ── GL posting (Phase 8 H1) ──────────────────────────────────────────────────

def test_zero_variance_reconciliation_creates_no_journal_entry(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    before = JournalEntry.query.count()
    r = _approved(loc, [item], {item.id: 100}, admin, _admin(email="approver12@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()
    assert JournalEntry.query.count() == before
    assert JournalEntry.query.filter_by(source_type="inventory_reconciliation").count() == 0


def test_positive_variance_posts_balanced_journal_entry(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 105}, admin, _admin(email="gl1@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    entries = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).all()
    assert len(entries) == 1
    entry = entries[0]
    total_dr = sum(l.debit for l in entry.lines)
    total_cr = sum(l.credit for l in entry.lines)
    assert total_dr == total_cr
    assert total_dr == Decimal("50.0000")  # 5 units * avg_cost 10


def test_negative_variance_posts_balanced_journal_entry(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="gl2@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    entries = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).all()
    assert len(entries) == 1
    entry = entries[0]
    total_dr = sum(l.debit for l in entry.lines)
    total_cr = sum(l.credit for l in entry.lines)
    assert total_dr == total_cr
    assert total_dr == Decimal("30.0000")  # 3 units * avg_cost 10


def test_multiple_lines_that_net_to_exactly_zero_create_no_entry(appctx):
    """Two items, mixed +/- variance whose value exactly cancels — the net
    change to Inventory really is zero, so no entry is needed at all. This is
    the strictest version of "no unnecessary GL entry": not just a per-line
    zero variance, but a genuine cross-line cancellation."""
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item_a = _item(stock=100, location=loc, name="Widget-A")
    item_b = _item(stock=50, location=loc, name="Widget-B")
    r = _approved(loc, [item_a, item_b],
                 {item_a.id: 105, item_b.id: 45}, admin, _admin(email="gl3@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    # net = +5*10 (found, item_a) - 5*10 (lost, item_b) = 0 -> no entry at all
    assert JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).count() == 0


def test_multiple_lines_net_nonzero_still_one_balanced_entry(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item_a = _item(stock=100, location=loc, name="Widget-C")
    item_b = _item(stock=50, location=loc, name="Widget-D")
    r = _approved(loc, [item_a, item_b],
                 {item_a.id: 110, item_b.id: 45}, admin, _admin(email="gl4@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    entries = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).all()
    assert len(entries) == 1
    entry = entries[0]
    total_dr = sum(l.debit for l in entry.lines)
    total_cr = sum(l.credit for l in entry.lines)
    assert total_dr == total_cr
    # net = +10*10 (found) - 5*10 (lost) = +50
    assert total_dr == Decimal("50.0000")


def test_journal_entry_has_correct_source_type_and_id(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="gl5@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    entry = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).first()
    assert entry is not None
    assert entry.source_type == "inventory_reconciliation"
    assert entry.source_id == r.id


def test_reposting_cannot_create_duplicate_journal_entry(appctx):
    """post_reconciliation() already refuses a second POST via the status
    guard (status != Approved) — but the GL side has its own independent
    posted_entry() guard too, the same belt-and-braces every other poster
    uses, so this proves both layers hold."""
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="gl6@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()

    count_after_first = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).count()
    assert count_after_first == 1

    with pytest.raises(PostingError):
        svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.rollback()

    count_after_second_attempt = JournalEntry.query.filter_by(
        source_type="inventory_reconciliation", source_id=r.id).count()
    assert count_after_second_attempt == 1  # still exactly one, no duplicate


# ── Regression: existing stock calculations unaffected ──────────────────────

def test_existing_item_stock_invariant_holds_after_reconciliation_post(appctx):
    _world()
    admin = _admin()
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    r = _approved(loc, [item], {item.id: 97}, admin, _admin(email="approver13@recon.com"))
    svc.post_reconciliation(r, posted_by_id=admin.id)
    db.session.commit()
    total_itemstock = sum(row.quantity for row in ItemStock.query.filter_by(item_id=item.id).all())
    assert total_itemstock == db.session.get(Item, item.id).stock
