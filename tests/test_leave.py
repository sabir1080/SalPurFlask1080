"""Phase 4: leave management, and what payroll makes of it.

The tests that matter most are the ones about not paying for the same day
twice and not silently rewriting a payslip that has already been posted. A
leave bug does not crash — it quietly overpays someone, or quietly docks them.

Every figure is checked against arithmetic worked out by hand.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import (app as flask_app, db, User, pwd_context, Account,
                 seed_chart_of_accounts, seed_fiscal_year)
from salpurflask.models.attendance import Attendance
from salpurflask.models.hr import Employee
from salpurflask.models.leave import (LeaveType, LeaveAllocation, LeaveRequest,
                                      leave_facts, remaining_days, used_days,
                                      working_days, seed_leave_types,
                                      allocation_for)
from salpurflask.models.payroll import (SalaryComponent, SalaryStructure,
                                        SalaryStructureLine, PayrollPeriod,
                                        seed_default_components)
from salpurflask.services import payroll_engine as engine
from salpurflask.services import payroll_accounting as acct
from salpurflask.services.feature_flags import module_enabled, set_module
from salpurflask.services.hr_permissions import has_permission


# ── helpers ───────────────────────────────────────────────────────────────────

def _enable(hr=True, leave=True, payroll=False, attendance=False):
    set_module("module_hr", hr, updated_by="test")
    set_module("module_leave", leave, updated_by="test")
    set_module("module_payroll", payroll, updated_by="test")
    set_module("module_attendance", attendance, updated_by="test")


def _client(role="admin", email=None):
    email = email or f"{role}@leave.com"
    db.session.add(User(name=role.title(), email=email,
                        password=pwd_context.hash("secret123"),
                        verified=True, role=role))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _employee(code="E-1", name="Worker"):
    e = Employee(code=code, name=name, joining_date=date(2025, 1, 1), active=True)
    db.session.add(e)
    db.session.commit()
    return e


def _type(code="ANNUAL"):
    seed_leave_types()
    return LeaveType.query.filter_by(code=code).one()


def _allocate(emp, code="ANNUAL", year=2026, days=24):
    lt = _type(code)
    a = LeaveAllocation(employee_id=emp.id, leave_type_id=lt.id, year=year,
                        days=Decimal(str(days)))
    db.session.add(a)
    db.session.commit()
    return a


def _request(emp, start, end, code="ANNUAL", status="Pending", portion="full"):
    lt = _type(code)
    r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                     start_date=start, end_date=end, day_portion=portion,
                     status=status)
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()
    return r


def _form(emp, start, end, code="ANNUAL", **over):
    data = {"employee_id": str(emp.id), "leave_type_id": str(_type(code).id),
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "day_portion": "full", "reason": "test"}
    data.update({k: str(v) for k, v in over.items()})
    return data


# ── module gating ─────────────────────────────────────────────────────────────

def test_leave_is_off_on_a_new_install(appctx):
    assert module_enabled("module_leave") is False


def test_leave_routes_are_refused_while_the_module_is_off(appctx):
    _enable(leave=False)
    c = _client("admin")
    for path in ("/leave/", "/leave/requests", "/leave/allocations", "/leave/types"):
        assert c.get(path, follow_redirects=False).status_code == 302, path


def test_leave_routes_open_once_the_module_is_on(appctx):
    _enable()
    c = _client("admin")
    for path in ("/leave/", "/leave/requests", "/leave/allocations", "/leave/types"):
        assert c.get(path).status_code == 200, path


def test_leave_follows_hr_off(appctx):
    _enable(hr=False, leave=True)
    assert module_enabled("module_leave") is False


def test_switching_leave_off_keeps_its_records(appctx):
    _enable()
    emp = _employee()
    _request(emp, date(2026, 3, 2), date(2026, 3, 3))
    set_module("module_leave", False, updated_by="test")
    assert LeaveRequest.query.count() == 1
    set_module("module_leave", True, updated_by="test")
    assert LeaveRequest.query.count() == 1


# ── 1. leave types ────────────────────────────────────────────────────────────

def test_the_standard_leave_types_are_seeded_once(appctx):
    assert seed_leave_types() > 0
    assert seed_leave_types() == 0
    codes = {t.code for t in LeaveType.query.all()}
    for expected in ("ANNUAL", "CASUAL", "SICK", "UNPAID"):
        assert expected in codes
    assert LeaveType.query.filter_by(code="UNPAID").one().paid is False


def test_creating_a_leave_type_through_the_form(appctx):
    _enable()
    c = _client("admin")
    c.post("/leave/types", data={"code": "HAJJ", "name": "Hajj Leave",
                                 "paid": "1", "requires_allocation": "1",
                                 "max_days_per_year": "30"},
           follow_redirects=True)
    lt = LeaveType.query.filter_by(code="HAJJ").one()
    assert lt.paid is True and float(lt.max_days_per_year) == 30


def test_a_duplicate_type_code_is_refused(appctx):
    _enable()
    c = _client("admin")
    for _ in range(2):
        c.post("/leave/types", data={"code": "DUP", "name": "Dup"},
               follow_redirects=True)
    assert LeaveType.query.filter_by(code="DUP").count() == 1


def test_a_standard_type_cannot_be_deleted(appctx):
    _enable()
    lt = _type("ANNUAL")
    c = _client("admin")
    c.post(f"/leave/types/{lt.id}/delete", follow_redirects=True)
    assert LeaveType.query.filter_by(code="ANNUAL").count() == 1


# ── 2. allocation ─────────────────────────────────────────────────────────────

def test_creating_an_allocation(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/leave/allocations",
           data={"employee_id": str(emp.id), "leave_type_id": str(_type().id),
                 "year": "2026", "days": "24"}, follow_redirects=True)
    a = LeaveAllocation.query.one()
    assert float(a.days) == 24
    assert float(a.remaining_days) == 24


def test_one_allocation_per_employee_type_and_year(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    with pytest.raises(Exception):
        db.session.add(LeaveAllocation(employee_id=emp.id,
                                       leave_type_id=_type().id,
                                       year=2026, days=5))
        db.session.commit()
    db.session.rollback()


def test_allocating_again_updates_rather_than_duplicates(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    # Both values stay under Annual Leave's seeded 24-day maximum, so the only
    # thing under test is that the second save updates rather than inserts.
    for days in ("18", "22"):
        c.post("/leave/allocations",
               data={"employee_id": str(emp.id), "leave_type_id": str(_type().id),
                     "year": "2026", "days": days}, follow_redirects=True)
    assert LeaveAllocation.query.count() == 1
    assert float(LeaveAllocation.query.one().days) == 22


def test_allocation_beyond_the_type_maximum_is_refused(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/leave/allocations",
           data={"employee_id": str(emp.id), "leave_type_id": str(_type().id),
                 "year": "2026", "days": "500"}, follow_redirects=True)
    assert LeaveAllocation.query.count() == 0


# ── 3. day counting ───────────────────────────────────────────────────────────

def test_working_days_skip_weekends(appctx):
    # Mon 2 Mar 2026 -> Fri 6 Mar = 5 working days
    assert working_days(date(2026, 3, 2), date(2026, 3, 6)) == Decimal("5")
    # Mon -> Sun spans a weekend: still 5
    assert working_days(date(2026, 3, 2), date(2026, 3, 8)) == Decimal("5")
    # a weekend alone is nothing
    assert working_days(date(2026, 3, 7), date(2026, 3, 8)) == Decimal("0")


def test_a_half_day_counts_as_half(appctx):
    assert working_days(date(2026, 3, 3), date(2026, 3, 3), "half") == Decimal("0.5")
    # half only means anything on a single date
    assert working_days(date(2026, 3, 2), date(2026, 3, 6), "half") == Decimal("5")


def test_an_end_before_the_start_is_zero(appctx):
    assert working_days(date(2026, 3, 6), date(2026, 3, 2)) == Decimal("0")


def test_the_day_count_comes_from_the_dates_not_the_form(appctx):
    """A user typing '1 day' over a fortnight must not get thirteen free ones."""
    _enable()
    emp = _employee()
    _allocate(emp)
    c = _client("admin")
    c.post("/leave/requests/new",
           data=_form(emp, date(2026, 3, 2), date(2026, 3, 13), days="1"),
           follow_redirects=True)
    assert float(LeaveRequest.query.one().days) == 10       # two full weeks


# ── 4. request lifecycle ──────────────────────────────────────────────────────

def test_submitting_a_leave_request(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    c = _client("admin")
    c.post("/leave/requests/new",
           data=_form(emp, date(2026, 3, 2), date(2026, 3, 4), submit_now="1"),
           follow_redirects=True)
    r = LeaveRequest.query.one()
    assert r.status == "Pending"
    assert float(r.days) == 3


def test_a_request_can_be_saved_as_a_draft_then_submitted(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    c = _client("admin")
    c.post("/leave/requests/new", data=_form(emp, date(2026, 3, 2), date(2026, 3, 3)),
           follow_redirects=True)
    r = LeaveRequest.query.one()
    assert r.status == "Draft"
    c.post(f"/leave/requests/{r.id}/submit", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Pending"


def test_approving_a_request(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 4))
    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Approved"
    assert r.decided_at is not None


def test_rejecting_a_request(appctx):
    _enable()
    emp = _employee()
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 4))
    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/reject", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Rejected"


def test_cancelling_an_approved_request(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 4), status="Approved")
    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/cancel", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Cancelled"


def test_only_a_pending_request_can_be_approved(appctx):
    _enable()
    emp = _employee()
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 4), status="Draft")
    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Draft"


# ── 5. balances ───────────────────────────────────────────────────────────────

def test_an_approved_request_reduces_the_balance(appctx):
    _enable()
    emp = _employee()
    _allocate(emp, days=24)
    assert remaining_days(emp.id, _type().id, 2026) == Decimal("24")

    _request(emp, date(2026, 3, 2), date(2026, 3, 6), status="Approved")   # 5
    assert used_days(emp.id, _type().id, 2026) == Decimal("5")
    assert remaining_days(emp.id, _type().id, 2026) == Decimal("19")


def test_a_pending_request_does_not_reduce_the_balance(appctx):
    """Only an approved request consumes allocation."""
    _enable()
    emp = _employee()
    _allocate(emp, days=24)
    _request(emp, date(2026, 3, 2), date(2026, 3, 6), status="Pending")
    assert remaining_days(emp.id, _type().id, 2026) == Decimal("24")


def test_cancelling_restores_the_balance(appctx):
    """No counter is touched -- the days come back because used_days counts
    approved requests only."""
    _enable()
    emp = _employee()
    _allocate(emp, days=24)
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 6), status="Approved")
    assert remaining_days(emp.id, _type().id, 2026) == Decimal("19")

    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/cancel", follow_redirects=True)
    assert remaining_days(emp.id, _type().id, 2026) == Decimal("24")


def test_approval_beyond_the_remaining_balance_is_refused(appctx):
    _enable()
    emp = _employee()
    _allocate(emp, days=3)
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 6))     # 5 days
    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Pending"


def test_a_type_that_needs_no_allocation_is_never_short(appctx):
    """Unpaid leave is a decision, not an entitlement."""
    _enable()
    emp = _employee()
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 6), code="UNPAID")
    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Approved"


# ── 6. overlap protection ─────────────────────────────────────────────────────

def test_overlapping_leave_is_refused(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    _request(emp, date(2026, 3, 2), date(2026, 3, 6), status="Approved")
    c = _client("admin")
    c.post("/leave/requests/new", data=_form(emp, date(2026, 3, 4), date(2026, 3, 10)),
           follow_redirects=True)
    assert LeaveRequest.query.count() == 1


def test_a_cancelled_request_frees_its_dates_again(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 6), status="Cancelled")
    c = _client("admin")
    c.post("/leave/requests/new", data=_form(emp, date(2026, 3, 2), date(2026, 3, 6)),
           follow_redirects=True)
    assert LeaveRequest.query.count() == 2


def test_two_employees_may_take_the_same_dates(appctx):
    _enable()
    a, b = _employee("E-1", "One"), _employee("E-2", "Two")
    _allocate(a)
    _allocate(b)
    c = _client("admin")
    for emp in (a, b):
        c.post("/leave/requests/new",
               data=_form(emp, date(2026, 3, 2), date(2026, 3, 4)),
               follow_redirects=True)
    assert LeaveRequest.query.count() == 2


# ── 7. permissions ────────────────────────────────────────────────────────────

def test_leave_permissions_match_the_roles_they_promise(appctx):
    with flask_app.test_request_context():
        staff = User(name="S", email="s@l.com", password="x", verified=True, role="staff")
        manager = User(name="M", email="m@l.com", password="x", verified=True, role="manager")
        admin = User(name="A", email="a@l.com", password="x", verified=True, role="admin")
        # Staff may raise a request but never decide one.
        assert has_permission("leave.create", staff) is True
        assert has_permission("leave.approve", staff) is False
        assert has_permission("leave.approve", manager) is True
        assert has_permission("leave.configure", manager) is False
        assert has_permission("leave.configure", admin) is True


def test_staff_cannot_approve_leave(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 4))
    c = _client("staff")
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Pending"


def test_staff_cannot_configure_leave_types(appctx):
    _enable()
    c = _client("staff")
    c.get("/leave/types")                 # seeds the standard types
    before = LeaveType.query.count()
    c.post("/leave/types", data={"code": "SNEAK", "name": "Sneak"},
           follow_redirects=True)
    assert LeaveType.query.count() == before
    assert LeaveType.query.filter_by(code="SNEAK").count() == 0


# ── 8. the payroll-facing contract ────────────────────────────────────────────

def test_leave_facts_split_paid_from_unpaid(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    _request(emp, date(2026, 3, 2), date(2026, 3, 4), status="Approved")            # 3 paid
    _request(emp, date(2026, 3, 9), date(2026, 3, 10), code="UNPAID",
             status="Approved")                                                     # 2 unpaid

    f = leave_facts(emp.id, date(2026, 3, 1), date(2026, 3, 31))
    assert f["paid_days"] == 3.0
    assert f["unpaid_days"] == 2.0
    assert f["total_days"] == 5.0


def test_leave_facts_ignore_requests_that_are_not_approved(appctx):
    _enable()
    emp = _employee()
    # One at a time: overlapping dates are fine here because these statuses do
    # not block, and none of them should reach payroll.
    for i, status in enumerate(("Draft", "Pending", "Rejected", "Cancelled")):
        start = date(2026, 3, 2) + timedelta(days=i * 7)
        _request(emp, start, start + timedelta(days=1), status=status)
    f = leave_facts(emp.id, date(2026, 3, 1), date(2026, 3, 31))
    assert f["total_days"] == 0.0


def test_leave_facts_mention_no_money(appctx):
    """The module reports days; payroll decides what a day is worth."""
    _enable()
    emp = _employee()
    _allocate(emp)
    _request(emp, date(2026, 3, 2), date(2026, 3, 4), status="Approved")
    f = leave_facts(emp.id, date(2026, 3, 1), date(2026, 3, 31))
    assert not any("salary" in k or "amount" in k or "rate" in k for k in f)


# ── 9. payroll integration ────────────────────────────────────────────────────

def _payroll_setup(basic=30000):
    """A period ready to calculate, with the chart in place."""
    if Account.query.count() == 0:
        seed_chart_of_accounts()
        db.session.commit()
    seed_fiscal_year(date(2026, 3, 15))
    seed_fiscal_year(date.today())
    db.session.commit()
    seed_default_components()

    emp = _employee()
    s = SalaryStructure(employee_id=emp.id, active=True)
    db.session.add(s)
    db.session.flush()
    db.session.add(SalaryStructureLine(
        structure_id=s.id,
        component_id=SalaryComponent.query.filter_by(code="BASIC").one().id,
        amount=Decimal(str(basic))))
    db.session.commit()

    p = PayrollPeriod(name="March 2026", start_date=date(2026, 3, 1),
                      end_date=date(2026, 3, 31), status="Draft")
    db.session.add(p)
    db.session.commit()
    return emp, p


def test_approved_paid_leave_does_not_reduce_salary(appctx):
    """31 days in March; 31000/31 = 1000 a day. Paid leave costs nothing."""
    _enable(payroll=True)
    emp, p = _payroll_setup(basic=31000)
    _allocate(emp)
    _request(emp, date(2026, 3, 2), date(2026, 3, 6), status="Approved")   # 5 paid

    r = engine.calculate(emp, p)
    assert r["paid_leave_days"] == 5.0
    assert r["gross_salary"] == Decimal("31000.00")


def test_approved_unpaid_leave_reduces_salary(appctx):
    """Five unpaid days at 1000 a day costs 5000."""
    _enable(payroll=True)
    emp, p = _payroll_setup(basic=31000)
    _request(emp, date(2026, 3, 2), date(2026, 3, 6), code="UNPAID",
             status="Approved")

    r = engine.calculate(emp, p)
    assert r["unpaid_leave_days"] == 5.0
    assert r["gross_salary"] == Decimal("26000.00")


def test_pending_unpaid_leave_does_not_reduce_salary(appctx):
    """Only an approved decision costs anybody anything."""
    _enable(payroll=True)
    emp, p = _payroll_setup(basic=31000)
    _request(emp, date(2026, 3, 2), date(2026, 3, 6), code="UNPAID",
             status="Pending")
    assert engine.calculate(emp, p)["gross_salary"] == Decimal("31000.00")


def test_leave_is_ignored_while_the_leave_module_is_off(appctx):
    _enable(payroll=True, leave=True)
    emp, p = _payroll_setup(basic=31000)
    _request(emp, date(2026, 3, 2), date(2026, 3, 6), code="UNPAID",
             status="Approved")
    assert engine.calculate(emp, p)["gross_salary"] == Decimal("26000.00")

    set_module("module_leave", False, updated_by="test")
    assert engine.calculate(emp, p)["gross_salary"] == Decimal("31000.00")


def test_absence_and_unpaid_leave_are_not_double_counted(appctx):
    """The same day marked absent AND covered by unpaid leave is one day off.

    Attendance would normally record it as Leave, but the engine caps the two
    at the days actually in the period so a mismarked sheet cannot deduct more
    than a whole month.
    """
    _enable(payroll=True, attendance=True)
    emp, p = _payroll_setup(basic=31000)
    # 31 unpaid leave days and 31 absent days: still only 31 days in March.
    _request(emp, date(2026, 3, 1), date(2026, 3, 31), code="UNPAID",
             status="Approved")
    for i in range(31):
        db.session.add(Attendance(employee_id=emp.id,
                                  date=date(2026, 3, 1) + timedelta(days=i),
                                  status="Absent"))
    db.session.commit()

    r = engine.calculate(emp, p)
    assert r["payable_days"] == 0.0
    assert r["gross_salary"] == Decimal("0.00")     # never negative


def test_attendance_leave_status_is_still_not_deducted(appctx):
    """Phase 2 behaviour, unchanged: attendance 'Leave' is not an absence."""
    _enable(payroll=True, attendance=True)
    emp, p = _payroll_setup(basic=31000)
    for i in range(5):
        db.session.add(Attendance(employee_id=emp.id,
                                  date=date(2026, 3, 2) + timedelta(days=i),
                                  status="Leave"))
    db.session.commit()
    assert engine.calculate(emp, p)["gross_salary"] == Decimal("31000.00")


# ── 10. leave spanning periods ────────────────────────────────────────────────

def test_leave_spanning_two_months_is_split_between_them(appctx):
    """28 Aug -> 3 Sep is charged to August and September once each, never twice."""
    _enable(payroll=True)
    emp = _employee()
    _request(emp, date(2026, 8, 28), date(2026, 9, 3), code="UNPAID",
             status="Approved")

    aug = leave_facts(emp.id, date(2026, 8, 1), date(2026, 8, 31))
    sep = leave_facts(emp.id, date(2026, 9, 1), date(2026, 9, 30))
    whole = leave_facts(emp.id, date(2026, 8, 1), date(2026, 9, 30))

    # 28 Aug is a Friday and 31 Aug a Monday, so August has two working days;
    # 29-30 Aug is a weekend. September gets the 1st, 2nd and 3rd.
    assert aug["unpaid_days"] == 2.0
    assert sep["unpaid_days"] == 3.0
    assert aug["unpaid_days"] + sep["unpaid_days"] == whole["unpaid_days"]


def test_leave_spanning_two_payroll_periods_deducts_once_in_each(appctx):
    _enable(payroll=True)
    emp, _p = _payroll_setup(basic=31000)
    _request(emp, date(2026, 8, 28), date(2026, 9, 3), code="UNPAID",
             status="Approved")

    aug = PayrollPeriod(name="Aug 2026", start_date=date(2026, 8, 1),
                        end_date=date(2026, 8, 31), status="Draft")
    sep = PayrollPeriod(name="Sep 2026", start_date=date(2026, 9, 1),
                        end_date=date(2026, 9, 30), status="Draft")
    db.session.add_all([aug, sep])
    db.session.commit()

    r_aug = engine.calculate(emp, aug)
    r_sep = engine.calculate(emp, sep)
    assert r_aug["unpaid_leave_days"] == 2.0
    assert r_sep["unpaid_leave_days"] == 3.0
    # The whole leave is five working days and neither month claims the other's.
    assert (r_aug["unpaid_leave_days"] + r_sep["unpaid_leave_days"]) == 5.0


def test_leave_spanning_new_year_draws_from_each_year(appctx):
    _enable()
    emp = _employee()
    _allocate(emp, year=2026, days=24)
    _allocate(emp, year=2027, days=24)
    # 30 Dec 2026 -> 5 Jan 2027
    _request(emp, date(2026, 12, 30), date(2027, 1, 5), status="Approved")

    u26 = used_days(emp.id, _type().id, 2026)
    u27 = used_days(emp.id, _type().id, 2027)
    assert u26 > 0 and u27 > 0
    assert u26 + u27 == working_days(date(2026, 12, 30), date(2027, 1, 5))


# ── 11. finalized payroll protection ──────────────────────────────────────────

def test_leave_cannot_be_approved_into_a_finalized_period(appctx):
    """Approving would change a payslip that is already posted and possibly paid."""
    _enable(payroll=True)
    emp, p = _payroll_setup(basic=31000)
    engine.process_period(p)
    acct.seed_payroll_accounts()
    acct.post_payroll_period(p)
    p.status = "Finalized"
    db.session.commit()

    r = _request(emp, date(2026, 3, 2), date(2026, 3, 6), code="UNPAID")
    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)

    db.session.refresh(r)
    assert r.status == "Pending", "leave was approved into a finalized period"


def test_approved_leave_cannot_be_cancelled_out_of_a_finalized_period(appctx):
    _enable(payroll=True)
    emp, p = _payroll_setup(basic=31000)
    _allocate(emp)
    r = _request(emp, date(2026, 3, 2), date(2026, 3, 6), status="Approved")

    engine.process_period(p)
    acct.seed_payroll_accounts()
    acct.post_payroll_period(p)
    p.status = "Finalized"
    db.session.commit()

    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/cancel", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Approved"


def test_a_finalized_payslip_is_not_silently_restated(appctx):
    """The stored payslip keeps the figures it was finalised with."""
    _enable(payroll=True)
    emp, p = _payroll_setup(basic=31000)
    engine.process_period(p)
    db.session.commit()
    before = p.entries.one().gross_salary
    p.status = "Finalized"
    db.session.commit()

    # A leave request approved directly in the database (bypassing the route
    # guard) still must not rewrite what was already stored.
    _request(emp, date(2026, 3, 9), date(2026, 3, 13), code="UNPAID",
             status="Approved")
    assert p.entries.one().gross_salary == before


def test_leave_in_a_later_period_is_unaffected_by_an_earlier_finalized_one(appctx):
    _enable(payroll=True)
    emp, march = _payroll_setup(basic=31000)
    engine.process_period(march)
    march.status = "Finalized"
    db.session.commit()

    april = PayrollPeriod(name="April 2026", start_date=date(2026, 4, 1),
                          end_date=date(2026, 4, 30), status="Draft")
    db.session.add(april)
    db.session.commit()

    r = _request(emp, date(2026, 4, 6), date(2026, 4, 10), code="UNPAID")
    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Approved"


# ── 12. accounting untouched ──────────────────────────────────────────────────

def test_approving_leave_posts_nothing_to_the_ledger(appctx):
    """Leave reaches the accounts only through payroll."""
    from app import JournalEntry
    _enable(payroll=True)
    emp = _employee()
    _allocate(emp)
    before = JournalEntry.query.count()

    r = _request(emp, date(2026, 3, 2), date(2026, 3, 6))
    c = _client("admin")
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)

    assert JournalEntry.query.count() == before


# ── 13. isolation ─────────────────────────────────────────────────────────────

def test_leave_tables_are_isolated_from_the_core_schema(appctx):
    insp = db.inspect(db.engine)
    for t in ("hr_leave_type", "hr_leave_allocation", "hr_leave_request"):
        assert t in insp.get_table_names()

    for core in ("item", "sale", "purchase", "journal_entry", "hr_employee",
                 "hr_attendance", "hr_payroll_entry"):
        cols = {c["name"] for c in insp.get_columns(core)}
        assert not any("leave_type" in c or "leave_request" in c for c in cols), \
            f"{core} gained a leave column"


def test_deleting_an_employee_takes_their_leave_with_them(appctx):
    _enable()
    emp = _employee()
    _allocate(emp)
    _request(emp, date(2026, 3, 2), date(2026, 3, 4))
    db.session.delete(emp)
    db.session.commit()
    assert LeaveRequest.query.count() == 0
    assert LeaveAllocation.query.count() == 0
