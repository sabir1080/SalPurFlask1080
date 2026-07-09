"""Tests for the accounting features: cash/bank account balances."""
from app import (
    db, FinancialAccount, Supplier, Customer, SupplierPayment, CustomerPayment, Expense,
    get_account_balance, total_cash_bank_balance,
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
