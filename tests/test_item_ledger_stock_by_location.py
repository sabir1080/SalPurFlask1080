"""Item Ledger's "Where This Item Is Right Now" section — a per-warehouse
current-stock breakdown, added directly to item_ledger() so a single item's
whole location footprint is visible without flipping through the Stock
Valuation report one warehouse at a time.

The rule under test: only active warehouses, only non-zero quantities, the
displayed total equals the sum of the displayed rows, and it respects the
same location-permission scoping as report_stock()/stock_movements() (see
test_location_permissions.py). Existing item_ledger behavior (transaction
entries, totals, date filter) is untouched — verified here only by absence
of regression in what those entries look like, not duplicated wholesale.
"""
from decimal import Decimal

from app import app as flask_app, db, User, pwd_context, Category, Item
from salpurflask.models.inventory_location import (
    Branch, Location, UserLocationAccess, get_or_create_default_location,
)
from salpurflask.models.models import item_add_stock


def _login(user):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
    return c


def _user(role, email=None):
    email = email or f"{role}{User.query.count()}@ledgertest.com"
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


def _item(name="Cotton Fabric"):
    cat = Category.query.first()
    if cat is None:
        cat = Category(name="Cat")
        db.session.add(cat)
        db.session.flush()
    it = Item(name=name, category_id=cat.id, stock=0, purchase_price=10, sale_price=20,
             item_type="STOCK")
    db.session.add(it)
    db.session.commit()
    return it


def test_shows_only_active_warehouses(appctx):
    admin = _admin()
    item = _item()
    default_loc = get_or_create_default_location()
    inactive_loc = _second_location("Retired WH")
    item_add_stock(item, 10, Decimal("100"), location_id=default_loc.id)
    item_add_stock(item, 5, Decimal("50"), location_id=inactive_loc.id)
    inactive_loc.active = False
    db.session.commit()

    c = _login(admin)
    r = c.get(f"/item/{item.id}/ledger")
    body = r.get_data(as_text=True)
    assert default_loc.name in body
    assert "Retired WH" not in body


def test_shows_only_nonzero_current_stock(appctx):
    admin = _admin()
    item = _item()
    default_loc = get_or_create_default_location()
    empty_loc = _second_location("Empty WH")
    item_add_stock(item, 10, Decimal("100"), location_id=default_loc.id)
    # Bring the second location's stock back to exactly zero -- it should
    # not appear as a row of zero.
    item_add_stock(item, 5, Decimal("50"), location_id=empty_loc.id)
    from salpurflask.models.models import item_remove_stock
    item_remove_stock(item, 5, location_id=empty_loc.id)
    db.session.commit()

    c = _login(admin)
    r = c.get(f"/item/{item.id}/ledger")
    body = r.get_data(as_text=True)
    assert default_loc.name in body
    assert "Empty WH" not in body


def test_total_equals_sum_of_displayed_warehouse_stock(appctx):
    admin = _admin()
    item = _item()
    default_loc = get_or_create_default_location()
    loc2 = _second_location("Lahore WH")
    item_add_stock(item, 50, Decimal("500"), location_id=default_loc.id)
    item_add_stock(item, 20, Decimal("200"), location_id=loc2.id)
    db.session.commit()

    c = _login(admin)
    r = c.get(f"/item/{item.id}/ledger")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert default_loc.name in body
    assert "Lahore WH" in body
    # The section's own total row -- 50 + 20 = 70.
    idx = body.find("Where This Item Is Right Now")
    section = body[idx:idx + 1500]
    assert "70.00" in section


def test_restricted_manager_sees_only_granted_locations(appctx):
    """Mirrors test_location_permissions.py's restriction rule: a non-admin
    with UserLocationAccess rows sees only those warehouses' stock here too,
    same as report_stock()/stock_movements() already enforce."""
    mgr = _manager()
    item = _item()
    default_loc = get_or_create_default_location()
    loc2 = _second_location("Lahore WH")
    _grant(mgr, loc2)  # restricted to exactly Lahore WH, not the default
    item_add_stock(item, 50, Decimal("500"), location_id=default_loc.id)
    item_add_stock(item, 20, Decimal("200"), location_id=loc2.id)
    db.session.commit()

    c = _login(mgr)
    r = c.get(f"/item/{item.id}/ledger")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Lahore WH" in body
    assert default_loc.name not in body


def test_unrestricted_manager_sees_all_locations(appctx):
    """Zero UserLocationAccess rows means unrestricted -- the same
    backward-compatibility default every pre-Phase-5 account depends on."""
    mgr = _manager()
    item = _item()
    default_loc = get_or_create_default_location()
    loc2 = _second_location("Lahore WH")
    item_add_stock(item, 50, Decimal("500"), location_id=default_loc.id)
    item_add_stock(item, 20, Decimal("200"), location_id=loc2.id)
    db.session.commit()

    c = _login(mgr)
    r = c.get(f"/item/{item.id}/ledger")
    body = r.get_data(as_text=True)
    assert default_loc.name in body
    assert "Lahore WH" in body


def test_existing_ledger_entries_and_totals_unaffected(appctx):
    """Regression check: the new section is purely additive -- the existing
    transaction-entries table, its Stock In/Out/Balance totals, and the
    Current Stock summary field must render exactly as before."""
    admin = _admin()
    item = _item()
    default_loc = get_or_create_default_location()
    item_add_stock(item, 30, Decimal("300"), location_id=default_loc.id)
    db.session.commit()

    c = _login(admin)
    r = c.get(f"/item/{item.id}/ledger")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Total Stock In" in body
    assert "Total Stock Out" in body
    assert "Current Stock" in body
    assert "Opening Stock" in body


def test_section_absent_when_item_has_no_stock_anywhere(appctx):
    """A brand-new item with zero stock everywhere shows no warehouse rows
    at all -- {% if stock_by_location %} hides the whole section rather
    than rendering an empty table."""
    admin = _admin()
    item = _item()
    db.session.commit()

    c = _login(admin)
    r = c.get(f"/item/{item.id}/ledger")
    body = r.get_data(as_text=True)
    assert "Where This Item Is Right Now" not in body
