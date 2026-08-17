"""Attendance module: module gating, permissions, CRUD, hours and summaries.

The two rules worth the most here are the ones payroll will depend on in phase 3:
one row per employee per day, and a summary whose numbers match what the history
page shows. A payroll run that counted a duplicated day would pay for it twice.
"""
from datetime import date, time, timedelta

from app import app as flask_app, db, User, pwd_context
from salpurflask.models.attendance import Attendance, STANDARD_DAY_HOURS
from salpurflask.models.hr import Department, Employee
from salpurflask.services.feature_flags import module_enabled, set_module
from salpurflask.services.hr_permissions import has_permission


def _client(role="admin", email=None):
    email = email or f"{role}@att.com"
    db.session.add(User(name=role.title(), email=email,
                        password=pwd_context.hash("secret123"),
                        verified=True, role=role))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _enable(hr=True, attendance=True):
    set_module("module_hr", hr, updated_by="test")
    set_module("module_attendance", attendance, updated_by="test")


def _employee(code="E-1", name="Worker", joining=date(2026, 1, 1), active=True):
    e = Employee(code=code, name=name, joining_date=joining, active=active)
    db.session.add(e)
    db.session.commit()
    return e


def _yesterday():
    return date.today() - timedelta(days=1)


def _form(emp, **over):
    data = {"employee_id": str(emp.id), "date": _yesterday().isoformat(),
            "status": "Present", "check_in": "09:00", "check_out": "17:00",
            "remarks": ""}
    data.update(over)
    return data


# ── module ON/OFF ─────────────────────────────────────────────────────────────

def test_attendance_is_off_on_a_new_install(appctx):
    assert module_enabled("module_attendance") is False


def test_attendance_routes_are_refused_while_the_module_is_off(appctx):
    _enable(hr=True, attendance=False)
    c = _client("admin")
    for path in ("/attendance/", "/attendance/daily", "/attendance/summary",
                 "/attendance/new"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 302, path


def test_attendance_routes_open_once_the_module_is_on(appctx):
    _enable()
    c = _client("admin")
    for path in ("/attendance/", "/attendance/daily", "/attendance/summary",
                 "/attendance/new"):
        assert c.get(path).status_code == 200, path


def test_attendance_follows_hr_off(appctx):
    """Attendance hangs off Employees; without HR it must not be reachable."""
    _enable(hr=False, attendance=True)
    assert module_enabled("module_attendance") is False
    c = _client("admin")
    assert c.get("/attendance/", follow_redirects=False).status_code == 302


def test_the_menu_appears_only_when_attendance_is_on(appctx):
    _enable(hr=True, attendance=False)
    c = _client("admin")
    assert "Daily Attendance" not in c.get("/").get_data(as_text=True)
    _enable(hr=True, attendance=True)
    assert "Daily Attendance" in c.get("/").get_data(as_text=True)


def test_switching_attendance_off_keeps_its_records(appctx):
    _enable()
    emp = _employee()
    db.session.add(Attendance(employee_id=emp.id, date=_yesterday(), status="Present"))
    db.session.commit()

    set_module("module_attendance", False, updated_by="test")
    assert Attendance.query.count() == 1
    set_module("module_attendance", True, updated_by="test")
    assert Attendance.query.count() == 1


# ── permissions ───────────────────────────────────────────────────────────────

def test_attendance_permissions_match_the_roles_they_promise(appctx):
    with flask_app.test_request_context():
        staff = User(name="S", email="s@a.com", password="x", verified=True, role="staff")
        manager = User(name="M", email="m@a.com", password="x", verified=True, role="manager")
        admin = User(name="A", email="a@a.com", password="x", verified=True, role="admin")
        assert has_permission("attendance.view", staff) is True
        assert has_permission("attendance.create", staff) is False
        assert has_permission("attendance.create", manager) is True
        assert has_permission("attendance.delete", manager) is False
        assert has_permission("attendance.delete", admin) is True


def test_staff_may_read_attendance_but_not_record_it(appctx):
    _enable()
    emp = _employee()
    c = _client("staff")
    assert c.get("/attendance/").status_code == 200
    r = c.post("/attendance/new", data=_form(emp), follow_redirects=False)
    assert r.status_code == 302
    assert Attendance.query.count() == 0


def test_a_manager_may_not_delete_attendance(appctx):
    _enable()
    emp = _employee()
    row = Attendance(employee_id=emp.id, date=_yesterday(), status="Present")
    db.session.add(row)
    db.session.commit()
    c = _client("manager")
    c.post(f"/attendance/{row.id}/delete")
    assert Attendance.query.count() == 1


# ── CRUD ──────────────────────────────────────────────────────────────────────

def test_recording_attendance(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/attendance/new", data=_form(emp), follow_redirects=True)

    row = Attendance.query.one()
    assert row.employee_id == emp.id
    assert row.status == "Present"
    assert float(row.working_hours) == 8.0


def test_editing_attendance_recalculates_the_hours(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/attendance/new", data=_form(emp), follow_redirects=True)
    row = Attendance.query.one()

    c.post(f"/attendance/{row.id}/edit",
           data=_form(emp, check_out="19:00"), follow_redirects=True)
    db.session.refresh(row)
    assert float(row.working_hours) == 10.0
    assert float(row.overtime_hours) == 2.0


def test_deleting_attendance(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/attendance/new", data=_form(emp), follow_redirects=True)
    row = Attendance.query.one()
    c.post(f"/attendance/{row.id}/delete", follow_redirects=True)
    assert Attendance.query.count() == 0


# ── duplicate prevention ──────────────────────────────────────────────────────

def test_the_same_employee_cannot_be_marked_twice_on_one_day(appctx):
    """Two rows for one person on one day is a contradiction, and payroll would
    pay for both."""
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/attendance/new", data=_form(emp), follow_redirects=True)
    c.post("/attendance/new", data=_form(emp, status="Absent"), follow_redirects=True)
    assert Attendance.query.count() == 1
    assert Attendance.query.one().status == "Present"


def test_two_employees_may_share_a_date(appctx):
    _enable()
    a = _employee("E-1", "One")
    b = _employee("E-2", "Two")
    c = _client("admin")
    c.post("/attendance/new", data=_form(a), follow_redirects=True)
    c.post("/attendance/new", data=_form(b), follow_redirects=True)
    assert Attendance.query.count() == 2


def test_editing_a_row_does_not_collide_with_itself(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/attendance/new", data=_form(emp), follow_redirects=True)
    row = Attendance.query.one()
    c.post(f"/attendance/{row.id}/edit", data=_form(emp, remarks="fixed"),
           follow_redirects=True)
    db.session.refresh(row)
    assert row.remarks == "fixed"


# ── validation ────────────────────────────────────────────────────────────────

def test_attendance_needs_an_employee_and_a_date(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/attendance/new", data=_form(emp, employee_id=""), follow_redirects=True)
    c.post("/attendance/new", data=_form(emp, date=""), follow_redirects=True)
    assert Attendance.query.count() == 0


def test_a_future_date_is_refused(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    future = (date.today() + timedelta(days=3)).isoformat()
    c.post("/attendance/new", data=_form(emp, date=future), follow_redirects=True)
    assert Attendance.query.count() == 0


def test_attendance_before_the_joining_date_is_refused(appctx):
    _enable()
    emp = _employee(joining=date.today() - timedelta(days=2))
    c = _client("admin")
    early = (date.today() - timedelta(days=10)).isoformat()
    c.post("/attendance/new", data=_form(emp, date=early), follow_redirects=True)
    assert Attendance.query.count() == 0


def test_an_unknown_status_is_refused(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/attendance/new", data=_form(emp, status="Holiday"), follow_redirects=True)
    assert Attendance.query.count() == 0


def test_an_unknown_employee_is_refused(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/attendance/new", data=_form(emp, employee_id="99999"),
           follow_redirects=True)
    assert Attendance.query.count() == 0


# ── hours calculation ─────────────────────────────────────────────────────────

def test_hours_between_two_times(appctx):
    assert Attendance.compute_hours(time(9, 0), time(17, 0)) == 8.0
    assert Attendance.compute_hours(time(9, 30), time(13, 0)) == 3.5
    assert Attendance.compute_hours(None, time(17, 0)) == 0.0
    assert Attendance.compute_hours(time(9, 0), None) == 0.0


def test_a_night_shift_crossing_midnight_is_not_negative(appctx):
    """22:00 -> 06:00 is eight hours of work, not minus sixteen."""
    assert Attendance.compute_hours(time(22, 0), time(6, 0)) == 8.0


def test_overtime_is_whatever_exceeds_a_standard_day(appctx):
    emp = _employee()
    row = Attendance(employee_id=emp.id, date=_yesterday(), status="Present",
                     check_in=time(9, 0), check_out=time(20, 0))
    row.recalculate()
    assert float(row.working_hours) == 11.0
    assert float(row.overtime_hours) == 11.0 - STANDARD_DAY_HOURS


def test_absent_and_leave_store_no_hours_whatever_the_clock_says(appctx):
    """A stray check-in left on an absent row must not earn overtime."""
    emp = _employee()
    for status in ("Absent", "Leave"):
        row = Attendance(employee_id=emp.id, date=_yesterday(), status=status,
                         check_in=time(9, 0), check_out=time(20, 0))
        row.recalculate()
        assert float(row.working_hours) == 0
        assert float(row.overtime_hours) == 0


def test_a_half_day_counts_as_half_a_worked_day(appctx):
    emp = _employee()
    rows = {}
    for status in ("Present", "Half Day", "Absent", "Leave", "Late"):
        r = Attendance(employee_id=emp.id, date=_yesterday(), status=status)
        rows[status] = r.day_fraction
    assert rows["Present"] == 1.0
    assert rows["Late"] == 1.0
    assert rows["Half Day"] == 0.5
    assert rows["Absent"] == 0.0
    assert rows["Leave"] == 0.0


# ── monthly summary ───────────────────────────────────────────────────────────

def test_the_monthly_summary_counts_each_status(appctx):
    emp = _employee()
    start = date(2026, 3, 1)
    plan = ["Present", "Present", "Late", "Half Day", "Absent", "Leave"]
    for i, status in enumerate(plan):
        r = Attendance(employee_id=emp.id, date=start + timedelta(days=i),
                       status=status, check_in=time(9, 0), check_out=time(17, 0))
        r.recalculate()
        db.session.add(r)
    db.session.commit()

    s = Attendance.summarise(emp.id, start, date(2026, 3, 31))
    assert s["records"] == 6
    assert s["present"] == 2
    assert s["late"] == 1
    assert s["half_day"] == 1
    assert s["absent"] == 1
    assert s["leave"] == 1
    # 2 present + 1 late + half a day = 3.5
    assert s["worked_days"] == 3.5
    # four worked rows at eight hours; absent and leave store zero
    assert s["working_hours"] == 32.0


def test_the_summary_ignores_days_outside_the_range(appctx):
    emp = _employee()
    for d in (date(2026, 2, 28), date(2026, 3, 15), date(2026, 4, 1)):
        db.session.add(Attendance(employee_id=emp.id, date=d, status="Present"))
    db.session.commit()
    s = Attendance.summarise(emp.id, date(2026, 3, 1), date(2026, 3, 31))
    assert s["records"] == 1


def test_the_summary_page_renders_the_totals(appctx):
    _enable()
    emp = _employee()
    r = Attendance(employee_id=emp.id, date=_yesterday(), status="Present",
                   check_in=time(9, 0), check_out=time(17, 0))
    r.recalculate()
    db.session.add(r)
    db.session.commit()

    c = _client("admin")
    y = _yesterday()
    body = c.get(f"/attendance/summary?year={y.year}&month={y.month}").get_data(as_text=True)
    assert emp.name in body


# ── daily sheet ───────────────────────────────────────────────────────────────

def test_the_daily_sheet_saves_a_whole_day(appctx):
    _enable()
    a = _employee("E-1", "One")
    b = _employee("E-2", "Two")
    c = _client("admin")
    day = _yesterday().isoformat()

    c.post("/attendance/mark", data={
        "date": day,
        f"status_{a.id}": "Present", f"check_in_{a.id}": "09:00", f"check_out_{a.id}": "17:00",
        f"status_{b.id}": "Absent",
    }, follow_redirects=True)

    assert Attendance.query.count() == 2
    assert float(Attendance.query.filter_by(employee_id=a.id).one().working_hours) == 8.0
    assert Attendance.query.filter_by(employee_id=b.id).one().status == "Absent"


def test_an_unmarked_employee_is_skipped_not_recorded_absent(appctx):
    """Not having marked someone is not the same as saying they did not come."""
    _enable()
    a = _employee("E-1", "One")
    b = _employee("E-2", "Two")
    c = _client("admin")
    c.post("/attendance/mark", data={"date": _yesterday().isoformat(),
                                     f"status_{a.id}": "Present"},
           follow_redirects=True)
    assert Attendance.query.count() == 1
    assert Attendance.query.filter_by(employee_id=b.id).count() == 0


def test_saving_the_daily_sheet_twice_updates_rather_than_duplicates(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    day = _yesterday().isoformat()
    c.post("/attendance/mark", data={"date": day, f"status_{emp.id}": "Present"},
           follow_redirects=True)
    c.post("/attendance/mark", data={"date": day, f"status_{emp.id}": "Late"},
           follow_redirects=True)
    assert Attendance.query.count() == 1
    assert Attendance.query.one().status == "Late"


# ── search and filtering ──────────────────────────────────────────────────────

def _table_rows(client, url):
    """Just the table body. The filter dropdowns list every employee by design,
    so searching the whole page would find a name the filter correctly excluded."""
    body = client.get(url).get_data(as_text=True)
    return body.split("<tbody>")[1].split("</tbody>")[0]


def test_filtering_the_history_by_employee_status_and_date(appctx):
    _enable()
    a = _employee("E-1", "Ahmed")
    b = _employee("E-2", "Bilal")
    db.session.add_all([
        Attendance(employee_id=a.id, date=_yesterday(), status="Present"),
        Attendance(employee_id=b.id, date=_yesterday(), status="Absent"),
    ])
    db.session.commit()
    c = _client("admin")

    rows = _table_rows(c, f"/attendance/?employee={a.id}")
    assert "Ahmed" in rows and "Bilal" not in rows

    rows = _table_rows(c, "/attendance/?status=Absent")
    assert "Bilal" in rows and "Ahmed" not in rows

    future = (date.today() + timedelta(days=1)).isoformat()
    rows = _table_rows(c, f"/attendance/?from={future}")
    assert "Ahmed" not in rows and "Bilal" not in rows


def test_filtering_by_department(appctx):
    _enable()
    dep = Department(name="Warehouse")
    db.session.add(dep)
    db.session.flush()
    a = _employee("E-1", "Ahmed")
    a.department_id = dep.id
    b = _employee("E-2", "Bilal")
    db.session.add_all([
        Attendance(employee_id=a.id, date=_yesterday(), status="Present"),
        Attendance(employee_id=b.id, date=_yesterday(), status="Present"),
    ])
    db.session.commit()

    c = _client("admin")
    rows = _table_rows(c, f"/attendance/?department={dep.id}")
    assert "Ahmed" in rows and "Bilal" not in rows


# ── isolation and payroll readiness ───────────────────────────────────────────

def test_attendance_lives_in_its_own_table_and_touches_no_core_table(appctx):
    insp = db.inspect(db.engine)
    assert "hr_attendance" in insp.get_table_names()

    uniques = {u["name"] for u in insp.get_unique_constraints("hr_attendance")}
    assert "uq_attendance_employee_date" in uniques

    for core in ("item", "sale", "purchase", "journal_entry", "hr_employee"):
        cols = {c["name"] for c in insp.get_columns(core)}
        assert not any("attendance" in c for c in cols), f"{core} gained an attendance column"


def test_deleting_an_employee_takes_their_attendance_with_them(appctx):
    """No orphan rows pointing at an employee who is gone."""
    _enable()
    emp = _employee()
    db.session.add(Attendance(employee_id=emp.id, date=_yesterday(), status="Present"))
    db.session.commit()

    db.session.delete(emp)
    db.session.commit()
    assert Attendance.query.count() == 0


def test_an_employee_with_attendance_is_deactivated_rather_than_deleted(appctx):
    """HR's own rule, now that attendance rows actually exist to trigger it."""
    _enable()
    emp = _employee()
    db.session.add(Attendance(employee_id=emp.id, date=_yesterday(), status="Present"))
    db.session.commit()

    c = _client("admin")
    c.post(f"/hr/employees/{emp.id}/delete", follow_redirects=True)

    assert Employee.query.count() == 1
    assert Employee.query.one().active is False
    assert Attendance.query.count() == 1


def test_summarise_gives_payroll_what_it_needs_without_knowing_about_money(appctx):
    """The phase 3 contract: counts only, no rate and no amount."""
    emp = _employee()
    start = date(2026, 5, 1)
    r = Attendance(employee_id=emp.id, date=start, status="Present",
                   check_in=time(9, 0), check_out=time(19, 0))
    r.recalculate()
    db.session.add(r)
    db.session.commit()

    s = Attendance.summarise(emp.id, start, date(2026, 5, 31))
    for key in ("worked_days", "absent", "late", "leave", "overtime_hours",
                "working_hours"):
        assert key in s
    assert s["overtime_hours"] == 2.0
    assert not any("salary" in k or "amount" in k or "rate" in k for k in s)
