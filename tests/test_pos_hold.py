"""Test the POS Bill Hold feature."""
import json
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Customer, Category, Item, FinancialAccount, Sale,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening,
)
from salpurflask.models import PosHold


def _manager(email="m@t.com"):
    db.session.add(User(name="M", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="manager"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _world():
    """Chart, open year, a funded cash account, and one item with stock on the shelf."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()

    cat = Category(name="Drinks")
    db.session.add(cat)
    db.session.flush()
    item = Item(name="Cola 500ml", category_id=cat.id, unit="Pcs",
                barcode="8964000112233", purchase_price=Decimal("60"),
                sale_price=Decimal("100"), opening_stock=50, stock=50, reorder_level=5,
                inventory_value=Decimal("3000"))
    db.session.add(item)
    db.session.flush()
    post_item_opening(item)
    db.session.commit()
    return item


def _cash_account_id():
    return FinancialAccount.query.filter_by(name="Cash").first().id


def test_can_hold_an_empty_cart_returns_error(appctx):
    """Holding an empty cart should be rejected."""
    item = _world()
    c = _manager()
    cust = Customer(name="Test", contact="123", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    r = c.post("/pos/hold", data=json.dumps({
        "customer_id": cust.id,
        "account_id": _cash_account_id(),
        "items": [],
    }), content_type="application/json")

    assert r.status_code == 400
    assert "empty" in r.get_json()["error"].lower()
    assert PosHold.query.count() == 0


def test_can_hold_a_bill_with_items(appctx):
    """A bill with items can be held."""
    item = _world()
    c = _manager()
    cust = Customer(name="Test", contact="123", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    r = c.post("/pos/hold", data=json.dumps({
        "customer_id": cust.id,
        "account_id": _cash_account_id(),
        "amount_paid": 100,
        "items": [
            {"id": item.id, "qty": 2, "price": 100, "name": "Cola", "stock": 50,
             "unit": "Pcs", "unitKey": "base", "unitName": "Pcs", "factor": 1,
             "units": [{"key": "base", "name": "Pcs", "factor": 1, "price": 100}]}
        ],
        "notes": "Customer said will pay tomorrow",
    }), content_type="application/json")

    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"]
    assert data["hold_id"]
    assert "HOLD-" in data["hold_no"]

    db.session.expire_all()
    hold = PosHold.query.one()
    assert hold.customer_id == cust.id
    assert hold.notes == "Customer said will pay tomorrow"
    assert hold.amount_paid_memo == Decimal("100")
    cart = json.loads(hold.cart_data)
    assert len(cart) == 1
    assert cart[0]["qty"] == 2


def test_hold_does_not_reduce_stock(appctx):
    """Holding a bill should not reduce inventory."""
    item = _world()
    c = _manager()
    cust = Customer(name="Test", contact="123", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    c.post("/pos/hold", data=json.dumps({
        "customer_id": cust.id,
        "account_id": _cash_account_id(),
        "items": [
            {"id": item.id, "qty": 10, "price": 100, "name": "Cola", "stock": 50,
             "unit": "Pcs", "unitKey": "base", "unitName": "Pcs", "factor": 1,
             "units": [{"key": "base", "name": "Pcs", "factor": 1, "price": 100}]}
        ],
    }), content_type="application/json")

    db.session.expire_all()
    assert db.session.get(Item, item.id).stock == 50  # unchanged


def test_can_list_held_bills(appctx):
    """Held bills can be listed."""
    item = _world()
    c = _manager()
    cust = Customer(name="Test", contact="123", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    c.post("/pos/hold", data=json.dumps({
        "customer_id": cust.id,
        "account_id": _cash_account_id(),
        "items": [
            {"id": item.id, "qty": 2, "price": 100, "name": "Cola", "stock": 50,
             "unit": "Pcs", "unitKey": "base", "unitName": "Pcs", "factor": 1,
             "units": [{"key": "base", "name": "Pcs", "factor": 1, "price": 100}]}
        ],
    }), content_type="application/json")

    r = c.get("/pos/held-bills")
    assert r.status_code == 200
    assert "Test" in r.data.decode()
    assert "HOLD-" in r.data.decode()


def test_can_fetch_held_bill_as_json(appctx):
    """A held bill can be fetched as JSON for resuming."""
    item = _world()
    c = _manager()
    cust = Customer(name="Test", contact="123", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    hold_r = c.post("/pos/hold", data=json.dumps({
        "customer_id": cust.id,
        "account_id": _cash_account_id(),
        "amount_paid": 200,
        "items": [
            {"id": item.id, "qty": 2, "price": 100, "name": "Cola", "stock": 50,
             "unit": "Pcs", "unitKey": "base", "unitName": "Pcs", "factor": 1,
             "units": [{"key": "base", "name": "Pcs", "factor": 1, "price": 100}]}
        ],
    }), content_type="application/json")

    hold_id = hold_r.get_json()["hold_id"]
    r = c.get(f"/pos/held-bills/{hold_id}")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"]
    assert data["customer_id"] == cust.id
    assert data["amount_paid"] == 200.0
    assert len(data["cart"]) == 1
    assert data["cart"][0]["qty"] == 2


def test_can_delete_held_bill(appctx):
    """A held bill can be deleted."""
    item = _world()
    c = _manager()
    cust = Customer(name="Test", contact="123", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    hold_r = c.post("/pos/hold", data=json.dumps({
        "customer_id": cust.id,
        "account_id": _cash_account_id(),
        "items": [
            {"id": item.id, "qty": 2, "price": 100, "name": "Cola", "stock": 50,
             "unit": "Pcs", "unitKey": "base", "unitName": "Pcs", "factor": 1,
             "units": [{"key": "base", "name": "Pcs", "factor": 1, "price": 100}]}
        ],
    }), content_type="application/json")

    hold_id = hold_r.get_json()["hold_id"]
    assert PosHold.query.count() == 1

    c.post(f"/pos/held-bills/{hold_id}/delete")
    assert PosHold.query.count() == 0


def test_resuming_nonexistent_hold_returns_error(appctx):
    """Fetching a non-existent hold should return 404."""
    _world()
    c = _manager()
    r = c.get("/pos/held-bills/99999")
    assert r.status_code == 404


def test_hold_requires_manager_role(appctx):
    """Only managers can hold bills."""
    item = _world()
    db.session.add(User(name="S", email="s@t.com", password=pwd_context.hash("secret123"),
                        verified=True, role="staff"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": "s@t.com", "password": "secret123"})
    cust = Customer(name="Test", contact="123", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    r = c.post("/pos/hold", data=json.dumps({
        "customer_id": cust.id,
        "account_id": _cash_account_id(),
        "items": [
            {"id": item.id, "qty": 1, "price": 100, "name": "Cola", "stock": 50,
             "unit": "Pcs", "unitKey": "base", "unitName": "Pcs", "factor": 1,
             "units": [{"key": "base", "name": "Pcs", "factor": 1, "price": 100}]}
        ],
    }), content_type="application/json", follow_redirects=False)

    assert r.status_code in (301, 302)  # redirected, not allowed


def test_hold_with_invalid_customer_returns_error(appctx):
    """Holding with an invalid customer ID should fail."""
    _world()
    c = _manager()

    r = c.post("/pos/hold", data=json.dumps({
        "customer_id": 99999,
        "account_id": _cash_account_id(),
        "items": [
            {"id": 1, "qty": 1, "price": 100, "name": "Cola", "stock": 50,
             "unit": "Pcs", "unitKey": "base", "unitName": "Pcs", "factor": 1,
             "units": [{"key": "base", "name": "Pcs", "factor": 1, "price": 100}]}
        ],
    }), content_type="application/json")

    assert r.status_code == 400
    assert "not found" in r.get_json()["error"].lower()


def test_checkout_after_hold_deletes_hold(appctx):
    """Finalizing a held bill should delete the hold record."""
    item = _world()
    c = _manager()
    cust = Customer(name="Test", contact="123", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    hold_r = c.post("/pos/hold", data=json.dumps({
        "customer_id": cust.id,
        "account_id": _cash_account_id(),
        "items": [
            {"id": item.id, "qty": 2, "price": 100, "name": "Cola", "stock": 50,
             "unit": "Pcs", "unitKey": "base", "unitName": "Pcs", "factor": 1,
             "units": [{"key": "base", "name": "Pcs", "factor": 1, "price": 100}]}
        ],
    }), content_type="application/json")

    hold_id = hold_r.get_json()["hold_id"]
    assert PosHold.query.count() == 1

    # Complete the sale with the hold_id
    c.post("/pos/checkout", data=json.dumps({
        "customer_id": cust.id,
        "account_id": _cash_account_id(),
        "amount_paid": 200,
        "hold_id": hold_id,
        "items": [{"item_id": item.id, "qty": 2, "price": 100, "unit_id": "base"}],
    }), content_type="application/json")

    db.session.expire_all()
    assert PosHold.query.count() == 0  # hold should be deleted
    assert Sale.query.count() == 1  # sale should exist
