"""Phase 3B: payroll posted to the general ledger.

The tests that matter most are not the happy path. They are the ones that keep
the ledger honest when something goes wrong: posting twice, cancelling twice,
and a posting that fails after the period was marked finalised. A payroll bug
that double-posts does not raise — it quietly doubles an expense, and someone
finds it at year end.

Every figure here is checked against arithmetic worked out by hand, never
against the code that produced it.
"""
from datetime import date
from decimal import Decimal

import pytest

from app import (app as flask_app, db, User, pwd_context,
                 Account, JournalEntry, JournalLine, PostingError,
                 seed_chart_of_accounts, seed_fiscal_year)
from salpurflask.models.hr import Employee
from salpurflask.models.payroll import (SalaryComponent, SalaryStructure,
                                        SalaryStructureLine, PayrollPeriod,
                                        EmployeeAdvance, seed_default_components)
from salpurflask.services import payroll_engine as engine
from salpurflask.services import payroll_accounting as acct
from salpurflask.services.feature_flags import set_module


# ── helpers ───────────────────────────────────────────────────────────────────

def _chart():
    if Account.query.count() == 0:
        seed_chart_of_accounts()
        db.session.commit()
    acct.seed_payroll_accounts()
    # The payroll period tested below sits in June 2026, but reverse_entry dates
    # a reversal *today* on purpose (a correction belongs in the month it was
    # made). Both need an open period, so seed the fiscal year covering each.
    seed_fiscal_year(date(2026, 6, 15))
    seed_fiscal_year(date.today())
    db.session.commit()


def _client(role="admin", email=None):
    email = email or f"{role}@gl.com"
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


def _employee(code="E-1", name="Worker"):
    e = Employee(code=code, name=name, joining_date=date(2025, 1, 1), active=True)
    db.session.add(e)
    db.session.commit()
    return e


def _component(code):
    seed_default_components()
    return SalaryComponent.query.filter_by(code=code).one()


def _structure(emp, **codes):
    s = SalaryStructure(employee_id=emp.id, active=True,
                        effective_from=date(2025, 1, 1))
    db.session.add(s)
    db.session.flush()
    for code, amount in codes.items():
        db.session.add(SalaryStructureLine(structure_id=s.id,
                                           component_id=_component(code).id,
                                           amount=Decimal(str(amount))))
    db.session.commit()
    return s


def _period(name="June 2026", start=date(2026, 6, 1), end=date(2026, 6, 30)):
    p = PayrollPeriod(name=name, start_date=start, end_date=end, status="Draft")
    db.session.add(p)
    db.session.commit()
    return p


def _processed(**codes):
    """A period with one employee processed and ready to finalise."""
    _chart()
    emp = _employee()
    _structure(emp, **(codes or {"BASIC": 30000}))
    p = _period()
    engine.process_period(p)
    db.session.commit()
    return emp, p


def _balance(entry):
    return entry.total_debit, entry.total_credit


def _role_amount(entry, role, side):
    """What one role's account was debited or credited on this entry."""
    account = acct.account_for(role)
    total = Decimal("0")
    for line in entry.lines:
        if line.account_id == account.id:
            total += Decimal(str((line.debit if side == "debit" else line.credit) or 0))
    return total


# ── the accounts ──────────────────────────────────────────────────────────────

def test_the_payroll_accounts_are_seeded_once(appctx):
    _chart()
    assert acct.seed_payroll_accounts() == 0            # idempotent
    for role in acct.PAYROLL_ROLES:
        a = acct.account_for(role)
        assert a is not None and not a.is_group


def test_the_existing_salaries_account_is_reused_not_duplicated(appctx):
    """6020 ships with the base chart. A second salary account beside it would
    split the same cost in two."""
    _chart()
    assert acct.account_for("salary_expense").code == "6020"
    assert Account.query.filter_by(name="Salaries & Wages").count() == 1


def test_payroll_never_posts_to_the_supplier_control_account(appctx):
    """2100 Accounts Payable belongs to the supplier subledger."""
    _chart()
    for role in acct.PAYROLL_ROLES:
        assert acct.account_for(role).code != "2100"


# ── basic posting ─────────────────────────────────────────────────────────────

def test_finalizing_posts_one_balanced_entry(appctx):
    _enable()
    emp, p = _processed(BASIC=30000)
    before = JournalEntry.query.count()

    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    assert JournalEntry.query.count() == before + 1
    entry = acct.posted_journal_entry(p)
    assert entry is not None
    dr, cr = _balance(entry)
    assert dr == cr == Decimal("30000.0000")


def test_the_entry_carries_the_payroll_source_reference(appctx):
    """Traceable both ways: payroll -> entry, and entry -> payroll."""
    _enable()
    emp, p = _processed()
    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    entry = acct.posted_journal_entry(p)
    assert entry.source_type == "payroll"
    assert entry.source_id == p.id
    assert entry.reference == f"PAY-{p.id}"
    assert p.name in entry.description


def test_the_entry_is_dated_on_the_period_end(appctx):
    _enable()
    emp, p = _processed()
    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    assert acct.posted_journal_entry(p).entry_date.date() == p.end_date


# ── the debit/credit shape ────────────────────────────────────────────────────

def test_every_component_lands_in_the_right_account(appctx):
    """Basic 100000 + allowances 20000 + bonus 5000, less tax 5000.

    Dr Salaries 100000, Dr Allowances 25000  = 125000
    Cr Tax Payable 5000, Cr Salaries Payable 120000 = 125000
    """
    _enable()
    _chart()
    emp = _employee()
    _structure(emp, BASIC=100000, HRA=15000, MEDICAL=5000, BONUS=5000, TAX=5000)
    p = _period()
    engine.process_period(p)
    db.session.commit()

    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    entry = acct.posted_journal_entry(p)

    assert _role_amount(entry, "salary_expense", "debit") == Decimal("100000.0000")
    assert _role_amount(entry, "allowance_expense", "debit") == Decimal("25000.0000")
    assert _role_amount(entry, "payroll_tax_payable", "credit") == Decimal("5000.0000")
    assert _role_amount(entry, "salaries_payable", "credit") == Decimal("120000.0000")
    dr, cr = _balance(entry)
    assert dr == cr == Decimal("125000.0000")


def test_overtime_reaches_its_own_expense_account(appctx):
    """30000/30 days = 1000/day, /8h = 125/h. Four OT hours = 500."""
    from datetime import time
    from salpurflask.models.attendance import Attendance

    _enable(attendance=True)
    _chart()
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()
    row = Attendance(employee_id=emp.id, date=date(2026, 6, 2), status="Present",
                     check_in=time(9, 0), check_out=time(21, 0))
    row.recalculate()
    db.session.add(row)
    db.session.commit()
    engine.process_period(p)
    db.session.commit()

    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    entry = acct.posted_journal_entry(p)

    assert _role_amount(entry, "overtime_expense", "debit") == Decimal("500.0000")
    dr, cr = _balance(entry)
    assert dr == cr == Decimal("30500.0000")


def test_absence_reduces_the_expense_it_never_creates_a_credit(appctx):
    """Three absences on 30000/30 days: the business spent 27000, not 30000."""
    from salpurflask.models.attendance import Attendance

    _enable(attendance=True)
    _chart()
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()
    for i in range(3):
        db.session.add(Attendance(employee_id=emp.id, date=date(2026, 6, 10 + i),
                                  status="Absent"))
    db.session.add(Attendance(employee_id=emp.id, date=date(2026, 6, 1), status="Present"))
    db.session.commit()
    engine.process_period(p)
    db.session.commit()

    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    entry = acct.posted_journal_entry(p)

    assert _role_amount(entry, "salary_expense", "debit") == Decimal("27000.0000")
    dr, cr = _balance(entry)
    assert dr == cr == Decimal("27000.0000")


def test_advance_recovery_credits_the_employee_receivable(appctx):
    """Recovering an advance reduces what the employee owes -- an asset falls."""
    _enable()
    _chart()
    emp = _employee()
    _structure(emp, BASIC=30000)
    db.session.add(EmployeeAdvance(employee_id=emp.id, advance_date=date(2026, 5, 1),
                                   amount=Decimal("10000"), instalment=Decimal("2500"),
                                   status="Active"))
    db.session.commit()
    p = _period()
    engine.process_period(p)
    db.session.commit()

    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    entry = acct.posted_journal_entry(p)

    assert _role_amount(entry, "employee_advance", "credit") == Decimal("2500.0000")
    assert _role_amount(entry, "salaries_payable", "credit") == Decimal("27500.0000")
    dr, cr = _balance(entry)
    assert dr == cr == Decimal("30000.0000")


def test_an_unmapped_component_still_reaches_the_ledger(appctx):
    """A component nobody mapped must not vanish -- a dropped line would
    unbalance the entry."""
    _enable()
    _chart()
    db.session.add(SalaryComponent(code="FUEL", name="Fuel Allowance",
                                   component_type="earning", calc_method="fixed",
                                   sort_order=55))
    db.session.commit()
    emp = _employee()
    _structure(emp, BASIC=30000, FUEL=4000)
    p = _period()
    engine.process_period(p)
    db.session.commit()

    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    entry = acct.posted_journal_entry(p)

    assert _role_amount(entry, "other_salary_expense", "debit") == Decimal("4000.0000")
    dr, cr = _balance(entry)
    assert dr == cr == Decimal("34000.0000")


def test_many_employees_produce_one_consolidated_entry(appctx):
    """One period, one entry -- not one per payslip."""
    _enable()
    _chart()
    for i in range(4):
        e = _employee(f"E-{i}", f"Worker {i}")
        _structure(e, BASIC=10000)
    p = _period()
    engine.process_period(p)
    db.session.commit()

    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    assert JournalEntry.query.filter_by(source_type="payroll", source_id=p.id).count() == 1
    entry = acct.posted_journal_entry(p)
    dr, cr = _balance(entry)
    assert dr == cr == Decimal("40000.0000")


# ── idempotency ───────────────────────────────────────────────────────────────

def test_finalizing_twice_does_not_post_twice(appctx):
    """The test the brief demands: count, repeat, count again."""
    _enable()
    emp, p = _processed()
    c = _client("admin")

    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    after_first = JournalEntry.query.count()

    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    assert JournalEntry.query.count() == after_first
    assert JournalEntry.query.filter_by(source_type="payroll", source_id=p.id).count() == 1


def test_calling_the_poster_directly_twice_is_also_safe(appctx):
    """Not only the route -- the service itself refuses a second posting."""
    _enable()
    emp, p = _processed()

    first = acct.post_payroll_period(p)
    db.session.commit()
    second = acct.post_payroll_period(p)
    db.session.commit()

    assert first is not None
    assert second is None
    assert JournalEntry.query.filter_by(source_type="payroll", source_id=p.id).count() == 1


# ── transaction safety ────────────────────────────────────────────────────────

def test_a_refused_posting_leaves_the_period_unfinalized(appctx):
    """A closed period refuses the entry. Payroll must not be left finalised
    with nothing behind it."""
    _enable()
    _chart()
    emp = _employee()
    _structure(emp, BASIC=30000)
    # A period whose date no fiscal year covers -- post_entry will refuse it.
    p = _period("Ancient 1990", date(1990, 1, 1), date(1990, 1, 31))
    engine.process_period(p)
    db.session.commit()

    before_entries = JournalEntry.query.count()
    before_lines = JournalLine.query.count()

    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    db.session.expire_all()
    p = db.session.get(PayrollPeriod, p.id)
    assert p.status != "Finalized"
    assert JournalEntry.query.count() == before_entries
    assert JournalLine.query.count() == before_lines


def test_a_failed_posting_leaves_no_orphan_lines(appctx):
    _enable()
    _chart()
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period("Ancient 1991", date(1991, 1, 1), date(1991, 1, 31))
    engine.process_period(p)
    db.session.commit()

    with pytest.raises(PostingError):
        acct.post_payroll_period(p)
    db.session.rollback()

    orphans = (JournalLine.query
               .outerjoin(JournalEntry, JournalLine.entry_id == JournalEntry.id)
               .filter(JournalEntry.id.is_(None)).count())
    assert orphans == 0


def test_advance_recovery_is_rolled_back_when_posting_fails(appctx):
    """Finalising and posting are one transaction, so a refused entry must not
    leave the advance marked as recovered."""
    _enable()
    _chart()
    emp = _employee()
    _structure(emp, BASIC=30000)
    adv = EmployeeAdvance(employee_id=emp.id, advance_date=date(1992, 1, 1),
                          amount=Decimal("10000"), instalment=Decimal("2500"),
                          status="Active")
    db.session.add(adv)
    db.session.commit()
    p = _period("Ancient 1992", date(1992, 1, 1), date(1992, 1, 31))
    engine.process_period(p)
    db.session.commit()

    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(EmployeeAdvance, adv.id).recovered == Decimal("0.0000")


# ── cancel and reversal ───────────────────────────────────────────────────────

def test_cancelling_a_posted_period_reverses_rather_than_deletes(appctx):
    _enable()
    emp, p = _processed()
    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    original = acct.posted_journal_entry(p)
    original_id = original.id

    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)

    # The original is still there, flagged, and a mirror image exists.
    db.session.expire_all()
    original = db.session.get(JournalEntry, original_id)
    assert original is not None
    assert original.is_reversed is True

    reversal = JournalEntry.query.filter_by(reversal_of_id=original_id).one()
    assert reversal.total_debit == original.total_credit
    assert reversal.total_credit == original.total_debit


def test_the_reversal_mirrors_every_line(appctx):
    _enable()
    _chart()
    emp = _employee()
    _structure(emp, BASIC=100000, HRA=20000, TAX=5000)
    p = _period()
    engine.process_period(p)
    db.session.commit()

    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    original = acct.posted_journal_entry(p)
    original_lines = {(l.account_id, Decimal(str(l.debit)), Decimal(str(l.credit)))
                      for l in original.lines}
    oid = original.id

    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    reversal = JournalEntry.query.filter_by(reversal_of_id=oid).one()
    reversed_lines = {(l.account_id, Decimal(str(l.credit)), Decimal(str(l.debit)))
                      for l in reversal.lines}
    assert original_lines == reversed_lines


def test_cancelling_twice_does_not_reverse_twice(appctx):
    _enable()
    emp, p = _processed()
    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    count = JournalEntry.query.count()

    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    assert JournalEntry.query.count() == count


def test_a_reversed_period_cannot_be_posted_again(appctx):
    """Cancelled means cancelled -- re-posting would resurrect the expense."""
    _enable()
    emp, p = _processed()
    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    count = JournalEntry.query.count()

    with pytest.raises(PostingError):
        acct.post_payroll_period(p)
    db.session.rollback()
    assert JournalEntry.query.count() == count


def test_cancelling_an_unposted_period_is_harmless(appctx):
    _enable()
    emp, p = _processed()
    before = JournalEntry.query.count()
    _client("admin").post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    assert JournalEntry.query.count() == before
    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status == "Cancelled"


# ── accounting status ─────────────────────────────────────────────────────────

def test_the_accounting_status_follows_the_ledger(appctx):
    _enable()
    emp, p = _processed()
    assert acct.accounting_status(p) == "NOT_POSTED"

    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    assert acct.accounting_status(p) == "POSTED"

    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    assert acct.accounting_status(p) == "REVERSED"


def test_the_period_page_shows_the_posting(appctx):
    _enable()
    emp, p = _processed()
    c = _client("admin")
    body = c.get(f"/payroll/periods/{p.id}").get_data(as_text=True)
    assert "Not Posted" in body

    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    body = c.get(f"/payroll/periods/{p.id}").get_data(as_text=True)
    assert "Posted" in body and f"PAY-{p.id}" in body


# ── lifecycle: only a finalised period posts ──────────────────────────────────

def test_processing_alone_posts_nothing(appctx):
    """Draft and Processing must leave the ledger alone."""
    _enable()
    emp, p = _processed()
    assert acct.posted_journal_entry(p) is None
    assert JournalEntry.query.filter_by(source_type="payroll").count() == 0


def test_a_preview_posts_nothing(appctx):
    _enable()
    emp, p = _processed()
    before = JournalEntry.query.count()
    _client("admin").get(f"/payroll/preview/{emp.id}/{p.id}")
    assert JournalEntry.query.count() == before


def test_a_cancelled_period_refuses_to_post(appctx):
    _enable()
    emp, p = _processed()
    p.status = "Cancelled"
    db.session.commit()
    with pytest.raises(PostingError):
        acct.post_payroll_period(p)
    db.session.rollback()


# ── permissions ───────────────────────────────────────────────────────────────

def test_a_manager_cannot_post_payroll_to_the_ledger(appctx):
    _enable()
    emp, p = _processed()
    before = JournalEntry.query.count()

    _client("manager").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    assert JournalEntry.query.count() == before
    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status != "Finalized"


def test_a_manager_cannot_reverse_a_posting(appctx):
    """Reversing is `payroll.post`, which a manager does not hold.

    Asserted against the permission map and a manager-only client. Two logins
    inside one test would trip the sign-in rate limiter (5 per IP), and the
    second client would silently keep the first one's session -- which would
    make this test pass for entirely the wrong reason.
    """
    from salpurflask.services.hr_permissions import has_permission

    _enable()
    emp, p = _processed()

    with flask_app.test_request_context():
        manager = User(name="M", email="mgr@gl.com", password="x",
                       verified=True, role="manager")
        assert has_permission("payroll.post", manager) is False
        assert has_permission("payroll.edit", manager) is True   # unchanged

    # And through the route, with the manager as the only logged-in user.
    _client("manager", "m2@gl.com").post(f"/payroll/periods/{p.id}/cancel",
                                         follow_redirects=True)
    assert acct.posted_journal_entry(p) is None      # never posted to begin with
    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status != "Finalized"


def test_staff_cannot_reach_payroll_posting_at_all(appctx):
    _enable()
    emp, p = _processed()
    before = JournalEntry.query.count()
    r = _client("staff").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=False)
    assert r.status_code == 302
    assert JournalEntry.query.count() == before


# ── module interaction ────────────────────────────────────────────────────────

def test_payroll_off_leaves_existing_accounting_alone(appctx):
    _enable(payroll=False)
    _chart()
    before = JournalEntry.query.count()
    c = _client("admin")
    assert c.get("/journal").status_code == 200
    assert c.get("/reports/trial_balance").status_code == 200
    assert JournalEntry.query.count() == before


def test_posting_works_with_attendance_off(appctx):
    """Full-period salary, posted the same way."""
    _enable(attendance=False)
    emp, p = _processed(BASIC=30000)
    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    entry = acct.posted_journal_entry(p)
    dr, cr = _balance(entry)
    assert dr == cr == Decimal("30000.0000")


# ── ledger-wide invariants ────────────────────────────────────────────────────

def test_no_payroll_entry_is_ever_unbalanced(appctx):
    _enable()
    _chart()
    for i in range(3):
        e = _employee(f"E-{i}", f"W{i}")
        _structure(e, BASIC=20000 + i * 1000, HRA=2000, TAX=500)
    p = _period()
    engine.process_period(p)
    db.session.commit()
    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    for entry in JournalEntry.query.filter_by(source_type="payroll").all():
        assert entry.total_debit == entry.total_credit, f"entry {entry.id} unbalanced"


def test_no_payroll_entry_is_orphaned_from_its_period(appctx):
    _enable()
    emp, p = _processed()
    _client("admin").post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    for entry in JournalEntry.query.filter_by(source_type="payroll").all():
        assert db.session.get(PayrollPeriod, entry.source_id) is not None
