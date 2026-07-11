"""Accounting tests: cash/bank balances, the GL-backed balance sheet, journal
entries, and the cash flow statement."""
from datetime import datetime
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    FinancialAccount, Supplier, Customer, Category, Item,
    SupplierPayment, CustomerPayment, Expense,
    Account, JournalEntry, JournalLine,
    ACC_CASH_IN_HAND, ACC_CAPITAL,
    seed_chart_of_accounts, seed_fiscal_year, get_account,
    get_account_balance, total_cash_bank_balance,
    accounting_position, cash_flow_statement,
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _supplier():
    s = Supplier(name="S", contact="03000000000", address="x"); db.session.add(s); db.session.flush(); return s


def _customer():
    c = Customer(name="C", contact="03000000000", address="x"); db.session.add(c); db.session.flush(); return c


def _login_manager(email="jn@test.com"):
    db.session.add(User(name="M", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="manager"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _setup_gl(year=2026):
    """Seed the chart, an open fiscal year (post_entry refuses a date that falls in
    no open period), and wire a Cash account to its GL leaf."""
    seed_chart_of_accounts()
    seed_fiscal_year(year)
    cash_gl = get_account(ACC_CASH_IN_HAND)
    db.session.add(FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                                    opening_balance=0, gl_account_id=cash_gl.id))
    db.session.commit()


def _post(date, desc, lines):
    """Post a balanced entry. `lines` is [(account_code, debit, credit)]."""
    e = JournalEntry(entry_date=date, description=desc)
    db.session.add(e); db.session.flush()
    for code, dr, cr in lines:
        db.session.add(JournalLine(entry_id=e.id, account_id=get_account(code).id,
                                   debit=dr, credit=cr))
    db.session.commit()
    return e


# ── cash / bank account balances ──────────────────────────────────────────────
def test_account_balance_from_movements(appctx):
    acc = FinancialAccount(name="Cash", method="Cash", account_type="Cash", opening_balance=1000)
    db.session.add(acc)
    s, c = _supplier(), _customer()
    db.session.add(CustomerPayment(customer_id=c.id, amount=500, payment_method="Cash"))   # +500 in
    db.session.add(SupplierPayment(supplier_id=s.id, amount=200, payment_method="Cash"))    # -200 out
    db.session.add(Expense(description="rent", amount=100, payment_method="Cash"))          # -100 out
    db.session.add(CustomerPayment(customer_id=c.id, amount=9999, payment_method="Bank"))   # other account
    db.session.commit()
    assert round(get_account_balance(acc), 2) == 1200.0    # 1000 + 500 - 200 - 100


def test_total_cash_bank_balance(appctx):
    db.session.add(FinancialAccount(name="Cash", method="Cash", account_type="Cash", opening_balance=300))
    db.session.add(FinancialAccount(name="Bank", method="Bank", account_type="Bank", opening_balance=700))
    c = _customer()
    db.session.add(CustomerPayment(customer_id=c.id, amount=50, payment_method="Bank"))
    db.session.commit()
    assert round(total_cash_bank_balance(), 2) == 1050.0    # 300 + (700+50)


# ── the GL-backed balance sheet ───────────────────────────────────────────────
def test_balance_sheet_balances_from_the_gl(appctx):
    """Equity is summed from the equity accounts, not plugged — so the sheet
    balancing is a real result."""
    _setup_gl()
    _post(datetime(2026, 1, 10), "capital introduced",
          [(ACC_CASH_IN_HAND, 1000, 0), (ACC_CAPITAL, 0, 1000)])

    p = accounting_position(as_of=datetime(2026, 12, 31))
    assert p["total_assets"] == Decimal("1000")
    assert p["total_equity"] == Decimal("1000")
    assert p["difference"] == Decimal("0")     # assets == liabilities + equity


# ── cash flow statement ───────────────────────────────────────────────────────
def test_cash_flow_reconciles_and_classifies(appctx):
    _setup_gl()
    # owner puts money in (financing), then the business pays rent (operating)
    _post(datetime(2026, 1, 10), "capital", [(ACC_CASH_IN_HAND, 1000, 0), (ACC_CAPITAL, 0, 1000)])
    _post(datetime(2026, 1, 20), "rent",    [("6010", 300, 0), (ACC_CASH_IN_HAND, 0, 300)])

    cf = cash_flow_statement(datetime(2026, 1, 1), datetime(2026, 1, 31, 23, 59, 59))
    assert cf["opening"] == Decimal("0")
    assert cf["totals"]["Financing"] == Decimal("1000")     # capital in
    assert cf["totals"]["Operating"] == Decimal("-300")     # rent out
    assert cf["totals"]["Investing"] == Decimal("0")
    assert cf["net_change"] == Decimal("700")
    assert cf["closing"] == Decimal("700")
    assert cf["reconciles"]                                  # opening + movement == closing


def test_cash_flow_honours_an_investing_tag(appctx):
    _setup_gl()
    db.session.add(Account(code="1500", name="Equipment", type="Asset",
                           is_group=False, cash_flow_section="Investing"))
    db.session.commit()
    _post(datetime(2026, 2, 1), "buy equipment",
          [("1500", 500, 0), (ACC_CASH_IN_HAND, 0, 500)])

    cf = cash_flow_statement(datetime(2026, 2, 1), datetime(2026, 2, 28, 23, 59, 59))
    assert cf["totals"]["Investing"] == Decimal("-500")     # not lumped into Operating
    assert cf["totals"]["Operating"] == Decimal("0")
    assert cf["reconciles"]


def test_cash_flow_ignores_entries_that_never_touch_cash(appctx):
    _setup_gl()
    # a credit purchase moves inventory and payables, but no cash
    _post(datetime(2026, 3, 1), "credit purchase",
          [("1200", 400, 0), ("2100", 0, 400)])

    cf = cash_flow_statement(datetime(2026, 3, 1), datetime(2026, 3, 31, 23, 59, 59))
    assert cf["net_change"] == Decimal("0")
    assert cf["totals"]["Operating"] == Decimal("0")
    assert cf["reconciles"]


# ── manual journal entries (posted against the chart of accounts) ─────────────
def test_journal_balanced_accepted_unbalanced_rejected(appctx):
    _setup_gl()
    c = _login_manager()
    cash_id = get_account(ACC_CASH_IN_HAND).id
    capital_id = get_account(ACC_CAPITAL).id
    before = JournalEntry.query.count()

    c.post("/journal/new", data={
        "entry_date": "2026-01-01", "description": "capital",
        "account_id[]": [str(cash_id), str(capital_id)],
        "debit[]": ["100", "0"], "credit[]": ["0", "100"]})
    assert JournalEntry.query.count() == before + 1

    # debits != credits -> refused
    c.post("/journal/new", data={
        "entry_date": "2026-01-01", "description": "bad",
        "account_id[]": [str(cash_id), str(capital_id)],
        "debit[]": ["100", "0"], "credit[]": ["0", "50"]})
    assert JournalEntry.query.count() == before + 1
