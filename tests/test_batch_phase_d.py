"""Batch/Lot + Expiry tracking - Phase D (Sale Return + Transfer).

Builds on Phase A's schema foundation and Phases B/C's Purchase/Sale wiring
by extending batch awareness to the two remaining stock-moving document
types that Phase C deliberately deferred: Sale Return and Transfer.

Sale Return policy (approved design, Decision 1): fully automatic,
deterministic, original-allocation-order restoration - no manual override,
no new schema (SaleReturn carries no batch column; the split is
reconstructed from SaleItem.batch_allocations plus the running total of
prior SaleReturn.quantity for that line).

Transfer policy (approved design): one TransferItem row per batch (mirrors
Purchase Phase B's "1 line = 1 batch" convention), reusing the existing
nullable TransferItem.batch_id column from Phase A - no new junction table.
Expired batches may be transferred freely (an internal movement, not a
sale) - no allow_expired flag needed.

Nothing here touches Reports, Stock Adjustment batch support, existing-
stock migration, or OCR/AI - out of scope for this phase.
"""
import json
from datetime import date
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Customer, Sale, SaleItem, SaleReturn,
    PostingError,
    Batch, SaleItemBatch,
    get_or_create_batch, item_add_stock_batched, item_add_stock,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, sync_customer_opening,
)
from salpurflask.models.models import (
    STATUS_DRAFT, STATUS_POSTED,
    resolve_sale_return_batch_allocations, resolve_sale_return_reversal_batches,
)
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, BatchStock, StockMovement, Transfer, TransferItem,
    get_or_create_default_location, stock_at_location,
)
from salpurflask.models.business_config import BusinessCategory
from salpurflask.services import transfers as svc


# ─── shared helpers ─────────────────────────────────────────────────────────


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


def _sale_via_form(client, customer, item, qty, price):
    return client.post("/sale", data={
        "customer_id": str(customer.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "sale_price[]": str(price),
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)


def _latest_sale():
    return Sale.query.order_by(Sale.id.desc()).first()


def _post_sale(client, sale_id):
    return client.post(f"/sale/{sale_id}/post", data={}, follow_redirects=True)


def _sale_return_via_form(client, si, qty, price=20, reason="test"):
    return client.post("/sale-return", data={
        "date": "2026-01-05",
        "sale_item_id[]": str(si.id), "quantity[]": str(qty),
        "return_price[]": str(price), "reason[]": reason,
    }, follow_redirects=True)


def _latest_sale_return():
    return SaleReturn.query.order_by(SaleReturn.id.desc()).first()


def _reverse_document(client, kind, doc_id, confirm_payment_warning=False):
    data = {}
    if confirm_payment_warning:
        data["confirm_payment_warning"] = "1"
    return client.post(f"/document/{kind}/{doc_id}/reverse", data=data, follow_redirects=True)


# ═══════════════════════════════════════════════════════════════════════════
# SALE RETURN
# ═══════════════════════════════════════════════════════════════════════════


def _sold_batch_tracked(customer, item, batches, qty_per_batch=None):
    """Receive a batch-tracked item into several batches, sell all of it in
    one Sale, and return the posted Sale + its single SaleItem."""
    manager = _manager("m_sr@t.com")
    for batch_no, qty, cost, exp in batches:
        _receive_batch(item, batch_no, qty, unit_cost=cost, expiry_date=exp)
    total_qty = sum(b[1] for b in batches)
    _sale_via_form(manager, customer, item, total_qty, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    db.session.refresh(sale)
    return sale


def test_sale_return_single_batch_full_return(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [("B001", 10, 100, date(2027, 1, 1))])
    si = sale.line_items[0]
    manager = _manager("m1@t.com")

    _sale_return_via_form(manager, si, 10)

    db.session.refresh(item)
    assert item.stock == 10
    sr = _latest_sale_return()
    assert sr.cost_restored == Decimal("1000.0000")


def test_sale_return_multi_batch_full_return(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [
        ("A", 10, 100, date(2027, 1, 1)), ("B", 5, 130, date(2027, 6, 1))])
    si = sale.line_items[0]
    manager = _manager("m2@t.com")

    _sale_return_via_form(manager, si, 15)

    db.session.refresh(item)
    assert item.stock == 15
    sr = _latest_sale_return()
    # 10*100 + 5*130 = 1650
    assert sr.cost_restored == Decimal("1650.0000")


def test_sale_return_partial_return(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [
        ("A", 10, 100, date(2027, 1, 1)), ("B", 5, 130, date(2027, 6, 1))])
    si = sale.line_items[0]
    manager = _manager("m3@t.com")

    _sale_return_via_form(manager, si, 5)

    db.session.refresh(item)
    assert item.stock == 5
    sr = _latest_sale_return()
    # First 5 units come from Batch A only.
    assert sr.cost_restored == Decimal("500.0000")


def test_sale_return_multiple_partial_returns_against_same_sale_item(appctx):
    """The exact worked example from the approved design: Batch A=10@100,
    Batch B=5@130. Return 5, then 7 more, then the remaining 3."""
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [
        ("A", 10, 100, date(2027, 1, 1)), ("B", 5, 130, date(2027, 6, 1))])
    si = sale.line_items[0]
    manager = _manager("m4@t.com")

    _sale_return_via_form(manager, si, 5)
    sr1 = _latest_sale_return()
    assert sr1.cost_restored == Decimal("500.0000")  # 5 from A

    _sale_return_via_form(manager, si, 7)
    sr2 = _latest_sale_return()
    # remaining A = 5, then 2 from B: 5*100 + 2*130 = 760
    assert sr2.cost_restored == Decimal("760.0000")

    _sale_return_via_form(manager, si, 3)
    sr3 = _latest_sale_return()
    # remaining B = 3: 3*130 = 390
    assert sr3.cost_restored == Decimal("390.0000")

    db.session.refresh(item)
    assert item.stock == 15  # all 15 units back


def test_sale_return_partial_spanning_multiple_batches_in_one_return(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [
        ("A", 10, 100, date(2027, 1, 1)), ("B", 5, 130, date(2027, 6, 1))])
    si = sale.line_items[0]
    manager = _manager("m5@t.com")

    _sale_return_via_form(manager, si, 12)

    sr = _latest_sale_return()
    # 10 from A + 2 from B: 1000 + 260 = 1260
    assert sr.cost_restored == Decimal("1260.0000")


def test_sale_return_quantity_exceeding_remaining_rejected(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [("A", 10, 100, date(2027, 1, 1))])
    si = sale.line_items[0]
    manager = _manager("m6@t.com")

    _sale_return_via_form(manager, si, 10)
    returns_before = SaleReturn.query.count()
    _sale_return_via_form(manager, si, 1)  # nothing left to return

    assert SaleReturn.query.count() == returns_before


def test_resolve_sale_return_batch_allocations_never_exceeds_original(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [("A", 10, 100, date(2027, 1, 1))])
    si = sale.line_items[0]

    try:
        resolve_sale_return_batch_allocations(si, 5, already_returned_qty=8)
        assert False, "expected PostingError"
    except PostingError:
        pass


def test_sale_return_batchstock_restoration(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [("A", 10, 100, date(2027, 1, 1))])
    si = sale.line_items[0]
    manager = _manager("m7@t.com")

    _sale_return_via_form(manager, si, 6)

    loc_id = get_or_create_default_location().id
    batch = Batch.query.filter_by(item_id=item.id, batch_no="A").first()
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_id).first()
    assert bstock.quantity == 6  # 10 sold - 10 + 6 returned = 6


def test_sale_return_itemstock_reconciliation(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [
        ("A", 6, 100, date(2027, 1, 1)), ("B", 4, 130, date(2027, 6, 1))])
    si = sale.line_items[0]
    manager = _manager("m8@t.com")

    _sale_return_via_form(manager, si, 10)

    loc_id = get_or_create_default_location().id
    istock = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first()
    batch_a = Batch.query.filter_by(item_id=item.id, batch_no="A").first()
    batch_b = Batch.query.filter_by(item_id=item.id, batch_no="B").first()
    bstock_a = BatchStock.query.filter_by(batch_id=batch_a.id, location_id=loc_id).first()
    bstock_b = BatchStock.query.filter_by(batch_id=batch_b.id, location_id=loc_id).first()
    assert istock.quantity == bstock_a.quantity + bstock_b.quantity == 10


def test_sale_return_stock_movement_tagged_with_batch_id(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [("A", 10, 100, date(2027, 1, 1))])
    si = sale.line_items[0]
    manager = _manager("m9@t.com")

    _sale_return_via_form(manager, si, 4)

    sr = _latest_sale_return()
    batch = Batch.query.filter_by(item_id=item.id, batch_no="A").first()
    movement = StockMovement.query.filter_by(item_id=item.id, source_type="sale_return",
                                              source_id=sr.id).first()
    assert movement is not None
    assert movement.batch_id == batch.id


def test_sale_return_gl_correctness(appctx):
    from app import get_account, gl_balances, ACC_INVENTORY, ACC_COGS
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [("A", 10, 100, date(2027, 1, 1))])
    si = sale.line_items[0]
    manager = _manager("m10@t.com")

    inv_before = gl_balances().get(get_account(ACC_INVENTORY).id, Decimal("0"))

    _sale_return_via_form(manager, si, 6)

    inv_after = gl_balances().get(get_account(ACC_INVENTORY).id, Decimal("0"))
    assert inv_after - inv_before == Decimal("600.0000")  # 6 * 100


def test_non_batch_sale_return_unaffected(appctx):
    _books()
    cust = _customer()
    item = _item(batch_tracked=False)
    item.stock = 20
    item.inventory_value = Decimal("200")
    db.session.commit()
    from app import post_item_opening
    post_item_opening(item)
    manager = _manager("m11@t.com")
    _sale_via_form(manager, cust, item, 5, 20)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    si = sale.line_items[0]
    db.session.refresh(item)
    assert item.stock == 15

    _sale_return_via_form(manager, si, 3)

    db.session.refresh(item)
    assert item.stock == 18
    sr = _latest_sale_return()
    assert SaleItemBatch.query.count() == 0
    assert sr.cost_restored == Decimal(str(si.cost_price)) * 3


def test_sale_return_reversal_restores_exact_batches(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [
        ("A", 10, 100, date(2027, 1, 1)), ("B", 5, 130, date(2027, 6, 1))])
    si = sale.line_items[0]
    manager = _manager("m12@t.com")
    admin = _admin("a12@t.com")

    _sale_return_via_form(manager, si, 12)  # 10 from A, 2 from B
    sr = _latest_sale_return()
    db.session.refresh(item)
    assert item.stock == 12

    resp = _reverse_document(admin, "sale_return", sr.id)

    db.session.refresh(item)
    assert item.stock == 0  # the return itself undone -- back to post-sale state
    loc_id = get_or_create_default_location().id
    batch_a = Batch.query.filter_by(item_id=item.id, batch_no="A").first()
    batch_b = Batch.query.filter_by(item_id=item.id, batch_no="B").first()
    bstock_a = BatchStock.query.filter_by(batch_id=batch_a.id, location_id=loc_id).first()
    bstock_b = BatchStock.query.filter_by(batch_id=batch_b.id, location_id=loc_id).first()
    assert bstock_a.quantity == 0
    assert bstock_b.quantity == 0


def test_sale_return_reversal_removes_exact_batch_quantities_not_blended(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [
        ("A", 10, 100, date(2027, 1, 1)), ("B", 5, 130, date(2027, 6, 1))])
    si = sale.line_items[0]
    manager = _manager("m13@t.com")
    admin = _admin("a13@t.com")

    _sale_return_via_form(manager, si, 5)  # all from A
    sr1 = _latest_sale_return()
    _sale_return_via_form(manager, si, 7)  # 5 from A, 2 from B
    sr2 = _latest_sale_return()

    loc_id = get_or_create_default_location().id
    batch_a = Batch.query.filter_by(item_id=item.id, batch_no="A").first()
    batch_b = Batch.query.filter_by(item_id=item.id, batch_no="B").first()

    # Reverse only sr2 (5 from A, 2 from B) -- sr1's contribution (5 from A)
    # must remain untouched.
    _reverse_document(admin, "sale_return", sr2.id)

    bstock_a = BatchStock.query.filter_by(batch_id=batch_a.id, location_id=loc_id).first()
    bstock_b = BatchStock.query.filter_by(batch_id=batch_b.id, location_id=loc_id).first()
    # Before sr2 reversal: A had 0 sold + 5(sr1) + 5(sr2 A-part) = 5 restored
    # (10-10+5+5=10 -> wait, sold 15 total, A=10 all sold, B=5 all sold).
    # After both returns: A restored 10 (5+5), B restored 2. Reversing sr2
    # removes A:5, B:2 -> A back to 5, B back to 0.
    assert bstock_a.quantity == 5
    assert bstock_b.quantity == 0


def test_sale_return_no_batch_allocation_duplication_across_partial_returns(appctx):
    """The central correctness property: two partial returns against the
    same SaleItem must never both draw from the same original units."""
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [("A", 10, 100, date(2027, 1, 1))])
    si = sale.line_items[0]
    manager = _manager("m14@t.com")

    _sale_return_via_form(manager, si, 4)
    _sale_return_via_form(manager, si, 4)
    _sale_return_via_form(manager, si, 2)  # exactly exhausts the 10

    total_returned = sum(sr.quantity for sr in SaleReturn.query.filter_by(sale_item_id=si.id).all())
    assert total_returned == 10
    db.session.refresh(item)
    assert item.stock == 10  # every unit restored exactly once, no double-credit

    # A further return must now be refused (nothing left).
    returns_before = SaleReturn.query.count()
    _sale_return_via_form(manager, si, 1)
    assert SaleReturn.query.count() == returns_before


def test_sale_item_batch_allocation_sum_invariant_unaffected_by_returns(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [
        ("A", 10, 100, date(2027, 1, 1)), ("B", 5, 130, date(2027, 6, 1))])
    si = sale.line_items[0]
    manager = _manager("m15@t.com")

    _sale_return_via_form(manager, si, 8)

    total_alloc = sum(a.quantity for a in SaleItemBatch.query.filter_by(sale_item_id=si.id).all())
    assert total_alloc == si.quantity == 15  # original allocation untouched by returns


def test_sale_return_batchstock_itemstock_invariant(appctx):
    _books()
    cust = _customer()
    item = _item()
    sale = _sold_batch_tracked(cust, item, [
        ("A", 10, 100, date(2027, 1, 1)), ("B", 5, 130, date(2027, 6, 1))])
    si = sale.line_items[0]
    manager = _manager("m16@t.com")

    _sale_return_via_form(manager, si, 9)

    loc_id = get_or_create_default_location().id
    istock = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first()
    total_batchstock = sum(
        bs.quantity for bs in BatchStock.query.filter(
            BatchStock.batch_id.in_(
                db.session.query(Batch.id).filter_by(item_id=item.id))).all())
    assert istock.quantity == total_batchstock


# ═══════════════════════════════════════════════════════════════════════════
# TRANSFER
# ═══════════════════════════════════════════════════════════════════════════


def _second_location(name="Second WH"):
    default = get_or_create_default_location()
    branch = Branch.query.filter_by(is_default=True).first()
    loc = Location(name=name, kind="warehouse", branch_id=branch.id)
    db.session.add(loc)
    db.session.commit()
    return loc


def _xfer_item(name="Med", batch_tracked=True):
    bcat = BusinessCategory(name="XCat-" + name, slug="xcat-" + name.lower(), is_enabled=True)
    db.session.add(bcat); db.session.flush()
    it = Item(name=name, business_category_id=bcat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             opening_stock=0, stock=0, inventory_value=Decimal("0"),
             batch_tracked=batch_tracked)
    db.session.add(it); db.session.commit()
    return it


def _new_transfer_with_batches(c, source, dest, lines, date_str="2026-03-01"):
    """`lines` = [(item, qty, batch_or_None), ...]."""
    data = {
        "source_location_id": str(source.id), "destination_location_id": str(dest.id),
        "date": date_str, "notes": "test",
        "item_id[]": [str(it.id) for it, _, _ in lines],
        "quantity[]": [str(q) for _, q, _ in lines],
        "batch_id[]": [str(b.id) if b else "" for _, _, b in lines],
    }
    return c.post("/transfers/new", data=data, follow_redirects=True)


def test_transfer_single_batch(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item()
    batch = _receive_batch(item, "A", 10, expiry_date=date(2027, 1, 1), location_id=source.id)
    c = _admin("xa1@t.com")

    r = _new_transfer_with_batches(c, source, dest, [(item, 6, batch)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    db.session.refresh(t)
    assert t.status == "Confirmed"
    src_bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=source.id).first()
    dst_bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=dest.id).first()
    assert src_bstock.quantity == 4
    assert dst_bstock.quantity == 6


def test_transfer_multi_batch_two_transfer_item_rows(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item()
    batch_a = _receive_batch(item, "A", 10, expiry_date=date(2027, 1, 1), location_id=source.id)
    batch_b = _receive_batch(item, "B", 5, expiry_date=date(2027, 6, 1), location_id=source.id)
    c = _admin("xa2@t.com")

    _new_transfer_with_batches(c, source, dest, [(item, 10, batch_a), (item, 5, batch_b)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    assert len(t.lines) == 2
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    db.session.refresh(t)
    assert t.status == "Confirmed"
    dst_a = BatchStock.query.filter_by(batch_id=batch_a.id, location_id=dest.id).first()
    dst_b = BatchStock.query.filter_by(batch_id=batch_b.id, location_id=dest.id).first()
    assert dst_a.quantity == 10
    assert dst_b.quantity == 5


def test_transfer_insufficient_source_batch_stock_rejected(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item()
    batch = _receive_batch(item, "A", 3, expiry_date=date(2027, 1, 1), location_id=source.id)
    c = _admin("xa3@t.com")

    _new_transfer_with_batches(c, source, dest, [(item, 5, batch)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    db.session.refresh(t)
    assert t.status == "Draft"  # confirm refused, stayed Draft
    src_bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=source.id).first()
    assert src_bstock.quantity == 3  # untouched


def test_transfer_source_batchstock_reconciliation(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item()
    batch = _receive_batch(item, "A", 10, expiry_date=date(2027, 1, 1), location_id=source.id)
    c = _admin("xa4@t.com")

    _new_transfer_with_batches(c, source, dest, [(item, 7, batch)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    src_istock = ItemStock.query.filter_by(item_id=item.id, location_id=source.id).first()
    src_bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=source.id).first()
    assert src_istock.quantity == src_bstock.quantity == 3


def test_transfer_destination_batchstock_reconciliation(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item()
    batch = _receive_batch(item, "A", 10, expiry_date=date(2027, 1, 1), location_id=source.id)
    c = _admin("xa5@t.com")

    _new_transfer_with_batches(c, source, dest, [(item, 7, batch)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    dst_istock = ItemStock.query.filter_by(item_id=item.id, location_id=dest.id).first()
    dst_bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=dest.id).first()
    assert dst_istock.quantity == dst_bstock.quantity == 7


def test_transfer_stock_movement_batch_id_on_transfer_out_and_in(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item()
    batch = _receive_batch(item, "A", 10, expiry_date=date(2027, 1, 1), location_id=source.id)
    c = _admin("xa6@t.com")

    _new_transfer_with_batches(c, source, dest, [(item, 6, batch)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    out_mv = StockMovement.query.filter_by(item_id=item.id, source_type="transfer",
                                           source_id=t.id, movement_type="transfer_out").first()
    in_mv = StockMovement.query.filter_by(item_id=item.id, source_type="transfer",
                                          source_id=t.id, movement_type="transfer_in").first()
    assert out_mv is not None and out_mv.batch_id == batch.id
    assert in_mv is not None and in_mv.batch_id == batch.id


def test_transfer_expired_batch_allowed(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item()
    batch = _receive_batch(item, "OLD", 10, expiry_date=date(2020, 1, 1), location_id=source.id)
    c = _admin("xa7@t.com")

    _new_transfer_with_batches(c, source, dest, [(item, 5, batch)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    db.session.refresh(t)
    assert t.status == "Confirmed"  # no expired-batch rejection for transfers
    dst_bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=dest.id).first()
    assert dst_bstock.quantity == 5


def test_transfer_atomic_rollback_when_second_line_fails(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item_a = _xfer_item(name="Med-A")
    item_b = _xfer_item(name="Med-B")
    batch_a = _receive_batch(item_a, "A", 10, expiry_date=date(2027, 1, 1), location_id=source.id)
    batch_b = _receive_batch(item_b, "B", 3, expiry_date=date(2027, 1, 1), location_id=source.id)
    c = _admin("xa8@t.com")

    # Line 1 (item_a, qty 5) would succeed alone; line 2 (item_b, qty 10)
    # exceeds batch_b's available 3 -- the whole confirm must fail together.
    _new_transfer_with_batches(c, source, dest, [(item_a, 5, batch_a), (item_b, 10, batch_b)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    db.session.refresh(t)
    assert t.status == "Draft"
    src_bstock_a = BatchStock.query.filter_by(batch_id=batch_a.id, location_id=source.id).first()
    assert src_bstock_a.quantity == 10  # line 1's deduction rolled back too
    assert BatchStock.query.filter_by(batch_id=batch_a.id, location_id=dest.id).count() == 0


def test_transfer_reversal_restores_exact_original_batches(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item()
    batch_a = _receive_batch(item, "A", 10, expiry_date=date(2027, 1, 1), location_id=source.id)
    batch_b = _receive_batch(item, "B", 5, expiry_date=date(2027, 6, 1), location_id=source.id)
    c = _admin("xa9@t.com")

    _new_transfer_with_batches(c, source, dest, [(item, 10, batch_a), (item, 5, batch_b)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    c.post(f"/transfers/{t.id}/reverse", follow_redirects=True)

    db.session.refresh(t)
    assert t.status == "Reversed"
    src_a = BatchStock.query.filter_by(batch_id=batch_a.id, location_id=source.id).first()
    src_b = BatchStock.query.filter_by(batch_id=batch_b.id, location_id=source.id).first()
    dst_a = BatchStock.query.filter_by(batch_id=batch_a.id, location_id=dest.id).first()
    dst_b = BatchStock.query.filter_by(batch_id=batch_b.id, location_id=dest.id).first()
    assert src_a.quantity == 10 and src_b.quantity == 5
    assert dst_a.quantity == 0 and dst_b.quantity == 0


def test_transfer_location_permission_regression(appctx):
    """A manager restricted to locations they don't hold for this transfer
    must still be refused -- existing require_transfer_access() behavior,
    unaffected by batch logic."""
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item()
    _receive_batch(item, "A", 10, expiry_date=date(2027, 1, 1), location_id=source.id)
    from salpurflask.models.inventory_location import UserLocationAccess
    u = User(name="Restricted", email="restricted@t.com",
            password=pwd_context.hash("secret123"), verified=True, role="manager")
    db.session.add(u); db.session.commit()
    # Grant access to source only, not destination.
    db.session.add(UserLocationAccess(user_id=u.id, location_id=source.id))
    db.session.commit()
    c = _login(u)

    resp = _new_transfer_with_batches(c, source, dest, [(item, 5, None)])
    assert Transfer.query.count() == 0  # refused before any Draft was created


def test_transfer_item_first_lock_order_static(appctx):
    """SQLite cannot demonstrate real concurrent locking (see Phase A/B/C's
    own precedent) -- proves the source-level ordering instead: both
    confirm_transfer() and reverse_transfer() call get_item_locked() before
    any BatchStock query."""
    import inspect
    source = inspect.getsource(svc.confirm_transfer)
    lock_pos = source.index("get_item_locked(item_id)")
    batchstock_pos = source.index("BatchStock.query")
    assert lock_pos < batchstock_pos

    source2 = inspect.getsource(svc.reverse_transfer)
    lock_pos2 = source2.index("get_item_locked(item_id)")
    batchstock_pos2 = source2.index("BatchStock.query")
    assert lock_pos2 < batchstock_pos2


def test_non_batch_transfer_regression(appctx):
    _books()
    source = get_or_create_default_location()
    dest = _second_location()
    item = _xfer_item(batch_tracked=False)
    item_add_stock(item, 20, Decimal("200"), location_id=source.id)
    db.session.commit()
    c = _admin("xa10@t.com")

    _new_transfer_with_batches(c, source, dest, [(item, 8, None)])
    t = Transfer.query.order_by(Transfer.id.desc()).first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)

    db.session.refresh(t)
    assert t.status == "Confirmed"
    assert stock_at_location(item.id, source.id) == 12
    assert stock_at_location(item.id, dest.id) == 8
    assert t.lines[0].batch_id is None
