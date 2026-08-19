"""Automatic employee codes: EMP-0001, EMP-0002, …

The codes are an identifier a payslip prints and an audit entry names, so the
tests that matter are the ones about a code never changing under a record and
never being handed out twice.
"""
from datetime import date

import pytest

from app import app as flask_app, db, User, pwd_context
from salpurflask.models.hr import (Employee, next_employee_code,
                                   EMPLOYEE_CODE_PREFIX, EMPLOYEE_CODE_SCOPE)
from salpurflask.models.models import DocumentSequence
from salpurflask.services.feature_flags import set_module


def _enable():
    set_module("module_hr", True, updated_by="test")


def _client(role="admin"):
    email = f"{role}@code.com"
    db.session.add(User(name=role.title(), email=email,
                        password=pwd_context.hash("secret123"),
                        verified=True, role=role))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _form(**over):
    data = {"name": "Worker", "joining_date": "2026-01-15",
            "employment_status": "Permanent", "basic_salary": "30000",
            "allowances": "0", "deductions": "0", "active": "1"}
    data.update(over)
    return data


def _counter():
    return DocumentSequence.query.filter_by(doc_type="employee",
                                            year=EMPLOYEE_CODE_SCOPE).first()


# ── 1-2. the sequence ─────────────────────────────────────────────────────────

def test_the_first_employee_gets_emp_0001(appctx):
    _enable()
    c = _client()
    c.post("/hr/employees/new", data=_form(name="First"), follow_redirects=True)
    assert Employee.query.one().code == "EMP-0001"


def test_the_next_employee_gets_emp_0002(appctx):
    _enable()
    c = _client()
    for name in ("First", "Second", "Third"):
        c.post("/hr/employees/new", data=_form(name=name), follow_redirects=True)

    codes = [e.code for e in Employee.query.order_by(Employee.id).all()]
    assert codes == ["EMP-0001", "EMP-0002", "EMP-0003"]


def test_the_code_is_zero_padded_to_four_digits(appctx):
    _enable()
    seq = DocumentSequence(doc_type="employee", year=EMPLOYEE_CODE_SCOPE,
                           prefix=EMPLOYEE_CODE_PREFIX, next_number=42)
    db.session.add(seq)
    db.session.commit()

    c = _client()
    c.post("/hr/employees/new", data=_form(), follow_redirects=True)
    assert Employee.query.one().code == "EMP-0042"


# ── 3. existing codes are left alone ──────────────────────────────────────────

def test_an_existing_hand_typed_code_is_preserved(appctx):
    """A database that already holds codes from before this existed."""
    _enable()
    old = Employee(code="STAFF-99", name="Old Hand", joining_date=date(2020, 1, 1))
    db.session.add(old)
    db.session.commit()

    c = _client()
    c.post("/hr/employees/new", data=_form(name="New Hire"), follow_redirects=True)

    db.session.refresh(old)
    assert old.code == "STAFF-99"                    # untouched
    assert Employee.query.filter_by(name="New Hire").one().code == "EMP-0001"


def test_the_generator_skips_a_code_already_taken_by_hand(appctx):
    """Somebody typed EMP-0001 before the counter existed."""
    _enable()
    db.session.add(Employee(code="EMP-0001", name="Typed",
                            joining_date=date(2020, 1, 1)))
    db.session.commit()

    c = _client()
    c.post("/hr/employees/new", data=_form(name="Generated"), follow_redirects=True)

    generated = Employee.query.filter_by(name="Generated").one()
    assert generated.code == "EMP-0002"
    assert Employee.query.filter_by(code="EMP-0001").count() == 1


# ── 4. a deleted code is never reused ─────────────────────────────────────────

def test_a_deleted_employees_code_is_not_handed_out_again(appctx):
    """The counter has already moved past it; a new hire gets a fresh number."""
    _enable()
    c = _client()
    c.post("/hr/employees/new", data=_form(name="Leaver"), follow_redirects=True)
    leaver = Employee.query.one()
    assert leaver.code == "EMP-0001"

    db.session.delete(leaver)
    db.session.commit()
    assert Employee.query.count() == 0

    c.post("/hr/employees/new", data=_form(name="Replacement"), follow_redirects=True)
    assert Employee.query.one().code == "EMP-0002"


def test_deleting_the_last_employee_does_not_rewind_the_counter(appctx):
    _enable()
    c = _client()
    for name in ("A", "B", "C"):
        c.post("/hr/employees/new", data=_form(name=name), follow_redirects=True)
    before = _counter().next_number

    for e in Employee.query.all():
        db.session.delete(e)
    db.session.commit()

    assert _counter().next_number == before
    c.post("/hr/employees/new", data=_form(name="D"), follow_redirects=True)
    assert Employee.query.one().code == "EMP-0004"


# ── 5. duplicates are impossible ──────────────────────────────────────────────

def test_two_employees_can_never_share_a_code(appctx):
    _enable()
    c = _client()
    for i in range(6):
        c.post("/hr/employees/new", data=_form(name=f"P{i}"), follow_redirects=True)

    codes = [e.code for e in Employee.query.all()]
    assert len(codes) == len(set(codes)) == 6


def test_the_database_refuses_a_duplicate_code(appctx):
    """The unique constraint is the last line of defence."""
    _enable()
    db.session.add(Employee(code="EMP-0001", name="One",
                            joining_date=date(2026, 1, 1)))
    db.session.commit()

    db.session.add(Employee(code="EMP-0001", name="Two",
                            joining_date=date(2026, 1, 1)))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


# ── 6. concurrency ────────────────────────────────────────────────────────────

def test_allocating_twice_in_one_transaction_gives_two_codes(appctx):
    """The counter advances per call, so two saves racing through the same
    transaction cannot come away with the same number."""
    _enable()
    first = next_employee_code()
    second = next_employee_code()
    assert first == "EMP-0001"
    assert second == "EMP-0002"
    assert first != second


def test_a_failed_save_does_not_burn_a_number(appctx):
    """A validation error must not leave a hole in the sequence.

    The number is allocated inside the transaction that writes the employee, so
    a rollback takes the counter back with it.
    """
    _enable()
    c = _client()
    # name is required -- this save fails validation before any code is issued
    c.post("/hr/employees/new", data=_form(name=""), follow_redirects=True)
    assert Employee.query.count() == 0

    c.post("/hr/employees/new", data=_form(name="Real"), follow_redirects=True)
    assert Employee.query.one().code == "EMP-0001"


def test_a_rolled_back_allocation_returns_its_number(appctx):
    _enable()
    code = next_employee_code()
    assert code == "EMP-0001"
    db.session.rollback()

    c = _client()
    c.post("/hr/employees/new", data=_form(name="After rollback"),
           follow_redirects=True)
    assert Employee.query.one().code == "EMP-0001"


# ── 7. editing never changes the code ─────────────────────────────────────────

def test_editing_an_employee_keeps_their_code(appctx):
    """A payslip, an attendance sheet and an audit entry all name an employee by
    their code. Changing it would rewrite what those records refer to."""
    _enable()
    c = _client()
    c.post("/hr/employees/new", data=_form(name="Before"), follow_redirects=True)
    emp = Employee.query.one()
    assert emp.code == "EMP-0001"

    c.post(f"/hr/employees/{emp.id}/edit",
           data=_form(name="After", basic_salary="45000"), follow_redirects=True)

    db.session.refresh(emp)
    assert emp.code == "EMP-0001"
    assert emp.name == "After"
    assert float(emp.basic_salary) == 45000


def test_a_posted_code_is_ignored_on_edit(appctx):
    """The form no longer sends one, but a crafted POST must not rename either."""
    _enable()
    c = _client()
    c.post("/hr/employees/new", data=_form(name="Worker"), follow_redirects=True)
    emp = Employee.query.one()

    c.post(f"/hr/employees/{emp.id}/edit",
           data=_form(name="Worker", code="HACKED-1"), follow_redirects=True)

    db.session.refresh(emp)
    assert emp.code == "EMP-0001"
    assert Employee.query.filter_by(code="HACKED-1").count() == 0


def test_a_posted_code_is_ignored_on_creation(appctx):
    _enable()
    c = _client()
    c.post("/hr/employees/new", data=_form(name="Worker", code="CHOSEN-1"),
           follow_redirects=True)
    assert Employee.query.one().code == "EMP-0001"


def test_editing_does_not_advance_the_counter(appctx):
    _enable()
    c = _client()
    c.post("/hr/employees/new", data=_form(name="Worker"), follow_redirects=True)
    emp = Employee.query.one()
    before = _counter().next_number

    c.post(f"/hr/employees/{emp.id}/edit", data=_form(name="Renamed"),
           follow_redirects=True)
    assert _counter().next_number == before


# ── the form ──────────────────────────────────────────────────────────────────

def test_the_form_no_longer_asks_for_a_code(appctx):
    _enable()
    c = _client()
    body = c.get("/hr/employees/new").get_data(as_text=True)
    assert 'name="code"' not in body
    assert "Assigned on save" in body


def test_the_edit_form_shows_the_code_read_only(appctx):
    _enable()
    c = _client()
    c.post("/hr/employees/new", data=_form(name="Worker"), follow_redirects=True)
    emp = Employee.query.one()

    body = c.get(f"/hr/employees/{emp.id}/edit").get_data(as_text=True)
    assert "EMP-0001" in body
    assert 'name="code"' not in body


# ── the counter itself ────────────────────────────────────────────────────────

def test_the_counter_does_not_disturb_invoice_numbering(appctx):
    """Employee codes share the sequence table with invoices but not its rows."""
    _enable()
    next_employee_code()
    db.session.commit()

    rows = DocumentSequence.query.filter_by(doc_type="employee").all()
    assert len(rows) == 1
    assert rows[0].year == EMPLOYEE_CODE_SCOPE
    # nothing was created for purchases or sales
    assert DocumentSequence.query.filter(
        DocumentSequence.doc_type.in_(("purchase", "sale"))).count() == 0


def test_employee_numbering_does_not_restart_each_year(appctx):
    """Invoices restart annually; staff numbers run on for the life of the business."""
    _enable()
    c = _client()
    c.post("/hr/employees/new", data=_form(name="A", joining_date="2025-06-01"),
           follow_redirects=True)
    c.post("/hr/employees/new", data=_form(name="B", joining_date="2027-06-01"),
           follow_redirects=True)

    codes = sorted(e.code for e in Employee.query.all())
    assert codes == ["EMP-0001", "EMP-0002"]
    assert DocumentSequence.query.filter_by(doc_type="employee").count() == 1
