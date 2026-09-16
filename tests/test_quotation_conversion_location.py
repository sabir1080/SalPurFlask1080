"""Quotation-to-Sale conversion — location-aware stock check.

Bug this file guards against: convert_quotation_to_sale() (app.py) checked
stock against Item.stock (the company-wide total) before converting, but
then removed stock via item_remove_stock() with no location_id, which
resolves to the DEFAULT warehouse only. For a multi-warehouse business
where an item's stock is split across locations, the company-wide check
could pass while the actual, location-scoped removal failed with
PostingError -- rolling back the whole transaction (Sale, SaleItem rows,
GL entry) via the global @app.errorhandler(PostingError), and leaving the
Quotation silently stuck on Draft with no obvious crash.

Fix: the conversion route now follows the same location-selection rule the
rest of the Sale/POS workflow already uses (sale(), post_sale_route() in
salpurflask/sales/routes.py) -- resolve_location_id()/require_location_access()
choose a warehouse (defaulting to the default location for a single-
warehouse business, unchanged), the created Sale is given that same
location_id, and BOTH the pre-check (stock_at_location) and the actual
removal (item_remove_stock(..., location_id=...)) use it consistently.

Nothing here touches item_remove_stock() itself (unmodified, its
location-scoped guard was always correct) or the established Sale/POS
location-selection mechanism (unmodified, only reused).
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context, Category, Item, Customer,
    Quotation, Sale, seed_chart_of_accounts, seed_fiscal_year,
)
from salpurflask.models.inventory_location import (
    Branch, Location, get_or_create_default_location,
)
from salpurflask.models.models import item_add_stock


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


def _manager(email="m@t.com"):
    u = User(name="M", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="manager")
    db.session.add(u)
    db.session.commit()
    return u


def _books():
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    db.session.commit()


def _second_location(name="Second WH-QC"):
    branch = Branch.query.filter_by(is_default=True).first()
    loc = Location(name=name, kind="warehouse", branch_id=branch.id)
    db.session.add(loc)
    db.session.commit()
    return loc


def _item(name="QCWidget", category_name="QCCat"):
    cat = Category(name=category_name)
    db.session.add(cat)
    db.session.flush()
    it = Item(name=name, category_id=cat.id, stock=0, purchase_price=10,
             sale_price=20, item_type="STOCK")
    db.session.add(it)
    db.session.commit()
    return it


def _customer(name="QCCustomer"):
    c = Customer(name=name, contact="0300", address="X", opening_balance=0)
    db.session.add(c)
    db.session.commit()
    return c


def _create_quotation(client, cust, item, qty):
    client.post("/quotations", data={
        "customer_id": str(cust.id), "quote_date": "2026-03-01", "valid_until": "",
        "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "sale_price[]": "20",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)
    return Quotation.query.order_by(Quotation.id.desc()).first()


def _convert(client, q, location_id=None):
    data = {"sale_date": "2026-03-02"}
    if location_id is not None:
        data["location_id"] = str(location_id)
    return client.post(f"/quotations/{q.id}/convert", data=data, follow_redirects=True)


# ─── 1: sufficient stock at the actual removal location -> succeeds ────────


def test_convert_succeeds_when_stock_sufficient_at_chosen_location(appctx):
    _books()
    mgr = _manager()
    c = _login(mgr)
    item = _item()
    default_loc = get_or_create_default_location()
    item_add_stock(item, 10, Decimal("100"), location_id=default_loc.id)
    db.session.commit()
    cust = _customer()

    q = _create_quotation(c, cust, item, 5)
    resp = _convert(c, q, location_id=default_loc.id)
    assert resp.status_code == 200

    db.session.refresh(q)
    assert q.status == "Converted"
    assert q.converted_sale_id is not None
    sal = db.session.get(Sale, q.converted_sale_id)
    assert sal.location_id == default_loc.id
    db.session.refresh(item)
    assert item.stock == 5


# ─── 2: insufficient stock at that location, sufficient company-wide ──────


def test_convert_rejected_when_insufficient_at_location_despite_company_wide_total(appctx):
    _books()
    mgr = _manager()
    c = _login(mgr)
    item = _item(name="QCWidget2", category_name="QCCat2")
    default_loc = get_or_create_default_location()
    second_loc = _second_location()
    # 2 at default, 8 at second -- company-wide total 10, but default only has 2.
    item_add_stock(item, 2, Decimal("20"), location_id=default_loc.id)
    item_add_stock(item, 8, Decimal("80"), location_id=second_loc.id)
    db.session.commit()
    cust = _customer()

    q = _create_quotation(c, cust, item, 10)
    resp = _convert(c, q, location_id=default_loc.id)
    assert resp.status_code == 200
    assert b"Insufficient stock" in resp.data
    assert b"only 2 in stock" in resp.data

    db.session.refresh(q)
    assert q.status == "Draft"
    assert q.converted_sale_id is None
    db.session.refresh(item)
    assert item.stock == 10  # untouched -- nothing was removed
    assert Sale.query.filter_by(customer_id=cust.id).count() == 0  # no partial Sale


# ─── 3: stock available only at another warehouse -> follows Sale/POS rule ─


def test_convert_succeeds_when_correct_warehouse_selected(appctx):
    """Same split-stock setup as test 2, but this time the SECOND warehouse
    (where the stock actually is) is selected -- matching the established
    Sale/POS rule that the user-selected location decides availability, not
    a company-wide total and not a hardcoded default-only restriction."""
    _books()
    mgr = _manager()
    c = _login(mgr)
    item = _item(name="QCWidget3", category_name="QCCat3")
    default_loc = get_or_create_default_location()
    second_loc = _second_location(name="Second WH-QC3")
    item_add_stock(item, 2, Decimal("20"), location_id=default_loc.id)
    item_add_stock(item, 8, Decimal("80"), location_id=second_loc.id)
    db.session.commit()
    cust = _customer()

    q = _create_quotation(c, cust, item, 8)
    resp = _convert(c, q, location_id=second_loc.id)
    assert resp.status_code == 200
    assert b"Insufficient stock" not in resp.data

    db.session.refresh(q)
    assert q.status == "Converted"
    assert q.converted_sale_id is not None
    sal = db.session.get(Sale, q.converted_sale_id)
    assert sal.location_id == second_loc.id
    db.session.refresh(item)
    assert item.stock == 2  # 10 total - 8 removed from the second warehouse


# ─── Single-warehouse regression: blank location_id still defaults cleanly ─


def test_convert_single_warehouse_blank_location_defaults_correctly(appctx):
    """A single-warehouse business's form has no dropdown (hidden field with
    the default location's id) -- this must keep working exactly as before,
    with no behavior change for the common case."""
    _books()
    mgr = _manager()
    c = _login(mgr)
    item = _item(name="QCWidget4", category_name="QCCat4")
    default_loc = get_or_create_default_location()
    item_add_stock(item, 5, Decimal("50"), location_id=default_loc.id)
    db.session.commit()
    cust = _customer()

    q = _create_quotation(c, cust, item, 3)
    # No location_id in the POST at all (blank field) -- resolve_location_id
    # falls back to the default location.
    resp = _convert(c, q, location_id=None)
    assert resp.status_code == 200
    assert b"Insufficient stock" not in resp.data

    db.session.refresh(q)
    assert q.status == "Converted"
    sal = db.session.get(Sale, q.converted_sale_id)
    assert sal.location_id == default_loc.id
