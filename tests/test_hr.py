"""HR module: feature flags, permissions and employee CRUD.

Two things are worth more than the CRUD coverage here:

  * a module switched off must refuse its routes, not merely hide its menu —
    a stale bookmark is a real way in; and
  * switching a module off must not delete anything, because the same mechanism
    will later gate payroll, whose rows are journal entries in the general ledger.
"""
from datetime import date

import pytest

from app import app as flask_app, db, User, pwd_context
from salpurflask.models.hr import Department, Designation, Employee
from salpurflask.services.feature_flags import (module_enabled, set_module,
                                                all_modules)
from salpurflask.services.hr_permissions import has_permission


def _client(role="admin", email=None):
    email = email or f"{role}@t.com"
    db.session.add(User(name=role.title(), email=email,
                        password=pwd_context.hash("secret123"),
                        verified=True, role=role))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _enable_hr():
    set_module("module_hr", True, updated_by="test")


def _employee_form(**over):
    data = {"code": "E-001", "name": "Ali Raza", "joining_date": "2026-01-15",
            "employment_status": "Permanent", "basic_salary": "50000",
            "allowances": "5000", "deductions": "1000", "active": "1"}
    data.update(over)
    return data


# ── feature flags ─────────────────────────────────────────────────────────────

def test_modules_are_off_until_someone_turns_them_on(appctx):
    """A new install must not grow menus nobody asked for."""
    for key in ("module_hr", "module_attendance", "module_payroll"):
        assert module_enabled(key) is False


def test_turning_a_module_on_and_off_again(appctx):
    _enable_hr()
    assert module_enabled("module_hr") is True
    set_module("module_hr", False, updated_by="test")
    assert module_enabled("module_hr") is False


def test_attendance_and_payroll_stay_off_while_hr_is_off(appctx):
    """Both hang off Employees. On with no HR would mean a menu leading nowhere."""
    set_module("module_attendance", True, updated_by="test")
    set_module("module_payroll", True, updated_by="test")
    assert module_enabled("module_attendance") is False
    assert module_enabled("module_payroll") is False

    _enable_hr()
    assert module_enabled("module_attendance") is True
    assert module_enabled("module_payroll") is True


def test_all_modules_reports_what_blocks_a_module(appctx):
    set_module("module_payroll", True, updated_by="test")
    row = {m["key"]: m for m in all_modules()}["module_payroll"]
    assert row["enabled"] is True          # its own flag is on
    assert row["effective"] is False       # but it is not usable
    assert row["blocked_by"] == "module_hr"


def test_switching_a_module_off_keeps_every_record(appctx):
    """The whole point of a flag rather than an uninstall."""
    _enable_hr()
    db.session.add(Employee(code="E-KEEP", name="Stays", joining_date=date(2026, 1, 1)))
    db.session.commit()

    set_module("module_hr", False, updated_by="test")
    assert Employee.query.filter_by(code="E-KEEP").count() == 1

    set_module("module_hr", True, updated_by="test")
    assert Employee.query.filter_by(code="E-KEEP").one().name == "Stays"


# ── module gating on routes ───────────────────────────────────────────────────

def test_hr_routes_refuse_themselves_while_the_module_is_off(appctx):
    """Hiding the menu is not access control."""
    c = _client("admin")
    r = c.get("/hr/employees", follow_redirects=False)
    assert r.status_code == 302
    assert "/hr/" not in (r.headers.get("Location") or "")


def test_hr_routes_open_once_the_module_is_on(appctx):
    _enable_hr()
    c = _client("admin")
    assert c.get("/hr/employees").status_code == 200


def test_the_menu_appears_only_when_the_module_is_on(appctx):
    c = _client("admin")
    assert "HR &amp; Payroll" not in c.get("/").get_data(as_text=True)
    _enable_hr()
    assert "HR &amp; Payroll" in c.get("/").get_data(as_text=True)


# ── permissions ───────────────────────────────────────────────────────────────

def test_permission_map_matches_the_roles_it_promises(appctx):
    with flask_app.test_request_context():
        staff = User(name="S", email="s@t.com", password="x", verified=True, role="staff")
        manager = User(name="M", email="m@t.com", password="x", verified=True, role="manager")
        admin = User(name="A", email="a@t.com", password="x", verified=True, role="admin")

        assert has_permission("hr.view", staff) is True
        assert has_permission("hr.create", staff) is False
        assert has_permission("hr.create", manager) is True
        assert has_permission("hr.delete", manager) is False
        assert has_permission("hr.delete", admin) is True
        # Posting payroll touches the GL — admin only, even for a manager.
        assert has_permission("payroll.post", manager) is False
        assert has_permission("payroll.post", admin) is True


def test_an_unverified_user_holds_no_permission(appctx):
    with flask_app.test_request_context():
        u = User(name="U", email="u@t.com", password="x", verified=False, role="admin")
        assert has_permission("hr.view", u) is False


def test_staff_may_read_employees_but_not_create_them(appctx):
    _enable_hr()
    c = _client("staff")
    assert c.get("/hr/employees").status_code == 200

    r = c.post("/hr/employees/new", data=_employee_form(), follow_redirects=False)
    assert r.status_code == 302
    assert Employee.query.count() == 0


def test_a_manager_may_not_delete_an_employee(appctx):
    _enable_hr()
    emp = Employee(code="E-9", name="Keep", joining_date=date(2026, 1, 1))
    db.session.add(emp)
    db.session.commit()

    c = _client("manager")
    c.post(f"/hr/employees/{emp.id}/delete")
    assert Employee.query.count() == 1


# ── employee CRUD ─────────────────────────────────────────────────────────────

def test_creating_an_employee(appctx):
    _enable_hr()
    c = _client("admin")
    c.post("/hr/employees/new", data=_employee_form(), follow_redirects=True)

    emp = Employee.query.filter_by(code="E-001").one()
    assert emp.name == "Ali Raza"
    assert emp.joining_date == date(2026, 1, 15)
    assert float(emp.basic_salary) == 50000
    assert emp.active is True


def test_employee_code_must_be_unique(appctx):
    _enable_hr()
    c = _client("admin")
    c.post("/hr/employees/new", data=_employee_form(), follow_redirects=True)
    c.post("/hr/employees/new", data=_employee_form(name="Someone Else"),
           follow_redirects=True)
    assert Employee.query.filter_by(code="E-001").count() == 1


def test_an_employee_needs_a_code_a_name_and_a_joining_date(appctx):
    _enable_hr()
    c = _client("admin")
    for bad in ({"code": ""}, {"name": ""}, {"joining_date": ""}):
        c.post("/hr/employees/new", data=_employee_form(**bad), follow_redirects=True)
    assert Employee.query.count() == 0


def test_a_negative_salary_is_refused(appctx):
    _enable_hr()
    c = _client("admin")
    c.post("/hr/employees/new", data=_employee_form(basic_salary="-1"),
           follow_redirects=True)
    assert Employee.query.count() == 0


def test_an_invalid_employment_status_is_refused(appctx):
    _enable_hr()
    c = _client("admin")
    c.post("/hr/employees/new", data=_employee_form(employment_status="Freelance"),
           follow_redirects=True)
    assert Employee.query.count() == 0


def test_editing_an_employee(appctx):
    _enable_hr()
    c = _client("admin")
    c.post("/hr/employees/new", data=_employee_form(), follow_redirects=True)
    emp = Employee.query.filter_by(code="E-001").one()

    c.post(f"/hr/employees/{emp.id}/edit",
           data=_employee_form(name="Ali Raza Khan", basic_salary="60000"),
           follow_redirects=True)

    db.session.refresh(emp)
    assert emp.name == "Ali Raza Khan"
    assert float(emp.basic_salary) == 60000


def test_deleting_an_employee_with_no_history(appctx):
    _enable_hr()
    c = _client("admin")
    c.post("/hr/employees/new", data=_employee_form(), follow_redirects=True)
    emp = Employee.query.filter_by(code="E-001").one()

    c.post(f"/hr/employees/{emp.id}/delete", follow_redirects=True)
    assert Employee.query.count() == 0


def test_gross_and_net_are_derived_from_the_components(appctx):
    emp = Employee(code="E-2", name="N", joining_date=date(2026, 1, 1),
                   basic_salary=40000, allowances=8000, deductions=3000)
    db.session.add(emp)
    db.session.commit()
    assert float(emp.gross_salary) == 48000
    assert float(emp.net_salary) == 45000


# ── search and filtering ──────────────────────────────────────────────────────

def test_searching_and_filtering_the_employee_list(appctx):
    _enable_hr()
    dep = Department(name="Sales")
    db.session.add(dep)
    db.session.flush()
    db.session.add_all([
        Employee(code="E-10", name="Ahmed", joining_date=date(2026, 1, 1),
                 department_id=dep.id, active=True),
        Employee(code="E-11", name="Bilal", joining_date=date(2026, 1, 1),
                 active=False),
    ])
    db.session.commit()

    c = _client("admin")
    assert "Ahmed" in c.get("/hr/employees?q=Ahmed").get_data(as_text=True)
    assert "Bilal" not in c.get("/hr/employees?q=Ahmed").get_data(as_text=True)
    assert "Bilal" not in c.get("/hr/employees?active=1").get_data(as_text=True)
    assert "Ahmed" in c.get(f"/hr/employees?department={dep.id}").get_data(as_text=True)


# ── departments and designations ──────────────────────────────────────────────

def test_adding_a_department_and_a_designation(appctx):
    _enable_hr()
    c = _client("admin")
    c.post("/hr/departments", data={"name": "Warehouse"}, follow_redirects=True)
    c.post("/hr/designations", data={"name": "Cashier"}, follow_redirects=True)
    assert Department.query.filter_by(name="Warehouse").count() == 1
    assert Designation.query.filter_by(name="Cashier").count() == 1


def test_department_names_do_not_repeat_in_a_different_case(appctx):
    _enable_hr()
    c = _client("admin")
    c.post("/hr/departments", data={"name": "Sales"}, follow_redirects=True)
    c.post("/hr/departments", data={"name": "sales"}, follow_redirects=True)
    assert Department.query.count() == 1


def test_a_department_still_holding_employees_is_not_deleted(appctx):
    _enable_hr()
    dep = Department(name="Accounts")
    db.session.add(dep)
    db.session.flush()
    db.session.add(Employee(code="E-20", name="X", joining_date=date(2026, 1, 1),
                            department_id=dep.id))
    db.session.commit()

    c = _client("admin")
    c.post(f"/hr/departments/{dep.id}/delete", follow_redirects=True)
    assert Department.query.count() == 1


# ── isolation from the core ───────────────────────────────────────────────────

def test_hr_tables_are_separate_from_the_core_schema(appctx):
    """HR must not have reached into an existing table."""
    insp = db.inspect(db.engine)
    for t in ("hr_employee", "hr_department", "hr_designation"):
        assert t in insp.get_table_names()

    for core in ("item", "sale", "purchase", "journal_entry"):
        cols = {c["name"] for c in insp.get_columns(core)}
        assert not any(c.startswith("employee") or c.startswith("hr_") for c in cols), \
            f"{core} gained an HR column"
