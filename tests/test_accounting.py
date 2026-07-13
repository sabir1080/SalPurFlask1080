"""Accounting tests: cash/bank balances, the GL-backed balance sheet, journal
entries, the cash flow statement, and fixed assets."""
from datetime import datetime
from decimal import Decimal

import pytest

from app import (
    app as flask_app, db, User, pwd_context,
    FinancialAccount, Supplier, Customer, Category, Item,
    SupplierPayment, CustomerPayment, Expense,
    Account, JournalEntry, JournalLine,
    FixedAsset, DepreciationCharge,
    ACC_CASH_IN_HAND, ACC_CAPITAL,
    month_end, depreciation_for_month, post_asset_acquisition,
    run_depreciation, post_asset_disposal, PostingError, post_account_opening,
    post_entry, reverse_entry, unwind_asset_entry, gl_profit,
    realign_backdated_reversals, ensure_gl_account_for_financial,
    post_customer_receipt, post_supplier_payment, post_expense,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    get_account, account_for_role,
    get_account_balance, total_cash_bank_balance, account_transactions,
    cash_balance_as_of,
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
    seed_fixed_asset_accounts()
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
# These read the GL, so the movements have to be posted the way the app posts them.
# A payment row written straight to the table and never posted is not money that
# moved — no report would see it either.
def _bank():
    fa = FinancialAccount(name="Bank", method="Bank", account_type="Bank", opening_balance=0)
    db.session.add(fa)
    db.session.flush()
    ensure_gl_account_for_financial(fa)
    db.session.commit()
    return fa


def test_account_balance_from_movements(appctx):
    _setup_gl()
    when = datetime(2026, 3, 1)
    cash, bank = FinancialAccount.query.filter_by(name="Cash").first(), _bank()
    cash.opening_balance = Decimal("1000")
    post_account_opening(cash)

    s, c = _supplier(), _customer()
    rcpt = CustomerPayment(customer_id=c.id, amount=500, payment_method="Cash",
                           payment_date=when, account_id=cash.id)
    pay = SupplierPayment(supplier_id=s.id, amount=200, payment_method="Cash",
                          payment_date=when, account_id=cash.id)
    exp = Expense(description="rent", amount=100, payment_method="Cash",
                  date=when, account_id=cash.id)
    other = CustomerPayment(customer_id=c.id, amount=9999, payment_method="Bank",
                            payment_date=when, account_id=bank.id)   # other account
    db.session.add_all([rcpt, pay, exp, other])
    db.session.flush()
    for r, fn in ((rcpt, post_customer_receipt), (pay, post_supplier_payment),
                  (exp, post_expense), (other, post_customer_receipt)):
        fn(r)
    db.session.commit()

    assert round(get_account_balance(cash), 2) == 1200.0    # 1000 + 500 - 200 - 100
    assert round(get_account_balance(bank), 2) == 9999.0    # and the other account is its own


def test_total_cash_bank_balance(appctx):
    _setup_gl()
    cash, bank = FinancialAccount.query.filter_by(name="Cash").first(), _bank()
    cash.opening_balance, bank.opening_balance = Decimal("300"), Decimal("700")
    post_account_opening(cash)
    post_account_opening(bank)

    c = _customer()
    rcpt = CustomerPayment(customer_id=c.id, amount=50, payment_method="Bank",
                           payment_date=datetime(2026, 3, 1), account_id=bank.id)
    db.session.add(rcpt); db.session.flush()
    post_customer_receipt(rcpt)
    db.session.commit()

    assert round(total_cash_bank_balance(), 2) == 1050.0    # 300 + (700+50)


def test_cash_opening_balance_reaches_the_balance_sheet(appctx):
    """The money used to sit on the FinancialAccount row and never reach the GL, so
    every report — which reads only the GL — showed zero."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    cash = FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                            opening_balance=500000)
    db.session.add(cash)
    db.session.flush()
    post_account_opening(cash)
    db.session.commit()

    p = accounting_position(as_of=datetime(2026, 12, 31))
    assert p["total_assets"] == Decimal("500000")     # the cash is visible
    assert p["total_equity"] == Decimal("500000")     # against Opening Balance Equity
    assert p["difference"] == Decimal("0")            # and the sheet balances


def test_editing_the_opening_balance_reposts_it(appctx):
    """Opening balances are the one figure a user legitimately corrects. The old
    entry is reversed and a fresh one posted, so the GL never drifts."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    cash = FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                            opening_balance=500000)
    db.session.add(cash); db.session.flush()
    post_account_opening(cash); db.session.commit()

    cash.opening_balance = 300000                     # the user corrects it
    post_account_opening(cash); db.session.commit()

    p = accounting_position(as_of=datetime(2026, 12, 31))
    assert p["total_assets"] == Decimal("300000")     # the correction, not 800,000
    assert p["difference"] == Decimal("0")


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
    """Fixed Assets at Cost is seeded tagged Investing, so buying an asset must not
    show up as operating cash."""
    _setup_gl()
    fixed_cost = account_for_role("fixed_cost")
    assert fixed_cost.cash_flow_section == "Investing"
    _post(datetime(2026, 2, 1), "buy equipment",
          [(fixed_cost.code, 500, 0), (ACC_CASH_IN_HAND, 0, 500)])

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


# ── fixed assets & depreciation ───────────────────────────────────────────────
def _asset(cost=12000, salvage=0, life=12, method="Straight Line", rate=None,
           acq=datetime(2026, 1, 5), name="Van"):
    a = FixedAsset(name=name, acquisition_date=acq, cost=cost, salvage_value=salvage,
                   method=method,
                   useful_life_months=life if method == "Straight Line" else None,
                   rate_percent=rate)
    db.session.add(a); db.session.flush()
    return a


def _cash_id():
    return get_account(ACC_CASH_IN_HAND).id


def _lines(entry):
    """Keyed by role for the accounts the fixed-asset module owns (their codes are
    assigned at seeding time and are not fixed), and by code for the rest."""
    return {(l.account.role or l.account.code): (l.debit, l.credit) for l in entry.lines}


def test_acquisition_and_straight_line_depreciation_post_to_the_gl(appctx):
    _setup_gl()
    a = _asset(cost=12000, life=12)                      # 1,000 a month
    acq = post_asset_acquisition(a, _cash_id())
    db.session.commit()

    assert _lines(acq)["fixed_cost"][0] == Decimal("12000")   # Dr cost
    assert _lines(acq)[ACC_CASH_IN_HAND][1] == Decimal("12000")  # Cr cash

    entry, total, count = run_depreciation(month_end(datetime(2026, 1, 1)))
    db.session.commit()

    assert (total, count) == (Decimal("1000"), 1)
    assert a.accumulated == Decimal("1000")
    assert a.net_book_value == Decimal("11000")
    assert _lines(entry)["depreciation"][0] == Decimal("1000")   # Dr depreciation
    assert _lines(entry)["accum_dep"][1] == Decimal("1000")      # Cr accumulated


def test_depreciation_stops_at_the_salvage_value(appctx):
    _setup_gl()
    a = _asset(cost=1000, salvage=100, life=3)           # only 900 is depreciable
    post_asset_acquisition(a, _cash_id())
    db.session.commit()

    for m in range(1, 7):                                # run well past its life
        try:
            run_depreciation(month_end(datetime(2026, m, 1)))
            db.session.commit()
        except PostingError:
            db.session.rollback()                        # nothing left to charge

    assert a.accumulated == Decimal("900")               # never past cost − salvage
    assert a.net_book_value == Decimal("100")            # the salvage floor holds
    assert a.status == "Fully Depreciated"


def test_a_month_cannot_be_depreciated_twice(appctx):
    _setup_gl()
    a = _asset(cost=1200, life=12)
    post_asset_acquisition(a, _cash_id())
    db.session.commit()

    run_depreciation(month_end(datetime(2026, 1, 1)))
    db.session.commit()
    with pytest.raises(PostingError):
        run_depreciation(month_end(datetime(2026, 1, 1)))
    db.session.rollback()
    assert DepreciationCharge.query.filter_by(asset_id=a.id).count() == 1


def test_depreciation_is_never_dated_in_the_future(appctx):
    """Run mid-month and a month-end entry date would sit ahead of today, so every
    report that runs "as of now" — the P&L, the balance sheet — would post the
    charge and then fail to show it."""
    now = datetime.now()
    _setup_gl(now.year)
    a = _asset(cost=1200, life=12, acq=datetime(now.year, now.month, 1))
    post_asset_acquisition(a, _cash_id())
    db.session.commit()

    entry, _, _ = run_depreciation(month_end(now))
    db.session.commit()

    assert entry.entry_date <= datetime.now()       # not ahead of today…
    assert entry.entry_date < month_end(now)        # …because it is not the month-end
    # and it is still recorded against the month it belongs to
    assert DepreciationCharge.query.one().period_end == month_end(now)


def test_a_month_that_has_not_started_cannot_be_depreciated(appctx):
    now = datetime.now()
    _setup_gl(now.year)
    a = _asset(cost=1200, life=12, acq=datetime(now.year, 1, 1))
    post_asset_acquisition(a, _cash_id())
    db.session.commit()

    with pytest.raises(PostingError, match="not started"):
        run_depreciation(month_end(datetime(now.year + 1, 1, 15)))


def test_buying_an_asset_moves_the_cash_account_the_page_shows(appctx):
    """The Cash & Bank page used to add up receipts, payments and expenses. A fixed
    asset is none of those, so cash spent on one never left the page's balance and it
    read 120,000 higher than the balance sheet — two numbers for one bank account."""
    _setup_gl()
    fa = FinancialAccount.query.filter_by(name="Cash").first()
    fa.opening_balance = Decimal("500000")
    post_account_opening(fa)
    db.session.commit()
    assert get_account_balance(fa) == 500000

    a = _asset(cost=120000, life=60)
    post_asset_acquisition(a, _cash_id())
    db.session.commit()

    assert get_account_balance(fa) == 380000            # the van was paid for in cash
    assert total_cash_bank_balance() == 380000
    # and the page agrees with the ledger every report is drawn from
    assert cash_balance_as_of(datetime.now()) == Decimal("380000")

    # the drill-down lists it too, so the rows still add up to the balance above them
    rows = account_transactions(fa)
    assert sum(r["inflow"] - r["outflow"] for r in rows) == 380000
    assert any(r["outflow"] == 120000 for r in rows)


def test_reversing_depreciation_clears_the_register_and_frees_the_month(appctx):
    """The charges live in the register, not the GL. Reversing the entry without
    removing them would leave the register claiming depreciation the ledger had just
    cancelled, and the month could never be run again."""
    _setup_gl()
    a = _asset(cost=1200, life=12)
    post_asset_acquisition(a, _cash_id())
    db.session.commit()

    entry, _, _ = run_depreciation(month_end(datetime(2026, 1, 1)))
    db.session.commit()
    assert a.accumulated == Decimal("100")

    reverse_entry(entry)
    unwind_asset_entry(entry)
    db.session.commit()

    db.session.refresh(a)
    assert a.accumulated == Decimal("0")            # the register agrees with the GL
    assert DepreciationCharge.query.count() == 0

    # and the month is free to be posted again
    entry2, total, _ = run_depreciation(month_end(datetime(2026, 1, 1)))
    db.session.commit()
    assert total == Decimal("100")


def test_a_reversal_is_never_dated_before_the_entry_it_cancels(appctx):
    """A reversal defaults to today, but an entry dated ahead of today would then be
    cancelled before it happened: every report cut between the two dates would show
    the credit and not the debit, and the expense would read as negative."""
    _setup_gl()
    now = datetime.now()
    future = datetime(now.year, now.month, 28, 0, 0) if now.day < 28 else \
             datetime(now.year + 1, 1, 28, 0, 0)
    expense = account_for_role("depreciation")

    entry = post_entry(
        entry_date=future, description="Accrual dated ahead of today",
        lines=[{"account_id": expense.id, "debit": Decimal("2000"), "credit": 0},
               {"account_id": get_account(ACC_CASH_IN_HAND).id,
                "debit": 0, "credit": Decimal("2000")}],
    )
    db.session.commit()

    reversal = reverse_entry(entry)
    db.session.commit()

    assert reversal.entry_date >= entry.entry_date
    # and so the two never split across a period boundary: as of today, neither the
    # charge nor its cancellation has landed, and the expense is not showing negative.
    income, expenses = gl_profit(datetime(now.year, 1, 1), datetime.now())
    assert (income, expenses) == (Decimal("0"), Decimal("0"))


def test_a_reversal_written_before_the_fix_is_pulled_back_onto_its_original(appctx):
    """Rows already in the database still carry the split. Reproduce one the way the
    old code wrote it — a reversal dated before the entry it cancels — and the backfill
    must close the gap, or the expense goes on reading negative in production."""
    _setup_gl()
    now = datetime.now()
    future = datetime(now.year, now.month, 28, 0, 0) if now.day < 28 else \
             datetime(now.year + 1, 1, 28, 0, 0)
    expense = account_for_role("depreciation")

    entry = post_entry(
        entry_date=future, description="Charge dated ahead of today",
        lines=[{"account_id": expense.id, "debit": Decimal("2000"), "credit": 0},
               {"account_id": get_account(ACC_CASH_IN_HAND).id,
                "debit": 0, "credit": Decimal("2000")}],
    )
    reversal = reverse_entry(entry, on_date=now)      # what the old code did
    db.session.commit()

    # the damage: as of today the credit has landed and the debit has not
    _, expenses = gl_profit(datetime(now.year, 1, 1), datetime.now())
    assert expenses == Decimal("-2000")

    assert realign_backdated_reversals() == 1

    db.session.refresh(reversal)
    assert reversal.entry_date == entry.entry_date
    _, expenses = gl_profit(datetime(now.year, 1, 1), datetime.now())
    assert expenses == Decimal("0")

    assert realign_backdated_reversals() == 0        # idempotent


def test_reversing_a_disposal_puts_the_asset_back(appctx):
    _setup_gl()
    a = _asset(cost=1000, life=10)
    post_asset_acquisition(a, _cash_id())
    db.session.commit()

    entry, _ = post_asset_disposal(a, datetime(2026, 2, 10), Decimal("900"), _cash_id())
    db.session.commit()
    assert a.status == "Disposed"

    reverse_entry(entry)
    unwind_asset_entry(entry)
    db.session.commit()

    db.session.refresh(a)
    assert a.status == "Active"                     # back on the register
    assert a.disposal_date is None


def test_an_acquisition_cannot_be_reversed_from_the_journal(appctx):
    """The register would still hold an asset the ledger no longer paid for."""
    _setup_gl()
    a = _asset(cost=1000, life=10)
    acq = post_asset_acquisition(a, _cash_id())
    db.session.commit()

    with pytest.raises(PostingError, match="Dispose of it instead"):
        unwind_asset_entry(acq)


def test_reducing_balance_charge_falls_each_month(appctx):
    _setup_gl()
    a = _asset(cost=10000, salvage=0, life=None,
               method="Reducing Balance", rate=Decimal("24"))     # 24% a year
    post_asset_acquisition(a, _cash_id())
    db.session.commit()

    first = depreciation_for_month(a, month_end(datetime(2026, 1, 1)))
    run_depreciation(month_end(datetime(2026, 1, 1)))
    db.session.commit()
    second = depreciation_for_month(a, month_end(datetime(2026, 2, 1)))

    assert first == Decimal("200")        # 10,000 × 24% ÷ 12
    assert second < first                 # charged on the written-down value


def test_disposal_above_book_value_posts_a_gain(appctx):
    _setup_gl()
    a = _asset(cost=1000, life=10)                       # 100 a month
    post_asset_acquisition(a, _cash_id())
    db.session.commit()
    run_depreciation(month_end(datetime(2026, 1, 1)))    # accumulated 100, NBV 900
    db.session.commit()

    entry, gain = post_asset_disposal(a, datetime(2026, 2, 10), Decimal("1000"), _cash_id())
    db.session.commit()

    assert gain == Decimal("100")                        # sold for 1,000, book value 900
    ln = _lines(entry)
    assert ln[ACC_CASH_IN_HAND][0] == Decimal("1000")    # money in
    assert ln["accum_dep"][0] == Decimal("100")        # accumulated depreciation out
    assert ln["fixed_cost"][1] == Decimal("1000")      # cost out
    assert ln["disposal_gain"][1] == Decimal("100")
    assert a.status == "Disposed"

    # and the books still balance afterwards
    assert accounting_position(as_of=datetime(2026, 12, 31))["difference"] == Decimal("0")


def test_disposal_below_book_value_posts_a_loss(appctx):
    _setup_gl()
    a = _asset(cost=1000, life=10)
    post_asset_acquisition(a, _cash_id())
    db.session.commit()
    run_depreciation(month_end(datetime(2026, 1, 1)))    # NBV 900
    db.session.commit()

    entry, gain = post_asset_disposal(a, datetime(2026, 2, 10), Decimal("500"), _cash_id())
    db.session.commit()

    assert gain == Decimal("-400")                       # sold for 500, book value 900
    assert _lines(entry)["disposal_loss"][0] == Decimal("400")
    assert accounting_position(as_of=datetime(2026, 12, 31))["difference"] == Decimal("0")


def test_buying_an_asset_is_investing_cash_not_operating(appctx):
    _setup_gl()
    a = _asset(cost=5000, life=10)
    post_asset_acquisition(a, _cash_id())
    db.session.commit()

    cf = cash_flow_statement(datetime(2026, 1, 1), datetime(2026, 1, 31, 23, 59, 59))
    assert cf["totals"]["Investing"] == Decimal("-5000")
    assert cf["totals"]["Operating"] == Decimal("0")
    assert cf["reconciles"]


def test_depreciation_never_reaches_the_cash_flow_statement(appctx):
    """It is a non-cash charge — no entry it appears in touches cash."""
    _setup_gl()
    a = _asset(cost=1200, life=12)
    post_asset_acquisition(a, _cash_id())
    db.session.commit()
    run_depreciation(month_end(datetime(2026, 2, 1)))
    db.session.commit()

    cf = cash_flow_statement(datetime(2026, 2, 1), datetime(2026, 2, 28, 23, 59, 59))
    assert cf["net_change"] == Decimal("0")          # February moved no cash at all
    assert cf["reconciles"]
