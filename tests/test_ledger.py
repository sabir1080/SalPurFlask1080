"""Tests for the ledger invariant — the accounting core of the app.

Every purchase/sale/payment must move the party's running balance correctly, and
deleting a transaction must recompute it back. These guard against a regression
silently corrupting balances.
"""
from decimal import Decimal

from app import (
    db, Supplier, Customer, Category, Item, Purchase, PurchaseItem, Sale, SaleItem,
    SupplierPayment, CustomerPayment, SupplierLedgerEntry,
    calc_discount_tax, purchase_total, sale_total,
    sync_supplier_opening, sync_supplier_purchase, sync_supplier_payment,
    sync_customer_opening, sync_customer_sale, sync_customer_receipt,
    remove_supplier_ledger_entry, recalculate_supplier_ledger,
    get_supplier_balance, get_customer_balance,
)


# ── helpers ───────────────────────────────────────────────────────────────────
def make_supplier(opening=0):
    s = Supplier(name="Sup", contact="03000000000", address="X", opening_balance=opening)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


def make_customer(opening=0):
    c = Customer(name="Cust", contact="03000000000", address="X", opening_balance=opening)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


def make_item():
    cat = Category(name="Cat"); db.session.add(cat); db.session.flush()
    it = Item(name="Widget", category_id=cat.id, stock=0, purchase_price=10, sale_price=20)
    db.session.add(it); db.session.flush()
    return it


def add_purchase(sup, item, qty, price, disc=0, tax=0):
    pur = Purchase(supplier_id=sup.id, item_id=item.id, quantity=qty, purchase_price=price)
    db.session.add(pur); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", disc, tax)
    pi = PurchaseItem(purchase_id=pur.id, item_id=item.id, quantity=qty, purchase_price=price,
                      discount_type="percent", discount_value=disc, discount_amount=d,
                      tax_percent=tax, tax_amount=t, amount=net)
    db.session.add(pi)
    item.stock += qty
    db.session.flush(); db.session.refresh(pur)
    sync_supplier_purchase(pur); db.session.commit()
    return pur


def add_sale(cust, item, qty, price, disc=0, tax=0):
    sale = Sale(customer_id=cust.id, item_id=item.id, quantity=qty, sale_price=price, cost_price=item.purchase_price or 0)
    db.session.add(sale); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", disc, tax)
    si = SaleItem(sale_id=sale.id, item_id=item.id, quantity=qty, sale_price=price,
                  cost_price=item.purchase_price or 0, discount_type="percent", discount_value=disc,
                  discount_amount=d, tax_percent=tax, tax_amount=t, amount=net)
    db.session.add(si)
    item.stock -= qty
    db.session.flush(); db.session.refresh(sale)
    sync_customer_sale(sale); db.session.commit()
    return sale


# ── tests ─────────────────────────────────────────────────────────────────────
def test_calc_discount_tax():
    # 100 gross, 10% discount -> 90 taxable, +5% tax -> 94.5
    d, t, net = calc_discount_tax(100, "percent", 10, 5)
    assert (round(d, 2), round(t, 2), round(net, 2)) == (10.0, 4.5, 94.5)
    # fixed discount is capped at the gross amount
    d, _, _ = calc_discount_tax(50, "fixed", 999, 0)
    assert round(d, 2) == 50.0


def test_opening_balance_sets_ledger(appctx):
    s = make_supplier(opening=1000)
    assert round(get_supplier_balance(s.id), 2) == 1000.0


def test_purchase_then_payment(appctx):
    s = make_supplier(opening=100)
    it = make_item()
    add_purchase(s, it, qty=3, price=50, disc=10, tax=5)   # net = 150 -15 + 6.75 = 141.75
    expected = 100 + 141.75
    assert round(get_supplier_balance(s.id), 2) == round(expected, 2)

    pay = SupplierPayment(supplier_id=s.id, amount=41.75, payment_method="Cash")
    db.session.add(pay); db.session.flush()
    sync_supplier_payment(pay); db.session.commit()
    assert round(get_supplier_balance(s.id), 2) == 200.0


def test_delete_purchase_recalculates(appctx):
    s = make_supplier(opening=0)
    it = make_item()
    pur = add_purchase(s, it, qty=2, price=100)            # payable 200
    assert round(get_supplier_balance(s.id), 2) == 200.0

    remove_supplier_ledger_entry("purchase", pur.id)
    for pi in list(pur.line_items):
        db.session.delete(pi)
    db.session.delete(pur)
    db.session.commit()
    recalculate_supplier_ledger(s.id)
    db.session.commit()
    assert round(get_supplier_balance(s.id), 2) == 0.0


def test_sale_then_receipt(appctx):
    c = make_customer(opening=0)
    it = make_item()
    add_sale(c, it, qty=4, price=25)                       # receivable 100
    assert round(get_customer_balance(c.id), 2) == 100.0

    rc = CustomerPayment(customer_id=c.id, amount=60, payment_method="Cash")
    db.session.add(rc); db.session.flush()
    sync_customer_receipt(rc); db.session.commit()
    assert round(get_customer_balance(c.id), 2) == 40.0


def test_money_stored_as_decimal(appctx):
    s = make_supplier(opening=0)
    it = make_item()
    add_purchase(s, it, qty=1, price=Decimal("33.33"))
    pi = PurchaseItem.query.first()
    assert isinstance(pi.amount, Decimal)
    assert isinstance(SupplierLedgerEntry.query.first().balance_after, Decimal)


def test_stock_moves_with_transactions(appctx):
    s = make_supplier(); c = make_customer(); it = make_item()
    add_purchase(s, it, qty=10, price=5)
    assert it.stock == 10
    add_sale(c, it, qty=4, price=8)
    assert it.stock == 6
