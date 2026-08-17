"""Attendance routes — daily entry, history and monthly summary.

Gated twice, exactly as HR is: `module_required("module_attendance")` decides
whether the feature exists, `permission_required(...)` decides whether this user
may use it. The module flag already refuses itself when HR is off, because
attendance declares HR as its parent in feature_flags.MODULE_REQUIRES.
"""

import calendar
from datetime import datetime, date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from salpurflask.extensions import db
from salpurflask.models.attendance import Attendance, ATTENDANCE_STATUSES
from salpurflask.models.hr import Department, Employee
from salpurflask.services.feature_flags import module_required
from salpurflask.services.hr_permissions import permission_required

attendance_bp = Blueprint("attendance", __name__, url_prefix="/attendance")


# ── helpers ───────────────────────────────────────────────────────────────────

def _audit(action, entity, entity_id=None, summary=""):
    try:
        from app import record_audit
        record_audit(action, entity, entity_id, summary)
    except Exception:
        pass


def _parse_date(raw, field="Date"):
    raw = (raw or "").strip()
    if not raw:
        raise ValueError(f"{field} is required.")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field} must be a valid date.")


def _parse_time(raw):
    """'HH:MM' (or 'HH:MM:SS') -> time, blank -> None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise ValueError("Time must be in HH:MM format.")


def _validate(form, record=None):
    """Validate one attendance row. Returns (data, errors)."""
    errors = []
    data = {}

    raw_emp = (form.get("employee_id") or "").strip()
    employee = None
    if not raw_emp:
        errors.append("Employee is required.")
    else:
        try:
            employee = db.session.get(Employee, int(raw_emp))
        except (TypeError, ValueError):
            employee = None
        if employee is None:
            errors.append("Employee not found.")
    data["employee_id"] = employee.id if employee else None

    try:
        d = _parse_date(form.get("date"), "Attendance date")
        # A date in the future is a typo, not a record of anything.
        if d > date.today():
            errors.append("Attendance cannot be recorded for a future date.")
        else:
            data["date"] = d
            # An employee cannot attend before they were hired.
            if employee and employee.joining_date and d < employee.joining_date:
                errors.append(
                    f"{employee.name} joined on {employee.joining_date}, so there "
                    f"is no attendance for {d}.")
    except ValueError as e:
        errors.append(str(e))

    status = (form.get("status") or "Present").strip()
    if status not in ATTENDANCE_STATUSES:
        errors.append("Invalid attendance status.")
    data["status"] = status

    for field, label in (("check_in", "Check-in"), ("check_out", "Check-out")):
        try:
            data[field] = _parse_time(form.get(field))
        except ValueError as e:
            errors.append(f"{label}: {e}")
            data[field] = None

    data["remarks"] = (form.get("remarks") or "").strip() or None

    # One row per employee per day — the table enforces it, but catching it here
    # gives the user a sentence instead of an integrity error.
    if data.get("employee_id") and data.get("date"):
        clash = Attendance.query.filter_by(employee_id=data["employee_id"],
                                           date=data["date"])
        if record is not None:
            clash = clash.filter(Attendance.id != record.id)
        if clash.first():
            name = employee.name if employee else "This employee"
            errors.append(f"{name} already has attendance recorded for {data['date']}.")

    return data, errors


def _employees():
    return (Employee.query.filter_by(active=True)
            .order_by(Employee.code).all())


# ── daily attendance / history ────────────────────────────────────────────────

@attendance_bp.route("/")
@module_required("module_attendance")
@permission_required("attendance.view")
def index():
    """Attendance history with search and filters."""
    emp_id = (request.args.get("employee") or "").strip()
    status = (request.args.get("status") or "").strip()
    dept_id = (request.args.get("department") or "").strip()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    query = Attendance.query.join(Employee, Attendance.employee_id == Employee.id)

    if emp_id.isdigit():
        query = query.filter(Attendance.employee_id == int(emp_id))
    if status:
        query = query.filter(Attendance.status == status)
    if dept_id.isdigit():
        query = query.filter(Employee.department_id == int(dept_id))
    for raw, op in ((date_from, "from"), (date_to, "to")):
        if not raw:
            continue
        try:
            d = _parse_date(raw)
        except ValueError:
            continue
        query = query.filter(Attendance.date >= d if op == "from"
                             else Attendance.date <= d)

    rows = query.order_by(Attendance.date.desc(), Employee.code).limit(500).all()

    totals = {"present": 0, "absent": 0, "late": 0, "half_day": 0, "leave": 0,
              "hours": 0.0, "overtime": 0.0}
    key = {"Present": "present", "Absent": "absent", "Late": "late",
           "Half Day": "half_day", "Leave": "leave"}
    for r in rows:
        totals[key[r.status]] += 1
        totals["hours"] += float(r.working_hours or 0)
        totals["overtime"] += float(r.overtime_hours or 0)
    totals["hours"] = round(totals["hours"], 2)
    totals["overtime"] = round(totals["overtime"], 2)

    return render_template(
        "attendance/index.html",
        rows=rows, totals=totals,
        employees=_employees(),
        departments=Department.query.order_by(Department.name).all(),
        statuses=ATTENDANCE_STATUSES,
        filters={"employee": emp_id, "status": status, "department": dept_id,
                 "from": date_from, "to": date_to},
    )


@attendance_bp.route("/daily", methods=["GET", "POST"])
@module_required("module_attendance")
@permission_required("attendance.view")
def daily():
    """One day at a time: every active employee, with whatever is recorded."""
    raw = (request.args.get("date") or "").strip()
    try:
        day = _parse_date(raw) if raw else date.today()
    except ValueError:
        day = date.today()

    employees = _employees()
    existing = {a.employee_id: a for a in
                Attendance.query.filter_by(date=day).all()}

    return render_template(
        "attendance/daily.html",
        day=day,
        prev_day=day - timedelta(days=1),
        next_day=day + timedelta(days=1),
        employees=employees,
        existing=existing,
        statuses=ATTENDANCE_STATUSES,
        recorded=len(existing),
    )


@attendance_bp.route("/new", methods=["GET", "POST"])
@module_required("module_attendance")
@permission_required("attendance.create")
def create():
    if request.method == "POST":
        data, errors = _validate(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("attendance/form.html", record=None,
                                   form_data=request.form,
                                   employees=_employees(),
                                   statuses=ATTENDANCE_STATUSES)
        row = Attendance(**data)
        row.recalculate()
        db.session.add(row)
        db.session.commit()
        _audit("create", "attendance", row.id,
               f"{row.employee.name} {row.date} {row.status}")
        flash(f"Attendance recorded for {row.employee.name} on {row.date}.", "success")
        return redirect(url_for("attendance.index"))

    prefill = {}
    if request.args.get("employee"):
        prefill["employee_id"] = request.args.get("employee")
    if request.args.get("date"):
        prefill["date"] = request.args.get("date")
    return render_template("attendance/form.html", record=None, form_data=prefill,
                           employees=_employees(), statuses=ATTENDANCE_STATUSES)


@attendance_bp.route("/<int:row_id>/edit", methods=["GET", "POST"])
@module_required("module_attendance")
@permission_required("attendance.edit")
def edit(row_id):
    row = db.session.get(Attendance, row_id)
    if row is None:
        abort(404)

    if request.method == "POST":
        data, errors = _validate(request.form, record=row)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("attendance/form.html", record=row,
                                   form_data=request.form,
                                   employees=_employees(),
                                   statuses=ATTENDANCE_STATUSES)
        for field, value in data.items():
            setattr(row, field, value)
        row.recalculate()
        db.session.commit()
        _audit("edit", "attendance", row.id,
               f"{row.employee.name} {row.date} {row.status}")
        flash("Attendance updated.", "success")
        return redirect(url_for("attendance.index"))

    return render_template("attendance/form.html", record=row, form_data={},
                           employees=_employees(), statuses=ATTENDANCE_STATUSES)


@attendance_bp.route("/<int:row_id>/delete", methods=["POST"])
@module_required("module_attendance")
@permission_required("attendance.delete")
def delete(row_id):
    row = db.session.get(Attendance, row_id)
    if row is None:
        abort(404)
    summary = f"{row.employee.name} {row.date} {row.status}"
    db.session.delete(row)
    db.session.commit()
    _audit("delete", "attendance", row_id, summary)
    flash("Attendance record deleted.", "success")
    return redirect(url_for("attendance.index"))


@attendance_bp.route("/mark", methods=["POST"])
@module_required("module_attendance")
@permission_required("attendance.create")
def mark():
    """Save the whole day from the daily sheet.

    Rows left blank are skipped rather than written as Absent: not having marked
    someone yet is not the same as recording that they did not come.
    """
    try:
        day = _parse_date(request.form.get("date"), "Attendance date")
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("attendance.daily"))

    if day > date.today():
        flash("Attendance cannot be recorded for a future date.", "danger")
        return redirect(url_for("attendance.daily", date=day.isoformat()))

    existing = {a.employee_id: a for a in Attendance.query.filter_by(date=day).all()}
    saved = updated = skipped = 0

    for emp in _employees():
        status = (request.form.get(f"status_{emp.id}") or "").strip()
        if not status:
            skipped += 1
            continue
        if status not in ATTENDANCE_STATUSES:
            continue
        if emp.joining_date and day < emp.joining_date:
            skipped += 1
            continue

        try:
            cin = _parse_time(request.form.get(f"check_in_{emp.id}"))
            cout = _parse_time(request.form.get(f"check_out_{emp.id}"))
        except ValueError:
            cin = cout = None
        remarks = (request.form.get(f"remarks_{emp.id}") or "").strip() or None

        row = existing.get(emp.id)
        if row is None:
            row = Attendance(employee_id=emp.id, date=day)
            db.session.add(row)
            saved += 1
        else:
            updated += 1
        row.status, row.check_in, row.check_out, row.remarks = status, cin, cout, remarks
        row.recalculate()

    db.session.commit()
    _audit("mark", "attendance", None,
           f"{day}: {saved} new, {updated} updated")
    flash(f"Attendance for {day} saved — {saved} new, {updated} updated"
          + (f", {skipped} left unmarked." if skipped else "."), "success")
    return redirect(url_for("attendance.daily", date=day.isoformat()))


# ── monthly summary ───────────────────────────────────────────────────────────

@attendance_bp.route("/summary")
@module_required("module_attendance")
@permission_required("attendance.view")
def summary():
    """Per-employee totals for one month.

    Built on Attendance.summarise(), the same call payroll will make in phase 3,
    so what is shown here and what a payroll run counts cannot drift apart.
    """
    today = date.today()
    try:
        year = int(request.args.get("year") or today.year)
        month = int(request.args.get("month") or today.month)
        if not 1 <= month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month = today.year, today.month

    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])

    dept_id = (request.args.get("department") or "").strip()
    employees = Employee.query.filter_by(active=True)
    if dept_id.isdigit():
        employees = employees.filter(Employee.department_id == int(dept_id))
    employees = employees.order_by(Employee.code).all()

    rows = []
    for emp in employees:
        s = Attendance.summarise(emp.id, start, end)
        s["employee"] = emp
        rows.append(s)

    grand = {k: round(sum(r[k] for r in rows), 2) for k in
             ("present", "absent", "late", "half_day", "leave",
              "worked_days", "working_hours", "overtime_hours")}

    return render_template(
        "attendance/summary.html",
        rows=rows, grand=grand, year=year, month=month,
        month_name=calendar.month_name[month],
        days_in_month=calendar.monthrange(year, month)[1],
        years=range(today.year - 5, today.year + 2),
        months=[(i, calendar.month_name[i]) for i in range(1, 13)],
        departments=Department.query.order_by(Department.name).all(),
        filters={"department": dept_id},
    )


__all__ = ["attendance_bp"]
