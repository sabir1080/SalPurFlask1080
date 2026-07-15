"""The POS is a fast counter screen, but it is not a separate cash register with its own
books. Every sale it rings up goes through the same posting layer as a sale typed on the
sales page — so stock moves, COGS posts, the customer ledger updates and the general
ledger balances, exactly as they would otherwise. These tests hold it to that.
"""
import json
from datetime import datetime
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Customer, Category, Item, FinancialAccount, Sale, CustomerPayment,
    get_walkin_customer, get_customer_balance, get_sale_received,
    get_account, gl_balances, natural_balance, sale_total,
    ACC_AR, ACC_SALES, ACC_INVENTORY, ACC_CASH_IN_HAND,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening, post_account_opening,
)


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
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()

    cat = Category(name="Drinks")
    db.session.add(cat)
    db.session.flush()
    item = Item(name="Cola 500ml", category_id=cat.id, unit="Pcs",
                barcode="8964000112233", purchase_price=Decimal("60"),
                sale_price=Decimal("100"), opening_stock=50, stock=50, reorder_level=5,
                inventory_value=Decimal("3000"))          # 50 @ 60
    db.session.add(item)
    db.session.flush()
    post_item_opening(item)                                # Inventory GL = 3000
    db.session.commit()
    return item


def _checkout(client, payload):
    return client.post("/pos/checkout", data=json.dumps(payload),
                       content_type="application/json")


def _cash_account_id():
    return FinancialAccount.query.filter_by(name="Cash").first().id


def test_barcode_lookup_finds_exactly_that_item(appctx):
    item = _world()
    c = _manager()
    r = c.get(f"/pos/lookup?q={item.barcode}")
    data = r.get_json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == item.id
    assert data["items"][0]["stock"] == 50


def test_a_pos_sale_posts_through_the_ledger_like_any_other_sale(appctx):
    item = _world()
    c = _manager()

    r = _checkout(c, {"items": [{"item_id": item.id, "qty": 3, "price": 100}],
                      "account_id": _cash_account_id(), "amount_paid": 300})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and body["total"] == 300 and body["change"] == 0

    db.session.expire_all()
    # stock moved
    assert db.session.get(Item, item.id).stock == 47

    # one real Sale, numbered, fully paid
    sal = Sale.query.one()
    assert sal.invoice_no and sal.invoice_no.startswith("INV-")
    assert float(sale_total(sal)) == 300
    assert float(get_sale_received(sal.id)) == 300

    # and the books: the receivable was raised and cleared, cash is up, sales posted
    b = gl_balances()
    def gl(code):
        a = get_account(code)
        return natural_balance(a, b.get(a.id, Decimal("0")))
    assert gl(ACC_AR) == 0                       # walk-in cleared by the receipt
    assert gl(ACC_SALES) == 300                  # revenue
    assert gl(ACC_INVENTORY) == Decimal("2820")  # 3000 - 3*60 cost of goods sold
    # cash rose by 300
    cash_leaf = FinancialAccount.query.filter_by(name="Cash").first().gl_account_id
    assert b.get(cash_leaf) == Decimal("300")


def test_the_till_cannot_oversell(appctx):
    item = _world()
    c = _manager()
    r = _checkout(c, {"items": [{"item_id": item.id, "qty": 51, "price": 100}],
                      "account_id": _cash_account_id(), "amount_paid": 5100})
    assert r.status_code == 400
    assert "in stock" in r.get_json()["error"]

    db.session.expire_all()
    assert db.session.get(Item, item.id).stock == 50      # nothing moved
    assert Sale.query.count() == 0                         # nothing committed


def test_overpayment_is_change_not_a_credit_on_the_customer(appctx):
    item = _world()
    c = _manager()
    r = _checkout(c, {"items": [{"item_id": item.id, "qty": 2, "price": 100}],
                      "account_id": _cash_account_id(), "amount_paid": 500})
    body = r.get_json()
    assert body["ok"]
    assert body["total"] == 200
    assert body["change"] == 300                  # 500 tendered, 200 sale

    db.session.expire_all()
    # the walk-in owes nothing — the extra was handed back, not banked as a credit
    walkin = get_walkin_customer()
    assert round(get_customer_balance(walkin.id), 2) == 0
    # and only the sale's worth reached the books as a receipt
    assert float(CustomerPayment.query.one().amount) == 200


def test_a_partial_payment_leaves_the_balance_on_a_named_customer(appctx):
    item = _world()
    cust = Customer(name="Corner Shop", contact="03001234567", address="x", opening_balance=0)
    db.session.add(cust)
    db.session.commit()
    c = _manager()

    r = _checkout(c, {"items": [{"item_id": item.id, "qty": 5, "price": 100}],
                      "customer_id": cust.id, "account_id": _cash_account_id(),
                      "amount_paid": 200})
    assert r.get_json()["ok"]

    db.session.expire_all()
    # 500 sale, 200 paid → 300 still owed by the named customer
    assert round(get_customer_balance(cust.id), 2) == 300


def test_the_walk_in_customer_is_created_once_and_reused(appctx):
    item = _world()
    c = _manager()
    for _ in range(2):
        _checkout(c, {"items": [{"item_id": item.id, "qty": 1, "price": 100}],
                      "account_id": _cash_account_id(), "amount_paid": 100})
    assert Customer.query.filter_by(name="Walk-in Customer").count() == 1


def test_staff_cannot_use_the_pos(appctx):
    _world()
    db.session.add(User(name="S", email="s@t.com", password=pwd_context.hash("secret123"),
                        verified=True, role="staff"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": "s@t.com", "password": "secret123"})
    r = c.get("/pos", follow_redirects=False)
    assert r.status_code in (301, 302)            # redirected away, not shown
