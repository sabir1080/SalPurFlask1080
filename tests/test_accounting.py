"""Tests for the accounting features: cash/bank account balances."""
from app import (
    db, FinancialAccount, Supplier, Customer, Category, Item,
    SupplierPayment, CustomerPayment, Expense,
    get_account_balance, total_cash_bank_balance, accounting_position,
)


def _supplier():
    s = Supplier(name="S", contact="03000000000", address="x"); db.session.add(s); db.session.flush(); return s


def _customer():
    c = Customer(name="C", contact="03000000000", address="x"); db.session.add(c); db.session.flush(); return c


def test_account_balance_from_movements(appctx):
    acc = FinancialAccount(name="Cash", method="Cash", account_type="Cash", opening_balance=1000)
    db.session.add(acc)
    s, c = _supplier(), _customer()
    db.session.add(CustomerPayment(customer_id=c.id, amount=500, payment_method="Cash"))   # +500 in
    db.session.add(SupplierPayment(supplier_id=s.id, amount=200, payment_method="Cash"))    # -200 out
    db.session.add(Expense(description="rent", amount=100, payment_method="Cash"))          # -100 out
    # a Bank-method movement must NOT affect the Cash account
    db.session.add(CustomerPayment(customer_id=c.id, amount=9999, payment_method="Bank"))
    db.session.commit()

    assert round(get_account_balance(acc), 2) == 1200.0    # 1000 + 500 - 200 - 100


def test_total_cash_bank_balance(appctx):
    db.session.add(FinancialAccount(name="Cash", method="Cash", account_type="Cash", opening_balance=300))
    db.session.add(FinancialAccount(name="Bank", method="Bank", account_type="Bank", opening_balance=700))
    c = _customer()
    db.session.add(CustomerPayment(customer_id=c.id, amount=50, payment_method="Bank"))
    db.session.commit()
    assert round(total_cash_bank_balance(), 2) == 1050.0    # 300 + (700+50)


def test_accounting_position_figures_and_balances(appctx):
    db.session.add(FinancialAccount(name="Cash", method="Cash", account_type="Cash", opening_balance=1000))
    cat = Category(name="C"); db.session.add(cat); db.session.flush()
    db.session.add(Item(name="I", category_id=cat.id, stock=10, purchase_price=20))   # inventory 200
    c = _customer()
    db.session.add(CustomerPayment(customer_id=c.id, amount=500, payment_method="Cash"))  # cash +500
    db.session.commit()
    p = accounting_position()
    assert round(p["cash_bank"], 2) == 1500.0
    assert round(p["inventory"], 2) == 200.0
    # the statement must always balance: assets == liabilities + equity
    assert round(p["total_assets"], 2) == round(p["total_liabilities"] + p["equity"], 2)
