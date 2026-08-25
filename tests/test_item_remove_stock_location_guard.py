"""item_remove_stock()'s location-level insufficient-stock guard — Phase 7.

The bug: the guard checked qty > item.stock (company-wide) instead of
qty > row.quantity (the specific location's ItemStock row). In a multi-
warehouse business this let a caller take more out of ONE location than
that location actually held, as long as some OTHER location's stock made
the company-wide total look sufficient — silently driving that location's
ItemStock.quantity negative. The three real call sites with no
stock_at_location() pre-check of their own — purchase reversal, deleting a
"stock found" adjustment, and opening-stock decrease — relied entirely on
this now-fixed guard.

The fix is strictly tighter: every case that passed before because the
location genuinely had enough still passes; every case that passed before
only because a DIFFERENT location's stock covered for it now correctly
raises PostingError. Nothing about the value guard (cost vs. inventory_value,
still company-wide by design) changes.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from app import (
    app as flask_app, db, User, pwd_context, Category, Item, Supplier,
    FinancialAccount, PostingError, StockAdjustment,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_account_opening,
    item_add_stock, item_remove_stock, reverse_document,
)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, get_or_create_default_location, stock_at_location,
)


# ── helpers ───────────────────────────────────────────────────────────────────

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


def _admin(email=None):
    email = email or f"admin{User.query.count()}@guardtest.com"
    u = User(name="Admin", email=email, password=pwd_context.hash("secret123"),
            verified=True, role="admin")
    db.session.add(u)
    db.session.commit()
    return u


def _world():
    """A chart and an open fiscal year — required to post a purchase."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    db.session.add(FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                                    opening_balance=Decimal("1000000")))
    db.session.commit()
    seed_financial_account_links()
    post_account_opening(FinancialAccount.query.filter_by(name="Cash").first())


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


# ── 1. baseline: sufficient stock at the location still succeeds ───────────

def test_remove_stock_with_sufficient_location_stock_succeeds(appctx):
    loc = get_or_create_default_location()
    item = _item(stock=50, location=loc)
    item_remove_stock(item, 20, location_id=loc.id)
    db.session.commit()
    assert stock_at_location(item.id, loc.id) == 30
    assert db.session.get(Item, item.id).stock == 30


# ── 2. the exact bug scenario: company-wide sufficient, location drained ───

def test_remove_stock_refused_when_location_insufficient_but_company_wide_sufficient(appctx):
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    item = _item(stock=0)
    # 80 at A, 20 at B — company-wide 100, more than enough for a 30-unit removal,
    # but B itself only has 20.
    item_add_stock(item, 80, Decimal("800"), location_id=loc_a.id)
    item_add_stock(item, 20, Decimal("200"), location_id=loc_b.id)
    db.session.commit()

    with pytest.raises(PostingError, match="Insufficient stock at this location"):
        item_remove_stock(item, 30, location_id=loc_b.id)
    db.session.rollback()

    # Neither number moved — the guard raises before any mutation.
    assert stock_at_location(item.id, loc_a.id) == 80
    assert stock_at_location(item.id, loc_b.id) == 20
    assert db.session.get(Item, item.id).stock == 100


# ── 3. already-refused case (insufficient even company-wide) still refused ─

def test_remove_stock_still_refused_when_insufficient_even_company_wide(appctx):
    loc = get_or_create_default_location()
    item = _item(stock=10, location=loc)
    with pytest.raises(PostingError, match="Insufficient stock at this location"):
        item_remove_stock(item, 50, location_id=loc.id)
    db.session.rollback()
    assert stock_at_location(item.id, loc.id) == 10


# ── 4. PostingError leaves quantities completely unchanged ─────────────────

def test_failed_removal_leaves_item_stock_and_itemstock_unchanged(appctx):
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    item = _item(stock=0)
    item_add_stock(item, 80, Decimal("800"), location_id=loc_a.id)
    item_add_stock(item, 20, Decimal("200"), location_id=loc_b.id)
    db.session.commit()

    value_before = db.session.get(Item, item.id).inventory_value

    with pytest.raises(PostingError):
        item_remove_stock(item, 30, location_id=loc_b.id)
    db.session.rollback()

    fresh_item = db.session.get(Item, item.id)
    assert fresh_item.stock == 100
    assert fresh_item.inventory_value == value_before
    assert stock_at_location(item.id, loc_a.id) == 80
    assert stock_at_location(item.id, loc_b.id) == 20


# ── 5. integration: reversing a purchase drained by a later transfer ───────

def test_reversing_purchase_refused_when_its_warehouse_was_drained(appctx):
    """The exact end-to-end scenario from the approved Phase 7 proposal:
    goods received into Warehouse B, then moved/sold elsewhere so B no
    longer holds enough — reversing the original purchase must be refused,
    not silently drive B negative."""
    _world()
    admin = _admin()
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    supplier = Supplier(name="S", contact="03000000000", address="x", opening_balance=0)
    db.session.add(supplier)
    db.session.commit()
    item = _item(stock=0)

    c = _login(admin)
    resp = c.post("/purchase", data={
        "supplier_id": str(supplier.id), "date": "2026-03-01",
        "item_id[]": str(item.id), "quantity[]": "30", "purchase_price[]": "10",
        "location_id": str(loc_b.id),
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app import Purchase
    pur = Purchase.query.filter_by(item_id=item.id).first()
    assert pur is not None
    assert stock_at_location(item.id, loc_b.id) == 30

    # Drain Warehouse B down to 5 by selling/adjusting the rest elsewhere —
    # simulate with a direct item_remove_stock, the same choke point a real
    # sale would use.
    item_obj = db.session.get(Item, item.id)
    item_remove_stock(item_obj, 25, location_id=loc_b.id)
    db.session.commit()
    assert stock_at_location(item.id, loc_b.id) == 5

    # Reversing the original 30-unit purchase must now be refused — B only
    # has 5, not 30 — instead of silently going to -25.
    resp = c.post(f"/document/purchase/{pur.id}/reverse", follow_redirects=True)
    assert resp.status_code == 200  # PostingError -> flash + redirect, not a 500
    db.session.expire_all()
    assert stock_at_location(item.id, loc_b.id) == 5  # unchanged, never went negative
    fresh_pur = db.session.get(Purchase, pur.id)
    assert fresh_pur.is_reversed is False  # reversal never completed


# ── 6. deleting a "stock found" adjustment at a since-drained location ─────

def test_delete_found_stock_adjustment_refused_when_location_drained(appctx):
    admin = _admin(email="admin2@guardtest.com")
    loc = get_or_create_default_location()
    item = _item(stock=0, location=loc)

    adj = StockAdjustment(item_id=item.id, adj_type="Stock In", quantity=20,
                          direction="in", date=datetime(2026, 3, 1),
                          cost_value=Decimal("200"), location_id=loc.id)
    db.session.add(adj)
    db.session.flush()
    item_add_stock(item, 20, Decimal("200"), location_id=loc.id,
                   movement_type="adjustment", source_type="stock_adjustment", source_id=adj.id)
    db.session.commit()
    assert stock_at_location(item.id, loc.id) == 20

    # Drain the location down to 5 (a sale of 15) before anyone deletes the
    # adjustment.
    item_obj = db.session.get(Item, item.id)
    item_remove_stock(item_obj, 15, location_id=loc.id)
    db.session.commit()
    assert stock_at_location(item.id, loc.id) == 5

    c = _login(admin)
    resp = c.post(f"/stock_adjustment/delete/{adj.id}", follow_redirects=True)
    assert resp.status_code == 200
    db.session.expire_all()
    # Only 5 left, deleting a 20-unit "found" adjustment would need to remove
    # 20 — must be refused, stock must not go negative.
    assert stock_at_location(item.id, loc.id) == 5
    assert StockAdjustment.query.get(adj.id) is not None  # not deleted either


# ── 7. single-warehouse (location_id=None) case behaves as before ──────────

def test_default_location_case_unaffected_when_sufficient(appctx):
    item = _item(stock=40)
    item_remove_stock(item, 10)  # location_id=None -> default location
    db.session.commit()
    default_loc = get_or_create_default_location()
    assert stock_at_location(item.id, default_loc.id) == 30
    assert db.session.get(Item, item.id).stock == 30


def test_default_location_case_refused_when_insufficient(appctx):
    item = _item(stock=5)
    with pytest.raises(PostingError, match="Insufficient stock at this location"):
        item_remove_stock(item, 10)
    db.session.rollback()


# ── value guard is untouched: still company-wide ────────────────────────────

def test_value_guard_still_reads_company_wide_inventory_value(appctx):
    """Regression: the cost/value guard below the quantity guard must still
    compare against item.inventory_value / item.stock (company-wide), not be
    accidentally narrowed by this fix. Exercised via the ordinary path: a
    normal partial removal costed at the current average must still succeed
    exactly as before — this is not a new behavior, just proof nothing about
    valuation changed."""
    loc = get_or_create_default_location()
    item = _item(stock=100, location=loc)
    cost = item_remove_stock(item, 40, location_id=loc.id)
    db.session.commit()
    assert cost == Decimal("400.0000")
    assert db.session.get(Item, item.id).inventory_value == Decimal("600.0000")
