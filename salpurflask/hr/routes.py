"""HR routes — employee, department and designation CRUD.

Every route is gated twice: `module_required("module_hr")` refuses it while the
module is off, and `permission_required(...)` refuses it for a user whose role
does not hold the permission. The two are independent — a module that is on
still respects permissions, and an admin still cannot reach a module that is off.

Writes follow the existing house pattern: mutate, commit, then `record_audit`
after the commit so a failed audit can never roll back real work.
"""

from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from salpurflask.extensions import db
from salpurflask.models.hr import (Department, Designation, Employee,
                                   EMPLOYMENT_STATUSES)
from salpurflask.services.feature_flags import module_required
from salpurflask.services.hr_permissions import permission_required

hr_bp = Blueprint("hr", __name__, url_prefix="/hr")


# ── helpers ───────────────────────────────────────────────────────────────────

def _audit(action, entity, entity_id=None, summary=""):
    """Best-effort audit via the app's existing helper (imported lazily so this
    module never imports app at import time)."""
    try:
        from app import record_audit
        record_audit(action, entity, entity_id, summary)
    except Exception:
        pass


def _money(raw):
    """A money field from a form -> Decimal-safe string. Blank means zero."""
    from decimal import Decimal, InvalidOperation
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        return Decimal("0")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError("must be a number")
    if value < 0:
        raise ValueError("cannot be negative")
    return value


def _parse_date(raw, field="Date"):
    raw = (raw or "").strip()
    if not raw:
        raise ValueError(f"{field} is required")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field} must be a valid date")


def _employee_form(form, employee=None):
    """Validate the employee form. Returns (data, errors)."""
    errors = []
    data = {}

    code = (form.get("code") or "").strip()
    if not code:
        errors.append("Employee code is required.")
    else:
        clash = Employee.query.filter(Employee.code == code)
        if employee is not None:
            clash = clash.filter(Employee.id != employee.id)
        if clash.first():
            errors.append(f"Employee code '{code}' is already used.")
    data["code"] = code

    name = (form.get("name") or "").strip()
    if not name:
        errors.append("Name is required.")
    data["name"] = name

    try:
        data["joining_date"] = _parse_date(form.get("joining_date"), "Joining date")
    except ValueError as e:
        errors.append(str(e))

    status = (form.get("employment_status") or "Permanent").strip()
    if status not in EMPLOYMENT_STATUSES:
        errors.append("Invalid employment status.")
    data["employment_status"] = status

    for field, label in (("basic_salary", "Basic salary"),
                         ("allowances", "Allowances"),
                         ("deductions", "Deductions")):
        try:
            data[field] = _money(form.get(field))
        except ValueError as e:
            errors.append(f"{label} {e}.")

    for fk, model, label in (("department_id", Department, "Department"),
                             ("designation_id", Designation, "Designation")):
        raw = (form.get(fk) or "").strip()
        if not raw:
            data[fk] = None
            continue
        try:
            obj = db.session.get(model, int(raw))
        except (TypeError, ValueError):
            obj = None
        if obj is None:
            errors.append(f"{label} not found.")
            data[fk] = None
        else:
            data[fk] = obj.id

    for field in ("phone", "email", "address", "cnic", "notes", "documents"):
        data[field] = (form.get(field) or "").strip() or None

    data["active"] = bool(form.get("active"))
    return data, errors


# ── employees ─────────────────────────────────────────────────────────────────

@hr_bp.route("/employees")
@module_required("module_hr")
@permission_required("hr.view")
def employees():
    q          = (request.args.get("q") or "").strip()
    dept_id    = (request.args.get("department") or "").strip()
    desig_id   = (request.args.get("designation") or "").strip()
    status     = (request.args.get("status") or "").strip()
    active     = (request.args.get("active") or "").strip()

    query = Employee.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Employee.name.ilike(like),
                                    Employee.code.ilike(like),
                                    Employee.phone.ilike(like),
                                    Employee.email.ilike(like)))
    if dept_id.isdigit():
        query = query.filter(Employee.department_id == int(dept_id))
    if desig_id.isdigit():
        query = query.filter(Employee.designation_id == int(desig_id))
    if status:
        query = query.filter(Employee.employment_status == status)
    if active in ("1", "0"):
        query = query.filter(Employee.active == (active == "1"))

    return render_template(
        "hr/employees.html",
        employees=query.order_by(Employee.code).all(),
        departments=Department.query.order_by(Department.name).all(),
        designations=Designation.query.order_by(Designation.name).all(),
        statuses=EMPLOYMENT_STATUSES,
        filters={"q": q, "department": dept_id, "designation": desig_id,
                 "status": status, "active": active},
    )


@hr_bp.route("/employees/new", methods=["GET", "POST"])
@module_required("module_hr")
@permission_required("hr.create")
def employee_new():
    if request.method == "POST":
        data, errors = _employee_form(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("hr/employee_form.html", employee=None,
                                   form_data=request.form,
                                   departments=Department.query.order_by(Department.name).all(),
                                   designations=Designation.query.order_by(Designation.name).all(),
                                   statuses=EMPLOYMENT_STATUSES)
        emp = Employee(**data)
        db.session.add(emp)
        db.session.commit()
        _audit("create", "employee", emp.id, f"{emp.code} {emp.name}")
        flash(f"Employee {emp.code} added.", "success")
        return redirect(url_for("hr.employees"))

    return render_template("hr/employee_form.html", employee=None, form_data={},
                           departments=Department.query.order_by(Department.name).all(),
                           designations=Designation.query.order_by(Designation.name).all(),
                           statuses=EMPLOYMENT_STATUSES)


@hr_bp.route("/employees/<int:emp_id>")
@module_required("module_hr")
@permission_required("hr.view")
def employee_profile(emp_id):
    emp = db.session.get(Employee, emp_id)
    if emp is None:
        abort(404)
    return render_template("hr/employee_profile.html", employee=emp)


@hr_bp.route("/employees/<int:emp_id>/edit", methods=["GET", "POST"])
@module_required("module_hr")
@permission_required("hr.edit")
def employee_edit(emp_id):
    emp = db.session.get(Employee, emp_id)
    if emp is None:
        abort(404)

    if request.method == "POST":
        data, errors = _employee_form(request.form, employee=emp)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("hr/employee_form.html", employee=emp,
                                   form_data=request.form,
                                   departments=Department.query.order_by(Department.name).all(),
                                   designations=Designation.query.order_by(Designation.name).all(),
                                   statuses=EMPLOYMENT_STATUSES)
        for field, value in data.items():
            setattr(emp, field, value)
        db.session.commit()
        _audit("edit", "employee", emp.id, f"{emp.code} {emp.name}")
        flash(f"Employee {emp.code} updated.", "success")
        return redirect(url_for("hr.employee_profile", emp_id=emp.id))

    return render_template("hr/employee_form.html", employee=emp, form_data={},
                           departments=Department.query.order_by(Department.name).all(),
                           designations=Designation.query.order_by(Designation.name).all(),
                           statuses=EMPLOYMENT_STATUSES)


@hr_bp.route("/employees/<int:emp_id>/delete", methods=["POST"])
@module_required("module_hr")
@permission_required("hr.delete")
def employee_delete(emp_id):
    emp = db.session.get(Employee, emp_id)
    if emp is None:
        abort(404)

    # An employee with financial or operational history is deactivated, never
    # deleted — payslips are already in the GL and attendance is a record of
    # what happened. Only a row that has never been used can truly go.
    blockers = []
    for table, label in (("hr_attendance", "attendance"), ("hr_payroll_entry", "payroll")):
        try:
            insp = db.inspect(db.engine)
            if table in insp.get_table_names():
                found = db.session.execute(
                    db.text(f"SELECT 1 FROM {table} WHERE employee_id = :i LIMIT 1"),
                    {"i": emp.id}).first()
                if found:
                    blockers.append(label)
        except Exception:
            pass

    if blockers:
        emp.active = False
        db.session.commit()
        _audit("deactivate", "employee", emp.id, f"{emp.code} has {', '.join(blockers)} history")
        flash(f"{emp.name} has {' and '.join(blockers)} history, so the record was "
              f"deactivated instead of deleted.", "warning")
        return redirect(url_for("hr.employees"))

    code, name = emp.code, emp.name
    db.session.delete(emp)
    db.session.commit()
    _audit("delete", "employee", emp_id, f"{code} {name}")
    flash(f"Employee {code} deleted.", "success")
    return redirect(url_for("hr.employees"))


# ── departments & designations ────────────────────────────────────────────────

def _lookup_list(model, template, title):
    return render_template(template,
                           rows=model.query.order_by(model.name).all(),
                           title=title)


@hr_bp.route("/departments", methods=["GET", "POST"])
@module_required("module_hr")
@permission_required("hr.view")
def departments():
    if request.method == "POST":
        from salpurflask.services.hr_permissions import has_permission
        if not has_permission("hr.create"):
            flash("You do not have permission to add departments.", "danger")
            return redirect(url_for("hr.departments"))
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Department name is required.", "danger")
        elif Department.query.filter(db.func.lower(Department.name) == name.lower()).first():
            flash(f"Department '{name}' already exists.", "danger")
        else:
            d = Department(name=name,
                           description=(request.form.get("description") or "").strip() or None)
            db.session.add(d)
            db.session.commit()
            _audit("create", "department", d.id, name)
            flash(f"Department '{name}' added.", "success")
        return redirect(url_for("hr.departments"))
    return _lookup_list(Department, "hr/departments.html", "Departments")


@hr_bp.route("/departments/<int:row_id>/delete", methods=["POST"])
@module_required("module_hr")
@permission_required("hr.delete")
def department_delete(row_id):
    d = db.session.get(Department, row_id)
    if d is None:
        abort(404)
    if Employee.query.filter_by(department_id=d.id).first():
        flash(f"'{d.name}' still has employees, so it cannot be deleted.", "warning")
        return redirect(url_for("hr.departments"))
    name = d.name
    db.session.delete(d)
    db.session.commit()
    _audit("delete", "department", row_id, name)
    flash(f"Department '{name}' deleted.", "success")
    return redirect(url_for("hr.departments"))


@hr_bp.route("/designations", methods=["GET", "POST"])
@module_required("module_hr")
@permission_required("hr.view")
def designations():
    if request.method == "POST":
        from salpurflask.services.hr_permissions import has_permission
        if not has_permission("hr.create"):
            flash("You do not have permission to add designations.", "danger")
            return redirect(url_for("hr.designations"))
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Designation name is required.", "danger")
        elif Designation.query.filter(db.func.lower(Designation.name) == name.lower()).first():
            flash(f"Designation '{name}' already exists.", "danger")
        else:
            d = Designation(name=name,
                            description=(request.form.get("description") or "").strip() or None)
            db.session.add(d)
            db.session.commit()
            _audit("create", "designation", d.id, name)
            flash(f"Designation '{name}' added.", "success")
        return redirect(url_for("hr.designations"))
    return _lookup_list(Designation, "hr/designations.html", "Designations")


@hr_bp.route("/designations/<int:row_id>/delete", methods=["POST"])
@module_required("module_hr")
@permission_required("hr.delete")
def designation_delete(row_id):
    d = db.session.get(Designation, row_id)
    if d is None:
        abort(404)
    if Employee.query.filter_by(designation_id=d.id).first():
        flash(f"'{d.name}' still has employees, so it cannot be deleted.", "warning")
        return redirect(url_for("hr.designations"))
    name = d.name
    db.session.delete(d)
    db.session.commit()
    _audit("delete", "designation", row_id, name)
    flash(f"Designation '{name}' deleted.", "success")
    return redirect(url_for("hr.designations"))


__all__ = ["hr_bp"]
