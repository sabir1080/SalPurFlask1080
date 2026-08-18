"""Employee self-service — what one signed-in employee may see about themselves.

Every route resolves the employee from the session (`current_employee`) and
filters by that id. No route accepts an employee id from the request, so there
is no id to tamper with: a page either shows your own records or refuses.

Records reached by their own id — a leave request, a payslip — go through
`require_own`, which 404s anything belonging to somebody else. 404 rather than
403, so a prober cannot even confirm the record exists.

Leave submission reuses the leave module's own validator and statuses. This file
contains no leave arithmetic, no payroll arithmetic and no attendance
arithmetic: it is a view onto modules that already own those rules.

Nothing here can approve, finalise, pay or post. Those routes live in the
manager-facing blueprints behind `leave.approve` and `payroll.post`, which staff
do not hold — so an employee cannot approve their own leave however they arrive.
"""

import calendar
from datetime import date, datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.services.feature_flags import module_enabled, module_required
from salpurflask.services.self_service import (current_employee, require_own,
                                               employee_required)

selfservice_bp = Blueprint("selfservice", __name__, url_prefix="/my")


def _audit(action, entity, entity_id=None, summary=""):
    try:
        from app import record_audit
        record_audit(action, entity, entity_id, summary)
    except Exception:
        pass


# ── profile ───────────────────────────────────────────────────────────────────

@selfservice_bp.route("/profile")
@module_required("module_hr")
@employee_required
def profile():
    emp = current_employee()
    return render_template("selfservice/profile.html", employee=emp,
                           attendance_on=module_enabled("module_attendance"),
                           leave_on=module_enabled("module_leave"),
                           payroll_on=module_enabled("module_payroll"))


# ── attendance (view only in this phase) ──────────────────────────────────────

@selfservice_bp.route("/attendance")
@module_required("module_attendance")
@employee_required
def attendance():
    from salpurflask.models.attendance import Attendance

    emp = current_employee()
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

    # Filtered by this employee in the query, not after fetching -- a page that
    # loads everyone and hides the rest is one template bug from a leak.
    rows = (Attendance.query
            .filter(Attendance.employee_id == emp.id,
                    Attendance.date >= start, Attendance.date <= end)
            .order_by(Attendance.date.desc()).all())

    summary = Attendance.summarise(emp.id, start, end)
    return render_template("selfservice/attendance.html", employee=emp,
                           rows=rows, summary=summary, year=year, month=month,
                           month_name=calendar.month_name[month],
                           years=range(today.year - 3, today.year + 1),
                           months=[(i, calendar.month_name[i]) for i in range(1, 13)])


# ── leave ─────────────────────────────────────────────────────────────────────

@selfservice_bp.route("/leaves")
@module_required("module_leave")
@employee_required
def leaves():
    from salpurflask.models.leave import (LeaveRequest, LeaveType,
                                          allocation_for, used_days,
                                          seed_leave_types)

    emp = current_employee()
    today = date.today()
    try:
        year = int(request.args.get("year") or today.year)
    except (TypeError, ValueError):
        year = today.year

    seed_leave_types()
    rows = (LeaveRequest.query
            .filter(LeaveRequest.employee_id == emp.id)
            .order_by(LeaveRequest.start_date.desc()).limit(200).all())

    # One pass over this employee's types only -- never over all employees.
    balances = []
    for lt in LeaveType.query.filter_by(active=True).order_by(LeaveType.name).all():
        alloc = allocation_for(emp.id, lt.id, year)
        used = used_days(emp.id, lt.id, year)
        if alloc is None and used == 0:
            continue
        balances.append({
            "leave_type": lt,
            "allocated": alloc.days if alloc else 0,
            "used": used,
            "remaining": (None if not lt.requires_allocation
                          else (alloc.remaining_days if alloc else -used)),
        })

    return render_template("selfservice/leaves.html", employee=emp, rows=rows,
                           balances=balances, year=year,
                           years=range(today.year - 3, today.year + 2))


@selfservice_bp.route("/leaves/new", methods=["GET", "POST"])
@module_required("module_leave")
@employee_required
def leave_new():
    """Raise a leave request for yourself.

    The employee id is taken from the session and pushed into the form data, so
    a posted employee_id is ignored rather than trusted. Validation and the day
    count come from the leave module's own validator -- this route adds no rules
    of its own.
    """
    from salpurflask.models.leave import LeaveRequest, LeaveType, DAY_PORTIONS
    from salpurflask.leave.routes import _validate_request

    emp = current_employee()
    types = LeaveType.query.filter_by(active=True).order_by(LeaveType.name).all()

    if request.method == "POST":
        form = request.form.to_dict()
        form["employee_id"] = str(emp.id)      # whatever was posted is discarded

        data, errors = _validate_request(form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("selfservice/leave_form.html", employee=emp,
                                   types=types, portions=DAY_PORTIONS,
                                   form_data=request.form)

        # Always Pending: an employee submits, a manager decides. There is no
        # path here that reaches Approved.
        row = LeaveRequest(**data, status="Pending",
                           created_by_id=getattr(current_user, "id", None))
        db.session.add(row)
        db.session.commit()
        _audit("create", "leave_request", row.id,
               f"self-service {emp.code} {row.start_date}–{row.end_date}")
        flash(f"Leave request submitted for approval ({row.days:g} day(s)).",
              "success")
        return redirect(url_for("selfservice.leaves"))

    return render_template("selfservice/leave_form.html", employee=emp,
                           types=types, portions=DAY_PORTIONS, form_data={})


@selfservice_bp.route("/leaves/<int:row_id>/cancel", methods=["POST"])
@module_required("module_leave")
@employee_required
def leave_cancel(row_id):
    """Withdraw your own request.

    Only while it is still Draft or Pending: once a manager has approved it the
    days may already have been counted by a payroll run, and withdrawing it is
    their decision, made through the leave module's own cancel route.
    """
    from salpurflask.models.leave import LeaveRequest

    row = db.session.get(LeaveRequest, row_id)
    if row is None:
        abort(404)
    require_own(row)                       # somebody else's request is a 404

    if row.status not in ("Draft", "Pending"):
        flash(f"This request is {row.status.lower()} and can no longer be "
              f"withdrawn here. Ask your manager.", "warning")
        return redirect(url_for("selfservice.leaves"))

    row.status = "Cancelled"
    row.decided_at = datetime.utcnow()
    db.session.commit()
    _audit("cancel", "leave_request", row.id, f"self-service {row.employee.code}")
    flash("Leave request withdrawn.", "success")
    return redirect(url_for("selfservice.leaves"))


# ── payslips ──────────────────────────────────────────────────────────────────

@selfservice_bp.route("/payslips")
@module_required("module_payroll")
@employee_required
def payslips():
    """Finalized payslips only.

    A draft or processing period is still being worked on and its figures can
    change; showing them would have people querying a number that was never
    theirs to see yet.
    """
    from salpurflask.models.payroll import PayrollEntry, PayrollPeriod

    emp = current_employee()
    rows = (PayrollEntry.query
            .join(PayrollPeriod, PayrollEntry.period_id == PayrollPeriod.id)
            .filter(PayrollEntry.employee_id == emp.id,
                    PayrollPeriod.status == "Finalized")
            .order_by(PayrollPeriod.start_date.desc()).all())
    return render_template("selfservice/payslips.html", employee=emp, rows=rows)


@selfservice_bp.route("/payslips/<int:entry_id>")
@module_required("module_payroll")
@employee_required
def payslip(entry_id):
    from salpurflask.models.payroll import PayrollEntry

    entry = db.session.get(PayrollEntry, entry_id)
    if entry is None:
        abort(404)
    require_own(entry)                     # another employee's payslip is a 404

    if entry.period is None or entry.period.status != "Finalized":
        abort(404)

    # What was paid against this period, so the employee can see their own
    # payment state -- read-only, and only for their own payslip.
    payments = []
    try:
        from salpurflask.models.payroll_payment import period_payment_status
        status = period_payment_status(entry.period)
        payments = entry.period.payments.filter_by(is_reversed=False).all()
    except Exception:
        status = None
    return render_template("selfservice/payslip_detail.html", entry=entry,
                           period=entry.period, payment_status=status,
                           payments=payments)


__all__ = ["selfservice_bp"]
