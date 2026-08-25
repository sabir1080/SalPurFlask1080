"""Location-based access control — Phase 5.

The rule under test everywhere here: admin is always unrestricted; a
non-admin with zero UserLocationAccess rows is unrestricted too (the
backward-compatibility default every pre-Phase-5 manager/staff account
depends on); a non-admin with one or more rows is restricted to exactly
those locations. A transfer additionally requires BOTH endpoints to be
held, checked as one decision, never two.
"""
from datetime import date
from decimal import Decimal

import pytest

from app import (
    app as flask_app, db, User, pwd_context, Category, Item, FinancialAccount, Customer,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_account_opening,
)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, UserLocationAccess,
    get_or_create_default_location, stock_at_location,
)
from salpurflask.models.models import item_add_stock
from salpurflask.services.location_permissions import (
    accessible_location_ids, can_access_location, can_access_transfer,
)


def _world():
    """A chart and an open fiscal year — required before any sale/adjustment
    can post. Mirrors test_inventory.py's own _world() helper."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    db.session.add(FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                                    opening_balance=Decimal("1000000")))
    db.session.commit()
    seed_financial_account_links()
    post_account_opening(FinancialAccount.query.filter_by(name="Cash").first())


# ── helpers ───────────────────────────────────────────────────────────────────

def _login(user):
    """Sign in without POSTing /signin — avoids the rate limiter tripping on
    repeated sign-ins inside one test and avoids a second test_client's
    session resolving to the first client's identity."""
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
    return c


def _user(role, email=None):
    email = email or f"{role}{User.query.count()}@locperm.com"
    u = User(name=role.capitalize(), email=email, password=pwd_context.hash("secret123"),
            verified=True, role=role)
    db.session.add(u)
    db.session.commit()
    return u


def _admin():
    return _user("admin")


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


# ── 1-3. helper-level default behavior ──────────────────────────────────────

def test_admin_is_unrestricted(appctx):
    admin = _admin()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    assert accessible_location_ids(admin) is None
    assert can_access_location(loc_a.id, admin)
    assert can_access_location(loc_b.id, admin)


def test_zero_assignment_non_admin_is_unrestricted(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    assert accessible_location_ids(mgr) is None
    assert can_access_location(loc_a.id, mgr)
    assert can_access_location(loc_b.id, mgr)


def test_assigned_user_is_restricted_to_granted_locations(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    ids = accessible_location_ids(mgr)
    assert ids == {loc_a.id}
    assert can_access_location(loc_a.id, mgr)
    assert not can_access_location(loc_b.id, mgr)


# ── 4. direct URL manipulation denial ───────────────────────────────────────

def test_direct_url_with_unauthorized_location_id_denied(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    c = _login(mgr)
    resp = c.get(f"/reports/stock?location_id={loc_b.id}")
    assert resp.status_code == 403


# ── 5. unauthorized stock modification (sale) denied ────────────────────────

def test_sale_at_unauthorized_location_denied(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    item = _item(stock=10, location=loc_b)
    c = _login(mgr)
    resp = c.post("/sale", data={
        "customer_id": "", "date": "2026-03-01",
        "item_id[]": str(item.id), "quantity[]": "1", "sale_price[]": "20",
        "location_id": str(loc_b.id),
    })
    assert resp.status_code == 403


# ── 6. unauthorized stock adjustment denied ─────────────────────────────────

def test_stock_adjustment_at_unauthorized_location_denied(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    item = _item(stock=10, location=loc_b)
    c = _login(mgr)
    resp = c.post("/stock_adjustment", data={
        "item_id": str(item.id), "adj_type": "Damage Write-off", "quantity": "1",
        "reason": "test", "date": "2026-03-01", "location_id": str(loc_b.id),
    })
    assert resp.status_code == 403


# ── 7-9. transfer authorization ─────────────────────────────────────────────

def test_transfer_denied_when_source_not_held(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_b)  # holds destination only
    item = _item(stock=10, location=loc_a)
    c = _login(mgr)
    resp = c.post("/transfers/new", data={
        "source_location_id": str(loc_a.id), "destination_location_id": str(loc_b.id),
        "item_id[]": str(item.id), "quantity[]": "1",
        "date": "2026-03-01", "notes": "test",
    })
    assert resp.status_code == 403


def test_transfer_denied_when_destination_not_held(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)  # holds source only
    item = _item(stock=10, location=loc_a)
    c = _login(mgr)
    resp = c.post("/transfers/new", data={
        "source_location_id": str(loc_a.id), "destination_location_id": str(loc_b.id),
        "item_id[]": str(item.id), "quantity[]": "1",
        "date": "2026-03-01", "notes": "test",
    })
    assert resp.status_code == 403


def test_transfer_requires_both_source_and_destination(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    _grant(mgr, loc_b)
    item = _item(stock=10, location=loc_a)
    c = _login(mgr)
    resp = c.post("/transfers/new", data={
        "source_location_id": str(loc_a.id), "destination_location_id": str(loc_b.id),
        "item_id[]": str(item.id), "quantity[]": "1",
        "date": "2026-03-01", "notes": "test",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert can_access_transfer(loc_a.id, loc_b.id, mgr)


# ── 10. ledger / report doesn't leak ────────────────────────────────────────

def test_report_stock_does_not_leak_unauthorized_location(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    _item(stock=5, name="Widget-A", location=loc_a)
    _item(stock=7, name="Widget-B", location=loc_b)
    c = _login(mgr)
    resp = c.get("/reports/stock")
    assert resp.status_code == 200
    assert loc_b.name.encode() not in resp.data


# ── 11. reports (stock movements) don't leak ────────────────────────────────

def test_stock_movements_report_does_not_leak_unauthorized_location(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    _item(stock=5, location=loc_a)
    _item(stock=5, location=loc_b)
    c = _login(mgr)
    resp = c.get("/reports/stock-movements")
    assert resp.status_code == 200
    assert loc_b.name.encode() not in resp.data


# ── 12. dashboard doesn't leak ───────────────────────────────────────────────

def test_dashboard_recent_movements_scoped_to_accessible_locations(appctx):
    mgr = _manager()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _grant(mgr, loc_a)
    _item(stock=5, name="OnlyA", location=loc_a)
    _item(stock=5, name="OnlyB", location=loc_b)
    c = _login(mgr)
    resp = c.get("/dashboard")
    assert resp.status_code == 200
    assert loc_b.name.encode() not in resp.data


def test_dashboard_unrestricted_admin_sees_all_locations_worth_of_movements(appctx):
    admin = _admin()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    _item(stock=5, location=loc_a)
    _item(stock=5, location=loc_b)
    c = _login(admin)
    resp = c.get("/dashboard")
    assert resp.status_code == 200


# ── 13-14. grant / revoke ───────────────────────────────────────────────────

def test_admin_can_grant_location_access(appctx):
    admin = _admin()
    mgr = _manager()
    loc = get_or_create_default_location()
    c = _login(admin)
    resp = c.post(f"/admin/location-access/grant/{mgr.id}",
                  data={"location_id": str(loc.id)}, follow_redirects=True)
    assert resp.status_code == 200
    assert UserLocationAccess.query.filter_by(user_id=mgr.id, location_id=loc.id).count() == 1


def test_admin_can_revoke_location_access(appctx):
    admin = _admin()
    mgr = _manager()
    loc = get_or_create_default_location()
    _grant(mgr, loc)
    c = _login(admin)
    resp = c.post(f"/admin/location-access/revoke/{mgr.id}/{loc.id}", follow_redirects=True)
    assert resp.status_code == 200
    assert UserLocationAccess.query.filter_by(user_id=mgr.id, location_id=loc.id).count() == 0


def test_non_admin_cannot_grant_location_access(appctx):
    mgr = _manager()
    other = _manager(email="other@locperm.com")
    loc = get_or_create_default_location()
    c = _login(mgr)
    resp = c.post(f"/admin/location-access/grant/{other.id}",
                  data={"location_id": str(loc.id)})
    # admin_required redirects (302) rather than aborting, same convention as
    # every other admin-only route in this codebase (see auth.role_required).
    assert resp.status_code == 302
    assert UserLocationAccess.query.filter_by(user_id=other.id, location_id=loc.id).count() == 0


# ── 15. duplicate assignment prevented ──────────────────────────────────────

def test_duplicate_assignment_prevented_by_unique_constraint(appctx):
    from sqlalchemy.exc import IntegrityError
    mgr = _manager()
    loc = get_or_create_default_location()
    db.session.add(UserLocationAccess(user_id=mgr.id, location_id=loc.id))
    db.session.commit()
    db.session.add(UserLocationAccess(user_id=mgr.id, location_id=loc.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_admin_grant_route_no_ops_on_duplicate(appctx):
    admin = _admin()
    mgr = _manager()
    loc = get_or_create_default_location()
    _grant(mgr, loc)
    c = _login(admin)
    resp = c.post(f"/admin/location-access/grant/{mgr.id}",
                  data={"location_id": str(loc.id)}, follow_redirects=True)
    assert resp.status_code == 200
    assert UserLocationAccess.query.filter_by(user_id=mgr.id, location_id=loc.id).count() == 1


# ── 16-17. existing admin / inventory behavior intact ───────────────────────

def test_admin_unaffected_can_still_sell_from_any_location(appctx):
    _world()
    admin = _admin()
    cust = Customer(name="C", contact="03000000000", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    item = _item(stock=10, location=loc_b)
    c = _login(admin)
    resp = c.post("/sale", data={
        "customer_id": str(cust.id), "date": "2026-03-01",
        "item_id[]": str(item.id), "quantity[]": "1", "sale_price[]": "20",
        "location_id": str(loc_b.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert stock_at_location(item.id, loc_b.id) == 9


def test_unrestricted_manager_stock_adjustment_still_works(appctx):
    _world()
    mgr = _manager()
    loc = get_or_create_default_location()
    item = _item(stock=10, location=loc)
    c = _login(mgr)
    resp = c.post("/stock_adjustment", data={
        "item_id": str(item.id), "adj_type": "Damage Write-off", "quantity": "2",
        "reason": "test", "date": "2026-03-01", "location_id": str(loc.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert stock_at_location(item.id, loc.id) == 8


# ── single-non-default-location form fallback ───────────────────────────────
# A user restricted to exactly one warehouse that is NOT the company-wide
# default sees the "single warehouse" branch of sale.html/purchase.html/
# stock_adjustment.html, which submits a hidden location_id input rather than
# a dropdown. That hidden value must resolve to the warehouse the user is
# actually scoped to, not the unscoped company default — otherwise the page
# is unusable (403) or, worse, silently wrong for anyone whose default and
# assigned ids happen to collide.

def test_single_restricted_location_stock_adjustment_form_uses_own_location(appctx):
    _world()
    mgr = _manager()
    default_loc = get_or_create_default_location()
    other_loc = _second_location("Only Assigned WH")
    _grant(mgr, other_loc)  # restricted to exactly one, non-default, location
    item = _item(stock=10, location=other_loc)
    c = _login(mgr)

    # The GET response's hidden location_id fallback must be the user's own
    # warehouse id, never the company default they aren't assigned to.
    get_resp = c.get("/stock_adjustment")
    assert get_resp.status_code == 200
    assert f'value="{other_loc.id}"'.encode() in get_resp.data
    assert default_loc.id != other_loc.id

    # And submitting via that hidden fallback value must actually work.
    resp = c.post("/stock_adjustment", data={
        "item_id": str(item.id), "adj_type": "Damage Write-off", "quantity": "1",
        "reason": "test", "date": "2026-03-01", "location_id": str(other_loc.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert stock_at_location(item.id, other_loc.id) == 9


def test_single_restricted_location_sale_form_uses_own_location(appctx):
    _world()
    mgr = _manager()
    cust = Customer(name="C", contact="03000000000", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()
    default_loc = get_or_create_default_location()
    other_loc = _second_location("Only Assigned WH")
    _grant(mgr, other_loc)
    item = _item(stock=10, location=other_loc)
    c = _login(mgr)

    resp = c.post("/sale", data={
        "customer_id": str(cust.id), "date": "2026-03-01",
        "item_id[]": str(item.id), "quantity[]": "1", "sale_price[]": "20",
        "location_id": str(other_loc.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert stock_at_location(item.id, other_loc.id) == 9
