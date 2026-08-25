"""Inventory integrity: stock and its value must never go negative, and an adjustment
must move stock in the direction the user asked for."""
from datetime import datetime
from decimal import Decimal

import pytest

from app import (
    app as flask_app, db, User, pwd_context,
    Supplier, Customer, Category, Item, Purchase, Sale, FinancialAccount,
    Account, PostingError,
    ADJUSTMENT_DIRECTIONS, ADJUSTMENT_TYPES,
    item_add_stock, item_remove_stock,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, get_account, ACC_INVENTORY,
    gl_balances, natural_balance, post_account_opening,
)


def _admin(email="a@t.com"):
    db.session.add(User(name="A", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _world():
    """A chart, an open year, a funded cash account, and one item."""
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


def _line(c, url, party_field, party_id, item_id, qty, price):
    return c.post(url, data={
        party_field: party_id, "date": "2026-03-01", "notes": "",
        "item_id[]": item_id, "quantity[]": str(qty),
        ("purchase_price[]" if "purchase" in url else "sale_price[]"): str(price),
        "discount_type[]": "", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)


def test_reversing_a_purchase_whose_goods_were_sold_is_refused(appctx):
    """Buy 100, sell 80, then reverse the purchase. Eighty of those widgets are with the
    customer; they cannot be handed back to the supplier. Without a guard the reversal
    went through anyway — stock landed on minus eighty, the Inventory account went
    negative, and it no longer matched the item it mirrors."""
    sup, cus, item = _world()
    c = _admin()
    _line(c, "/purchase", "supplier_id", sup.id, item.id, 100, 100)
    _line(c, "/sale", "customer_id", cus.id, item.id, 80, 250)

    db.session.expire_all()
    item = db.session.get(Item, item.id)
    assert item.stock == 20

    pur = Purchase.query.first()
    r = c.post(f"/document/purchase/{pur.id}/reverse", follow_redirects=True)
    assert r.status_code == 200
    assert "alert-danger" in r.get_data(as_text=True)     # refused, with a reason

    db.session.expire_all()
    item = db.session.get(Item, item.id)
    assert item.stock == 20                               # untouched
    assert item.inventory_value == Decimal("2000.0000")
    assert not Purchase.query.first().is_reversed

    # and the books still agree
    inv = get_account(ACC_INVENTORY)
    gl_inv = natural_balance(inv, gl_balances().get(inv.id, Decimal("0")))
    assert Decimal(str(gl_inv)) == Decimal("2000.0000")


def test_a_sale_cannot_exceed_the_stock_on_hand(appctx):
    sup, cus, item = _world()
    c = _admin()
    _line(c, "/purchase", "supplier_id", sup.id, item.id, 10, 100)

    r = _line(c, "/sale", "customer_id", cus.id, item.id, 11, 250)
    assert "alert-danger" in r.get_data(as_text=True)

    db.session.expire_all()
    item = db.session.get(Item, item.id)
    assert item.stock == 10                               # nothing sold, nothing moved
    assert Sale.query.count() == 0


def test_stock_can_never_be_taken_below_zero(appctx):
    _world()
    item = Item.query.first()
    item_add_stock(item, 10, Decimal("1000"))
    db.session.flush()

    with pytest.raises(PostingError, match="Insufficient stock at this location"):
        item_remove_stock(item, 11)

    assert item.stock == 10                               # nothing moved
    assert item.inventory_value == Decimal("1000.0000")


def test_value_cannot_be_taken_below_zero(appctx):
    """The cost of goods already blended into stock that has since been sold cannot be
    pulled back out — the item would be left holding widgets worth less than nothing."""
    _world()
    item = Item.query.first()
    item_add_stock(item, 10, Decimal("1000"))             # 10 @ 100
    item_remove_stock(item, 5)                            # sold 5 @ 100 → 5 left, 500
    db.session.flush()

    with pytest.raises(PostingError, match="cannot be taken out of it"):
        item_remove_stock(item, 4, cost_total=Decimal("900"))

    assert item.stock == 5
    assert item.inventory_value == Decimal("500.0000")

    # taking the *whole* remaining stock out is still fine — that is an ordinary sale
    assert item_remove_stock(item, 5) == Decimal("500.0000")
    assert item.stock == 0
    assert item.inventory_value == 0


def test_every_adjustment_type_declares_its_direction(appctx):
    """The direction used to be inferred by matching the label against a list of the
    outbound ones, so anything missing from that list silently added stock."""
    for t in ADJUSTMENT_TYPES:
        assert ADJUSTMENT_DIRECTIONS[t] in ("in", "out"), t


def test_a_stocktake_can_record_a_shortfall(appctx):
    """A count that finds goods *missing* has to be enterable. There used to be one
    "Count Correction" and it only ever went in, so the only way to reduce stock was to
    call it damage — which it was not."""
    _world()
    c = _admin()
    item = Item.query.first()
    item_add_stock(item, 100, Decimal("10000"))
    db.session.commit()

    r = c.post("/stock_adjustment", data={
        "item_id": item.id, "adj_type": "Count Correction (Decrease)",
        "quantity": "8", "date": "2026-03-01", "reason": "Stocktake shortfall",
    }, follow_redirects=True)
    assert r.status_code == 200

    db.session.expire_all()
    item = db.session.get(Item, item.id)
    assert item.stock == 92                               # went DOWN, as asked
    assert item.inventory_value == Decimal("9200.0000")


def test_an_unknown_adjustment_type_is_refused_not_guessed(appctx):
    _world()
    c = _admin()
    item = Item.query.first()
    item_add_stock(item, 100, Decimal("10000"))
    db.session.commit()

    r = c.post("/stock_adjustment", data={
        "item_id": item.id, "adj_type": "Shrinkage",     # not a type we know
        "quantity": "5", "date": "2026-03-01", "reason": "",
    }, follow_redirects=True)
    assert "Unknown adjustment type" in r.get_data(as_text=True)

    db.session.expire_all()
    assert db.session.get(Item, item.id).stock == 100     # not quietly increased
