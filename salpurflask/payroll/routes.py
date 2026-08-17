"""Payroll routes — structures, periods, processing, payslips and advances.

Routes decide who and when; `payroll_engine` decides how much. No arithmetic
lives in this file beyond reading a form.

Gated twice, as HR and attendance are: `module_required("module_payroll")` and
`permission_required(...)`. Finalising is separated from editing and needs
`payroll.post`, which only an admin holds — in phase 3B that same action will
write to the general ledger.
"""

import calendar
from datetime import datetime, date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models.hr import Employee
from salpurflask.models.payroll import (SalaryComponent, SalaryStructure,
                                        SalaryStructureLine, PayrollPeriod,
                                        PayrollEntry, EmployeeAdvance,
                                        PERIOD_STATUSES, ADVANCE_STATUSES,
                                        seed_default_components, _dec)
from salpurflask.services.feature_flags import module_required
from salpurflask.services.hr_permissions import permission_required, has_permission
from salpurflask.services import payroll_engine as engine

payroll_bp = Blueprint("payroll", __name__, url_prefix="/payroll")


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


def _money(raw, field="Amount"):
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


def _components():
    seed_default_components()
    return (SalaryComponent.query.filter_by(active=True)
            .order_by(SalaryComponent.sort_order, SalaryComponent.id).all())


# ── overview ──────────────────────────────────────────────────────────────────

@payroll_bp.route("/")
@module_required("module_payroll")
@permission_required("payroll.view")
def index():
    periods = (PayrollPeriod.query
               .order_by(PayrollPeriod.start_date.desc()).limit(24).all())
    return render_template(
        "payroll/index.html",
        periods=periods,
        employees=Employee.query.filter_by(active=True).count(),
        structures=SalaryStructure.query.filter_by(active=True).count(),
        open_advances=EmployeeAdvance.query.filter_by(status="Active").count(),
    )


# ── salary structures ─────────────────────────────────────────────────────────

@payroll_bp.route("/structures")
@module_required("module_payroll")
@permission_required("payroll.view")
def structures():
    rows = (SalaryStructure.query.filter_by(active=True)
            .join(Employee).order_by(Employee.code).all())
    covered = {s.employee_id for s in rows}
    missing = (Employee.query.filter_by(active=True)
               .filter(~Employee.id.in_(covered) if covered else True)
               .order_by(Employee.code).all())
    return render_template("payroll/structures.html", structures=rows,
                           missing=missing)


@payroll_bp.route("/structures/employee/<int:emp_id>", methods=["GET", "POST"])
@module_required("module_payroll")
@permission_required("payroll.edit")
def structure_edit(emp_id):
    emp = db.session.get(Employee, emp_id)
    if emp is None:
        abort(404)

    structure = engine.active_structure(emp.id)
    components = _components()

    if request.method == "POST":
        errors = []
        values = {}
        for comp in components:
            if comp.calc_method == "attendance":
                continue            # computed per period, never contracted
            try:
                values[comp.id] = _money(request.form.get(f"amount_{comp.id}"),
                                         comp.name)
            except ValueError as e:
                errors.append(str(e))

        basic = next((values.get(c.id) for c in components if c.code == "BASIC"), None)
        if basic is not None and basic <= 0:
            errors.append("Basic salary must be more than zero.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("payroll/structure_form.html", employee=emp,
                                   structure=structure, components=components,
                                   form_data=request.form)

        if structure is None:
            structure = SalaryStructure(employee_id=emp.id, active=True,
                                        effective_from=date.today())
            db.session.add(structure)
            db.session.flush()

        existing = {l.component_id: l for l in structure.lines}
        for comp in components:
            if comp.calc_method == "attendance":
                continue
            amount = values.get(comp.id, 0)
            line = existing.get(comp.id)
            if amount == 0 and line is None:
                continue
            if line is None:
                db.session.add(SalaryStructureLine(structure_id=structure.id,
                                                   component_id=comp.id,
                                                   amount=amount))
            else:
                line.amount = amount

        structure.notes = (request.form.get("notes") or "").strip() or None
        db.session.commit()
        _audit("edit", "salary_structure", structure.id, f"{emp.code} {emp.name}")
        flash(f"Salary structure saved for {emp.name}.", "success")
        return redirect(url_for("payroll.structures"))

    return render_template("payroll/structure_form.html", employee=emp,
                           structure=structure, components=components,
                           form_data={})


# ── periods ───────────────────────────────────────────────────────────────────

@payroll_bp.route("/periods/new", methods=["GET", "POST"])
@module_required("module_payroll")
@permission_required("payroll.create")
def period_new():
    today = date.today()
    if request.method == "POST":
        errors = []
        name = (request.form.get("name") or "").strip()
        if not name:
            errors.append("Period name is required.")
        elif PayrollPeriod.query.filter(db.func.lower(PayrollPeriod.name) ==
                                        name.lower()).first():
            errors.append(f"A period named '{name}' already exists.")

        start = end = None
        try:
            start = _parse_date(request.form.get("start_date"), "Start date")
            end = _parse_date(request.form.get("end_date"), "End date")
            if end < start:
                errors.append("End date cannot be before the start date.")
        except ValueError as e:
            errors.append(str(e))

        if start and end and not errors:
            clash = PayrollPeriod.query.filter_by(start_date=start, end_date=end).first()
            if clash:
                errors.append(f"Those dates are already covered by '{clash.name}'.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("payroll/period_form.html", form_data=request.form,
                                   today=today)

        period = PayrollPeriod(name=name, start_date=start, end_date=end,
                               status="Draft",
                               notes=(request.form.get("notes") or "").strip() or None)
        db.session.add(period)
        db.session.commit()
        _audit("create", "payroll_period", period.id, period.name)
        flash(f"Payroll period '{period.name}' created.", "success")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    # Default to the current month, which is what a user almost always wants.
    last = calendar.monthrange(today.year, today.month)[1]
    return render_template("payroll/period_form.html", today=today, form_data={
        "name": f"{calendar.month_name[today.month]} {today.year}",
        "start_date": date(today.year, today.month, 1).isoformat(),
        "end_date": date(today.year, today.month, last).isoformat(),
    })


@payroll_bp.route("/periods/<int:period_id>")
@module_required("module_payroll")
@permission_required("payroll.view")
def period_detail(period_id):
    period = db.session.get(PayrollPeriod, period_id)
    if period is None:
        abort(404)
    entries = (period.entries.join(Employee).order_by(Employee.code).all())
    return render_template("payroll/period_detail.html", period=period,
                           entries=entries, totals=period.totals,
                           statuses=PERIOD_STATUSES)


@payroll_bp.route("/periods/<int:period_id>/process", methods=["POST"])
@module_required("module_payroll")
@permission_required("payroll.create")
def period_process(period_id):
    period = db.session.get(PayrollPeriod, period_id)
    if period is None:
        abort(404)

    try:
        processed, skipped = engine.process_period(period)
        db.session.commit()
    except engine.PayrollError as e:
        db.session.rollback()
        flash(str(e), "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    _audit("process", "payroll_period", period.id,
           f"{period.name}: {len(processed)} processed")
    flash(f"{period.name} processed — {len(processed)} payslip(s) calculated"
          + (f", {len(skipped)} skipped." if skipped else "."), "success")
    for emp, reason in skipped:
        flash(f"Skipped {emp.name}: {reason}", "warning")
    return redirect(url_for("payroll.period_detail", period_id=period.id))


@payroll_bp.route("/periods/<int:period_id>/finalize", methods=["POST"])
@module_required("module_payroll")
@permission_required("payroll.post")
def period_finalize(period_id):
    """Close a period.

    Phase 3B will post the journal entries here. Until then finalising freezes
    the run and recovers advances — deliberately not the same as posting, so
    when the GL step arrives it has one obvious place to live.
    """
    period = db.session.get(PayrollPeriod, period_id)
    if period is None:
        abort(404)

    if period.status == "Finalized":
        flash(f"{period.name} is already finalized.", "warning")
        return redirect(url_for("payroll.period_detail", period_id=period.id))
    if period.status == "Cancelled":
        flash(f"{period.name} is cancelled and cannot be finalized.", "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))
    if not period.entries.count():
        flash("Nothing to finalize — process the period first.", "warning")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    engine.recover_advances(period)
    period.status = "Finalized"
    period.finalized_at = datetime.utcnow()
    try:
        period.finalized_by = current_user.id
    except Exception:
        period.finalized_by = None
    db.session.commit()

    _audit("finalize", "payroll_period", period.id, period.name)
    flash(f"{period.name} finalized. Accounting posting arrives in the next phase.",
          "success")
    return redirect(url_for("payroll.period_detail", period_id=period.id))


@payroll_bp.route("/periods/<int:period_id>/cancel", methods=["POST"])
@module_required("module_payroll")
@permission_required("payroll.post")
def period_cancel(period_id):
    period = db.session.get(PayrollPeriod, period_id)
    if period is None:
        abort(404)
    if period.status == "Cancelled":
        flash("Already cancelled.", "warning")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    period.status = "Cancelled"
    db.session.commit()
    _audit("cancel", "payroll_period", period.id, period.name)
    flash(f"{period.name} cancelled. Its payslips are kept for the record.",
          "success")
    return redirect(url_for("payroll.period_detail", period_id=period.id))


@payroll_bp.route("/periods/<int:period_id>/delete", methods=["POST"])
@module_required("module_payroll")
@permission_required("payroll.delete")
def period_delete(period_id):
    period = db.session.get(PayrollPeriod, period_id)
    if period is None:
        abort(404)
    if period.status == "Finalized":
        flash(f"{period.name} is finalized and cannot be deleted. Cancel it instead.",
              "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    name = period.name
    db.session.delete(period)
    db.session.commit()
    _audit("delete", "payroll_period", period_id, name)
    flash(f"Payroll period '{name}' deleted.", "success")
    return redirect(url_for("payroll.index"))


# ── payslip ───────────────────────────────────────────────────────────────────

@payroll_bp.route("/payslip/<int:entry_id>")
@module_required("module_payroll")
@permission_required("payroll.view")
def payslip(entry_id):
    entry = db.session.get(PayrollEntry, entry_id)
    if entry is None:
        abort(404)
    return render_template("payroll/payslip.html", entry=entry,
                           period=entry.period)


@payroll_bp.route("/preview/<int:emp_id>/<int:period_id>")
@module_required("module_payroll")
@permission_required("payroll.view")
def preview(emp_id, period_id):
    """What this employee would be paid, without writing anything."""
    emp = db.session.get(Employee, emp_id)
    period = db.session.get(PayrollPeriod, period_id)
    if emp is None or period is None:
        abort(404)
    try:
        result = engine.calculate(emp, period)
    except engine.PayrollError as e:
        flash(str(e), "warning")
        return redirect(url_for("payroll.period_detail", period_id=period.id))
    return render_template("payroll/preview.html", result=result, period=period,
                           employee=emp)


# ── advances ──────────────────────────────────────────────────────────────────

@payroll_bp.route("/advances", methods=["GET", "POST"])
@module_required("module_payroll")
@permission_required("payroll.view")
def advances():
    if request.method == "POST":
        if not has_permission("payroll.create"):
            flash("You do not have permission to record advances.", "danger")
            return redirect(url_for("payroll.advances"))

        errors = []
        emp = None
        raw = (request.form.get("employee_id") or "").strip()
        try:
            emp = db.session.get(Employee, int(raw))
        except (TypeError, ValueError):
            emp = None
        if emp is None:
            errors.append("Employee not found.")

        amount = instalment = None
        try:
            amount = _money(request.form.get("amount"), "Amount")
            if amount <= 0:
                errors.append("Advance amount must be more than zero.")
            instalment = _money(request.form.get("instalment"), "Instalment")
        except ValueError as e:
            errors.append(str(e))

        adv_date = None
        try:
            adv_date = _parse_date(request.form.get("advance_date"), "Advance date")
        except ValueError as e:
            errors.append(str(e))

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            adv = EmployeeAdvance(employee_id=emp.id, advance_date=adv_date,
                                  amount=amount, instalment=instalment,
                                  status="Active",
                                  remarks=(request.form.get("remarks") or "").strip() or None)
            db.session.add(adv)
            db.session.commit()
            _audit("create", "employee_advance", adv.id, f"{emp.code} {amount}")
            flash(f"Advance recorded for {emp.name}.", "success")
        return redirect(url_for("payroll.advances"))

    status = (request.args.get("status") or "").strip()
    query = EmployeeAdvance.query.join(Employee)
    if status in ADVANCE_STATUSES:
        query = query.filter(EmployeeAdvance.status == status)
    return render_template(
        "payroll/advances.html",
        rows=query.order_by(EmployeeAdvance.advance_date.desc()).all(),
        employees=Employee.query.filter_by(active=True).order_by(Employee.code).all(),
        statuses=ADVANCE_STATUSES, filters={"status": status}, today=date.today())


@payroll_bp.route("/advances/<int:row_id>/cancel", methods=["POST"])
@module_required("module_payroll")
@permission_required("payroll.edit")
def advance_cancel(row_id):
    adv = db.session.get(EmployeeAdvance, row_id)
    if adv is None:
        abort(404)
    adv.status = "Cancelled"
    db.session.commit()
    _audit("cancel", "employee_advance", adv.id, str(adv.amount))
    flash("Advance cancelled.", "success")
    return redirect(url_for("payroll.advances"))


__all__ = ["payroll_bp"]
