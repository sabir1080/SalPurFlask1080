"""Multi-branch / multi-warehouse — Phase 2: sales, purchases, adjustments.

Every route in this phase resolves a location_id (default when the form omits
one), validates availability against that location's ItemStock — never
Item.stock, the company-wide total — and calls item_add_stock()/
item_remove_stock() with that same location_id, so reversal always lands back
on the warehouse the original transaction touched.
"""
from datetime import date
from decimal import Decimal

import pytest

from app import (app as flask_app, db, User, pwd_context, Category, Item,
                 Supplier, Customer, Purchase, Sale, StockAdjustment,
                 PostingError, seed_chart_of_accounts, seed_fixed_asset_accounts,
                 seed_fiscal_year, seed_financial_account_links, get_account,
                 ACC_INVENTORY, FinancialAccount, post_account_opening)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, get_or_create_default_location,
    stock_at_location, backfill_item_stock_locations,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _admin(email=None):
    email = email or f"admin{User.query.count()}@phase2.com"
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _world():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    db.session.add(FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                                    opening_balance=Decimal("1000000")))
    db.session.commit()
    seed_financial_account_links()
    post_account_opening(FinancialAccount.query.filter_by(name="Cash").first())

    sup = Supplier(name="S", contact="03000000000", address="x", opening_balance=0)
    cus = Customer(name="C", contact="03000000000", address="x", opening_balance=0)
    cat = Category(name="H")
    db.session.add_all([sup, cus, cat])
    db.session.flush()
    item = Item(name="Widget", category_id=cat.id, unit="Pcs",
                purchase_price=Decimal("100"), sale_price=Decimal("250"),
                stock=0, reorder_level=0, inventory_value=0)
    db.session.add(item)
    db.session.commit()
    return sup, cus, item


def _second_location():
    default = get_or_create_default_location()
    branch = Branch.query.filter_by(is_default=True).first()
    loc = Location(name="Second Warehouse", kind="warehouse", branch_id=branch.id)
    db.session.add(loc)
    db.session.commit()
    return loc


def _purchase(c, sup, item, qty, price, location_id=None):
    data = {"supplier_id": sup.id, "date": "2026-03-01", "notes": "",
           "item_id[]": item.id, "quantity[]": str(qty), "purchase_price[]": str(price),
           "discount_type[]": "", "discount_value[]": "0", "tax_percent[]": "0"}
    if location_id is not None:
        data["location_id"] = str(location_id)
    return c.post("/purchase", data=data, follow_redirects=True)


def _sale(c, cus, item, qty, price, location_id=None):
    data = {"customer_id": cus.id, "date": "2026-03-01", "notes": "",
           "item_id[]": item.id, "quantity[]": str(qty), "sale_price[]": str(price),
           "discount_type[]": "", "discount_value[]": "0", "tax_percent[]": "0"}
    if location_id is not None:
        data["location_id"] = str(location_id)
    return c.post("/sale", data=data, follow_redirects=True)


def _adjust(c, item, adj_type, qty, location_id=None):
    data = {"item_id": item.id, "adj_type": adj_type, "quantity": str(qty),
           "date": "2026-03-01", "reason": "test"}
    if location_id is not None:
        data["location_id"] = str(location_id)
    return c.post("/stock_adjustment", data=data, follow_redirects=True)


# ── sales ─────────────────────────────────────────────────────────────────────

def test_sale_from_default_warehouse(appctx):
    sup, cus, item = _world()
    c = _admin()
    _purchase(c, sup, item, 50, 100)
    default = get_or_create_default_location()
    r = _sale(c, cus, item, 10, 250)
    assert r.status_code == 200
    db.session.expire_all()
    sal = Sale.query.first()
    assert sal.location_id == default.id
    assert stock_at_location(item.id, default.id) == 40
    assert db.session.get(Item, item.id).stock == 40


def test_sale_from_non_default_warehouse(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _purchase(c, sup, item, 30, 100, location_id=loc2.id)
    r = _sale(c, cus, item, 5, 250, location_id=loc2.id)
    assert r.status_code == 200
    db.session.expire_all()
    sal = Sale.query.first()
    assert sal.location_id == loc2.id
    assert stock_at_location(item.id, loc2.id) == 25
    assert db.session.get(Item, item.id).stock == 25


def test_insufficient_stock_at_selected_warehouse(appctx):
    sup, cus, item = _world()
    c = _admin()
    _purchase(c, sup, item, 10, 100)
    r = _sale(c, cus, item, 999, 250)
    assert "alert-danger" in r.get_data(as_text=True)
    assert Sale.query.count() == 0
    assert db.session.get(Item, item.id).stock == 10


def test_sufficient_company_stock_but_insufficient_selected_warehouse_stock(appctx):
    """The core multi-location correctness case: company-wide total is
    plenty, but the chosen warehouse alone does not have enough — the sale
    must be refused, proving Item.stock is never consulted for availability
    once a location is in play."""
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _purchase(c, sup, item, 5, 100)                       # 5 at default
    _purchase(c, sup, item, 100, 100, location_id=loc2.id)  # 100 at loc2
    db.session.expire_all()
    item = db.session.get(Item, item.id)
    assert item.stock == 105                              # company total is plenty

    r = _sale(c, cus, item, 20, 250)                       # sells from default (only 5 there)
    assert "alert-danger" in r.get_data(as_text=True)
    assert Sale.query.count() == 0
    assert stock_at_location(item.id, get_or_create_default_location().id) == 5
    assert stock_at_location(item.id, loc2.id) == 100


def test_sale_cancellation_restores_same_warehouse(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _purchase(c, sup, item, 50, 100, location_id=loc2.id)
    _sale(c, cus, item, 10, 250, location_id=loc2.id)
    db.session.expire_all()
    assert stock_at_location(item.id, loc2.id) == 40
    default = get_or_create_default_location()
    assert stock_at_location(item.id, default.id) == 0

    sal = Sale.query.first()
    r = c.post(f"/document/sale/{sal.id}/reverse", follow_redirects=True)
    assert r.status_code == 200
    db.session.expire_all()
    assert stock_at_location(item.id, loc2.id) == 50       # restored to loc2, not default
    assert stock_at_location(item.id, default.id) == 0     # never touched


def test_sale_company_total_remains_correct_across_two_warehouses(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _purchase(c, sup, item, 20, 100)
    _purchase(c, sup, item, 30, 100, location_id=loc2.id)
    _sale(c, cus, item, 5, 250)
    _sale(c, cus, item, 8, 250, location_id=loc2.id)
    db.session.expire_all()
    item = db.session.get(Item, item.id)
    default = get_or_create_default_location()
    total_at_locations = (stock_at_location(item.id, default.id)
                          + stock_at_location(item.id, loc2.id))
    assert item.stock == total_at_locations == 37


# ── purchases ────────────────────────────────────────────────────────────────

def test_purchase_into_default_warehouse(appctx):
    sup, cus, item = _world()
    c = _admin()
    default = get_or_create_default_location()
    r = _purchase(c, sup, item, 25, 100)
    assert r.status_code == 200
    db.session.expire_all()
    pur = Purchase.query.first()
    assert pur.location_id == default.id
    assert stock_at_location(item.id, default.id) == 25
    assert db.session.get(Item, item.id).stock == 25


def test_purchase_into_non_default_warehouse(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    r = _purchase(c, sup, item, 40, 100, location_id=loc2.id)
    assert r.status_code == 200
    db.session.expire_all()
    pur = Purchase.query.first()
    assert pur.location_id == loc2.id
    assert stock_at_location(item.id, loc2.id) == 40
    default = get_or_create_default_location()
    assert stock_at_location(item.id, default.id) == 0


def test_purchase_reversal_restores_same_warehouse(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _purchase(c, sup, item, 40, 100, location_id=loc2.id)
    db.session.expire_all()
    pur = Purchase.query.first()

    r = c.post(f"/document/purchase/{pur.id}/reverse", follow_redirects=True)
    assert r.status_code == 200
    db.session.expire_all()
    assert stock_at_location(item.id, loc2.id) == 0
    default = get_or_create_default_location()
    assert stock_at_location(item.id, default.id) == 0
    assert db.session.get(Item, item.id).stock == 0


def test_purchase_company_total_remains_correct(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _purchase(c, sup, item, 15, 100)
    _purchase(c, sup, item, 22, 100, location_id=loc2.id)
    db.session.expire_all()
    item = db.session.get(Item, item.id)
    default = get_or_create_default_location()
    total_at_locations = (stock_at_location(item.id, default.id)
                          + stock_at_location(item.id, loc2.id))
    assert item.stock == 37 == total_at_locations


# ── adjustments ──────────────────────────────────────────────────────────────

def test_positive_adjustment_at_a_location(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    r = _adjust(c, item, "Stock In", 15, location_id=loc2.id)
    assert r.status_code == 200
    db.session.expire_all()
    assert stock_at_location(item.id, loc2.id) == 15
    assert db.session.get(Item, item.id).stock == 15
    adj = StockAdjustment.query.first()
    assert adj.location_id == loc2.id


def test_negative_adjustment_at_a_location(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _adjust(c, item, "Stock In", 20, location_id=loc2.id)
    r = _adjust(c, item, "Stock Out", 8, location_id=loc2.id)
    assert r.status_code == 200
    db.session.expire_all()
    assert stock_at_location(item.id, loc2.id) == 12
    assert db.session.get(Item, item.id).stock == 12


def test_adjustment_reversal_restores_same_warehouse(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _adjust(c, item, "Stock In", 20, location_id=loc2.id)
    db.session.expire_all()
    adj = StockAdjustment.query.first()

    # A posted adjustment is reversed via the generic document-reversal route,
    # not deleted directly (the same rule already proven for purchases/sales
    # in test_inventory.py's test_reversing_a_purchase_whose_goods_were_sold).
    r = c.post(f"/document/stock_adjustment/{adj.id}/reverse", follow_redirects=True)
    assert r.status_code == 200
    db.session.expire_all()
    assert stock_at_location(item.id, loc2.id) == 0
    default = get_or_create_default_location()
    assert stock_at_location(item.id, default.id) == 0
    assert db.session.get(Item, item.id).stock == 0


def test_adjustment_warehouse_isolation(appctx):
    """Adjusting one warehouse's count must never move stock at another."""
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _adjust(c, item, "Stock In", 50, location_id=None)   # default location
    _adjust(c, item, "Stock Out", 10, location_id=None)  # still default
    default = get_or_create_default_location()
    assert stock_at_location(item.id, loc2.id) == 0
    assert stock_at_location(item.id, default.id) == 40
    assert db.session.get(Item, item.id).stock == 40


def test_negative_adjustment_insufficient_at_location_is_refused(appctx):
    """Company total may be irrelevant; only the chosen location's balance
    governs whether a Stock Out adjustment is allowed."""
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _adjust(c, item, "Stock In", 5, location_id=None)      # 5 at default
    _adjust(c, item, "Stock In", 100, location_id=loc2.id)  # 100 at loc2
    r = _adjust(c, item, "Stock Out", 20, location_id=None)  # tries to remove 20 from default (only 5)
    assert "alert-danger" in r.get_data(as_text=True)
    default = get_or_create_default_location()
    assert stock_at_location(item.id, default.id) == 5     # unchanged, refused


# ── reports ──────────────────────────────────────────────────────────────────

def test_warehouse_stock_report_shows_location_specific_quantity(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _purchase(c, sup, item, 12, 100)
    _purchase(c, sup, item, 30, 100, location_id=loc2.id)

    r = c.get(f"/reports/stock?location_id={loc2.id}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "30" in body


def test_consolidated_stock_report_shows_company_total(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _purchase(c, sup, item, 12, 100)
    _purchase(c, sup, item, 30, 100, location_id=loc2.id)

    r = c.get("/reports/stock")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "42" in body


# ── backward compatibility (single warehouse) ──────────────────────────────────

def test_single_warehouse_business_never_sees_a_selector(appctx):
    """With exactly one location, the sale/purchase/adjustment forms render
    no warehouse dropdown at all — the hidden default-location field covers
    it silently."""
    sup, cus, item = _world()
    c = _admin()
    body = c.get("/sale").get_data(as_text=True)
    assert 'name="location_id"' in body   # the hidden field is present
    assert "Warehouse</label>" not in body  # but no visible selector


def test_single_warehouse_sale_and_purchase_work_without_a_location_field(appctx):
    """Omitting location_id entirely (an old client, or a single-warehouse
    form that never renders the field) still resolves to the default."""
    sup, cus, item = _world()
    c = _admin()
    _purchase(c, sup, item, 20, 100, location_id=None)
    r = _sale(c, cus, item, 5, 250, location_id=None)
    assert r.status_code == 200
    default = get_or_create_default_location()
    assert stock_at_location(item.id, default.id) == 15
    assert db.session.get(Item, item.id).stock == 15


# ── the central invariant ────────────────────────────────────────────────────────

def test_item_stock_equals_sum_of_itemstock_after_mixed_multi_warehouse_activity(appctx):
    sup, cus, item = _world()
    c = _admin()
    loc2 = _second_location()
    _purchase(c, sup, item, 50, 100)
    _purchase(c, sup, item, 80, 100, location_id=loc2.id)
    _sale(c, cus, item, 10, 250)
    _sale(c, cus, item, 15, 250, location_id=loc2.id)
    _adjust(c, item, "Stock In", 5, location_id=None)
    _adjust(c, item, "Stock Out", 3, location_id=loc2.id)

    db.session.expire_all()
    item = db.session.get(Item, item.id)
    default = get_or_create_default_location()
    total_at_locations = (stock_at_location(item.id, default.id)
                          + stock_at_location(item.id, loc2.id))
    assert item.stock == total_at_locations
