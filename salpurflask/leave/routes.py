"""Leave routes — types, allocations, requests and approval.

Gated twice like every other optional module: `module_required("module_leave")`
decides whether the feature exists, `permission_required(...)` decides whether
this user may use it. Approving is `leave.approve`, which staff do not hold, so
nobody signs off their own leave.

The day count is always computed from the dates by the model. A `days` field
posted from a form is ignored — that is the difference between a leave system
and an honour system.

No route here writes to the general ledger. Leave reaches the accounts only
through the payroll engine, which reads approved leave when it calculates.
"""

from datetime import date, datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models.hr import Employee
from salpurflask.models.leave import (LeaveType, LeaveAllocation, LeaveRequest,
                                      LEAVE_STATUSES, DAY_PORTIONS,
                                      allocation_for, remaining_days,
                                      overlapping_requests, seed_leave_types,
                                      used_days, working_days)
from salpurflask.services.feature_flags import module_required
from salpurflask.services.hr_permissions import permission_required, has_permission
from salpurflask.services.notifications import notify, notify_roles

leave_bp = Blueprint("leave", __name__, url_prefix="/leave")


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


def _number(raw, field="Days"):
    from decimal import Decimal, InvalidOperation
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        return Decimal("0")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a number.")
    if value < 0:
        raise ValueError(f"{field} cannot be negative.")
    return value


def _types(active_only=True):
    seed_leave_types()
    q = LeaveType.query
    if active_only:
        q = q.filter_by(active=True)
    return q.order_by(LeaveType.name).all()


def _employees():
    return Employee.query.filter_by(active=True).order_by(Employee.code).all()


def _finalized_period_covering(start, end):
    """A finalized or paid payroll period overlapping these dates, or None.

    Approving leave that lands inside a period already finalised would change
    what a payslip should have said, and that payslip may already be posted to
    the ledger and paid. TradeFlow corrects a posted period by cancelling and
    reversing it, never by editing underneath it — so this refuses instead, and
    says which period is in the way.
    """
    try:
        from salpurflask.models.payroll import PayrollPeriod
        from salpurflask.services.feature_flags import module_enabled
        if not module_enabled("module_payroll"):
            return None
        return (PayrollPeriod.query
                .filter(PayrollPeriod.status == "Finalized",
                        PayrollPeriod.start_date <= end,
                        PayrollPeriod.end_date >= start)
                .first())
    except Exception:
        return None


# ── leave types ───────────────────────────────────────────────────────────────

@leave_bp.route("/types", methods=["GET", "POST"])
@module_required("module_leave")
@permission_required("leave.view")
def types():
    if request.method == "POST":
        if not has_permission("leave.configure"):
            flash("You do not have permission to configure leave types.", "danger")
            return redirect(url_for("leave.types"))

        code = (request.form.get("code") or "").strip().upper()
        name = (request.form.get("name") or "").strip()
        errors = []
        if not code:
            errors.append("Code is required.")
        elif LeaveType.query.filter(db.func.lower(LeaveType.code) == code.lower()).first():
            errors.append(f"A leave type with code '{code}' already exists.")
        if not name:
            errors.append("Name is required.")

        try:
            max_days = request.form.get("max_days_per_year")
            max_days = _number(max_days, "Maximum days") if (max_days or "").strip() else None
            cf_limit = request.form.get("carry_forward_limit")
            cf_limit = _number(cf_limit, "Carry-forward limit") if (cf_limit or "").strip() else None
        except ValueError as e:
            errors.append(str(e))
            max_days = cf_limit = None

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            lt = LeaveType(
                code=code, name=name,
                paid=bool(request.form.get("paid")),
                requires_allocation=bool(request.form.get("requires_allocation")),
                max_days_per_year=max_days,
                carry_forward=bool(request.form.get("carry_forward")),
                carry_forward_limit=cf_limit,
                description=(request.form.get("description") or "").strip() or None,
                active=True)
            db.session.add(lt)
            db.session.commit()
            _audit("create", "leave_type", lt.id, f"{code} {name}")
            flash(f"Leave type '{name}' added.", "success")
        return redirect(url_for("leave.types"))

    return render_template("leave/types.html", rows=_types(active_only=False))


@leave_bp.route("/types/<int:type_id>/toggle", methods=["POST"])
@module_required("module_leave")
@permission_required("leave.configure")
def type_toggle(type_id):
    lt = db.session.get(LeaveType, type_id)
    if lt is None:
        abort(404)
    lt.active = not lt.active
    db.session.commit()
    _audit("edit", "leave_type", lt.id, f"active={lt.active}")
    flash(f"'{lt.name}' is now {'active' if lt.active else 'inactive'}.", "success")
    return redirect(url_for("leave.types"))


@leave_bp.route("/types/<int:type_id>/delete", methods=["POST"])
@module_required("module_leave")
@permission_required("leave.delete")
def type_delete(type_id):
    lt = db.session.get(LeaveType, type_id)
    if lt is None:
        abort(404)
    if lt.system:
        flash(f"'{lt.name}' is a standard type and cannot be deleted. "
              f"Deactivate it instead.", "warning")
        return redirect(url_for("leave.types"))
    if LeaveRequest.query.filter_by(leave_type_id=lt.id).first():
        flash(f"'{lt.name}' has leave history, so it was deactivated rather "
              f"than deleted.", "warning")
        lt.active = False
        db.session.commit()
        return redirect(url_for("leave.types"))

    name = lt.name
    db.session.delete(lt)
    db.session.commit()
    _audit("delete", "leave_type", type_id, name)
    flash(f"Leave type '{name}' deleted.", "success")
    return redirect(url_for("leave.types"))


# ── allocations ───────────────────────────────────────────────────────────────

@leave_bp.route("/allocations", methods=["GET", "POST"])
@module_required("module_leave")
@permission_required("leave.view")
def allocations():
    today = date.today()
    if request.method == "POST":
        if not has_permission("leave.edit"):
            flash("You do not have permission to allocate leave.", "danger")
            return redirect(url_for("leave.allocations"))

        errors = []
        emp = lt = None
        try:
            emp = db.session.get(Employee, int(request.form.get("employee_id") or 0))
        except (TypeError, ValueError):
            emp = None
        if emp is None:
            errors.append("Employee not found.")
        try:
            lt = db.session.get(LeaveType, int(request.form.get("leave_type_id") or 0))
        except (TypeError, ValueError):
            lt = None
        if lt is None:
            errors.append("Leave type not found.")

        try:
            year = int(request.form.get("year") or today.year)
        except (TypeError, ValueError):
            year = today.year
            errors.append("Year must be a number.")

        try:
            days = _number(request.form.get("days"), "Allocated days")
        except ValueError as e:
            errors.append(str(e))
            days = None

        if lt is not None and days is not None and lt.max_days_per_year:
            if days > lt.max_days_per_year:
                errors.append(f"{lt.name} allows at most "
                              f"{lt.max_days_per_year:g} days a year.")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            existing = allocation_for(emp.id, lt.id, year)
            if existing:
                existing.days = days
                existing.notes = (request.form.get("notes") or "").strip() or None
                action = "edit"
                row_id = existing.id
            else:
                alloc = LeaveAllocation(
                    employee_id=emp.id, leave_type_id=lt.id, year=year, days=days,
                    notes=(request.form.get("notes") or "").strip() or None)
                db.session.add(alloc)
                db.session.flush()
                action = "create"
                row_id = alloc.id
            db.session.commit()
            _audit(action, "leave_allocation", row_id,
                   f"{emp.code} {lt.code} {year}: {days}")
            flash(f"Allocation saved for {emp.name}.", "success")
        return redirect(url_for("leave.allocations", year=request.form.get("year") or today.year))

    try:
        year = int(request.args.get("year") or today.year)
    except (TypeError, ValueError):
        year = today.year

    rows = (LeaveAllocation.query.filter_by(year=year)
            .join(Employee).order_by(Employee.code).all())
    return render_template("leave/allocations.html", rows=rows, year=year,
                           employees=_employees(), types=_types(),
                           years=range(today.year - 3, today.year + 3),
                           today=today)


@leave_bp.route("/allocations/<int:row_id>/delete", methods=["POST"])
@module_required("module_leave")
@permission_required("leave.delete")
def allocation_delete(row_id):
    alloc = db.session.get(LeaveAllocation, row_id)
    if alloc is None:
        abort(404)
    if alloc.used_days > 0:
        flash("That allocation has leave taken against it and cannot be deleted.",
              "warning")
        return redirect(url_for("leave.allocations", year=alloc.year))
    year = alloc.year
    db.session.delete(alloc)
    db.session.commit()
    _audit("delete", "leave_allocation", row_id, "")
    flash("Allocation deleted.", "success")
    return redirect(url_for("leave.allocations", year=year))


# ── requests ──────────────────────────────────────────────────────────────────

def _validate_request(form, existing=None):
    errors = []
    data = {}

    emp = None
    try:
        emp = db.session.get(Employee, int(form.get("employee_id") or 0))
    except (TypeError, ValueError):
        emp = None
    if emp is None:
        errors.append("Employee not found.")
    data["employee_id"] = emp.id if emp else None

    lt = None
    try:
        lt = db.session.get(LeaveType, int(form.get("leave_type_id") or 0))
    except (TypeError, ValueError):
        lt = None
    if lt is None:
        errors.append("Leave type not found.")
    elif not lt.active:
        errors.append(f"{lt.name} is not available.")
    data["leave_type_id"] = lt.id if lt else None

    start = end = None
    try:
        start = _parse_date(form.get("start_date"), "Start date")
    except ValueError as e:
        errors.append(str(e))
    try:
        end = _parse_date(form.get("end_date"), "End date")
    except ValueError as e:
        errors.append(str(e))
    if start and end and end < start:
        errors.append("End date cannot be before the start date.")
    data["start_date"], data["end_date"] = start, end

    portion = (form.get("day_portion") or "full").strip()
    if portion not in DAY_PORTIONS:
        portion = "full"
    if portion == "half" and start and end and start != end:
        errors.append("A half day applies to a single date only.")
    data["day_portion"] = portion

    # The day count is ours, never the form's.
    if start and end and not errors:
        computed = working_days(start, end, portion)
        if computed <= 0:
            errors.append("Those dates contain no working days.")
        data["days"] = computed

    if emp and start and end and not errors:
        clash = overlapping_requests(emp.id, start, end,
                                     exclude_id=existing.id if existing else None)
        if clash:
            other = clash[0]
            errors.append(f"{emp.name} already has leave "
                          f"{other.start_date}–{other.end_date} ({other.status}).")

    data["reason"] = (form.get("reason") or "").strip() or None
    return data, errors


@leave_bp.route("/requests")
@module_required("module_leave")
@permission_required("leave.view")
def requests_list():
    emp_id = (request.args.get("employee") or "").strip()
    status = (request.args.get("status") or "").strip()
    type_id = (request.args.get("type") or "").strip()

    q = LeaveRequest.query.join(Employee)
    if emp_id.isdigit():
        q = q.filter(LeaveRequest.employee_id == int(emp_id))
    if status in LEAVE_STATUSES:
        q = q.filter(LeaveRequest.status == status)
    if type_id.isdigit():
        q = q.filter(LeaveRequest.leave_type_id == int(type_id))

    rows = q.order_by(LeaveRequest.start_date.desc()).limit(300).all()
    counts = {s: LeaveRequest.query.filter_by(status=s).count()
              for s in LEAVE_STATUSES}
    return render_template("leave/requests.html", rows=rows, counts=counts,
                           employees=_employees(), types=_types(),
                           statuses=LEAVE_STATUSES,
                           filters={"employee": emp_id, "status": status,
                                    "type": type_id})


@leave_bp.route("/requests/new", methods=["GET", "POST"])
@module_required("module_leave")
@permission_required("leave.create")
def request_new():
    if request.method == "POST":
        data, errors = _validate_request(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("leave/request_form.html", req=None,
                                   form_data=request.form, employees=_employees(),
                                   types=_types(), portions=DAY_PORTIONS)

        submit = bool(request.form.get("submit_now"))
        row = LeaveRequest(**data, status="Pending" if submit else "Draft",
                           created_by_id=getattr(current_user, "id", None))
        db.session.add(row)
        db.session.commit()
        _audit("create", "leave_request", row.id,
               f"{row.employee.code} {row.start_date}–{row.end_date} {row.status}")
        if submit:
            notify_roles(("admin", "manager"), "leave_pending",
                        "Leave request awaiting approval",
                        f"{row.employee.name} requested {row.days:g} day(s) of "
                        f"{row.leave_type.name if row.leave_type else 'leave'} "
                        f"({row.start_date}–{row.end_date}).",
                        source_type="leave_request", source_id=row.id, severity="info")
            db.session.commit()
        flash(f"Leave request {'submitted' if submit else 'saved as draft'} "
              f"for {row.employee.name} ({row.days:g} day(s)).", "success")
        return redirect(url_for("leave.requests_list"))

    return render_template("leave/request_form.html", req=None, form_data={},
                           employees=_employees(), types=_types(),
                           portions=DAY_PORTIONS)


@leave_bp.route("/requests/<int:row_id>/edit", methods=["GET", "POST"])
@module_required("module_leave")
@permission_required("leave.edit")
def request_edit(row_id):
    row = db.session.get(LeaveRequest, row_id)
    if row is None:
        abort(404)
    if row.status not in ("Draft", "Pending"):
        flash(f"A {row.status.lower()} request can no longer be edited.", "warning")
        return redirect(url_for("leave.requests_list"))

    if request.method == "POST":
        data, errors = _validate_request(request.form, existing=row)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("leave/request_form.html", req=row,
                                   form_data=request.form, employees=_employees(),
                                   types=_types(), portions=DAY_PORTIONS)
        for field, value in data.items():
            setattr(row, field, value)
        db.session.commit()
        _audit("edit", "leave_request", row.id, f"{row.start_date}–{row.end_date}")
        flash("Leave request updated.", "success")
        return redirect(url_for("leave.requests_list"))

    return render_template("leave/request_form.html", req=row, form_data={},
                           employees=_employees(), types=_types(),
                           portions=DAY_PORTIONS)


@leave_bp.route("/requests/<int:row_id>/submit", methods=["POST"])
@module_required("module_leave")
@permission_required("leave.create")
def request_submit(row_id):
    row = db.session.get(LeaveRequest, row_id)
    if row is None:
        abort(404)
    if row.status != "Draft":
        flash(f"Only a draft can be submitted (this one is {row.status.lower()}).",
              "warning")
        return redirect(url_for("leave.requests_list"))
    row.status = "Pending"
    db.session.commit()
    _audit("submit", "leave_request", row.id, str(row.days))
    notify_roles(("admin", "manager"), "leave_pending",
                "Leave request awaiting approval",
                f"{row.employee.name} requested {row.days:g} day(s) of "
                f"{row.leave_type.name if row.leave_type else 'leave'} "
                f"({row.start_date}–{row.end_date}).",
                source_type="leave_request", source_id=row.id, severity="info")
    db.session.commit()
    flash("Leave request submitted for approval.", "success")
    return redirect(url_for("leave.requests_list"))


@leave_bp.route("/requests/<int:row_id>/approve", methods=["POST"])
@module_required("module_leave")
@permission_required("leave.approve")
def request_approve(row_id):
    row = db.session.get(LeaveRequest, row_id)
    if row is None:
        abort(404)
    if row.status != "Pending":
        flash(f"Only a pending request can be approved (this one is "
              f"{row.status.lower()}).", "warning")
        return redirect(url_for("leave.requests_list"))

    # Never change what a finalised payroll should have said.
    blocking = _finalized_period_covering(row.start_date, row.end_date)
    if blocking is not None:
        flash(f"Payroll for {blocking.name} is already finalized and covers these "
              f"dates. Approving now would change a payslip that has been posted. "
              f"Cancel that payroll period first, or apply this leave to a later "
              f"period.", "danger")
        return redirect(url_for("leave.requests_list"))

    # Allocation check, for types that draw on one.
    lt = row.leave_type
    if lt is not None and lt.requires_allocation:
        left = remaining_days(row.employee_id, lt.id, row.start_date.year)
        if left is not None and row.days > left:
            flash(f"{row.employee.name} has {left:g} day(s) of {lt.name} left "
                  f"but this request is {row.days:g}.", "danger")
            return redirect(url_for("leave.requests_list"))

    row.status = "Approved"
    row.decided_by_id = getattr(current_user, "id", None)
    row.decided_at = datetime.utcnow()
    row.decision_note = (request.form.get("note") or "").strip() or None
    db.session.commit()
    _audit("approve", "leave_request", row.id,
           f"{row.employee.code} {row.days} day(s)")
    if row.employee.user_id:
        notify(row.employee.user_id, "leave_decided", "Leave request approved",
              f"Your leave request for {row.start_date}–{row.end_date} "
              f"({row.days:g} day(s)) was approved.",
              source_type="leave_request", source_id=row.id, severity="info", dedupe=False)
        db.session.commit()
    flash(f"Leave approved for {row.employee.name} ({row.days:g} day(s)).",
          "success")
    return redirect(url_for("leave.requests_list"))


@leave_bp.route("/requests/<int:row_id>/reject", methods=["POST"])
@module_required("module_leave")
@permission_required("leave.approve")
def request_reject(row_id):
    row = db.session.get(LeaveRequest, row_id)
    if row is None:
        abort(404)
    if row.status != "Pending":
        flash("Only a pending request can be rejected.", "warning")
        return redirect(url_for("leave.requests_list"))
    row.status = "Rejected"
    row.decided_by_id = getattr(current_user, "id", None)
    row.decided_at = datetime.utcnow()
    row.decision_note = (request.form.get("note") or "").strip() or None
    db.session.commit()
    _audit("reject", "leave_request", row.id, row.employee.code)
    if row.employee.user_id:
        notify(row.employee.user_id, "leave_decided", "Leave request rejected",
              f"Your leave request for {row.start_date}–{row.end_date} "
              f"({row.days:g} day(s)) was rejected.",
              source_type="leave_request", source_id=row.id, severity="warning", dedupe=False)
        db.session.commit()
    flash("Leave request rejected.", "success")
    return redirect(url_for("leave.requests_list"))


@leave_bp.route("/requests/<int:row_id>/cancel", methods=["POST"])
@module_required("module_leave")
@permission_required("leave.approve")
def request_cancel(row_id):
    row = db.session.get(LeaveRequest, row_id)
    if row is None:
        abort(404)
    if row.status == "Cancelled":
        flash("That request is already cancelled.", "warning")
        return redirect(url_for("leave.requests_list"))

    # Cancelling approved leave inside a finalised period would change a posted
    # payslip just as approving would.
    if row.status == "Approved":
        blocking = _finalized_period_covering(row.start_date, row.end_date)
        if blocking is not None:
            flash(f"Payroll for {blocking.name} is finalized and covers these "
                  f"dates. Cancel that payroll period first if this leave must "
                  f"be withdrawn.", "danger")
            return redirect(url_for("leave.requests_list"))

    row.status = "Cancelled"
    row.decided_by_id = getattr(current_user, "id", None)
    row.decided_at = datetime.utcnow()
    db.session.commit()
    # No balance to restore by hand: `used_days` counts approved requests only,
    # so the days come back the moment the status changes.
    _audit("cancel", "leave_request", row.id, row.employee.code)
    flash("Leave cancelled. The days are back in the employee's balance.",
          "success")
    return redirect(url_for("leave.requests_list"))


# ── balances / report ─────────────────────────────────────────────────────────

@leave_bp.route("/")
@module_required("module_leave")
@permission_required("leave.view")
def index():
    today = date.today()
    try:
        year = int(request.args.get("year") or today.year)
    except (TypeError, ValueError):
        year = today.year

    emp_filter = (request.args.get("employee") or "").strip()
    employees = _employees()
    if emp_filter.isdigit():
        employees = [e for e in employees if e.id == int(emp_filter)]

    types = _types()
    rows = []
    for emp in employees:
        for lt in types:
            alloc = allocation_for(emp.id, lt.id, year)
            used = used_days(emp.id, lt.id, year)
            if alloc is None and used == 0:
                continue          # nothing to say about this pair
            allocated = alloc.days if alloc else 0
            base = LeaveRequest.query.filter_by(employee_id=emp.id,
                                                leave_type_id=lt.id)
            rows.append({
                "employee": emp, "leave_type": lt,
                "allocated": allocated, "used": used,
                "remaining": (None if not lt.requires_allocation
                              else (alloc.remaining_days if alloc else -used)),
                "pending": base.filter_by(status="Pending").count(),
                "approved": base.filter_by(status="Approved").count(),
                "rejected": base.filter_by(status="Rejected").count(),
            })

    return render_template("leave/index.html", rows=rows, year=year,
                           employees=_employees(),
                           years=range(today.year - 3, today.year + 3),
                           filters={"employee": emp_filter})


__all__ = ["leave_bp"]
