"""Payroll phase 3A: module gating, structures, periods and the salary engine.

The arithmetic tests matter most. A payroll bug does not raise an exception — it
quietly pays the wrong amount, and someone notices on payday. So the engine is
tested against figures worked out by hand, not against itself.

Nothing here touches the general ledger; posting is phase 3B.
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest

from app import app as flask_app, db, User, pwd_context
from salpurflask.models.attendance import Attendance
from salpurflask.models.hr import Employee
from salpurflask.models.payroll import (SalaryComponent, SalaryStructure,
                                        SalaryStructureLine, PayrollPeriod,
                                        PayrollEntry, EmployeeAdvance,
                                        seed_default_components)
from salpurflask.services import payroll_engine as engine
from salpurflask.services.feature_flags import module_enabled, set_module
from salpurflask.services.hr_permissions import has_permission


def _client(role="admin", email=None):
    email = email or f"{role}@pay.com"
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


def _employee(code="E-1", name="Worker", joining=date(2025, 1, 1)):
    e = Employee(code=code, name=name, joining_date=joining, active=True)
    db.session.add(e)
    db.session.commit()
    return e


def _component(code):
    seed_default_components()
    return SalaryComponent.query.filter_by(code=code).one()


def _structure(emp, **codes):
    """_structure(emp, BASIC=30000, HRA=5000) -> an active structure."""
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


# ── module ON/OFF ─────────────────────────────────────────────────────────────

def test_payroll_is_off_on_a_new_install(appctx):
    assert module_enabled("module_payroll") is False


def test_payroll_routes_are_refused_while_the_module_is_off(appctx):
    _enable(hr=True, payroll=False)
    c = _client("admin")
    for path in ("/payroll/", "/payroll/structures", "/payroll/advances",
                 "/payroll/periods/new"):
        assert c.get(path, follow_redirects=False).status_code == 302, path


def test_payroll_routes_open_once_the_module_is_on(appctx):
    _enable()
    c = _client("admin")
    for path in ("/payroll/", "/payroll/structures", "/payroll/advances",
                 "/payroll/periods/new"):
        assert c.get(path).status_code == 200, path


def test_payroll_follows_hr_off(appctx):
    """Payroll pays employees; without HR there are none."""
    _enable(hr=False, payroll=True)
    assert module_enabled("module_payroll") is False
    c = _client("admin")
    assert c.get("/payroll/", follow_redirects=False).status_code == 302


def test_the_menu_appears_only_when_payroll_is_on(appctx):
    _enable(hr=True, payroll=False)
    c = _client("admin")
    assert "Salary Structures" not in c.get("/").get_data(as_text=True)
    _enable(hr=True, payroll=True)
    assert "Salary Structures" in c.get("/").get_data(as_text=True)


def test_switching_payroll_off_keeps_its_data(appctx):
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000)
    _period()
    set_module("module_payroll", False, updated_by="test")
    assert SalaryStructure.query.count() == 1
    assert PayrollPeriod.query.count() == 1
    set_module("module_payroll", True, updated_by="test")
    assert SalaryStructure.query.count() == 1


# ── permissions ───────────────────────────────────────────────────────────────

def test_payroll_permissions_match_the_roles_they_promise(appctx):
    with flask_app.test_request_context():
        staff = User(name="S", email="s@p.com", password="x", verified=True, role="staff")
        manager = User(name="M", email="m@p.com", password="x", verified=True, role="manager")
        admin = User(name="A", email="a@p.com", password="x", verified=True, role="admin")
        # Payroll is salary data — staff cannot even read it.
        assert has_permission("payroll.view", staff) is False
        assert has_permission("payroll.view", manager) is True
        assert has_permission("payroll.create", manager) is True
        assert has_permission("payroll.delete", manager) is False
        # Finalising writes to the ledger in 3B — admin only.
        assert has_permission("payroll.post", manager) is False
        assert has_permission("payroll.post", admin) is True


def test_staff_cannot_reach_payroll_at_all(appctx):
    _enable()
    c = _client("staff")
    assert c.get("/payroll/", follow_redirects=False).status_code == 302


def test_a_manager_cannot_finalize_a_period(appctx):
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()
    engine.process_period(p)
    db.session.commit()

    c = _client("manager")
    c.post(f"/payroll/periods/{p.id}/finalize")
    db.session.refresh(p)
    assert p.status != "Finalized"


# ── salary components and structures ──────────────────────────────────────────

def test_the_default_components_are_seeded_once(appctx):
    first = seed_default_components()
    assert first > 0
    assert seed_default_components() == 0          # idempotent
    codes = {c.code for c in SalaryComponent.query.all()}
    for expected in ("BASIC", "HRA", "MEDICAL", "CONVEYANCE", "OTHER_ALLOW",
                     "BONUS", "OVERTIME", "TAX", "LOAN", "ADVANCE", "OTHER_DED"):
        assert expected in codes


def test_components_are_rows_so_a_new_one_needs_no_migration(appctx):
    """The point of the component table: a business adds Fuel Allowance itself."""
    seed_default_components()
    db.session.add(SalaryComponent(code="FUEL", name="Fuel Allowance",
                                   component_type="earning", calc_method="fixed",
                                   sort_order=55))
    db.session.commit()

    emp = _employee()
    _structure(emp, BASIC=30000, FUEL=4000)
    p = _period()
    result = engine.calculate(emp, p)
    assert result["gross_salary"] == Decimal("34000.00")
    assert any(line["code"] == "FUEL" for line in result["earnings"])


def test_a_structure_line_cannot_repeat_a_component(appctx):
    emp = _employee()
    s = _structure(emp, BASIC=30000)
    db.session.add(SalaryStructureLine(structure_id=s.id,
                                       component_id=_component("BASIC").id,
                                       amount=Decimal("1")))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_saving_a_structure_through_the_form(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    basic, hra = _component("BASIC"), _component("HRA")
    c.post(f"/payroll/structures/employee/{emp.id}",
           data={f"amount_{basic.id}": "40000", f"amount_{hra.id}": "8000"},
           follow_redirects=True)

    s = engine.active_structure(emp.id)
    assert s is not None
    assert s.amount_for("BASIC") == Decimal("40000.0000")
    assert s.amount_for("HRA") == Decimal("8000.0000")


def test_a_structure_with_zero_basic_is_refused(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    basic = _component("BASIC")
    c.post(f"/payroll/structures/employee/{emp.id}",
           data={f"amount_{basic.id}": "0"}, follow_redirects=True)
    assert SalaryStructure.query.count() == 0


# ── periods ───────────────────────────────────────────────────────────────────

def test_creating_a_payroll_period(appctx):
    _enable()
    c = _client("admin")
    c.post("/payroll/periods/new",
           data={"name": "July 2026", "start_date": "2026-07-01",
                 "end_date": "2026-07-31"}, follow_redirects=True)
    p = PayrollPeriod.query.one()
    assert p.name == "July 2026"
    assert p.status == "Draft"


def test_two_periods_cannot_share_a_name(appctx):
    _enable()
    c = _client("admin")
    for _ in range(2):
        c.post("/payroll/periods/new",
               data={"name": "July 2026", "start_date": "2026-07-01",
                     "end_date": "2026-07-31"}, follow_redirects=True)
    assert PayrollPeriod.query.count() == 1


def test_two_periods_cannot_cover_the_same_dates(appctx):
    """The same month run twice under two names would pay twice."""
    _enable()
    c = _client("admin")
    c.post("/payroll/periods/new", data={"name": "July 2026",
                                         "start_date": "2026-07-01",
                                         "end_date": "2026-07-31"},
           follow_redirects=True)
    c.post("/payroll/periods/new", data={"name": "Jul-26",
                                         "start_date": "2026-07-01",
                                         "end_date": "2026-07-31"},
           follow_redirects=True)
    assert PayrollPeriod.query.count() == 1


def test_a_period_ending_before_it_starts_is_refused(appctx):
    _enable()
    c = _client("admin")
    c.post("/payroll/periods/new", data={"name": "Bad", "start_date": "2026-07-31",
                                         "end_date": "2026-07-01"},
           follow_redirects=True)
    assert PayrollPeriod.query.count() == 0


# ── the calculation engine ────────────────────────────────────────────────────

def test_gross_and_net_for_a_simple_structure(appctx):
    """30000 + 6000 + 2000 = 38000 gross; less 3000 tax = 35000 net."""
    emp = _employee()
    _structure(emp, BASIC=30000, HRA=6000, MEDICAL=2000, TAX=3000)
    r = engine.calculate(emp, _period())

    assert r["gross_salary"] == Decimal("38000.00")
    assert r["total_deductions"] == Decimal("3000.00")
    assert r["net_salary"] == Decimal("35000.00")


def test_every_earning_and_deduction_type_is_honoured(appctx):
    emp = _employee()
    _structure(emp, BASIC=20000, HRA=1000, MEDICAL=500, CONVEYANCE=500,
               OTHER_ALLOW=1000, BONUS=2000,
               TAX=1500, LOAN=1000, OTHER_DED=500)
    r = engine.calculate(emp, _period())

    assert r["gross_salary"] == Decimal("25000.00")     # 20000+1000+500+500+1000+2000
    assert r["total_deductions"] == Decimal("3000.00")  # 1500+1000+500
    assert r["net_salary"] == Decimal("22000.00")


def test_an_employee_without_a_structure_is_refused_not_paid_zero(appctx):
    """Paying zero silently would look like a successful run."""
    emp = _employee()
    with pytest.raises(engine.PayrollError):
        engine.calculate(emp, _period())


def test_a_structure_with_no_basic_is_refused(appctx):
    emp = _employee()
    _structure(emp, HRA=5000)
    with pytest.raises(engine.PayrollError):
        engine.calculate(emp, _period())


def test_a_percentage_component_is_taken_from_basic(appctx):
    seed_default_components()
    comp = SalaryComponent(code="PF", name="Provident Fund",
                           component_type="deduction", calc_method="percent",
                           percent_of_basic=Decimal("10"), sort_order=125)
    db.session.add(comp)
    db.session.commit()

    emp = _employee()
    s = _structure(emp, BASIC=30000)
    db.session.add(SalaryStructureLine(structure_id=s.id, component_id=comp.id,
                                       amount=0))
    db.session.commit()

    r = engine.calculate(emp, _period())
    assert r["total_deductions"] == Decimal("3000.00")      # 10% of 30000
    assert r["net_salary"] == Decimal("27000.00")


def test_an_override_replaces_a_contracted_amount_for_one_period_only(appctx):
    emp = _employee()
    _structure(emp, BASIC=30000, BONUS=1000)
    p = _period()

    r = engine.calculate(emp, p, overrides={"BONUS": Decimal("5000")})
    assert r["gross_salary"] == Decimal("35000.00")

    # The contract itself is untouched.
    assert engine.active_structure(emp.id).amount_for("BONUS") == Decimal("1000.0000")


# ── attendance-driven figures ─────────────────────────────────────────────────

def test_a_full_period_is_paid_when_attendance_is_off(appctx):
    """No attendance must not mean 'absent every day'."""
    _enable(attendance=False)
    emp = _employee()
    _structure(emp, BASIC=30000)
    r = engine.calculate(emp, _period())

    assert r["attendance_tracked"] is False
    assert r["gross_salary"] == Decimal("30000.00")
    assert r["absent_days"] == 0


def test_absence_is_deducted_at_the_day_rate(appctx):
    """June has 30 days; 30000/30 = 1000 a day. Three absences cost 3000."""
    _enable(attendance=True)
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()

    for i in range(3):
        db.session.add(Attendance(employee_id=emp.id,
                                  date=date(2026, 6, 10 + i), status="Absent"))
    db.session.add(Attendance(employee_id=emp.id, date=date(2026, 6, 1),
                              status="Present"))
    db.session.commit()

    r = engine.calculate(emp, p)
    assert r["attendance_tracked"] is True
    assert r["absent_days"] == 3
    assert r["payable_days"] == 27.0
    assert r["gross_salary"] == Decimal("27000.00")


def test_overtime_is_paid_at_the_hourly_rate_implied_by_basic(appctx):
    """30000/30 days = 1000/day; /8 h = 125/h. Four OT hours = 500."""
    _enable(attendance=True)
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()

    row = Attendance(employee_id=emp.id, date=date(2026, 6, 2), status="Present",
                     check_in=time(9, 0), check_out=time(21, 0))   # 12 h -> 4 OT
    row.recalculate()
    db.session.add(row)
    db.session.commit()

    r = engine.calculate(emp, p)
    assert r["overtime_hours"] == 4.0
    assert r["gross_salary"] == Decimal("30500.00")


def test_attendance_is_ignored_while_the_attendance_module_is_off(appctx):
    """Rows exist, but the module is off, so payroll must not read them."""
    _enable(attendance=True)
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()
    for i in range(5):
        db.session.add(Attendance(employee_id=emp.id, date=date(2026, 6, 5 + i),
                                  status="Absent"))
    db.session.commit()

    with_attendance = engine.calculate(emp, p)
    assert with_attendance["gross_salary"] == Decimal("25000.00")

    set_module("module_attendance", False, updated_by="test")
    without = engine.calculate(emp, p)
    assert without["gross_salary"] == Decimal("30000.00")


# ── advances ──────────────────────────────────────────────────────────────────

def test_an_advance_instalment_is_deducted(appctx):
    emp = _employee()
    _structure(emp, BASIC=30000)
    db.session.add(EmployeeAdvance(employee_id=emp.id, advance_date=date(2026, 5, 1),
                                   amount=Decimal("10000"), instalment=Decimal("2500"),
                                   status="Active"))
    db.session.commit()

    r = engine.calculate(emp, _period())
    assert r["total_deductions"] == Decimal("2500.00")
    assert r["net_salary"] == Decimal("27500.00")


def test_recovery_never_exceeds_what_is_outstanding(appctx):
    """An instalment bigger than the balance must not over-recover."""
    emp = _employee()
    _structure(emp, BASIC=30000)
    db.session.add(EmployeeAdvance(employee_id=emp.id, advance_date=date(2026, 5, 1),
                                   amount=Decimal("1000"), recovered=Decimal("800"),
                                   instalment=Decimal("5000"), status="Active"))
    db.session.commit()

    r = engine.calculate(emp, _period())
    assert r["total_deductions"] == Decimal("200.00")


def test_a_settled_or_cancelled_advance_is_not_recovered_again(appctx):
    emp = _employee()
    _structure(emp, BASIC=30000)
    db.session.add_all([
        EmployeeAdvance(employee_id=emp.id, advance_date=date(2026, 5, 1),
                        amount=Decimal("1000"), recovered=Decimal("1000"),
                        status="Settled"),
        EmployeeAdvance(employee_id=emp.id, advance_date=date(2026, 5, 2),
                        amount=Decimal("500"), status="Cancelled"),
    ])
    db.session.commit()

    assert engine.calculate(emp, _period())["total_deductions"] == Decimal("0.00")


def test_a_preview_does_not_change_what_is_owed(appctx):
    """Calculating must be safe to do a hundred times."""
    emp = _employee()
    _structure(emp, BASIC=30000)
    adv = EmployeeAdvance(employee_id=emp.id, advance_date=date(2026, 5, 1),
                          amount=Decimal("10000"), instalment=Decimal("2500"),
                          status="Active")
    db.session.add(adv)
    db.session.commit()

    p = _period()
    for _ in range(3):
        engine.calculate(emp, p)
    db.session.refresh(adv)
    assert adv.recovered == Decimal("0.0000")


def test_finalising_moves_the_advance_balance(appctx):
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000)
    adv = EmployeeAdvance(employee_id=emp.id, advance_date=date(2026, 5, 1),
                          amount=Decimal("10000"), instalment=Decimal("2500"),
                          status="Active")
    db.session.add(adv)
    db.session.commit()

    p = _period()
    engine.process_period(p)
    db.session.commit()

    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    db.session.refresh(adv)
    assert adv.recovered == Decimal("2500.0000")
    assert adv.status == "Active"          # 7500 still outstanding


# ── processing a period ───────────────────────────────────────────────────────

def test_processing_writes_one_payslip_per_employee(appctx):
    _enable()
    a, b = _employee("E-1", "One"), _employee("E-2", "Two")
    _structure(a, BASIC=30000)
    _structure(b, BASIC=20000)
    p = _period()

    processed, skipped = engine.process_period(p)
    db.session.commit()

    assert len(processed) == 2 and not skipped
    assert p.entries.count() == 2
    assert p.status == "Processing"


def test_processing_twice_replaces_rather_than_duplicates(appctx):
    """Running a period again must never pay it twice."""
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()

    engine.process_period(p)
    db.session.commit()
    engine.process_period(p)
    db.session.commit()

    assert p.entries.count() == 1
    assert PayrollEntry.query.count() == 1


def test_an_employee_without_a_structure_is_skipped_with_a_reason(appctx):
    _enable()
    ok = _employee("E-1", "Paid")
    _structure(ok, BASIC=30000)
    _employee("E-2", "Unpaid")          # no structure
    p = _period()

    processed, skipped = engine.process_period(p)
    db.session.commit()

    assert len(processed) == 1
    assert len(skipped) == 1
    assert "salary structure" in skipped[0][1]


def test_a_finalized_period_cannot_be_recalculated(appctx):
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()
    engine.process_period(p)
    p.status = "Finalized"
    db.session.commit()

    with pytest.raises(engine.PayrollError):
        engine.process_period(p)


def test_a_finalized_period_cannot_be_deleted(appctx):
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()
    engine.process_period(p)
    p.status = "Finalized"
    db.session.commit()

    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/delete", follow_redirects=True)
    assert PayrollPeriod.query.count() == 1


def test_the_stored_payslip_matches_what_the_engine_calculated(appctx):
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000, HRA=5000, TAX=2000)
    p = _period()
    expected = engine.calculate(emp, p)

    engine.process_period(p)
    db.session.commit()

    entry = PayrollEntry.query.one()
    assert entry.gross_salary == expected["gross_salary"]
    assert entry.net_salary == expected["net_salary"]
    assert entry.total_deductions == expected["total_deductions"]


def test_a_payslip_keeps_the_component_name_it_was_paid_under(appctx):
    """Renaming a component next year must not rewrite last year's payslip."""
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000, HRA=5000)
    p = _period()
    engine.process_period(p)
    db.session.commit()

    hra = _component("HRA")
    hra.name = "Housing Allowance (revised)"
    db.session.commit()

    entry = PayrollEntry.query.one()
    stored = [i.name for i in entry.items if i.code == "HRA"]
    assert stored == ["House Rent Allowance"]


def test_one_employee_cannot_have_two_payslips_in_one_period(appctx):
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()
    engine.process_period(p)
    db.session.commit()

    db.session.add(PayrollEntry(period_id=p.id, employee_id=emp.id,
                                gross_salary=1, net_salary=1))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


# ── the whole flow through the UI ─────────────────────────────────────────────

def test_process_then_finalize_through_the_routes(appctx):
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000, TAX=2000)
    p = _period()
    c = _client("admin")

    c.post(f"/payroll/periods/{p.id}/process", follow_redirects=True)
    db.session.refresh(p)
    assert p.status == "Processing"
    assert p.entries.count() == 1

    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    db.session.refresh(p)
    assert p.status == "Finalized"
    assert p.finalized_at is not None

    entry = PayrollEntry.query.one()
    assert c.get(f"/payroll/payslip/{entry.id}").status_code == 200
    assert c.get(f"/payroll/preview/{emp.id}/{p.id}").status_code == 200


def test_recording_an_advance_through_the_route(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/payroll/advances", data={"employee_id": str(emp.id),
                                      "advance_date": "2026-06-01",
                                      "amount": "5000", "instalment": "1000"},
           follow_redirects=True)
    adv = EmployeeAdvance.query.one()
    assert adv.amount == Decimal("5000.0000")
    assert adv.outstanding == Decimal("5000.0000")


def test_an_advance_of_zero_is_refused(appctx):
    _enable()
    emp = _employee()
    c = _client("admin")
    c.post("/payroll/advances", data={"employee_id": str(emp.id),
                                      "advance_date": "2026-06-01",
                                      "amount": "0"}, follow_redirects=True)
    assert EmployeeAdvance.query.count() == 0


# ── isolation ─────────────────────────────────────────────────────────────────

def test_payroll_tables_are_isolated_from_the_core_schema(appctx):
    insp = db.inspect(db.engine)
    for t in ("hr_salary_component", "hr_salary_structure", "hr_salary_structure_line",
              "hr_payroll_period", "hr_payroll_entry", "hr_payroll_item",
              "hr_employee_advance"):
        assert t in insp.get_table_names()

    for core in ("item", "sale", "purchase", "journal_entry", "journal_line",
                 "hr_employee", "hr_attendance"):
        cols = {c["name"] for c in insp.get_columns(core)}
        assert not any("payroll" in c or "salary_structure" in c for c in cols), \
            f"{core} gained a payroll column"


def test_phase_3a_posts_nothing_to_the_ledger(appctx):
    """Accounting integration is 3B. A finalised run must leave the GL alone."""
    from app import JournalEntry
    _enable()
    emp = _employee()
    _structure(emp, BASIC=30000)
    p = _period()
    before = JournalEntry.query.count()

    engine.process_period(p)
    db.session.commit()
    c = _client("admin")
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)

    assert JournalEntry.query.count() == before
