"""Warehouse-to-warehouse stock transfers — Phase 3.

A transfer is a real document (Draft -> Confirmed -> Cancelled/Reversed), not
a direct ItemStock update. Every stock movement still goes through
item_add_stock()/item_remove_stock() — the choke points from Phase 1 — so the
Item.stock == SUM(ItemStock.quantity) invariant is inherited, never
re-implemented here.
"""
from datetime import date
from decimal import Decimal

import pytest

from app import app as flask_app, db, User, pwd_context, Category, Item, PostingError
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, Transfer, TransferItem,
    get_or_create_default_location, stock_at_location,
)
from salpurflask.models.models import item_add_stock
from salpurflask.services import transfers as svc


# ── helpers ───────────────────────────────────────────────────────────────────

def _login(user):
    """Sign in without POSTing /signin — see test_selfservice.py's _login()
    for why: two sign-ins from one IP inside a single test trip the rate
    limiter, and the second silently fails while leaving the first session
    active, which would make a permission test pass for the wrong reason."""
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
    return c


def _admin(email=None):
    email = email or f"admin{User.query.count()}@xfer.com"
    u = User(name="Admin", email=email, password=pwd_context.hash("secret123"),
            verified=True, role="admin")
    db.session.add(u)
    db.session.commit()
    return _login(u)


def _manager(email=None):
    email = email or f"manager{User.query.count()}@xfer.com"
    u = User(name="Manager", email=email, password=pwd_context.hash("secret123"),
            verified=True, role="manager")
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


def _item(stock=0, name=None):
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
                       location_id=get_or_create_default_location().id)
        db.session.commit()
    return it


def _company_total(item_id):
    return db.session.get(Item, item_id).stock


def _sum_all_locations(item_id):
    return sum(r.quantity for r in ItemStock.query.filter_by(item_id=item_id).all())


def _new_transfer(c, source, dest, item, qty, date_str="2026-03-01"):
    return c.post("/transfers/new", data={
        "source_location_id": str(source.id), "destination_location_id": str(dest.id),
        "item_id[]": str(item.id), "quantity[]": str(qty),
        "date": date_str, "notes": "test",
    }, follow_redirects=True)


# ── A. basic transfer / B/C. source down, dest up / D. Item.stock unchanged ────

def test_basic_transfer_moves_stock_between_warehouses(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=100)
    c = _admin()

    r = _new_transfer(c, default, loc2, item, 30)
    assert r.status_code == 200
    t = Transfer.query.first()
    assert t.status == "Draft"

    r2 = c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)
    assert r2.status_code == 200
    db.session.refresh(t)
    assert t.status == "Confirmed"
    assert t.transfer_no is not None

    assert stock_at_location(item.id, default.id) == 70    # B: source decreased
    assert stock_at_location(item.id, loc2.id) == 30        # C: destination increased
    assert _company_total(item.id) == 100                   # D: total unchanged
    assert _sum_all_locations(item.id) == 100


# ── E. insufficient source stock ────────────────────────────────────────────

def test_insufficient_source_stock_rejected(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=10)
    c = _admin()

    _new_transfer(c, default, loc2, item, 15)
    t = Transfer.query.first()
    r = c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)
    assert "alert-danger" in r.get_data(as_text=True)
    db.session.refresh(t)
    assert t.status == "Draft"                              # never confirmed
    assert stock_at_location(item.id, default.id) == 10      # untouched
    assert stock_at_location(item.id, loc2.id) == 0
    assert _company_total(item.id) == 10


# ── F. source == destination rejected ──────────────────────────────────────

def test_same_source_and_destination_rejected(appctx):
    default = get_or_create_default_location()
    item = _item(stock=50)
    c = _admin()

    r = _new_transfer(c, default, default, item, 5)
    assert "alert-danger" in r.get_data(as_text=True)
    assert Transfer.query.count() == 0


def test_service_level_same_source_destination_raises(appctx):
    default = get_or_create_default_location()
    item = _item(stock=50)
    with pytest.raises(PostingError):
        svc.create_transfer(source_location_id=default.id, destination_location_id=default.id,
                            lines=[(item.id, 5)])


# ── G. zero/negative quantity rejected ──────────────────────────────────────

def test_zero_quantity_rejected(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=50)
    with pytest.raises(PostingError):
        svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                            lines=[(item.id, 0)])
    assert Transfer.query.count() == 0


def test_negative_quantity_rejected(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=50)
    with pytest.raises(PostingError):
        svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                            lines=[(item.id, -5)])
    assert Transfer.query.count() == 0


# ── H. nonexistent location rejected ────────────────────────────────────────

def test_nonexistent_source_location_rejected(appctx):
    loc2 = _second_location()
    item = _item(stock=50)
    with pytest.raises(PostingError):
        svc.create_transfer(source_location_id=999999, destination_location_id=loc2.id,
                            lines=[(item.id, 5)])


def test_nonexistent_destination_location_rejected(appctx):
    default = get_or_create_default_location()
    item = _item(stock=50)
    with pytest.raises(PostingError):
        svc.create_transfer(source_location_id=default.id, destination_location_id=999999,
                            lines=[(item.id, 5)])


def test_malformed_location_id_from_form_handled_cleanly(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=50)
    c = _admin()
    r = c.post("/transfers/new", data={
        "source_location_id": "not-a-number", "destination_location_id": str(loc2.id),
        "item_id[]": str(item.id), "quantity[]": "5", "date": "2026-03-01",
    }, follow_redirects=True)
    assert "alert-danger" in r.get_data(as_text=True)
    assert Transfer.query.count() == 0


# ── I. nonexistent item rejected ─────────────────────────────────────────────

def test_nonexistent_item_rejected(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    with pytest.raises(PostingError):
        svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                            lines=[(999999, 5)])
    assert Transfer.query.count() == 0


# ── J. atomic rollback when destination operation fails ─────────────────────

def test_atomic_rollback_when_a_line_fails_mid_confirmation(appctx, monkeypatch):
    """Two lines; the second item's item_add_stock is made to blow up mid-
    confirmation. Neither line's stock movement, nor the status change, may
    survive — confirm_transfer() never commits itself (the route does), so a
    raised exception here must leave everything exactly as it was before the
    call, once the caller rolls back."""
    default = get_or_create_default_location()
    loc2 = _second_location()
    item1 = _item(stock=50, name="Good")
    item2 = _item(stock=50, name="Bad")

    t = svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                            lines=[(item1.id, 5), (item2.id, 5)])
    db.session.commit()

    from salpurflask.models import models as models_module
    real_item_add_stock = models_module.item_add_stock

    def failing_add_stock(item, qty, cost, location_id=None, **kwargs):
        if item.id == item2.id:
            raise RuntimeError("simulated destination failure")
        return real_item_add_stock(item, qty, cost, location_id=location_id, **kwargs)

    monkeypatch.setattr(models_module, "item_add_stock", failing_add_stock)

    before = {
        (item1.id, default.id): stock_at_location(item1.id, default.id),
        (item2.id, default.id): stock_at_location(item2.id, default.id),
        (item1.id, loc2.id): stock_at_location(item1.id, loc2.id),
        (item2.id, loc2.id): stock_at_location(item2.id, loc2.id),
    }

    with pytest.raises(RuntimeError):
        svc.confirm_transfer(t)
    db.session.rollback()
    monkeypatch.setattr(models_module, "item_add_stock", real_item_add_stock)

    db.session.refresh(t)
    assert t.status == "Draft"                              # never falsely confirmed
    assert stock_at_location(item1.id, default.id) == before[(item1.id, default.id)]
    assert stock_at_location(item2.id, default.id) == before[(item2.id, default.id)]
    assert stock_at_location(item1.id, loc2.id) == before[(item1.id, loc2.id)]
    assert stock_at_location(item2.id, loc2.id) == before[(item2.id, loc2.id)]
    assert _company_total(item1.id) == 50
    assert _company_total(item2.id) == 50


# ── K. confirmed transfer cannot be confirmed twice ─────────────────────────

def test_confirmed_transfer_cannot_be_confirmed_again(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=50)
    c = _admin()

    _new_transfer(c, default, loc2, item, 10)
    t = Transfer.query.first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)
    db.session.refresh(t)
    assert t.status == "Confirmed"

    r = c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)
    assert "alert-danger" in r.get_data(as_text=True)
    db.session.refresh(t)
    assert stock_at_location(item.id, default.id) == 40      # unchanged by the 2nd attempt
    assert stock_at_location(item.id, loc2.id) == 10


# ── L/M. reversal restores exact quantities ─────────────────────────────────

def test_reversal_restores_exact_source_and_destination_quantities(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=100)
    c = _admin()

    _new_transfer(c, default, loc2, item, 30)
    t = Transfer.query.first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)
    db.session.refresh(t)
    assert stock_at_location(item.id, default.id) == 70
    assert stock_at_location(item.id, loc2.id) == 30

    r = c.post(f"/transfers/{t.id}/reverse", follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(t)
    assert t.status == "Reversed"
    assert t.is_reversed is True
    assert stock_at_location(item.id, default.id) == 100     # exact restore
    assert stock_at_location(item.id, loc2.id) == 0
    assert _company_total(item.id) == 100
    assert _sum_all_locations(item.id) == 100


# ── N. reversed transfer cannot be reversed twice ───────────────────────────

def test_reversed_transfer_cannot_be_reversed_again(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=100)
    c = _admin()

    _new_transfer(c, default, loc2, item, 30)
    t = Transfer.query.first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)
    c.post(f"/transfers/{t.id}/reverse", follow_redirects=True)
    db.session.refresh(t)
    assert t.status == "Reversed"

    r = c.post(f"/transfers/{t.id}/reverse", follow_redirects=True)
    assert "alert-danger" in r.get_data(as_text=True)
    db.session.refresh(t)
    assert stock_at_location(item.id, default.id) == 100      # unchanged by 2nd reversal
    assert stock_at_location(item.id, loc2.id) == 0


def test_draft_transfer_cannot_be_reversed(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=50)
    t = svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                            lines=[(item.id, 10)])
    db.session.commit()
    with pytest.raises(PostingError):
        svc.reverse_transfer(t)


# ── O. multiple items in one transfer ───────────────────────────────────────

def test_multiple_items_in_one_transfer(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item1 = _item(stock=50, name="A")
    item2 = _item(stock=80, name="B")
    c = _admin()

    r = c.post("/transfers/new", data={
        "source_location_id": str(default.id), "destination_location_id": str(loc2.id),
        "item_id[]": [str(item1.id), str(item2.id)],
        "quantity[]": ["20", "35"],
        "date": "2026-03-01",
    }, follow_redirects=True)
    assert r.status_code == 200
    t = Transfer.query.first()
    assert len(t.lines) == 2

    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)
    assert stock_at_location(item1.id, default.id) == 30
    assert stock_at_location(item1.id, loc2.id) == 20
    assert stock_at_location(item2.id, default.id) == 45
    assert stock_at_location(item2.id, loc2.id) == 35
    assert _company_total(item1.id) == 50
    assert _company_total(item2.id) == 80


# ── P. multiple warehouses ───────────────────────────────────────────────────

def test_transfer_across_three_warehouses(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location("WH-2")
    loc3 = _second_location("WH-3")
    item = _item(stock=100)
    c = _admin()

    _new_transfer(c, default, loc2, item, 40)
    t1 = Transfer.query.filter_by(destination_location_id=loc2.id).first()
    c.post(f"/transfers/{t1.id}/confirm", follow_redirects=True)

    _new_transfer(c, loc2, loc3, item, 15)
    t2 = Transfer.query.filter_by(destination_location_id=loc3.id).first()
    c.post(f"/transfers/{t2.id}/confirm", follow_redirects=True)

    assert stock_at_location(item.id, default.id) == 60
    assert stock_at_location(item.id, loc2.id) == 25
    assert stock_at_location(item.id, loc3.id) == 15
    assert _company_total(item.id) == 100
    assert _sum_all_locations(item.id) == 100


# ── Q. existing single-warehouse behavior remains intact ───────────────────

def test_single_warehouse_business_never_sees_transfers_menu_requirement(appctx):
    """With only one location, the transfer form itself refuses to offer a
    workflow that cannot exist — this is the transfer-specific version of
    Phase 2's 'no unnecessary selector' rule: here there is genuinely nothing
    useful to show, so the page says so instead of rendering a broken form."""
    c = _admin()
    body = c.get("/transfers/new").get_data(as_text=True)
    assert "needs at least two warehouses" in body


def test_existing_sale_and_purchase_unaffected_by_transfer_module_existing(appctx):
    """Confirms Phase 2 workflows still work exactly as before when no
    transfer has ever been created — the mere existence of this module
    changes nothing about single-location behaviour."""
    from salpurflask.models.models import item_remove_stock
    default = get_or_create_default_location()
    item = _item(stock=20)
    item_remove_stock(item, 5, location_id=default.id)
    db.session.commit()
    assert stock_at_location(item.id, default.id) == 15
    assert _company_total(item.id) == 15


# ── Cancellation of a Draft ──────────────────────────────────────────────────

def test_draft_transfer_can_be_cancelled_without_moving_stock(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=50)
    c = _admin()

    _new_transfer(c, default, loc2, item, 10)
    t = Transfer.query.first()
    r = c.post(f"/transfers/{t.id}/cancel", follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(t)
    assert t.status == "Cancelled"
    assert stock_at_location(item.id, default.id) == 50       # untouched
    assert stock_at_location(item.id, loc2.id) == 0


def test_confirmed_transfer_cannot_be_cancelled(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=50)
    c = _admin()

    _new_transfer(c, default, loc2, item, 10)
    t = Transfer.query.first()
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)
    db.session.refresh(t)

    r = c.post(f"/transfers/{t.id}/cancel", follow_redirects=True)
    assert "alert-danger" in r.get_data(as_text=True)
    db.session.refresh(t)
    assert t.status == "Confirmed"


# ── permissions ──────────────────────────────────────────────────────────────

def test_reversal_requires_admin_not_manager(appctx):
    """Only one authenticated client exists in this test — this codebase's own
    Flask-Login test harness does not reliably keep two separately-created
    test_client() sessions apart within a single test (confirmed by direct
    reproduction: a second session_transaction()-authenticated client resolved
    current_user back to the first client's user), so the setup below is done
    directly through the service layer, not through an admin's HTTP session,
    and only the manager ever logs in via HTTP."""
    default = get_or_create_default_location()
    loc2 = _second_location()
    item = _item(stock=50)

    t = svc.create_transfer(source_location_id=default.id, destination_location_id=loc2.id,
                            lines=[(item.id, 10)])
    svc.confirm_transfer(t)
    db.session.commit()
    assert t.status == "Confirmed"

    mgr = _manager()
    r = mgr.post(f"/transfers/{t.id}/reverse", follow_redirects=False)
    assert r.status_code in (302, 403)
    db.session.refresh(t)
    assert t.status == "Confirmed"                             # manager could not reverse it
    assert stock_at_location(item.id, default.id) == 40
    assert stock_at_location(item.id, loc2.id) == 10


# ── the central invariant, exhaustively ─────────────────────────────────────

def test_invariant_holds_through_full_transfer_and_reversal_cycle(appctx):
    default = get_or_create_default_location()
    loc2 = _second_location()
    item1 = _item(stock=100, name="X")
    item2 = _item(stock=60, name="Y")
    c = _admin()

    r = c.post("/transfers/new", data={
        "source_location_id": str(default.id), "destination_location_id": str(loc2.id),
        "item_id[]": [str(item1.id), str(item2.id)],
        "quantity[]": ["25", "10"],
        "date": "2026-03-01",
    }, follow_redirects=True)
    t = Transfer.query.first()

    total_before = {item1.id: _sum_all_locations(item1.id), item2.id: _sum_all_locations(item2.id)}
    c.post(f"/transfers/{t.id}/confirm", follow_redirects=True)
    for iid in total_before:
        assert _sum_all_locations(iid) == total_before[iid]
        assert db.session.get(Item, iid).stock == _sum_all_locations(iid)

    c.post(f"/transfers/{t.id}/reverse", follow_redirects=True)
    for iid in total_before:
        assert _sum_all_locations(iid) == total_before[iid]
        assert db.session.get(Item, iid).stock == _sum_all_locations(iid)
