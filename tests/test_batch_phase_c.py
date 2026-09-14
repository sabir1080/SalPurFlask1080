"""Batch/Lot + Expiry tracking - Phase C (Sale, POS, Sale Correction).

Builds on Phase A's schema foundation and Phase B's Purchase-side wiring by
extending batch-tracked lines through the Sale/POS Post/Checkout/Correction
lifecycle. Unlike Purchase, a Sale/POS line never names a batch itself - it
draws from existing batches, chosen by FEFO (First-Expiry-First-Out) unless
an explicit, server-validated manual override is submitted in the request
payload (never persisted in Draft schema, per the approved Phase C design).

Nothing here touches Sale Return, Transfers, Reports, Stock Adjustment,
existing-stock migration, or OCR/AI - those are out of scope for this phase.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Customer, Sale, SaleItem,
    JournalEntry, StockMovement, PostingError,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening, sync_customer_opening,
    Batch, SaleItemBatch,
    get_or_create_batch, item_add_stock_batched,
    fefo_allocate_batches, resolve_sale_batch_allocations,
    NEAR_EXPIRY_WARNING_DAYS,
)
from salpurflask.models.models import STATUS_DRAFT, STATUS_POSTED
from salpurflask.models.inventory_location import BatchStock, ItemStock, get_or_create_default_location
from salpurflask.models.business_config import BusinessCategory


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()
    return FinancialAccount.query.filter_by(name="Cash").first().id


def _item(name="Medicine", batch_tracked=True):
    bcat = BusinessCategory(name="Cat-" + name, slug="cat-" + name.lower(), is_enabled=True)
    db.session.add(bcat); db.session.flush()
    it = Item(name=name, business_category_id=bcat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             opening_stock=0, stock=0, inventory_value=Decimal("0"),
             batch_tracked=batch_tracked)
    db.session.add(it); db.session.flush()
    db.session.commit()
    return it


def _customer(name="Customer A"):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


def _receive_batch(item, batch_no, qty, unit_cost=10, expiry_date=None, location_id=None):
    """Seed batch-tracked stock directly (bypassing Purchase) - the Phase A
    layer this file exercises on the Sale side, same as test_batch_phase_a.py
    does for its own foundation-only tests."""
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
        self._clear_g()
        return self._client.get(*a, **kw)

    def post(self, *a, **kw):
        self._clear_g()
        return self._client.post(*a, **kw)


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
    return _RoleClient(c)


def _manager(email="m@t.com"):
    u = User(name="M", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="manager")
    db.session.add(u); db.session.commit()
    return _login(u)


def _admin(email="a@t.com"):
    u = User(name="A", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="admin")
    db.session.add(u); db.session.commit()
    return _login(u)


def _sale_via_form(client, customer, item, qty, price, disc_type="percent",
                    disc_value="0", tax="0"):
    return client.post("/sale", data={
        "customer_id": str(customer.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "sale_price[]": str(price),
        "discount_type[]": disc_type, "discount_value[]": disc_value, "tax_percent[]": tax,
    }, follow_redirects=True)


def _latest_sale():
    return Sale.query.order_by(Sale.id.desc()).first()


def _post_sale(client, sale_id, batch_allocations=None, allow_expired=False):
    data = {}
    if batch_allocations is not None:
        data["batch_allocations"] = json.dumps(batch_allocations)
    if allow_expired:
        data["allow_expired_sale"] = "1"
    return client.post(f"/sale/{sale_id}/post", data=data, follow_redirects=True)


def _checkout(client, payload):
    return client.post("/pos/checkout", data=json.dumps(payload), content_type="application/json")


def _pos_line(item, qty, price=20, batch_allocations=None):
    ln = {"item_id": item.id, "qty": qty, "price": price, "unit_id": "",
          "discount_type": "percent", "discount_value": 0, "tax_percent": 0}
    if batch_allocations is not None:
        ln["batch_allocations"] = batch_allocations
    return ln


# ─── 1-2: FEFO allocator ────────────────────────────────────────────────────


def test_fefo_single_batch(appctx):
    _books()
    item = _item()
    _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))
    loc_id = get_or_create_default_location().id

    allocations = fefo_allocate_batches(item.id, loc_id, 5)

    assert len(allocations) == 1
    batch, qty = allocations[0]
    assert batch.batch_no == "B001"
    assert qty == 5


def test_fefo_multi_batch_split(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    _receive_batch(item, "EARLY", 5, expiry_date=date(2027, 1, 1))
    _receive_batch(item, "LATER", 5, expiry_date=date(2027, 6, 1))

    allocations = fefo_allocate_batches(item.id, loc_id, 8)

    assert len(allocations) == 2
    assert allocations[0][0].batch_no == "EARLY" and allocations[0][1] == 5
    assert allocations[1][0].batch_no == "LATER" and allocations[1][1] == 3


# ─── 3: Post Sale allocation ────────────────────────────────────────────────


def test_post_sale_creates_sale_item_batch_allocation(appctx):
    _books()
    cust = _customer()
    item = _item()
    _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))
    client = _manager()
    _sale_via_form(client, cust, item, 4, 20)
    sale = _latest_sale()
    si = sale.line_items[0]

    _post_sale(client, sale.id)

    allocs = SaleItemBatch.query.filter_by(sale_item_id=si.id).all()
    assert len(allocs) == 1
    assert allocs[0].quantity == 4
    db.session.refresh(item)
    assert item.stock == 6


# ─── 4: POS allocation ──────────────────────────────────────────────────────


def test_pos_checkout_creates_sale_item_batch_allocation(appctx):
    _books()
    item = _item()
    _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))
    client = _manager()

    resp = _checkout(client, {
        "customer_id": None, "account_id": "", "amount_paid": 80,
        "items": [_pos_line(item, 4)],
    })
    data = resp.get_json()
    assert data["ok"] is True

    sale = db.session.get(Sale, data["sale_id"])
    si = sale.line_items[0]
    allocs = SaleItemBatch.query.filter_by(sale_item_id=si.id).all()
    assert len(allocs) == 1 and allocs[0].quantity == 4
    db.session.refresh(item)
    assert item.stock == 6


# ─── 5-8: Expiry handling ───────────────────────────────────────────────────


def test_expired_batch_excluded_from_fefo(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    _receive_batch(item, "OLD", 5, expiry_date=date(2020, 1, 1))
    _receive_batch(item, "FRESH", 5, expiry_date=date(2027, 1, 1))

    allocations = fefo_allocate_batches(item.id, loc_id, 3)

    assert len(allocations) == 1
    assert allocations[0][0].batch_no == "FRESH"


def test_expiry_today_is_allowed(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    today = date.today()
    _receive_batch(item, "TODAY", 5, expiry_date=today)

    allocations = fefo_allocate_batches(item.id, loc_id, 3)

    assert len(allocations) == 1
    assert allocations[0][0].batch_no == "TODAY"


def test_null_expiry_sorted_last(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    _receive_batch(item, "DATED", 3, expiry_date=date(2027, 1, 1))
    _receive_batch(item, "UNDATED", 3, expiry_date=None)

    allocations = fefo_allocate_batches(item.id, loc_id, 6)

    assert allocations[0][0].batch_no == "DATED"
    assert allocations[1][0].batch_no == "UNDATED"


def test_near_expiry_warning_surfaced_via_pos_item_batches_endpoint(appctx):
    _books()
    item = _item()
    near = date.today() + timedelta(days=NEAR_EXPIRY_WARNING_DAYS - 5)
    _receive_batch(item, "SOON", 5, expiry_date=near)
    client = _manager()

    resp = client.get(f"/pos/item-batches/{item.id}")
    data = resp.get_json()

    assert data["ok"] is True
    batch_row = next(b for b in data["batches"] if b["batch_no"] == "SOON")
    assert batch_row["near_expiry"] is True
    assert batch_row["expired"] is False


# ─── 9: Insufficient combined valid batch stock ────────────────────────────


def test_fefo_insufficient_combined_stock_raises(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    _receive_batch(item, "B001", 3, expiry_date=date(2027, 1, 1))

    try:
        fefo_allocate_batches(item.id, loc_id, 5)
        assert False, "expected PostingError"
    except PostingError:
        pass


# ─── 10-14: Manual override ─────────────────────────────────────────────────


def test_manual_valid_allocation(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    batch = _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))

    allocations = resolve_sale_batch_allocations(
        item.id, loc_id, 4, [{"batch_id": batch.id, "quantity": 4}])

    assert len(allocations) == 1
    assert allocations[0][1] == 4


def test_manual_split_allocation(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    b1 = _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))
    b2 = _receive_batch(item, "B002", 10, expiry_date=date(2027, 6, 1))

    allocations = resolve_sale_batch_allocations(
        item.id, loc_id, 6,
        [{"batch_id": b1.id, "quantity": 4}, {"batch_id": b2.id, "quantity": 2}])

    assert len(allocations) == 2
    total = sum(qty for _, qty in allocations)
    assert total == 6


def test_manual_quantity_sum_mismatch_rejected(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    batch = _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))

    try:
        resolve_sale_batch_allocations(
            item.id, loc_id, 5, [{"batch_id": batch.id, "quantity": 3}])
        assert False, "expected PostingError"
    except PostingError:
        pass


def test_manual_batch_belongs_to_wrong_item_rejected(appctx):
    _books()
    item_a = _item(name="Med-A")
    item_b = _item(name="Med-B")
    loc_id = get_or_create_default_location().id
    batch_b = _receive_batch(item_b, "B001", 10, expiry_date=date(2027, 1, 1))

    try:
        resolve_sale_batch_allocations(
            item_a.id, loc_id, 4, [{"batch_id": batch_b.id, "quantity": 4}])
        assert False, "expected PostingError"
    except PostingError:
        pass


def test_manual_insufficient_selected_batch_rejected(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    batch = _receive_batch(item, "B001", 3, expiry_date=date(2027, 1, 1))

    try:
        resolve_sale_batch_allocations(
            item.id, loc_id, 5, [{"batch_id": batch.id, "quantity": 5}])
        assert False, "expected PostingError"
    except PostingError:
        pass


# ─── 15: expired-sale authorization ────────────────────────────────────────


def test_expired_batch_rejected_without_authorization(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    batch = _receive_batch(item, "OLD", 5, expiry_date=date(2020, 1, 1))

    try:
        resolve_sale_batch_allocations(
            item.id, loc_id, 3, [{"batch_id": batch.id, "quantity": 3}])
        assert False, "expected PostingError"
    except PostingError:
        pass


def test_expired_batch_allowed_with_explicit_authorization(appctx):
    _books()
    item = _item()
    loc_id = get_or_create_default_location().id
    batch = _receive_batch(item, "OLD", 5, expiry_date=date(2020, 1, 1))

    allocations = resolve_sale_batch_allocations(
        item.id, loc_id, 3, [{"batch_id": batch.id, "quantity": 3}], allow_expired=True)

    assert len(allocations) == 1


# ─── 16: mixed batch/non-batch Sale ────────────────────────────────────────


def test_mixed_batch_and_non_batch_sale_posts_correctly(appctx):
    _books()
    cust = _customer()
    batch_item = _item(name="Tracked")
    plain_item = _item(name="Plain", batch_tracked=False)
    plain_item.stock = 20
    plain_item.inventory_value = Decimal("200")
    db.session.commit()
    post_item_opening(plain_item)
    _receive_batch(batch_item, "B001", 10, expiry_date=date(2027, 1, 1))

    client = _manager()
    resp = client.post("/sale", data={
        "customer_id": str(cust.id), "date": "2026-01-01", "notes": "",
        "item_id[]": [str(batch_item.id), str(plain_item.id)],
        "quantity[]": ["3", "2"],
        "sale_price[]": ["20", "20"],
        "discount_type[]": ["percent", "percent"],
        "discount_value[]": ["0", "0"],
        "tax_percent[]": ["0", "0"],
    }, follow_redirects=True)
    sale = _latest_sale()

    _post_sale(client, sale.id)

    db.session.refresh(batch_item)
    db.session.refresh(plain_item)
    assert batch_item.stock == 7
    assert plain_item.stock == 18
    batch_si = next(si for si in sale.line_items if si.item_id == batch_item.id)
    plain_si = next(si for si in sale.line_items if si.item_id == plain_item.id)
    assert SaleItemBatch.query.filter_by(sale_item_id=batch_si.id).count() == 1
    assert SaleItemBatch.query.filter_by(sale_item_id=plain_si.id).count() == 0


# ─── 17-18: Costing / GL ───────────────────────────────────────────────────


def test_weighted_batch_cost_on_sale_item(appctx):
    _books()
    cust = _customer()
    item = _item()
    _receive_batch(item, "CHEAP", 5, unit_cost=8, expiry_date=date(2027, 1, 1))
    _receive_batch(item, "PRICEY", 5, unit_cost=12, expiry_date=date(2027, 6, 1))
    client = _manager()
    _sale_via_form(client, cust, item, 8, 20)
    sale = _latest_sale()

    _post_sale(client, sale.id)

    db.session.refresh(sale)
    si = sale.line_items[0]
    # 5 @ 8 + 3 @ 12 = 76, / 8 = 9.5
    assert Decimal(str(si.cost_price)) == Decimal("9.5000") or float(si.cost_price) == 9.5


def test_gl_cogs_correct_for_batch_tracked_sale(appctx):
    from app import get_account, gl_balances, ACC_COGS, ACC_INVENTORY
    _books()
    cust = _customer()
    item = _item()
    _receive_batch(item, "B001", 10, unit_cost=10, expiry_date=date(2027, 1, 1))
    client = _manager()
    _sale_via_form(client, cust, item, 4, 20)
    sale = _latest_sale()

    _post_sale(client, sale.id)

    cogs_acct = get_account(ACC_COGS)
    balances = gl_balances()
    assert balances.get(cogs_acct.id, 0) == Decimal("40.0000")


# ─── 19-21: Correction ──────────────────────────────────────────────────────


def test_correction_restores_exact_original_batch_allocations(appctx):
    _books()
    cust = _customer()
    item = _item()
    b1 = _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))
    b2 = _receive_batch(item, "B002", 10, expiry_date=date(2027, 6, 1))
    manager = _manager()
    admin = _admin()

    resp = _checkout(manager, {
        "customer_id": cust.id, "account_id": "", "amount_paid": 300,
        "items": [_pos_line(item, 15, batch_allocations=[
            {"batch_id": b1.id, "quantity": 10}, {"batch_id": b2.id, "quantity": 5}])],
    })
    sale_id = resp.get_json()["sale_id"]
    db.session.refresh(item)
    assert item.stock == 5

    # Correct with the same quantity: reversal restores B001:10, B002:5
    # (both back to full), then fresh FEFO reallocates 15 -> B001:10, B002:5.
    admin.post(f"/sale/correct/{sale_id}", data={
        "customer_id": str(cust.id), "notes": "", "reason": "test correction",
        "item_id[]": str(item.id), "quantity[]": "15", "sale_price[]": "20",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)

    db.session.refresh(item)
    assert item.stock == 5
    sale = db.session.get(Sale, sale_id)
    si = sale.line_items[0]
    allocs = {a.batch_id: a.quantity for a in SaleItemBatch.query.filter_by(sale_item_id=si.id).all()}
    assert allocs.get(b1.id) == 10
    assert allocs.get(b2.id) == 5


def test_correction_re_fefo_when_quantity_changes(appctx):
    _books()
    cust = _customer()
    item = _item()
    b1 = _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))
    manager = _manager()
    admin = _admin()
    _sale_via_form(manager, cust, item, 4, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    db.session.refresh(item)
    assert item.stock == 6

    admin.post(f"/sale/correct/{sale.id}", data={
        "customer_id": str(cust.id), "notes": "", "reason": "qty change",
        "item_id[]": str(item.id), "quantity[]": "7", "sale_price[]": "20",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)

    db.session.refresh(item)
    assert item.stock == 3
    sale = db.session.get(Sale, sale.id)
    si = sale.line_items[0]
    allocs = SaleItemBatch.query.filter_by(sale_item_id=si.id).all()
    assert sum(a.quantity for a in allocs) == 7


def test_sale_item_batch_no_orphans_after_correction(appctx):
    """A correction deletes and rebuilds SaleItem rows -- on SQLite the new
    row can legitimately reuse the same primary key the old one had (plain
    AUTOINCREMENT reuse), so identity alone cannot prove the old row is
    gone. What actually matters: exactly one SaleItem now exists for this
    Sale, and its SaleItemBatch allocations sum to its own current
    quantity -- never the old quantity, and never a stray extra row left
    over from the pre-correction allocation (the Phase B bug this mirrors:
    a raw bulk delete that skips ORM cascade)."""
    _books()
    cust = _customer()
    item = _item()
    _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))
    manager = _manager()
    admin = _admin()
    _sale_via_form(manager, cust, item, 4, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)

    admin.post(f"/sale/correct/{sale.id}", data={
        "customer_id": str(cust.id), "notes": "", "reason": "test",
        "item_id[]": str(item.id), "quantity[]": "3", "sale_price[]": "20",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)

    sale = db.session.get(Sale, sale.id)
    assert len(sale.line_items) == 1
    si = sale.line_items[0]
    allocs = SaleItemBatch.query.filter_by(sale_item_id=si.id).all()
    assert sum(a.quantity for a in allocs) == si.quantity == 3
    # No allocation anywhere in the table points at a SaleItem that isn't
    # this Sale's current (and only) line.
    assert SaleItemBatch.query.join(SaleItem).filter(
        SaleItem.sale_id == sale.id, SaleItem.id != si.id).count() == 0


# ─── 22-23: Atomicity ───────────────────────────────────────────────────────


def test_post_sale_atomic_rollback_on_second_line_failure(appctx):
    from app import item_add_stock
    _books()
    cust = _customer()
    item_a = _item(name="Med-A")
    item_b = _item(name="Med-B")
    _receive_batch(item_a, "B001", 10, expiry_date=date(2027, 1, 1))
    # item_b passes the Draft-time stock_at_location() check (ItemStock has
    # quantity) but has NO BatchStock at all -- its line's FEFO allocation
    # fails only at Post, which is exactly the scenario this test needs: a
    # second line that fails deep inside the batch-aware branch, after the
    # first line has already succeeded in the same loop.
    item_add_stock(item_b, 3, Decimal("30"))
    client = _manager()

    resp = client.post("/sale", data={
        "customer_id": str(cust.id), "date": "2026-01-01", "notes": "",
        "item_id[]": [str(item_a.id), str(item_b.id)],
        "quantity[]": ["5", "3"],
        "sale_price[]": ["20", "20"],
        "discount_type[]": ["percent", "percent"],
        "discount_value[]": ["0", "0"],
        "tax_percent[]": ["0", "0"],
    }, follow_redirects=True)
    sale = _latest_sale()

    _post_sale(client, sale.id)

    db.session.refresh(sale)
    db.session.refresh(item_a)
    assert sale.status == STATUS_DRAFT
    assert item_a.stock == 10  # line 1's deduction rolled back too -- still full
    batch_a = Batch.query.filter_by(item_id=item_a.id, batch_no="B001").first()
    bstock = BatchStock.query.filter_by(batch_id=batch_a.id).first()
    assert bstock.quantity == 10
    assert SaleItemBatch.query.join(SaleItem).filter(SaleItem.sale_id == sale.id).count() == 0


def test_pos_checkout_atomic_rollback_on_second_line_failure(appctx):
    from app import item_add_stock
    _books()
    item_a = _item(name="Med-A")
    item_b = _item(name="Med-B")
    _receive_batch(item_a, "B001", 10, expiry_date=date(2027, 1, 1))
    # Same ItemStock-without-BatchStock setup as the Post-side atomic test --
    # passes the Draft/checkout-time availability check, fails only inside
    # FEFO allocation.
    item_add_stock(item_b, 3, Decimal("30"))
    client = _manager()

    sales_before = Sale.query.count()
    resp = _checkout(client, {
        "customer_id": None, "account_id": "", "amount_paid": 200,
        "items": [_pos_line(item_a, 5), _pos_line(item_b, 3)],
    })
    data = resp.get_json()

    assert data["ok"] is False
    assert Sale.query.count() == sales_before
    db.session.refresh(item_a)
    assert item_a.stock == 10  # line 1's deduction rolled back too -- still full
    batch_a = Batch.query.filter_by(item_id=item_a.id, batch_no="B001").first()
    bstock = BatchStock.query.filter_by(batch_id=batch_a.id).first()
    assert bstock.quantity == 10
    assert SaleItemBatch.query.count() == 0
    # No dangling StockMovement with a NULL source_id left behind either --
    # the whole request rolled back, including line_a's own movement row.
    assert StockMovement.query.filter_by(item_id=item_a.id, source_type="sale",
                                         source_id=None).count() == 0


# ─── 24-25: Invariants ──────────────────────────────────────────────────────


def test_sale_item_batch_quantity_invariant(appctx):
    _books()
    cust = _customer()
    item = _item()
    _receive_batch(item, "B001", 5, expiry_date=date(2027, 1, 1))
    _receive_batch(item, "B002", 5, expiry_date=date(2027, 6, 1))
    client = _manager()
    _sale_via_form(client, cust, item, 8, 20)
    sale = _latest_sale()

    _post_sale(client, sale.id)

    sale = db.session.get(Sale, sale.id)
    si = sale.line_items[0]
    total_alloc = sum(a.quantity for a in SaleItemBatch.query.filter_by(sale_item_id=si.id).all())
    assert total_alloc == si.quantity


def test_batchstock_itemstock_reconciliation_after_sale(appctx):
    _books()
    cust = _customer()
    item = _item()
    _receive_batch(item, "B001", 10, expiry_date=date(2027, 1, 1))
    client = _manager()
    _sale_via_form(client, cust, item, 4, 20)
    sale = _latest_sale()

    _post_sale(client, sale.id)

    loc_id = get_or_create_default_location().id
    istock = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first()
    batch = Batch.query.filter_by(item_id=item.id, batch_no="B001").first()
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert istock.quantity == bstock.quantity == 6


# ─── 26: Static lock-order protection ──────────────────────────────────────


def test_fefo_allocate_batches_locks_item_before_batch_query(appctx):
    """SQLite cannot demonstrate real concurrent locking (see Phase A's own
    precedent for this style of test) -- this proves the source-level
    ordering instead: get_item_locked() is called, and nothing that reads
    Batch/BatchStock happens before it, inside fefo_allocate_batches()."""
    import inspect
    from salpurflask.models import models as models_module

    source = inspect.getsource(models_module.fefo_allocate_batches)
    lock_pos = source.index("get_item_locked(item_id)")
    batch_query_pos = source.index("Batch.query" if "Batch.query" in source else "db.session.query(Batch")
    assert lock_pos < batch_query_pos


def test_resolve_sale_batch_allocations_locks_item_before_batch_lookup(appctx):
    import inspect
    from salpurflask.models import models as models_module

    source = inspect.getsource(models_module.resolve_sale_batch_allocations)
    lock_pos = source.index("get_item_locked(item_id)")
    batch_get_pos = source.index("db.session.get(Batch, batch_id)")
    assert lock_pos < batch_get_pos


# ─── 27: Existing non-batch Sale/POS regression coverage ───────────────────


def test_non_batch_sale_post_unaffected(appctx):
    _books()
    cust = _customer()
    item = _item(batch_tracked=False)
    item.stock = 20
    item.inventory_value = Decimal("200")
    db.session.commit()
    post_item_opening(item)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sale = _latest_sale()

    _post_sale(client, sale.id)

    db.session.refresh(item)
    assert item.stock == 15
    si = sale.line_items[0]
    assert SaleItemBatch.query.filter_by(sale_item_id=si.id).count() == 0


def test_non_batch_pos_checkout_unaffected(appctx):
    _books()
    item = _item(batch_tracked=False)
    item.stock = 20
    item.inventory_value = Decimal("200")
    db.session.commit()
    post_item_opening(item)
    client = _manager()

    resp = _checkout(client, {
        "customer_id": None, "account_id": "", "amount_paid": 100,
        "items": [_pos_line(item, 5)],
    })
    data = resp.get_json()

    assert data["ok"] is True
    db.session.refresh(item)
    assert item.stock == 15
