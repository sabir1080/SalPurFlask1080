"""Payroll cancellation safety — the two fixes from the real-world E2E run.

Bug #1: cancelling a period with a live salary payment reversed the payroll
posting but left the payment standing, so Salaries Payable ended up on the
wrong side of zero. A period with any live payment now refuses to cancel; the
payment has to be reversed first, through its own reversal route, so cash and
the liability move back together.

Bug #2: the new-period overlap check counted a Cancelled period as occupying
its dates forever, so a cancelled month could never be re-run. Cancelled
periods no longer block; every live status still does.
"""
from datetime import date
from decimal import Decimal

import pytest

from app import (app as flask_app, db, User, pwd_context,
                 Account, FinancialAccount, JournalEntry, JournalLine,
                 seed_chart_of_accounts, seed_fiscal_year, get_account,
                 ACC_CASH_IN_HAND, ensure_gl_account_for_financial)
from salpurflask.models.hr import Employee
from salpurflask.models.payroll import (SalaryComponent, SalaryStructure,
                                        SalaryStructureLine, PayrollPeriod,
                                        seed_default_components)
from salpurflask.models.payroll_payment import PayrollPayment, period_paid_total
from salpurflask.services import payroll_engine as engine
from salpurflask.services import payroll_accounting as acct
from salpurflask.services.feature_flags import set_module


# ── helpers (match the conventions in test_payroll_payment.py) ───────────────

def _chart():
    if Account.query.count() == 0:
        seed_chart_of_accounts()
        db.session.commit()
    acct.seed_payroll_accounts()
    seed_fiscal_year(date(2026, 6, 15))
    seed_fiscal_year(date.today())
    db.session.commit()


def _cash():
    existing = FinancialAccount.query.filter_by(name="Cash").first()
    if existing:
        return existing
    gl = get_account(ACC_CASH_IN_HAND)
    fa_acc = FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                              opening_balance=0, gl_account_id=gl.id)
    db.session.add(fa_acc)
    db.session.commit()
    return fa_acc


def _client(role="admin", email=None):
    email = email or f"{role}@cancelsafety.com"
    db.session.add(User(name=role.title(), email=email,
                        password=pwd_context.hash("secret123"),
                        verified=True, role=role))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _enable():
    set_module("module_hr", True, updated_by="test")
    set_module("module_payroll", True, updated_by="test")


def _finalized_period(name="June 2026", start=date(2026, 6, 1),
                      end=date(2026, 6, 30), basic=30000):
    """A finalized, posted period with one employee. Mirrors
    _period_with_payroll in test_payroll_payment.py."""
    _chart()
    _cash()
    emp = Employee(code=f"E-{name}", name="Worker", joining_date=date(2025, 1, 1))
    db.session.add(emp)
    db.session.commit()
    seed_default_components()

    s = SalaryStructure(employee_id=emp.id, active=True)
    db.session.add(s)
    db.session.flush()
    db.session.add(SalaryStructureLine(
        structure_id=s.id,
        component_id=SalaryComponent.query.filter_by(code="BASIC").one().id,
        amount=Decimal(str(basic))))
    db.session.commit()

    p = PayrollPeriod(name=name, start_date=start, end_date=end, status="Draft")
    db.session.add(p)
    db.session.commit()
    engine.process_period(p)
    db.session.commit()
    acct.post_payroll_period(p)
    p.status = "Finalized"
    db.session.commit()
    return emp, p


def _pay(client, period, **over):
    data = {"account_id": str(_cash().id)}
    data.update({k: str(v) for k, v in over.items()})
    return client.post(f"/payroll/periods/{period.id}/pay", data=data,
                       follow_redirects=True)


def _leaf(gl_id):
    dr = (db.session.query(db.func.sum(JournalLine.debit))
          .filter(JournalLine.account_id == gl_id).scalar() or 0)
    cr = (db.session.query(db.func.sum(JournalLine.credit))
          .filter(JournalLine.account_id == gl_id).scalar() or 0)
    return Decimal(str(dr)) - Decimal(str(cr))


def _flash_texts(resp):
    import re
    return [" ".join(t.split()) for _, t in
            re.findall(r'alert-(danger|success|warning)[^>]*>(.{0,200})',
                       resp.get_data(as_text=True), re.S)]


# ══════════════════════════════════════════════════════════════════════════
# BUG #1 — cancel refused while a live payment exists
# ══════════════════════════════════════════════════════════════════════════

def test_cancel_without_payment_succeeds(appctx):
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")

    r = c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status == "Cancelled"
    assert "cancelled" in " ".join(_flash_texts(r)).lower()


def test_cancel_with_full_payment_is_refused(appctx):
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")
    _pay(c, p)                              # pays the full 30,000
    assert period_paid_total(p) == Decimal("30000.0000")

    r = c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status == "Finalized"
    msg = " ".join(_flash_texts(r))
    assert "salary payments exist" in msg
    assert "Reverse the salary payment" in msg


def test_cancel_with_partial_payment_is_refused(appctx):
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")
    _pay(c, p, amount="12000")              # partial
    assert period_paid_total(p) == Decimal("12000.0000")

    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status == "Finalized"


def test_reversing_the_payment_then_allows_cancel(appctx):
    """The intended workflow: reverse the payment through its own route, then
    cancel succeeds."""
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")
    _pay(c, p)
    payment = p.payments.filter_by(is_reversed=False).one()

    # cancel is refused first
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status == "Finalized"

    # reverse the payment using the EXISTING reversal route
    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(PayrollPayment, payment.id).is_reversed is True
    assert period_paid_total(p) == Decimal("0")

    # now cancel succeeds
    r = c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status == "Cancelled"


def test_duplicate_payment_reversal_is_refused(appctx):
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")
    _pay(c, p)
    payment = p.payments.filter_by(is_reversed=False).one()

    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)
    reversal_count = JournalEntry.query.filter_by(
        reversal_of_id=acct.posted_payment_entry(payment).id
        if acct.posted_payment_entry(payment) else None).count()

    # second reversal attempt
    r = c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)
    msg = " ".join(_flash_texts(r)).lower()
    assert "already been reversed" in msg or "already reversed" in msg

    # exactly one reversal entry exists for this payment
    entries = JournalEntry.query.filter_by(source_type=acct.PAYMENT_SOURCE_TYPE,
                                           source_id=payment.id).all()
    reversals = [e for e in entries if e.reversal_of_id is not None]
    assert len(reversals) == 1


def test_duplicate_cancel_is_idempotent(appctx):
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status == "Cancelled"

    reversal_count_before = JournalEntry.query.filter(
        JournalEntry.reversal_of_id.isnot(None)).count()

    r = c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    assert "already cancelled" in " ".join(_flash_texts(r)).lower()

    reversal_count_after = JournalEntry.query.filter(
        JournalEntry.reversal_of_id.isnot(None)).count()
    assert reversal_count_after == reversal_count_before


# ── payment reversal accounting correctness ───────────────────────────────

def test_payment_reversal_creates_correct_reverse_entry(appctx):
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")
    _pay(c, p)
    payment = p.payments.filter_by(is_reversed=False).one()
    original = acct.posted_payment_entry(payment)
    original_id = original.id
    original_lines = {(l.account_id, Decimal(str(l.debit)), Decimal(str(l.credit)))
                      for l in original.lines}

    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)

    db.session.expire_all()
    # original still exists, flagged
    original = db.session.get(JournalEntry, original_id)
    assert original is not None
    assert original.is_reversed is True

    reversal = JournalEntry.query.filter_by(reversal_of_id=original_id).one()
    reversed_lines = {(l.account_id, Decimal(str(l.credit)), Decimal(str(l.debit)))
                      for l in reversal.lines}
    assert original_lines == reversed_lines
    assert reversal.total_debit == reversal.total_credit


def test_original_payment_entry_remains_in_history_after_reversal(appctx):
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")
    _pay(c, p)
    payment = p.payments.filter_by(is_reversed=False).one()
    original_id = acct.posted_payment_entry(payment).id

    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)

    # never deleted
    assert JournalEntry.query.get(original_id) is not None
    entries_for_payment = JournalEntry.query.filter_by(
        source_type=acct.PAYMENT_SOURCE_TYPE, source_id=payment.id).count()
    assert entries_for_payment == 2          # original + reversal


def test_cancellation_produces_exactly_one_payroll_reversal(appctx):
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")

    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)

    payroll_entries = JournalEntry.query.filter_by(
        source_type="payroll", source_id=p.id).all()
    reversals = [e for e in payroll_entries if e.reversal_of_id is not None]
    assert len(reversals) == 1


def test_salaries_payable_correct_after_payment_reversal_and_cancel(appctx):
    """The scenario that produced the original bug: pay, reverse the payment,
    cancel the period. Salaries Payable must return to exactly zero."""
    _enable()
    emp, p = _finalized_period(basic=30000)
    c = _client("admin")
    payable_gl = acct.account_for("salaries_payable")

    assert _leaf(payable_gl.id) == Decimal("-30000.0000")   # credit, owed

    _pay(c, p)
    assert _leaf(payable_gl.id) == Decimal("0")              # settled

    payment = p.payments.filter_by(is_reversed=False).one()
    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)
    assert _leaf(payable_gl.id) == Decimal("-30000.0000")    # owed again

    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    assert _leaf(payable_gl.id) == Decimal("0")               # nothing owed


def test_cash_balance_correct_after_payment_reversal(appctx):
    _enable()
    emp, p = _finalized_period(basic=30000)
    c = _client("admin")
    cash = _cash()
    cash_gl = ensure_gl_account_for_financial(cash)
    db.session.commit()
    before = _leaf(cash_gl.id)

    _pay(c, p)
    assert before - _leaf(cash_gl.id) == Decimal("30000.0000")

    payment = p.payments.filter_by(is_reversed=False).one()
    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)
    # cash is back to where it started
    assert _leaf(cash_gl.id) == before


def test_all_journal_entries_remain_balanced_through_the_whole_cycle(appctx):
    _enable()
    emp, p = _finalized_period()
    c = _client("admin")
    _pay(c, p)
    payment = p.payments.filter_by(is_reversed=False).one()
    c.post(f"/payroll/payments/{payment.id}/reverse", follow_redirects=True)
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)

    for e in JournalEntry.query.all():
        assert e.total_debit == e.total_credit, f"JE#{e.id} unbalanced"


# ══════════════════════════════════════════════════════════════════════════
# BUG #2 — cancelled periods do not block their dates
# ══════════════════════════════════════════════════════════════════════════

def test_cancelled_period_frees_its_dates_at_the_application_level(appctx):
    """The route-level clash check -- the one a user actually hits -- no longer
    treats a cancelled period as occupying its dates. A payroll period table
    still carries a database-level UNIQUE(start_date, end_date) that has no
    concept of status (dropping it is blocking DDL -- see CLAUDE.md), so this
    is verified directly against the check the route performs, not by
    inserting the identical row twice."""
    _enable()
    emp, p = _finalized_period(name="March 2026", start=date(2026, 3, 1),
                               end=date(2026, 3, 31))
    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    db.session.expire_all()

    clash = (PayrollPeriod.query
             .filter_by(start_date=date(2026, 3, 1), end_date=date(2026, 3, 31))
             .filter(PayrollPeriod.status != "Cancelled")
             .first())
    assert clash is None, "a cancelled period must not appear as a clash"


def test_cancelled_period_exact_dates_still_hit_the_db_constraint_cleanly(appctx):
    """Known, reported limitation: the underlying UNIQUE(start_date, end_date)
    still exists (see the module docstring), so inserting the *exact* dates of
    a still-present cancelled row is refused -- but cleanly, with a flash
    message, not a stack trace."""
    _enable()
    emp, p = _finalized_period(name="March 2026", start=date(2026, 3, 1),
                               end=date(2026, 3, 31))
    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)

    r = c.post("/payroll/periods/new", data={
        "name": "March 2026 Rerun", "start_date": "2026-03-01",
        "end_date": "2026-03-31"}, follow_redirects=True)

    assert PayrollPeriod.query.filter_by(name="March 2026 Rerun").count() == 0
    assert r.status_code == 200
    msg = " ".join(_flash_texts(r)).lower()
    assert "already exist" in msg or "cancelled" in msg


def test_cancelled_period_frees_the_month_for_a_new_date_range(appctx):
    """The scenario that actually matters in practice: a payroll run for a
    month is cancelled, and the business re-runs it -- typically as a fresh
    period, which is not required to reuse the exact old row's dates."""
    _enable()
    emp, p = _finalized_period(name="March 2026", start=date(2026, 3, 1),
                               end=date(2026, 3, 31))
    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)

    r = c.post("/payroll/periods/new", data={
        "name": "March 2026 Rerun", "start_date": "2026-03-02",
        "end_date": "2026-03-31"}, follow_redirects=True)

    new_period = PayrollPeriod.query.filter_by(name="March 2026 Rerun").first()
    assert new_period is not None, " ".join(_flash_texts(r))
    assert new_period.status == "Draft"


def test_active_period_overlap_is_still_refused(appctx):
    _enable()
    c = _client("admin")
    c.post("/payroll/periods/new", data={
        "name": "First", "start_date": "2026-07-01", "end_date": "2026-07-31"},
        follow_redirects=True)

    r = c.post("/payroll/periods/new", data={
        "name": "Second", "start_date": "2026-07-01", "end_date": "2026-07-31"},
        follow_redirects=True)

    assert PayrollPeriod.query.filter_by(name="Second").count() == 0
    assert "already covered" in " ".join(_flash_texts(r))


def test_finalized_period_overlap_is_still_refused(appctx):
    _enable()
    emp, p = _finalized_period(name="August 2026", start=date(2026, 8, 1),
                               end=date(2026, 8, 31))
    c = _client("admin")

    r = c.post("/payroll/periods/new", data={
        "name": "August Again", "start_date": "2026-08-01",
        "end_date": "2026-08-31"}, follow_redirects=True)

    assert PayrollPeriod.query.filter_by(name="August Again").count() == 0
    assert "already covered" in " ".join(_flash_texts(r))


def test_paid_period_overlap_is_still_refused(appctx):
    _enable()
    emp, p = _finalized_period(name="September 2026", start=date(2026, 9, 1),
                               end=date(2026, 9, 30))
    c = _client("admin")
    _pay(c, p)

    r = c.post("/payroll/periods/new", data={
        "name": "September Again", "start_date": "2026-09-01",
        "end_date": "2026-09-30"}, follow_redirects=True)

    assert PayrollPeriod.query.filter_by(name="September Again").count() == 0
    assert "already covered" in " ".join(_flash_texts(r))


def test_cancelled_period_remains_visible_in_history(appctx):
    """The old cancelled period is never deleted -- it just stops blocking a
    NEW date range at the application-check level (the exact old row's dates
    still hit the DB constraint; see test_payroll_cancel_safety's other tests
    for that documented limitation)."""
    _enable()
    emp, p = _finalized_period(name="October 2026", start=date(2026, 10, 1),
                               end=date(2026, 10, 31))
    old_id = p.id
    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)

    c.post("/payroll/periods/new", data={
        "name": "October Rerun", "start_date": "2026-10-02",
        "end_date": "2026-10-31"}, follow_redirects=True)

    # the original cancelled period is still in the database, findable, and
    # its journal entries (original + reversal) are intact
    still_there = db.session.get(PayrollPeriod, old_id)
    assert still_there is not None
    assert still_there.status == "Cancelled"
    assert JournalEntry.query.filter_by(source_type="payroll",
                                        source_id=old_id).count() == 2

    body = c.get("/payroll/").get_data(as_text=True)
    assert "October 2026" in body
    assert "October Rerun" in body


# ── permissions on the new guard ──────────────────────────────────────────

def test_manager_still_cannot_cancel_payroll(appctx):
    """The new payment guard must not change who may cancel -- payroll.post
    stays admin-only."""
    _enable()
    emp, p = _finalized_period()
    db.session.add(User(name="M", email="mgr@cancelsafety.com",
                        password=pwd_context.hash("secret123"),
                        verified=True, role="manager"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": "mgr@cancelsafety.com",
                            "password": "secret123"})

    r = c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=False)
    assert r.status_code == 302

    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status == "Finalized"


def test_staff_cannot_cancel_payroll(appctx):
    _enable()
    emp, p = _finalized_period()
    db.session.add(User(name="S", email="staff@cancelsafety.com",
                        password=pwd_context.hash("secret123"),
                        verified=True, role="staff"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": "staff@cancelsafety.com",
                            "password": "secret123"})

    r = c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=False)
    assert r.status_code == 302

    db.session.expire_all()
    assert db.session.get(PayrollPeriod, p.id).status == "Finalized"
