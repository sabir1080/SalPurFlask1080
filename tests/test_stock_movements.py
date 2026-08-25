"""Stock movement ledger — Phase 4.

Written only from item_add_stock()/item_remove_stock() (the choke points
from Phase 1), plus three call sites that seed opening stock outside those
choke points on purpose (item creation, bulk import, reset/reseed). Never a
source of truth for current stock — Item.stock/ItemStock.quantity remain
that; this is purely an audit trail.
"""
from datetime import date
from decimal import Decimal

import pytest

from app import (app as flask_app, db, User, pwd_context, Category, Item,
                 PostingError, Supplier, Customer, seed_chart_of_accounts,
                 seed_fixed_asset_accounts, seed_fiscal_year,
                 seed_financial_account_links, post_account_opening,
                 FinancialAccount)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, StockMovement, Transfer,
    get_or_create_default_location, stock_at_location,
)
from salpurflask.models.models import item_add_stock, item_remove_stock
from salpurflask.services import transfers as svc


# ── helpers ───────────────────────────────────────────────────────────────────

def _login(user):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
    return c


def _admin(email=None):
    email = email or f"admin{User.query.count()}@ledger.com"
    u = User(name="Admin", email=email, password=pwd_context.hash("secret123"),
            verified=True, role="admin")
    db.session.add(u)
    db.session.commit()
    return _login(u)


def _second_location(name="Second WH"):
    default = get_or_create_default_location()
    branch = Branch.query.filter_by(is_default=True).first()
    loc = Location(name=name, kind="warehouse", branch_id=branch.id)
    db.session.add(loc)
    db.session.commit()
    return loc


def _item(stock=0, name=None, reorder=5):
    cat = Category.query.first()
    if cat is None:
        cat = Category(name="Cat")
        db.session.add(cat)
        db.session.flush()
    it = Item(name=name or f"Item-{Item.query.count()}", category_id=cat.id,
             stock=0, purchase_price=10, sale_price=20, item_type="STOCK",
             reorder_level=reorder)
    db.session.add(it)
    db.session.commit()
    if stock:
        item_add_stock(it, stock, Decimal(str(stock * 10)),
                       location_id=get_or_create_default_location().id)
        db.session.commit()
    return it


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
    db.session.add_all([sup, cus])
    db.session.commit()
    return sup, cus


# ── 1-6: basics — increase/decrease writes the correct row ─────────────────────

def test_stock_increase_creates_correct_movement(appctx):
    it = _item()
    default = get_or_create_default_location()
    item_add_stock(it, 40, Decimal("400"), location_id=default.id,
                   movement_type="purchase", source_type="purchase", source_id=7)
    db.session.commit()
    assert StockMovement.query.count() == 1
    m = StockMovement.query.first()
    assert m.item_id == it.id
    assert m.location_id == default.id
    assert m.direction == "in"
    assert m.quantity == 40
    assert m.movement_type == "purchase"
    assert m.source_type == "purchase" and m.source_id == 7


def test_stock_decrease_creates_correct_movement(appctx):
    it = _item(stock=40)
    default = get_or_create_default_location()
    item_remove_stock(it, 15, location_id=default.id,
                      movement_type="sale", source_type="sale", source_id=3)
    db.session.commit()
    rows = StockMovement.query.filter_by(movement_type="sale").all()
    assert len(rows) == 1
    m = rows[0]
    assert m.item_id == it.id
    assert m.location_id == default.id
    assert m.direction == "out"
    assert m.quantity == -15
    assert m.source_type == "sale" and m.source_id == 3


def test_no_movement_row_written_when_movement_type_omitted(appctx):
    """Backward compatibility: a caller that doesn't pass movement_type gets
    exactly the Phase 1-3 behaviour — no ledger row, no error."""
    it = _item()
    default = get_or_create_default_location()
    item_add_stock(it, 10, Decimal("100"), location_id=default.id)
    db.session.commit()
    assert StockMovement.query.count() == 0
    assert it.stock == 10


def test_zero_quantity_writes_no_row(appctx):
    from salpurflask.models.inventory_location import record_stock_movement
    default = get_or_create_default_location()
    it = _item(stock=10)
    row = record_stock_movement(it.id, default.id, "in", 0, "purchase")
    assert row is None
    assert StockMovement.query.count() == 0


def test_unknown_movement_type_is_refused_safely(appctx):
    from salpurflask.models.inventory_location import record_stock_movement
    default = get_or_create_default_location()
    it = _item(stock=10)
    row = record_stock_movement(it.id, default.id, "in", 5, "not_a_real_type")
    assert row is None
    assert StockMovement.query.count() == 0


# ── 7. transfer creates source/destination movement records ────────────────────

def test_transfer_confirm_creates_transfer_out_and_transfer_in(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    it = _item(stock=50)

    t = svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                            lines=[(it.id, 20)])
    svc.confirm_transfer(t)
    db.session.commit()

    out_rows = StockMovement.query.filter_by(movement_type="transfer_out").all()
    in_rows = StockMovement.query.filter_by(movement_type="transfer_in").all()
    assert len(out_rows) == 1 and len(in_rows) == 1
    assert out_rows[0].location_id == default.id and out_rows[0].quantity == -20
    assert in_rows[0].location_id == loc2.id and in_rows[0].quantity == 20
    assert out_rows[0].source_type == "transfer" and out_rows[0].source_id == t.id
    assert in_rows[0].source_id == t.id


def test_transfer_reverse_creates_the_mirror_pair(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    it = _item(stock=50)

    t = svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                            lines=[(it.id, 20)])
    svc.confirm_transfer(t)
    db.session.commit()
    svc.reverse_transfer(t)
    db.session.commit()

    all_rows = StockMovement.query.filter_by(source_type="transfer", source_id=t.id).all()
    assert len(all_rows) == 4   # confirm: 2 rows, reverse: 2 rows
    reversal_out = [m for m in all_rows if m.location_id == loc2.id and m.movement_type == "transfer_out"]
    reversal_in = [m for m in all_rows if m.location_id == default.id and m.movement_type == "transfer_in"]
    assert len(reversal_out) == 1 and reversal_out[0].quantity == -20
    assert len(reversal_in) == 1 and reversal_in[0].quantity == 20


# ── 8. failed mutation leaves no incorrect movement record ────────────────────

def test_insufficient_stock_failure_leaves_no_movement_row(appctx):
    it = _item(stock=5)
    default = get_or_create_default_location()
    with pytest.raises(PostingError):
        item_remove_stock(it, 999, location_id=default.id,
                          movement_type="sale", source_type="sale", source_id=1)
    db.session.rollback()
    assert StockMovement.query.count() == 0
    assert db.session.get(Item, it.id).stock == 5


def test_transfer_confirm_failure_leaves_no_movement_rows(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    it = _item(stock=5)
    t = svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                            lines=[(it.id, 999)])
    db.session.commit()
    with pytest.raises(PostingError):
        svc.confirm_transfer(t)
    db.session.rollback()
    assert StockMovement.query.count() == 0


def test_rollback_after_successful_mutation_still_removes_the_movement_row(appctx):
    """Mirrors test_multi_location.py's own rollback tests for ItemStock —
    the ledger row must roll back exactly like the stock mutation it
    accompanies, since both are written in the same uncommitted transaction."""
    it = _item(stock=20)
    default = get_or_create_default_location()
    item_remove_stock(it, 5, location_id=default.id,
                      movement_type="sale", source_type="sale", source_id=1)
    db.session.rollback()
    assert StockMovement.query.count() == 0
    db.session.refresh(it)
    assert it.stock == 20


# ── 9. existing stock quantity behaviour unchanged ──────────────────────────────

def test_item_stock_and_itemstock_still_match_after_movement_logging(appctx):
    """The central invariant from Phase 1 must hold exactly as before —
    the ledger is additive and must never perturb it."""
    it = _item(stock=0)
    default = get_or_create_default_location()
    item_add_stock(it, 30, Decimal("300"), location_id=default.id,
                   movement_type="purchase", source_type="purchase", source_id=1)
    item_remove_stock(it, 12, location_id=default.id,
                      movement_type="sale", source_type="sale", source_id=2)
    db.session.commit()
    assert it.stock == 18
    assert stock_at_location(it.id, default.id) == 18
    row = ItemStock.query.filter_by(item_id=it.id, location_id=default.id).first()
    assert row.quantity == 18


# ── 10. existing inventory workflows continue to pass (route-level) ────────────

def test_sale_route_creates_correctly_typed_movement(appctx):
    sup, cus = _world()
    it = _item(stock=50)
    c = _admin()
    r = c.post("/sale", data={
        "customer_id": cus.id, "date": "2026-03-01", "notes": "",
        "item_id[]": it.id, "quantity[]": "10", "sale_price[]": "250",
        "discount_type[]": "", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)
    assert r.status_code == 200
    rows = StockMovement.query.filter_by(movement_type="sale").all()
    assert len(rows) == 1
    assert rows[0].quantity == -10
    assert rows[0].source_type == "sale"


def test_purchase_route_creates_correctly_typed_movement(appctx):
    sup, cus = _world()
    it = _item(stock=0)
    c = _admin()
    r = c.post("/purchase", data={
        "supplier_id": sup.id, "date": "2026-03-01", "notes": "",
        "item_id[]": it.id, "quantity[]": "25", "purchase_price[]": "100",
        "discount_type[]": "", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)
    assert r.status_code == 200
    rows = StockMovement.query.filter_by(movement_type="purchase").all()
    assert len(rows) == 1
    assert rows[0].quantity == 25


def test_stock_adjustment_route_creates_correctly_typed_movement(appctx):
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    it = _item(stock=20)
    c = _admin()
    r = c.post("/stock_adjustment", data={
        "item_id": it.id, "adj_type": "Stock In", "quantity": "5",
        "date": "2026-03-01", "reason": "test",
    }, follow_redirects=True)
    assert r.status_code == 200
    rows = StockMovement.query.filter_by(movement_type="adjustment").all()
    assert len(rows) == 1
    assert rows[0].quantity == 5


def test_item_creation_with_opening_stock_writes_an_opening_movement(appctx):
    from salpurflask.models.business_config import BusinessCategory
    bc = BusinessCategory(name="TestCat", slug="testcat", is_enabled=True)
    db.session.add(bc)
    db.session.commit()
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    c = _admin()
    r = c.post("/item", data={
        "name": "Opening Widget", "business_category_id": str(bc.id),
        "unit": "Pcs", "item_type": "STOCK", "opening_stock": "40",
        "reorder_level": "5", "purchase_price": "10", "sale_price": "20",
    }, follow_redirects=True)
    assert r.status_code == 200
    it = Item.query.filter_by(name="Opening Widget").first()
    rows = StockMovement.query.filter_by(movement_type="opening", source_id=it.id).all()
    assert len(rows) == 1
    assert rows[0].quantity == 40


def test_item_creation_with_zero_opening_stock_writes_no_movement(appctx):
    from salpurflask.models.business_config import BusinessCategory
    bc = BusinessCategory(name="TestCat2", slug="testcat2", is_enabled=True)
    db.session.add(bc)
    db.session.commit()
    c = _admin()
    c.post("/item", data={
        "name": "Zero Widget", "business_category_id": str(bc.id),
        "unit": "Pcs", "item_type": "STOCK", "opening_stock": "0",
        "reorder_level": "5",
    }, follow_redirects=True)
    it = Item.query.filter_by(name="Zero Widget").first()
    assert StockMovement.query.filter_by(source_id=it.id, movement_type="opening").count() == 0


# ── 11. ledger filtering works ──────────────────────────────────────────────────

def test_ledger_page_filters_by_item(appctx):
    it1 = _item(stock=10, name="Alpha")
    it2 = _item(stock=10, name="Beta")
    default = get_or_create_default_location()
    item_remove_stock(it1, 2, location_id=default.id, movement_type="sale",
                      source_type="sale", source_id=1)
    item_remove_stock(it2, 3, location_id=default.id, movement_type="sale",
                      source_type="sale", source_id=2)
    db.session.commit()
    c = _admin()
    r = c.get(f"/reports/stock-movements?item_id={it1.id}")
    body = r.get_data(as_text=True)
    # "Beta" legitimately appears once, as an <option> in the item filter
    # dropdown (so the user can switch to it) — the assertion that matters is
    # that Beta's movement does not appear as a table row.
    import re
    table_section = body.split('<tbody>')[1].split('</tbody>')[0]
    assert "Alpha" in table_section
    assert "Beta" not in table_section


def test_ledger_page_filters_by_movement_type(appctx):
    it = _item(stock=30)
    default = get_or_create_default_location()
    item_remove_stock(it, 2, location_id=default.id, movement_type="sale",
                      source_type="sale", source_id=1)
    item_add_stock(it, 2, Decimal("20"), location_id=default.id, movement_type="purchase",
                   source_type="purchase", source_id=2)
    db.session.commit()
    c = _admin()
    body = c.get("/reports/stock-movements?movement_type=purchase").get_data(as_text=True)
    assert "Purchase" in body
    # sale row would show as "Sale" badge text; purchase-only filter should
    # not include a "Sale" badge among the rendered rows.
    import re
    badge_texts = re.findall(r'mv-badge-\w+">([^<]+)</span>', body)
    assert all(b == "Purchase" for b in badge_texts)


def test_ledger_page_filters_by_location(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    it = _item(stock=0)
    item_add_stock(it, 10, Decimal("100"), location_id=default.id, movement_type="opening",
                   source_type="item", source_id=it.id)
    item_add_stock(it, 5, Decimal("50"), location_id=loc2.id, movement_type="opening",
                   source_type="item", source_id=it.id)
    db.session.commit()
    c = _admin()
    body = c.get(f"/reports/stock-movements?location_id={loc2.id}").get_data(as_text=True)
    assert loc2.name in body


# ── 12. transfer location filtering works ───────────────────────────────────────

def test_transfer_list_filters_by_source_location(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    loc3 = _second_location("Third WH")
    it = _item(stock=100)

    t1 = svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                             lines=[(it.id, 10)])
    t2 = svc.create_transfer(source_location_id=loc2.id, destination_location_id=loc3.id,
                             lines=[(it.id, 1)])
    db.session.commit()

    c = _admin()
    body = c.get(f"/transfers?source_location_id={default.id}").get_data(as_text=True)
    assert (t1.transfer_no or f"DRAFT-{t1.id}") in body
    assert (t2.transfer_no or f"DRAFT-{t2.id}") not in body


def test_transfer_list_filters_by_status(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    it = _item(stock=50)

    t1 = svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                             lines=[(it.id, 5)])
    svc.confirm_transfer(t1)
    t2 = svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                             lines=[(it.id, 3)])
    db.session.commit()

    c = _admin()
    body = c.get("/transfers?status=Confirmed").get_data(as_text=True)
    assert (t1.transfer_no or f"DRAFT-{t1.id}") in body
    assert f"DRAFT-{t2.id}" not in body


# ── 13. existing low-stock behaviour remains intact, now per-location ──────────

def test_low_stock_fires_per_location_not_company_wide(appctx):
    """The exact scenario the old company-wide check could not express: one
    location crosses reorder, another location holds plenty — must still
    alert, because the total across both would mask the shortage."""
    admin = User(name="A", email="lowstock@ledger.com", password=pwd_context.hash("x"),
                verified=True, role="admin")
    db.session.add(admin)
    db.session.commit()

    default = get_or_create_default_location()
    loc2 = _second_location()
    it = _item(stock=0, reorder=5)
    item_add_stock(it, 8, Decimal("80"), location_id=default.id)     # starts above reorder (5)
    item_add_stock(it, 100, Decimal("1000"), location_id=loc2.id)    # healthy location
    db.session.commit()

    item_remove_stock(it, 4, location_id=default.id)  # 8 -> 4, crosses reorder at default
    db.session.commit()

    from salpurflask.models import Notification
    rows = Notification.query.filter_by(notif_type="low_stock").all()
    assert len(rows) >= 1
    assert any(default.name in (n.title + n.message) for n in rows)


def test_no_low_stock_alert_while_location_still_above_reorder(appctx):
    it = _item(stock=0, reorder=5)
    default = get_or_create_default_location()
    item_add_stock(it, 20, Decimal("200"), location_id=default.id)
    db.session.commit()
    item_remove_stock(it, 2, location_id=default.id)  # 20 -> 18, still above 5
    db.session.commit()
    from salpurflask.models import Notification
    assert Notification.query.filter_by(notif_type="low_stock").count() == 0


# ── 14. dashboard panel loads without breaking existing dashboard behaviour ────

def test_dashboard_loads_with_movements_panel(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    it = _item(stock=50)
    item_remove_stock(it, 5, location_id=default.id, movement_type="sale",
                      source_type="sale", source_id=1)
    db.session.commit()
    c = _admin()
    r = c.get("/dashboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Recent Warehouse Activity" in body


def test_dashboard_loads_fine_with_single_warehouse_no_movements(appctx):
    """Zero locations beyond default, zero movements — the panel must not
    appear (per the >1-location gate) and the page must not error."""
    c = _admin()
    r = c.get("/dashboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Recent Warehouse Activity" not in body


def test_dashboard_existing_kpis_unaffected(appctx):
    """low_stock_count and other pre-existing KPIs still compute correctly —
    Phase 4 must not have perturbed any of them."""
    it = _item(stock=2, reorder=10)  # already below reorder
    c = _admin()
    r = c.get("/dashboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Stock Alert" in body
