"""Phase 3C: paying salary — settling the liability payroll posting created.

    Salaries Payable  DR
        Cash / Bank       CR

The tests that earn their place are the ones about not paying twice and not
double-counting the expense. A payment bug does not crash; it quietly pays a
workforce twice, or reports a cost that was already reported last month.

Every amount is checked against arithmetic worked out by hand.
"""
from datetime import date
from decimal import Decimal

import pytest

from app import (app as flask_app, db, User, pwd_context,
                 Account, FinancialAccount, JournalEntry, JournalLine,
                 PostingError, seed_chart_of_accounts, seed_fiscal_year,
                 get_account, get_account_balance, ACC_CASH_IN_HAND,
                 ensure_gl_account_for_financial)
from salpurflask.models.hr import Employee
from salpurflask.models.payroll import (SalaryComponent, SalaryStructure,
                                        SalaryStructureLine, PayrollPeriod,
                                        seed_default_components)
from salpurflask.models.payroll_payment import (PayrollPayment,
                                                period_net_total,
                                                period_paid_total,
                                                period_payable_balance,
                                                period_payment_status)
from salpurflask.services import payroll_engine as engine
from salpurflask.services import payroll_accounting as acct
from salpurflask.services.feature_flags import set_module


# ── helpers ───────────────────────────────────────────────────────────────────

def _chart():
    if Account.query.count() == 0:
        seed_chart_of_accounts()
        db.session.commit()
    acct.seed_payroll_accounts()
    seed_fiscal_year(date(2026, 6, 15))
    seed_fiscal_year(date.today())
    db.session.commit()


def _cash():
    """A cash account with its GL leaf, the way every payment form supplies one."""
    existing = FinancialAccount.query.filter_by(name="Cash").first()
    if existing:
        return existing
    gl = get_account(ACC_CASH_IN_HAND)
    fa = FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                          opening_balance=0, gl_account_id=gl.id)
    db.session.add(fa)
    db.session.commit()
    return fa


def _client(role="admin", email=None):
    email = email or f"{role}@pay3c.com"
    db.session.add(User(name=role.title(), email=email,
                        password=pwd_context.hash("secret123"),
                        verified=True, role=role))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _enable(hr=True, payroll=True, attendance=False):
    set_module("module_hr", hr, updated_by="test")
    set_module("module_payroll", payroll, updated_by="test")
    set_module("module_attendance", attendance, updated_by="test")


def _period_with_payroll(basic=30000, tax=0, status=None, name="June 2026"):
    """A processed period; finalised (and posted) unless `status` says otherwise."""
    _chart()
    _cash()
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1))
    db.session.add(emp)
    db.session.commit()
    seed_default_components()

    s = SalaryStructure(employee_id=emp.id, active=True)
    db.session.add(s)
    db.session.flush()
    codes = {"BASIC": basic}
    if tax:
        codes["TAX"] = tax
    for code, amount in codes.items():
        db.session.add(SalaryStructureLine(
            structure_id=s.id,
            component_id=SalaryComponent.query.filter_by(code=code).one().id,
            amount=Decimal(str(amount))))
    db.session.commit()

    p = PayrollPeriod(name=name, start_date=date(2026, 6, 1),
                      end_date=date(2026, 6, 30), status="Draft")
    db.session.add(p)
    db.session.commit()
    engine.process_period(p)
    db.session.commit()

    if status is not None:
        p.status = status
        db.session.commit()
        return emp, p

    acct.post_payroll_period(p)
    p.status = "Finalized"
    db.session.commit()
    return emp, p


def _pay(client, period, **over):
    data = {"account_id": str(_cash().id)}
    data.update({k: str(v) for k, v in over.items()})
    return client.post(f"/payroll/periods/{period.id}/pay", data=data,
                       follow_redirects=True)


def _payment_entries(period):
    ids = [p.id for p in period.payments.all()]
    if not ids:
        return []
    return (JournalEntry.query
            .filter(JournalEntry.source_type == acct.PAYMENT_SOURCE_TYPE,
                    JournalEntry.source_id.in_(ids)).all())


def _line_for(entry, account_id, side):
    total = Decimal("0")
    for l in entry.lines:
        if l.account_id == account_id:
            total += Decimal(str((l.debit if side == "debit" else l.credit) or 0))
    return total


# ── 1. a finalized payroll can be paid ────────────────────────────────────────

def test_a_finalized_payroll_can_be_paid(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    c = _client("admin")

    _pay(c, p)

    assert p.payments.count() == 1
    payment = p.payments.one()
    assert payment.amount == Decimal("30000.0000")
    assert period_payment_status(p) == "PAID"
    assert period_payable_balance(p) == Decimal("0")


# ── 2-4. states that cannot be paid ───────────────────────────────────────────

def test_a_draft_payroll_cannot_be_paid(appctx):
    _enable()
    emp, p = _period_with_payroll(status="Draft")
    _pay(_client("admin"), p)
    assert p.payments.count() == 0


def test_a_processing_payroll_cannot_be_paid(appctx):
    """Processing figures can still change — paying them would pay a draft."""
    _enable()
    emp, p = _period_with_payroll(status="Processing")
    _pay(_client("admin"), p)
    assert p.payments.count() == 0


def test_a_cancelled_payroll_cannot_be_paid(appctx):
    _enable()
    emp, p = _period_with_payroll(status="Cancelled")
    _pay(_client("admin"), p)
    assert p.payments.count() == 0


def test_a_period_with_no_live_posting_cannot_be_paid(appctx):
    """Finalized on paper but its entry was reversed: nothing left to settle."""
    _enable()
    emp, p = _period_with_payroll()
    entry = acct.posted_journal_entry(p)
    from app import reverse_entry
    reverse_entry(entry)
    db.session.commit()

    _pay(_client("admin"), p)
    assert p.payments.count() == 0


# ── 5-6. duplicate and idempotency ────────────────────────────────────────────

def test_paying_twice_is_refused_once_it_is_settled(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    c = _client("admin")

    _pay(c, p)
    _pay(c, p)                      # the second click

    assert p.payments.count() == 1
    assert period_paid_total(p) == Decimal("30000.0000")


def test_a_double_click_creates_no_duplicate_journal_entry(appctx):
    """The brief's test: count, repeat, count again."""
    _enable()
    emp, p = _period_with_payroll()
    c = _client("admin")

    _pay(c, p)
    after_first = JournalEntry.query.count()

    _pay(c, p)
    assert JournalEntry.query.count() == after_first
    assert len(_payment_entries(p)) == 1


def test_posting_the_same_payment_row_twice_is_a_no_op(appctx):
    """Not only the route — the service itself refuses a second posting."""
    _enable()
    emp, p = _period_with_payroll()
    payment = PayrollPayment(period_id=p.id, amount=Decimal("30000"),
                             payment_date=date.today(), account_id=_cash().id)
    db.session.add(payment)
    db.session.flush()

    first = acct.post_payroll_payment(payment)
    db.session.commit()
    second = acct.post_payroll_payment(payment)
    db.session.commit()

    assert first is not None and second is None
    assert len(_payment_entries(p)) == 1


# ── 7-10. the accounting shape ────────────────────────────────────────────────

def test_the_payment_entry_is_balanced(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    _pay(_client("admin"), p)

    entry = _payment_entries(p)[0]
    assert entry.total_debit == entry.total_credit == Decimal("30000.0000")


def test_salaries_payable_is_debited_and_cash_is_credited(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    cash = _cash()
    _pay(_client("admin"), p)

    entry = _payment_entries(p)[0]
    payable = acct.account_for("salaries_payable")
    cash_gl = ensure_gl_account_for_financial(cash)

    assert _line_for(entry, payable.id, "debit") == Decimal("30000.0000")
    assert _line_for(entry, cash_gl.id, "credit") == Decimal("30000.0000")


def test_salary_expense_is_not_posted_again(appctx):
    """The cost was recognised at finalisation. Debiting it again would double
    it in the P&L."""
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    expense_ids = {acct.account_for(r).id for r in
                   ("salary_expense", "allowance_expense", "overtime_expense",
                    "other_salary_expense")}

    _pay(_client("admin"), p)
    entry = _payment_entries(p)[0]

    for line in entry.lines:
        assert line.account_id not in expense_ids, "payment touched a salary expense account"


def _leaf_balance(gl_account):
    """Debit minus credit on one GL leaf, straight from the ledger.

    get_account_balance() takes a FinancialAccount (it reads .gl_account_id),
    so it is the wrong tool for reading a bare Account.
    """
    dr = (db.session.query(db.func.sum(JournalLine.debit))
          .filter(JournalLine.account_id == gl_account.id).scalar() or 0)
    cr = (db.session.query(db.func.sum(JournalLine.credit))
          .filter(JournalLine.account_id == gl_account.id).scalar() or 0)
    return Decimal(str(dr)) - Decimal(str(cr))


def test_the_liability_nets_to_zero_once_paid(appctx):
    """Posting credits Salaries Payable; paying debits it. Net effect: nil."""
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    payable = acct.account_for("salaries_payable")

    # After finalisation the liability stands at 30000 credit (-30000 net).
    assert _leaf_balance(payable) == Decimal("-30000.0000")

    _pay(_client("admin"), p)

    # Paying it debits the same account, bringing the liability back to nil.
    assert _leaf_balance(payable) == Decimal("0")


def test_the_expense_is_recognised_exactly_once(appctx):
    """Across finalise and pay, the salary expense is debited one time only."""
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    salary_gl = acct.account_for("salary_expense")

    _pay(_client("admin"), p)

    debits = (db.session.query(db.func.sum(JournalLine.debit))
              .filter(JournalLine.account_id == salary_gl.id).scalar() or 0)
    assert Decimal(str(debits)) == Decimal("30000.0000")


def test_deductions_do_not_reduce_what_is_paid_out_twice(appctx):
    """Basic 30000 less tax 5000: the liability, and the payment, are 25000."""
    _enable()
    emp, p = _period_with_payroll(basic=30000, tax=5000)
    assert period_net_total(p) == Decimal("25000.0000")

    _pay(_client("admin"), p)
    entry = _payment_entries(p)[0]
    assert entry.total_debit == Decimal("25000.0000")


# ── partial payment ───────────────────────────────────────────────────────────

def test_a_partial_payment_leaves_the_rest_outstanding(appctx):
    """Supported because each payment is its own row with its own entry -- the
    same shape SupplierPayment uses."""
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    c = _client("admin")

    _pay(c, p, amount="10000")
    assert period_paid_total(p) == Decimal("10000.0000")
    assert period_payable_balance(p) == Decimal("20000.0000")
    assert period_payment_status(p) == "PARTIALLY_PAID"

    _pay(c, p, amount="20000")
    assert period_payable_balance(p) == Decimal("0")
    assert period_payment_status(p) == "PAID"
    assert p.payments.count() == 2
    assert len(_payment_entries(p)) == 2


def test_paying_more_than_the_balance_is_refused(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    _pay(_client("admin"), p, amount="30001")
    assert p.payments.count() == 0


def test_a_second_payment_cannot_exceed_what_remains(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    c = _client("admin")
    _pay(c, p, amount="25000")
    _pay(c, p, amount="10000")          # only 5000 left
    assert period_paid_total(p) == Decimal("25000.0000")
    assert p.payments.count() == 1


def test_a_zero_or_negative_payment_is_refused(appctx):
    _enable()
    emp, p = _period_with_payroll()
    c = _client("admin")
    _pay(c, p, amount="0")
    _pay(c, p, amount="-500")
    assert p.payments.count() == 0


# ── 11. transaction safety ────────────────────────────────────────────────────

def test_a_refused_posting_leaves_no_payment_row(appctx):
    """A date no fiscal year covers: post_entry refuses, so the payment must not
    survive either."""
    _enable()
    emp, p = _period_with_payroll()
    before_entries = JournalEntry.query.count()
    before_lines = JournalLine.query.count()

    _pay(_client("admin"), p, payment_date="1990-01-15")

    assert p.payments.count() == 0
    assert JournalEntry.query.count() == before_entries
    assert JournalLine.query.count() == before_lines


def test_a_failed_payment_leaves_no_orphan_lines(appctx):
    _enable()
    emp, p = _period_with_payroll()
    payment = PayrollPayment(period_id=p.id, amount=Decimal("30000"),
                             payment_date=date(1991, 1, 15), account_id=_cash().id)
    db.session.add(payment)
    db.session.flush()

    with pytest.raises(PostingError):
        acct.post_payroll_payment(payment)
    db.session.rollback()

    orphans = (JournalLine.query
               .outerjoin(JournalEntry, JournalLine.entry_id == JournalEntry.id)
               .filter(JournalEntry.id.is_(None)).count())
    assert orphans == 0


def test_a_payment_without_an_account_is_refused(appctx):
    _enable()
    emp, p = _period_with_payroll()
    _client("admin").post(f"/payroll/periods/{p.id}/pay",
                          data={"account_id": ""}, follow_redirects=True)
    assert p.payments.count() == 0


# ── 12. permissions ───────────────────────────────────────────────────────────

def test_payment_permission_is_admin_only(appctx):
    from salpurflask.services.hr_permissions import has_permission
    with flask_app.test_request_context():
        manager = User(name="M", email="m@p3c.com", password="x",
                       verified=True, role="manager")
        staff = User(name="S", email="s@p3c.com", password="x",
                     verified=True, role="staff")
        assert has_permission("payroll.post", manager) is False
        assert has_permission("payroll.post", staff) is False


def test_a_manager_cannot_pay_salary(appctx):
    _enable()
    emp, p = _period_with_payroll()
    _pay(_client("manager"), p)
    assert p.payments.count() == 0


def test_staff_cannot_pay_salary(appctx):
    _enable()
    emp, p = _period_with_payroll()
    r = _client("staff").post(f"/payroll/periods/{p.id}/pay",
                              data={"account_id": str(_cash().id)},
                              follow_redirects=False)
    assert r.status_code == 302
    assert p.payments.count() == 0


def test_payment_routes_are_refused_while_the_module_is_off(appctx):
    _enable(payroll=False)
    _chart()
    _cash()
    p = PayrollPeriod(name="X 2026", start_date=date(2026, 6, 1),
                      end_date=date(2026, 6, 30), status="Finalized")
    db.session.add(p)
    db.session.commit()

    r = _client("admin").post(f"/payroll/periods/{p.id}/pay",
                              data={"account_id": str(_cash().id)},
                              follow_redirects=False)
    assert r.status_code == 302
    assert PayrollPayment.query.count() == 0


# ── reversal ──────────────────────────────────────────────────────────────────

def test_reversing_a_payment_keeps_both_entries(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    c = _client("admin")
    _pay(c, p)
    payment = p.payments.one()
    original = acct.posted_payment_entry(payment)
    oid = original.id

    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(JournalEntry, oid) is not None
    reversal = JournalEntry.query.filter_by(reversal_of_id=oid).one()
    assert reversal.total_debit == original.total_credit
    assert reversal.total_credit == original.total_debit

    payment = db.session.get(PayrollPayment, payment.id)
    assert payment.is_reversed is True
    # The liability is owed again, so the period is payable once more.
    assert period_payable_balance(p) == Decimal("30000.0000")
    assert period_payment_status(p) == "UNPAID"


def test_reversing_twice_does_not_reverse_twice(appctx):
    _enable()
    emp, p = _period_with_payroll()
    c = _client("admin")
    _pay(c, p)
    payment = p.payments.one()

    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)
    count = JournalEntry.query.count()
    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)
    assert JournalEntry.query.count() == count


def test_a_period_can_be_paid_again_after_a_reversal(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    c = _client("admin")
    _pay(c, p)
    payment = p.payments.one()
    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)

    _pay(c, p)
    assert period_paid_total(p) == Decimal("30000.0000")
    assert p.payments.filter_by(is_reversed=False).count() == 1


def test_a_manager_cannot_reverse_a_payment(appctx):
    """The manager must be the only login in this test.

    Signing in twice trips the rate limiter (5 per IP), the second login fails
    silently, and the request then runs under the first session — which would
    make this pass for entirely the wrong reason. So the payment is posted
    directly rather than through an admin client.
    """
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    payment = PayrollPayment(period_id=p.id, amount=Decimal("30000"),
                             payment_date=date.today(), account_id=_cash().id)
    db.session.add(payment)
    db.session.flush()
    acct.post_payroll_payment(payment)
    db.session.commit()
    original = acct.posted_payment_entry(payment)

    _client("manager").post(f"/payroll/payments/{payment.id}/reverse",
                            follow_redirects=True)

    assert JournalEntry.query.filter_by(reversal_of_id=original.id).count() == 0
    db.session.expire_all()
    assert db.session.get(PayrollPayment, payment.id).is_reversed is False


# ── 13-14. regression and reporting ───────────────────────────────────────────

def test_the_period_page_shows_the_payment_state(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    c = _client("admin")

    body = c.get(f"/payroll/periods/{p.id}").get_data(as_text=True)
    assert "Unpaid" in body and "Pay Salary" in body

    _pay(c, p)
    body = c.get(f"/payroll/periods/{p.id}").get_data(as_text=True)
    assert "Paid" in body


def test_cash_and_payable_both_move_by_the_payment(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    cash = _cash()
    cash_gl = ensure_gl_account_for_financial(cash)
    db.session.commit()

    before_cash = _leaf_balance(cash_gl)
    before_payable = _leaf_balance(acct.account_for("salaries_payable"))

    _pay(_client("admin"), p)

    # Cash fell by exactly the payment; the liability rose by the same amount.
    assert before_cash - _leaf_balance(cash_gl) == Decimal("30000.0000")
    assert (_leaf_balance(acct.account_for("salaries_payable"))
            - before_payable) == Decimal("30000.0000")


def test_payment_works_with_attendance_off(appctx):
    _enable(attendance=False)
    emp, p = _period_with_payroll(basic=30000)
    _pay(_client("admin"), p)
    entry = _payment_entries(p)[0]
    assert entry.total_debit == entry.total_credit == Decimal("30000.0000")


def test_every_payment_entry_has_a_valid_source(appctx):
    _enable()
    emp, p = _period_with_payroll()
    _pay(_client("admin"), p)

    for entry in JournalEntry.query.filter_by(
            source_type=acct.PAYMENT_SOURCE_TYPE).all():
        assert db.session.get(PayrollPayment, entry.source_id) is not None


def test_no_payment_entry_is_ever_unbalanced(appctx):
    _enable()
    emp, p = _period_with_payroll(basic=30000)
    c = _client("admin")
    _pay(c, p, amount="12000")
    _pay(c, p, amount="18000")

    for entry in JournalEntry.query.filter_by(
            source_type=acct.PAYMENT_SOURCE_TYPE).all():
        assert entry.total_debit == entry.total_credit
