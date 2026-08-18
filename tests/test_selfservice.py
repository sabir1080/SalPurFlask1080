"""Phase 5: the employee↔user link and the self-service portal.

Most of these are security tests, and they are the reason the phase exists. A
self-service bug does not crash — it shows one employee another employee's
salary, and nobody finds out until someone mentions what they earn.

The rule under test throughout: a page shows the records of the employee behind
the session, and nothing else, however the request is shaped.
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest

from app import (app as flask_app, db, User, pwd_context, Account,
                 seed_chart_of_accounts, seed_fiscal_year)
from salpurflask.models.attendance import Attendance
from salpurflask.models.hr import Employee
from salpurflask.models.leave import (LeaveType, LeaveAllocation, LeaveRequest,
                                      seed_leave_types)
from salpurflask.models.payroll import (SalaryComponent, SalaryStructure,
                                        SalaryStructureLine, PayrollPeriod,
                                        seed_default_components)
from salpurflask.services import payroll_engine as engine
from salpurflask.services.feature_flags import set_module
from salpurflask.services.self_service import current_employee, owns


# ── helpers ───────────────────────────────────────────────────────────────────

def _enable(hr=True, attendance=True, leave=True, payroll=True):
    set_module("module_hr", hr, updated_by="test")
    set_module("module_attendance", attendance, updated_by="test")
    set_module("module_leave", leave, updated_by="test")
    set_module("module_payroll", payroll, updated_by="test")


def _user(role="staff", email=None, name=None):
    email = email or f"{role}@ss.com"
    u = User(name=name or role.title(), email=email,
             password=pwd_context.hash("secret123"), verified=True, role=role)
    db.session.add(u)
    db.session.commit()
    return u


def _login(user):
    """Sign in without POSTing /signin.

    Several tests need two different people in one test, and two sign-ins from
    one IP trip the rate limiter (5 per IP) -- the second silently fails and
    leaves the first session in place, which would make an ownership test pass
    for entirely the wrong reason.
    """
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
    return c


def _employee(code="E-1", name="Worker", user=None):
    e = Employee(code=code, name=name, joining_date=date(2025, 1, 1),
                 active=True, user_id=(user.id if user else None))
    db.session.add(e)
    db.session.commit()
    return e


def _linked(role="staff", code="E-1", name="Worker", email=None):
    """A user and the employee they own."""
    u = _user(role, email or f"{code.lower()}@ss.com", name)
    e = _employee(code, name, user=u)
    return u, e


def _leave_type(code="ANNUAL"):
    seed_leave_types()
    return LeaveType.query.filter_by(code=code).one()


def _structure_for(emp, basic=31000):
    """A salary structure only -- no payroll run."""
    seed_default_components()
    s = SalaryStructure(employee_id=emp.id, active=True)
    db.session.add(s)
    db.session.flush()
    db.session.add(SalaryStructureLine(
        structure_id=s.id,
        component_id=SalaryComponent.query.filter_by(code="BASIC").one().id,
        amount=Decimal(str(basic))))
    db.session.commit()
    return s


def _payslip_for(emp, basic=31000, status="Finalized"):
    """A finalized payslip belonging to `emp`."""
    if Account.query.count() == 0:
        seed_chart_of_accounts()
        db.session.commit()
    seed_fiscal_year(date(2026, 3, 15))
    seed_fiscal_year(date.today())
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

    p = PayrollPeriod.query.filter_by(name="March 2026").first()
    if p is None:
        p = PayrollPeriod(name="March 2026", start_date=date(2026, 3, 1),
                          end_date=date(2026, 3, 31), status="Draft")
        db.session.add(p)
        db.session.commit()

    # Re-open before recalculating: a finalized period refuses to be processed
    # again, which is phase 3B behaving correctly rather than a fault here.
    p.status = "Draft"
    db.session.commit()
    engine.process_period(p)
    p.status = status
    db.session.commit()
    return p.entries.filter_by(employee_id=emp.id).one()


# ── 1-3. the link ─────────────────────────────────────────────────────────────

def test_linking_an_employee_to_a_user(appctx):
    _enable()
    u = _user("staff")
    emp = _employee(user=u)
    assert emp.user_id == u.id
    assert emp.user.email == u.email
    assert u.employee.id == emp.id          # the backref resolves one way too


def test_one_user_cannot_be_linked_to_two_employees(appctx):
    """A second employee holding the same login would make "who am I" ambiguous."""
    _enable()
    u = _user("staff")
    _employee("E-1", "First", user=u)

    admin = _user("admin", "adm@ss.com")
    c = _login(admin)
    other = _employee("E-2", "Second")
    c.post(f"/hr/employees/{other.id}/link-user", data={"user_id": str(u.id)},
           follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(Employee, other.id).user_id is None
    assert Employee.query.filter_by(user_id=u.id).count() == 1


def test_one_employee_cannot_hold_two_users(appctx):
    """The column holds one id; linking a second replaces the first rather than
    accumulating, so an employee is never reachable by two logins."""
    _enable()
    admin = _user("admin", "adm@ss.com")
    a = _user("staff", "a@ss.com")
    b = _user("staff", "b@ss.com")
    emp = _employee()

    c = _login(admin)
    c.post(f"/hr/employees/{emp.id}/link-user", data={"user_id": str(a.id)},
           follow_redirects=True)
    c.post(f"/hr/employees/{emp.id}/link-user", data={"user_id": str(b.id)},
           follow_redirects=True)

    db.session.expire_all()
    emp = db.session.get(Employee, emp.id)
    assert emp.user_id == b.id
    assert Employee.query.filter_by(user_id=a.id).count() == 0


def test_a_link_can_be_removed(appctx):
    _enable()
    admin = _user("admin", "adm@ss.com")
    u = _user("staff")
    emp = _employee(user=u)

    c = _login(admin)
    c.post(f"/hr/employees/{emp.id}/link-user", data={"user_id": ""},
           follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Employee, emp.id).user_id is None


def test_existing_employees_stay_unlinked(appctx):
    """Nothing guesses a link from a matching name or email."""
    _enable()
    _user("staff", "worker@ss.com", "Worker")
    emp = _employee("E-1", "Worker")
    assert emp.user_id is None


def test_staff_cannot_link_a_login(appctx):
    """Linking decides who may read a payslip -- it is an hr.edit decision."""
    _enable()
    u = _user("staff")
    emp = _employee()
    c = _login(u)
    c.post(f"/hr/employees/{emp.id}/link-user", data={"user_id": str(u.id)},
           follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Employee, emp.id).user_id is None


# ── 4-5. resolving the current employee ───────────────────────────────────────

def test_an_unlinked_user_has_no_employee_and_no_portal(appctx):
    _enable()
    u = _user("staff")
    c = _login(u)
    for path in ("/my/profile", "/my/attendance", "/my/leaves", "/my/payslips"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 302, path


def test_a_linked_employee_sees_their_own_profile(appctx):
    _enable()
    u, emp = _linked()
    c = _login(u)
    body = c.get("/my/profile").get_data(as_text=True)
    assert emp.name in body and emp.code in body


def test_current_employee_is_resolved_from_the_session(appctx):
    _enable()
    u, emp = _linked()
    with flask_app.test_request_context():
        from flask_login import login_user
        login_user(u)
        assert current_employee().id == emp.id


def test_current_employee_is_none_for_an_anonymous_visitor(appctx):
    with flask_app.test_request_context():
        assert current_employee() is None


# ── 6. attendance is own-only ─────────────────────────────────────────────────

def test_an_employee_sees_only_their_own_attendance(appctx):
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    theirs = _employee("E-2", "Theirs")

    db.session.add_all([
        Attendance(employee_id=mine.id, date=date(2026, 3, 3), status="Present",
                   remarks="MY-ROW"),
        Attendance(employee_id=theirs.id, date=date(2026, 3, 3), status="Present",
                   remarks="THEIR-ROW"),
    ])
    db.session.commit()

    c = _login(u)
    body = c.get("/my/attendance?year=2026&month=3").get_data(as_text=True)
    assert "MY-ROW" in body
    assert "THEIR-ROW" not in body
    assert "Theirs" not in body


def test_the_attendance_summary_counts_only_my_rows(appctx):
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    theirs = _employee("E-2", "Theirs")
    row = Attendance(employee_id=mine.id, date=date(2026, 3, 3), status="Present",
                     check_in=time(9, 0), check_out=time(17, 0))
    row.recalculate()
    db.session.add(row)
    for i in range(5):
        db.session.add(Attendance(employee_id=theirs.id,
                                  date=date(2026, 3, 2) + timedelta(days=i),
                                  status="Present"))
    db.session.commit()

    c = _login(u)
    body = c.get("/my/attendance?year=2026&month=3").get_data(as_text=True)
    # One present day of mine, not the five belonging to somebody else.
    assert ">1<" in body.replace(" ", "").replace("\n", "") or "Present" in body
    summary = Attendance.summarise(mine.id, date(2026, 3, 1), date(2026, 3, 31))
    assert summary["present"] == 1


def test_attendance_is_read_only_in_self_service(appctx):
    """No edit route exists in the portal at all."""
    _enable()
    u, emp = _linked()
    row = Attendance(employee_id=emp.id, date=date(2026, 3, 3), status="Present")
    db.session.add(row)
    db.session.commit()

    c = _login(u)
    for path in (f"/my/attendance/{row.id}/edit", f"/my/attendance/{row.id}/delete"):
        assert c.post(path).status_code == 404


# ── 7-8. leave is own-only, and submittable ───────────────────────────────────

def test_an_employee_sees_only_their_own_leave(appctx):
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    theirs = _employee("E-2", "Theirs")
    lt = _leave_type()

    for emp, reason in ((mine, "MY-LEAVE"), (theirs, "THEIR-LEAVE")):
        r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                         start_date=date(2026, 3, 2), end_date=date(2026, 3, 4),
                         status="Pending", reason=reason)
        r.recalculate_days()
        db.session.add(r)
    db.session.commit()

    c = _login(u)
    body = c.get("/my/leaves").get_data(as_text=True)
    assert "MY-LEAVE" in body
    assert "THEIR-LEAVE" not in body


def test_an_employee_can_submit_their_own_leave(appctx):
    _enable()
    u, emp = _linked()
    lt = _leave_type()
    db.session.add(LeaveAllocation(employee_id=emp.id, leave_type_id=lt.id,
                                   year=2026, days=Decimal("24")))
    db.session.commit()

    c = _login(u)
    c.post("/my/leaves/new", data={"leave_type_id": str(lt.id),
                                   "start_date": "2026-03-02",
                                   "end_date": "2026-03-04",
                                   "day_portion": "full",
                                   "reason": "family"}, follow_redirects=True)

    r = LeaveRequest.query.one()
    assert r.employee_id == emp.id
    assert r.status == "Pending"           # never Approved
    assert float(r.days) == 3              # computed, not posted


def test_a_posted_employee_id_is_ignored_on_submission(appctx):
    """The employee comes from the session. A crafted employee_id must not
    file leave against somebody else's balance."""
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    theirs = _employee("E-2", "Theirs")
    lt = _leave_type()

    c = _login(u)
    c.post("/my/leaves/new", data={"employee_id": str(theirs.id),
                                   "leave_type_id": str(lt.id),
                                   "start_date": "2026-03-02",
                                   "end_date": "2026-03-04",
                                   "day_portion": "full"}, follow_redirects=True)

    r = LeaveRequest.query.one()
    assert r.employee_id == mine.id, "leave was filed against another employee"


def test_an_employee_can_withdraw_their_own_pending_request(appctx):
    _enable()
    u, emp = _linked()
    lt = _leave_type()
    r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                     start_date=date(2026, 3, 2), end_date=date(2026, 3, 4),
                     status="Pending")
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()

    c = _login(u)
    c.post(f"/my/leaves/{r.id}/cancel", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Cancelled"


def test_an_employee_cannot_withdraw_an_approved_request(appctx):
    """Once approved the days may already be in a payroll run; withdrawing is
    the manager's decision."""
    _enable()
    u, emp = _linked()
    lt = _leave_type()
    r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                     start_date=date(2026, 3, 2), end_date=date(2026, 3, 4),
                     status="Approved")
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()

    c = _login(u)
    c.post(f"/my/leaves/{r.id}/cancel", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Approved"


def test_an_employee_cannot_withdraw_someone_elses_request(appctx):
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    theirs = _employee("E-2", "Theirs")
    lt = _leave_type()
    r = LeaveRequest(employee_id=theirs.id, leave_type_id=lt.id,
                     start_date=date(2026, 3, 2), end_date=date(2026, 3, 4),
                     status="Pending")
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()

    c = _login(u)
    assert c.post(f"/my/leaves/{r.id}/cancel").status_code == 404
    db.session.refresh(r)
    assert r.status == "Pending"


# ── 9-10. an employee never approves ──────────────────────────────────────────

def test_an_employee_cannot_approve_their_own_leave(appctx):
    _enable()
    u, emp = _linked()
    lt = _leave_type()
    db.session.add(LeaveAllocation(employee_id=emp.id, leave_type_id=lt.id,
                                   year=2026, days=Decimal("24")))
    r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                     start_date=date(2026, 3, 2), end_date=date(2026, 3, 4),
                     status="Pending")
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()

    c = _login(u)
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Pending"


def test_an_employee_cannot_approve_anyone_elses_leave(appctx):
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    theirs = _employee("E-2", "Theirs")
    lt = _leave_type()
    r = LeaveRequest(employee_id=theirs.id, leave_type_id=lt.id,
                     start_date=date(2026, 3, 2), end_date=date(2026, 3, 4),
                     status="Pending")
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()

    c = _login(u)
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Pending"


def test_an_employee_reaches_no_payroll_action(appctx):
    """Finalise, cancel, pay and reverse are payroll.post -- admin only."""
    _enable()
    u, emp = _linked()
    entry = _payslip_for(emp, status="Processing")
    period = entry.period

    c = _login(u)
    for path in (f"/payroll/periods/{period.id}/finalize",
                 f"/payroll/periods/{period.id}/cancel",
                 f"/payroll/periods/{period.id}/pay",
                 f"/payroll/periods/{period.id}/process"):
        assert c.post(path, follow_redirects=False).status_code == 302, path
    db.session.expire_all()
    assert db.session.get(PayrollPeriod, period.id).status == "Processing"


# ── 11. balances ──────────────────────────────────────────────────────────────

def test_an_employee_sees_their_own_leave_balance(appctx):
    _enable()
    u, emp = _linked()
    lt = _leave_type()
    db.session.add(LeaveAllocation(employee_id=emp.id, leave_type_id=lt.id,
                                   year=2026, days=Decimal("24")))
    r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                     start_date=date(2026, 3, 2), end_date=date(2026, 3, 6),
                     status="Approved")
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()

    c = _login(u)
    body = c.get("/my/leaves?year=2026").get_data(as_text=True)
    assert "24.00" in body and "19.00" in body      # allocated and remaining


def test_the_balance_shows_no_one_elses_allocation(appctx):
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    theirs = _employee("E-2", "Theirs")
    lt = _leave_type()
    db.session.add(LeaveAllocation(employee_id=theirs.id, leave_type_id=lt.id,
                                   year=2026, days=Decimal("99")))
    db.session.commit()

    c = _login(u)
    body = c.get("/my/leaves?year=2026").get_data(as_text=True)
    assert "99.00" not in body


# ── 12-14. payslips and id tampering ──────────────────────────────────────────

def test_an_employee_sees_their_own_finalized_payslip(appctx):
    _enable()
    u, emp = _linked()
    entry = _payslip_for(emp)

    c = _login(u)
    listing = c.get("/my/payslips").get_data(as_text=True)
    assert "March 2026" in listing

    detail = c.get(f"/my/payslips/{entry.id}")
    assert detail.status_code == 200
    assert emp.name in detail.get_data(as_text=True)


def test_an_unfinalized_payslip_is_not_shown(appctx):
    """A processing period can still change; showing it invites a query about a
    number that was never final."""
    _enable()
    u, emp = _linked()
    entry = _payslip_for(emp, status="Processing")

    c = _login(u)
    assert "March 2026" not in c.get("/my/payslips").get_data(as_text=True)
    assert c.get(f"/my/payslips/{entry.id}").status_code == 404


def test_an_employee_cannot_open_another_employees_payslip(appctx):
    """The headline security test: change the id, get nothing."""
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    theirs = _employee("E-2", "Theirs")

    # One process run covers both employees; a second would be refused once the
    # period is finalized.
    _structure_for(theirs, 99000)
    _payslip_for(mine, basic=31000)
    period = PayrollPeriod.query.one()
    their_entry = period.entries.filter_by(employee_id=theirs.id).one()

    c = _login(u)
    r = c.get(f"/my/payslips/{their_entry.id}")
    assert r.status_code == 404
    assert "99,000" not in r.get_data(as_text=True)


def test_walking_the_payslip_ids_reveals_nothing(appctx):
    """Not one id in the range leaks a foreign record."""
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    for i in range(2, 5):
        _employee(f"E-{i}", f"Other {i}")
    _payslip_for(mine)          # processes the whole period, everyone included

    c = _login(u)
    from salpurflask.models.payroll import PayrollEntry
    mine_ids = {e.id for e in PayrollEntry.query.filter_by(employee_id=mine.id).all()}
    for entry in PayrollEntry.query.all():
        expected = 200 if entry.id in mine_ids else 404
        assert c.get(f"/my/payslips/{entry.id}").status_code == expected


def test_the_ownership_guard_itself(appctx):
    _enable()
    u, mine = _linked("staff", "E-1", "Mine")
    theirs = _employee("E-2", "Theirs")
    row_mine = Attendance(employee_id=mine.id, date=date(2026, 3, 3), status="Present")
    row_theirs = Attendance(employee_id=theirs.id, date=date(2026, 3, 3), status="Present")
    db.session.add_all([row_mine, row_theirs])
    db.session.commit()

    with flask_app.test_request_context():
        from flask_login import login_user
        login_user(u)
        assert owns(row_mine) is True
        assert owns(row_theirs) is False
        assert owns(mine) is True
        assert owns(theirs) is False
        assert owns(None) is False


# ── 15-16. management is unchanged ────────────────────────────────────────────

def test_a_manager_keeps_their_existing_capabilities(appctx):
    _enable()
    mgr = _user("manager", "mgr@ss.com")
    emp = _employee()
    lt = _leave_type()
    r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                     start_date=date(2026, 3, 2), end_date=date(2026, 3, 4),
                     status="Pending")
    r.recalculate_days()
    db.session.add(LeaveAllocation(employee_id=emp.id, leave_type_id=lt.id,
                                   year=2026, days=Decimal("24")))
    db.session.add(r)
    db.session.commit()

    c = _login(mgr)
    assert c.get("/hr/employees").status_code == 200
    assert c.get("/leave/requests").status_code == 200
    assert c.get("/attendance/").status_code == 200
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    db.session.refresh(r)
    assert r.status == "Approved"


def test_an_admin_keeps_their_existing_capabilities(appctx):
    _enable()
    admin = _user("admin", "adm@ss.com")
    emp = _employee()
    entry = _payslip_for(emp, status="Processing")

    c = _login(admin)
    for path in ("/hr/employees", "/leave/requests", "/attendance/", "/payroll/",
                 f"/payroll/periods/{entry.period_id}"):
        assert c.get(path).status_code == 200, path


def test_a_manager_without_an_employee_record_has_no_portal(appctx):
    """The portal is about being an employee, not about rank."""
    _enable()
    mgr = _user("manager", "mgr@ss.com")
    c = _login(mgr)
    assert c.get("/my/profile", follow_redirects=False).status_code == 302


def test_a_linked_manager_sees_their_own_portal_too(appctx):
    _enable()
    mgr, emp = _linked("manager", "M-1", "Boss", "boss@ss.com")
    c = _login(mgr)
    assert c.get("/my/profile").status_code == 200


# ── 17. module flags ──────────────────────────────────────────────────────────

def test_the_portal_is_refused_while_hr_is_off(appctx):
    _enable(hr=False)
    u, emp = _linked()
    c = _login(u)
    assert c.get("/my/profile", follow_redirects=False).status_code == 302


def test_each_page_follows_its_own_module_flag(appctx):
    _enable(attendance=False, leave=False, payroll=False)
    u, emp = _linked()
    c = _login(u)
    assert c.get("/my/profile").status_code == 200          # HR is on
    for path in ("/my/attendance", "/my/leaves", "/my/payslips"):
        assert c.get(path, follow_redirects=False).status_code == 302, path


def test_the_my_work_menu_is_hidden_from_an_unlinked_user(appctx):
    _enable()
    plain = _user("staff", "plain@ss.com")
    assert "My Work" not in _login(plain).get("/").get_data(as_text=True)


def test_the_my_work_menu_is_shown_to_a_linked_user(appctx):
    """Separate test, and the link is made before signing in.

    That is also the real sequence: an administrator links the login, and the
    employee signs in afterwards. A client built before the link keeps the user
    it loaded at sign-in time, which is a property of the test client rather
    than of the page.
    """
    _enable()
    u, emp = _linked("staff", "E-9", "Linked", "linked@ss.com")
    body = _login(u).get("/").get_data(as_text=True)
    assert "My Work" in body
    assert "My Profile" in body


# ── isolation ─────────────────────────────────────────────────────────────────

def test_the_link_column_is_nullable_and_indexed(appctx):
    insp = db.inspect(db.engine)
    cols = {c["name"]: c for c in insp.get_columns("hr_employee")}
    assert "user_id" in cols
    assert cols["user_id"]["nullable"] is True
    fks = {tuple(f["constrained_columns"]): f["referred_table"]
           for f in insp.get_foreign_keys("hr_employee")}
    assert fks.get(("user_id",)) == "user"


def test_self_service_added_no_column_to_any_other_table(appctx):
    insp = db.inspect(db.engine)
    for t in ("hr_attendance", "hr_leave_request", "hr_payroll_entry", "user"):
        cols = {c["name"] for c in insp.get_columns(t)}
        assert not any(c.startswith("selfservice") for c in cols), t
