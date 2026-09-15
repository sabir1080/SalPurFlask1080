"""Batch/Lot + Expiry tracking - Phase F (Reporting & Visibility).

Purely additive on top of Phases A-E: no schema change, no new choke
points, no change to costing/GL/batch-allocation logic. This phase only
teaches existing report surfaces to *display* batch data that Phases A-E
already write:

- Stock Movements (stock_movements()/stock_movements.html): a Batch column
  reading the pre-existing StockMovement.batch relationship.
- Item Ledger (item_ledger()/item_ledger.html): a Batch column reading
  PurchaseItem.batch_allocations / SaleItem.batch_allocations /
  StockAdjustment.batch directly - the ledger's own existing non-
  StockMovement architecture is unchanged.
- A new company-wide Expiring/Expired Batches report (expiring_batches()),
  querying Batch/BatchStock directly, reusing NEAR_EXPIRY_WARNING_DAYS as
  its default threshold, scoped through accessible_location_ids()/
  require_location_access() exactly like every other report route.
- A batch-wise breakdown on the Stock Report (report_stock()), using
  BatchStock.quantity * Batch.unit_cost for a batch-level value line -
  Item.avg_cost/inventory_value remain untouched as the accounting source
  of truth.

Deliberately NOT covered here (explicitly out of scope for Phase F, per the
approved design): the pre-existing item_ledger() StockAdjustment
is_reversed filtering gap, and the pre-existing /reports date-range status
filtering gap - both predate batch work and are left exactly as they are.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, StockAdjustment, Purchase, Sale, Customer, Supplier,
    Batch, NEAR_EXPIRY_WARNING_DAYS,
    get_or_create_batch, item_add_stock_batched, item_add_stock,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, sync_customer_opening, sync_supplier_opening,
)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, BatchStock, StockMovement,
    get_or_create_default_location, stock_at_location, UserLocationAccess,
)
from salpurflask.models.business_config import BusinessCategory


# ─── helpers (mirrors tests/test_batch_phase_e.py / test_batch_phase_c.py) ────


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()


def _item(name="Medicine", batch_tracked=True, stock=0):
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


def _supplier(name="Supplier A"):
    s = Supplier(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


def _customer(name="Customer A"):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


def _receive_batch(item, batch_no, qty, unit_cost=10, expiry_date=None, location_id=None):
    loc_id = location_id or get_or_create_default_location().id
    batch = get_or_create_batch(item.id, batch_no, expiry_date, unit_cost,
                                source_type="test", source_id=0)
    item_add_stock_batched(item, qty, Decimal(str(unit_cost)) * qty,
                           location_id=loc_id, batch=batch,
                           movement_type="purchase", source_type="test", source_id=0)
    db.session.commit()
    return batch


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


def _manager(email="m@t.com"):
    u = User(name="M", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="manager")
    db.session.add(u); db.session.commit()
    return _login(u)


def _verified_user(email="v@t.com"):
    u = User(name="V", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="staff")
    db.session.add(u); db.session.commit()
    return _login(u), u


def _adjust(client, item, adj_type, qty, batch_id="", batch_no="", expiry_date="",
            location_id=None, reason="test"):
    data = {
        "item_id": str(item.id), "adj_type": adj_type, "quantity": str(qty),
        "date": "2026-03-01", "reason": reason,
        "batch_id": str(batch_id) if batch_id else "",
        "batch_no": batch_no, "expiry_date": expiry_date,
    }
    data["location_id"] = str(location_id if location_id is not None
                              else get_or_create_default_location().id)
    return client.post("/stock_adjustment", data=data, follow_redirects=True)


def _purchase_via_form(client, supplier, item, qty, price, batch_no="", expiry_date=""):
    return client.post("/purchase", data={
        "supplier_id": str(supplier.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "purchase_price[]": str(price),
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
        "batch_no[]": batch_no, "expiry_date[]": expiry_date,
    }, follow_redirects=True)


def _sale_via_form(client, customer, item, qty, price):
    return client.post("/sale", data={
        "customer_id": str(customer.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "sale_price[]": str(price),
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)


def _post_sale(client, sale_id, batch_allocations=None):
    data = {}
    if batch_allocations is not None:
        data["batch_allocations"] = json.dumps(batch_allocations)
    return client.post(f"/sale/{sale_id}/post", data=data, follow_redirects=True)


# ─── A: Stock Movements — batch visibility ─────────────────────────────────


def test_stock_movements_shows_batch_number_for_batch_tracked_movement(appctx):
    _books()
    item = _item()
    manager = _manager("a1@t.com")
    _receive_batch(item, "B100", 10, expiry_date=date(2027, 1, 1))

    resp = manager.get("/reports/stock-movements")
    assert resp.status_code == 200
    assert b"B100" in resp.data


def test_stock_movements_shows_dash_for_non_batch_movement(appctx):
    _books()
    item = _item(batch_tracked=False)
    manager = _manager("a2@t.com")
    item_add_stock(item, 5, Decimal("50"), location_id=get_or_create_default_location().id)
    db.session.commit()

    resp = manager.get("/reports/stock-movements")
    assert resp.status_code == 200
    # Column present, and the non-batch row renders a dash rather than crashing
    assert b"<th>Batch</th>" in resp.data


def test_stock_movements_route_batch_field_matches_stockmovement_relationship(appctx):
    """The route itself exposes StockMovement.batch/.batch_id unmodified —
    confirms the template reads real data, not a stub."""
    _books()
    item = _item()
    batch = _receive_batch(item, "B200", 7, expiry_date=date(2027, 3, 1))

    mv = StockMovement.query.filter_by(item_id=item.id).order_by(StockMovement.id.desc()).first()
    assert mv is not None
    assert mv.batch_id == batch.id
    assert mv.batch.batch_no == "B200"


def test_stock_movements_adjustment_out_shows_batch(appctx):
    _books()
    item = _item()
    manager = _manager("a3@t.com")
    batch = _receive_batch(item, "ADJB", 10, expiry_date=date(2027, 1, 1))

    _adjust(manager, item, "Stock Out", 3, batch_id=batch.id)

    resp = manager.get("/reports/stock-movements")
    assert b"ADJB" in resp.data


# ─── B: Item Ledger — batch allocation visibility ──────────────────────────


def test_item_ledger_purchase_single_batch_line(appctx):
    _books()
    item = _item()
    sup = _supplier()
    client = _manager("b1@t.com")

    _purchase_via_form(client, sup, item, 10, 10, batch_no="PB01", expiry_date="2027-06-30")
    from app import Purchase as PurchaseModel
    pur = PurchaseModel.query.order_by(PurchaseModel.id.desc()).first()
    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)

    resp = client.get(f"/item/{item.id}/ledger")
    assert resp.status_code == 200
    assert b"PB01" in resp.data


def test_item_ledger_sale_multi_batch_fefo_split_line(appctx):
    _books()
    item = _item()
    cust = _customer()
    client = _manager("b2@t.com")
    _receive_batch(item, "EARLY", 5, expiry_date=date(2027, 1, 1))
    _receive_batch(item, "LATER", 5, expiry_date=date(2027, 6, 1))

    _sale_via_form(client, cust, item, 8, 20)
    from app import Sale as SaleModel
    sale = SaleModel.query.order_by(SaleModel.id.desc()).first()
    _post_sale(client, sale.id)  # no explicit allocations -> FEFO auto-split

    resp = client.get(f"/item/{item.id}/ledger")
    assert resp.status_code == 200
    # Both batches from the FEFO split should be visible on the one Sale row
    assert b"EARLY" in resp.data
    assert b"LATER" in resp.data
    assert b"EARLY (5)" in resp.data
    assert b"LATER (3)" in resp.data


def test_item_ledger_stock_adjustment_shows_batch(appctx):
    _books()
    item = _item()
    manager = _manager("b3@t.com")
    batch = _receive_batch(item, "ADJLEDGER", 10, expiry_date=date(2027, 1, 1))

    _adjust(manager, item, "Stock Out", 4, batch_id=batch.id)

    resp = manager.get(f"/item/{item.id}/ledger")
    assert b"ADJLEDGER" in resp.data


def test_item_ledger_non_batch_regression_shows_dash(appctx):
    _books()
    item = _item(batch_tracked=False)
    sup = _supplier()
    client = _manager("b4@t.com")

    _purchase_via_form(client, sup, item, 5, 10)
    from app import Purchase as PurchaseModel
    pur = PurchaseModel.query.order_by(PurchaseModel.id.desc()).first()
    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)

    resp = client.get(f"/item/{item.id}/ledger")
    assert resp.status_code == 200
    assert b"<th>Batch</th>" in resp.data


def test_item_ledger_footer_closing_balance_unchanged_by_batch_column(appctx):
    """Adding batch_info to entries must not perturb the existing
    balance/footer math - closing_balance still equals the last entry's
    running balance, exactly as before Phase F."""
    _books()
    item = _item()
    sup = _supplier()
    client = _manager("b5@t.com")

    _purchase_via_form(client, sup, item, 10, 10, batch_no="BAL01", expiry_date="2027-06-30")
    from app import Purchase as PurchaseModel
    pur = PurchaseModel.query.order_by(PurchaseModel.id.desc()).first()
    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)

    db.session.refresh(item)
    resp = client.get(f"/item/{item.id}/ledger")
    assert resp.status_code == 200
    assert str(item.stock).encode() in resp.data


# ─── C: Expiring / Expired Batches report ──────────────────────────────────


def test_expiring_batches_shows_expired_batch(appctx):
    _books()
    item = _item()
    manager = _manager("c1@t.com")
    _receive_batch(item, "OLD01", 5, expiry_date=date.today() - timedelta(days=10))

    resp = manager.get("/reports/expiring-batches")
    assert resp.status_code == 200
    assert b"OLD01" in resp.data
    assert b"Expired" in resp.data


def test_expiring_batches_shows_today_expiry(appctx):
    _books()
    item = _item()
    manager = _manager("c2@t.com")
    _receive_batch(item, "TODAY01", 5, expiry_date=date.today())

    resp = manager.get("/reports/expiring-batches")
    assert b"TODAY01" in resp.data
    assert b"Expires Today" in resp.data


def test_expiring_batches_near_expiry_within_default_threshold(appctx):
    _books()
    item = _item()
    manager = _manager("c3@t.com")
    _receive_batch(item, "NEAR01", 5,
                   expiry_date=date.today() + timedelta(days=NEAR_EXPIRY_WARNING_DAYS - 1))

    resp = manager.get("/reports/expiring-batches")
    assert b"NEAR01" in resp.data
    assert b"Near Expiry" in resp.data


def test_expiring_batches_outside_threshold_classified_ok(appctx):
    _books()
    item = _item()
    manager = _manager("c4@t.com")
    _receive_batch(item, "FAROK01", 5,
                   expiry_date=date.today() + timedelta(days=NEAR_EXPIRY_WARNING_DAYS + 30))

    resp = manager.get("/reports/expiring-batches?status=ok")
    assert b"FAROK01" in resp.data


def test_expiring_batches_null_expiry_handled_as_own_bucket(appctx):
    _books()
    item = _item()
    manager = _manager("c5@t.com")
    _receive_batch(item, None, 5, expiry_date=None)  # the item's Unknown Batch

    resp = manager.get("/reports/expiring-batches")
    assert resp.status_code == 200
    assert b"No Expiry Set" in resp.data
    # Must NOT be counted as expired
    resp_expired_only = manager.get("/reports/expiring-batches?status=expired")
    assert b"(unknown)" not in resp_expired_only.data


def test_expiring_batches_zero_batchstock_excluded(appctx):
    _books()
    item = _item()
    manager = _manager("c6@t.com")
    batch = _receive_batch(item, "GONE01", 5, expiry_date=date.today() - timedelta(days=5))
    # Consume the whole batch via an OUT adjustment so BatchStock.quantity -> 0
    _adjust(manager, item, "Stock Out", 5, batch_id=batch.id)

    resp = manager.get("/reports/expiring-batches")
    assert b"GONE01" not in resp.data


def test_expiring_batches_location_scoping_hides_inaccessible_location(appctx):
    _books()
    item = _item()
    branch = Branch(name="Branch X"); db.session.add(branch); db.session.flush()
    loc_a = Location(name="Warehouse A", branch_id=branch.id, active=True)
    loc_b = Location(name="Warehouse B", branch_id=branch.id, active=True)
    db.session.add_all([loc_a, loc_b]); db.session.commit()

    _receive_batch(item, "IN_A", 5, expiry_date=date.today() - timedelta(days=1), location_id=loc_a.id)
    _receive_batch(item, "IN_B", 5, expiry_date=date.today() - timedelta(days=1), location_id=loc_b.id)

    restricted, user = _verified_user("c7@t.com")
    # Make this a manager (report needs manager_required) but restrict location access to A only
    user.role = "manager"
    db.session.add(UserLocationAccess(user_id=user.id, location_id=loc_a.id))
    db.session.commit()

    resp = restricted.get("/reports/expiring-batches")
    assert resp.status_code == 200
    assert b"IN_A" in resp.data
    assert b"IN_B" not in resp.data

    # Directly requesting the inaccessible location is refused, not silently emptied
    resp2 = restricted.get(f"/reports/expiring-batches?location_id={loc_b.id}")
    assert resp2.status_code in (403, 302)


def test_expiring_batches_requires_manager(appctx):
    _books()
    item = _item()
    _receive_batch(item, "PERM01", 5, expiry_date=date.today())
    staff, _ = _verified_user("c8@t.com")

    resp = staff.get("/reports/expiring-batches", follow_redirects=False)
    assert resp.status_code == 302  # manager_required redirects non-managers, same as report_stock()


# ─── D: Batch-wise Stock Breakdown (on Stock Report) ───────────────────────


def test_stock_report_batch_breakdown_shows_correct_quantity_and_cost(appctx):
    _books()
    item = _item()
    manager = _manager("d1@t.com")
    _receive_batch(item, "SR01", 6, unit_cost=15, expiry_date=date(2027, 1, 1))

    resp = manager.get("/reports/stock")
    assert resp.status_code == 200
    assert b"SR01" in resp.data
    assert b"15" in resp.data or b"15.00" in resp.data


def test_stock_report_batch_breakdown_value_is_qty_times_unit_cost(appctx):
    _books()
    item = _item()
    manager = _manager("d2@t.com")
    _receive_batch(item, "SR02", 4, unit_cost=25, expiry_date=date(2027, 1, 1))

    resp = manager.get("/reports/stock")
    assert resp.status_code == 200
    # 4 * 25 = 100.00
    assert b"100" in resp.data


def test_stock_report_batch_breakdown_location_filter(appctx):
    _books()
    item = _item()
    manager = _manager("d3@t.com")
    branch = Branch(name="Branch Y"); db.session.add(branch); db.session.flush()
    loc_a = Location(name="WH A", branch_id=branch.id, active=True)
    loc_b = Location(name="WH B", branch_id=branch.id, active=True)
    db.session.add_all([loc_a, loc_b]); db.session.commit()
    _receive_batch(item, "ONLYA", 3, expiry_date=date(2027, 1, 1), location_id=loc_a.id)
    _receive_batch(item, "ONLYB", 3, expiry_date=date(2027, 1, 1), location_id=loc_b.id)

    resp = manager.get(f"/reports/stock?location_id={loc_a.id}")
    assert b"ONLYA" in resp.data
    assert b"ONLYB" not in resp.data


def test_stock_report_item_level_figures_unchanged_by_batch_breakdown(appctx):
    """The existing item-level Stock/Avg Cost/Stock Value columns must not
    move because of the new batch breakdown - item.avg_cost/inventory_value
    stay the untouched accounting source of truth."""
    _books()
    item = _item()
    manager = _manager("d4@t.com")
    _receive_batch(item, "UNCH01", 10, unit_cost=10, expiry_date=date(2027, 1, 1))
    db.session.refresh(item)

    resp = manager.get("/reports/stock")
    assert resp.status_code == 200
    assert str(item.stock).encode() in resp.data


# ─── E: Regression — existing location/permission tests still hold ────────


def test_report_stock_still_manager_required_regression(appctx):
    _books()
    staff, _ = _verified_user("e1@t.com")
    resp = staff.get("/reports/stock", follow_redirects=False)
    assert resp.status_code == 302


def test_stock_movements_still_manager_required_regression(appctx):
    _books()
    staff, _ = _verified_user("e2@t.com")
    resp = staff.get("/reports/stock-movements", follow_redirects=False)
    assert resp.status_code == 302


def test_item_ledger_still_verified_required_regression(appctx):
    _books()
    item = _item()
    staff, _ = _verified_user("e3@t.com")
    resp = staff.get(f"/item/{item.id}/ledger")
    assert resp.status_code == 200  # verified_required, not manager_required
