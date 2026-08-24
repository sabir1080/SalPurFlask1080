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
from sqlalchemy.exc import IntegrityError

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
from salpurflask.services import payroll_accounting as accounting
from salpurflask.models import payroll_payment as payments
from salpurflask.services.notifications import notify_roles

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
            # A cancelled period does not hold its dates. It represents a run
            # that was undone, not one that happened -- the same reasoning that
            # already lets a cancelled advance be superseded and a rejected
            # leave request re-submitted for the same days.
            clash = (PayrollPeriod.query
                     .filter_by(start_date=start, end_date=end)
                     .filter(PayrollPeriod.status != "Cancelled")
                     .first())
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
        try:
            db.session.commit()
        except IntegrityError:
            # The clash check above only excludes Cancelled periods, but the
            # column pair still carries a database-level unique constraint that
            # does not know about status -- dropping it would be the kind of
            # blocking DDL that hangs a zero-downtime deploy (see CLAUDE.md), so
            # it stays. The practical effect: a cancelled period frees its dates
            # for a period with a *different* name, but the exact same
            # start_date/end_date pair as a row still in the table -- cancelled
            # or not -- cannot be inserted twice. Caught here so the user sees a
            # plain sentence instead of a stack trace.
            db.session.rollback()
            flash("Those exact dates already exist as a payroll period "
                  "(including a cancelled one). Use different dates, or "
                  "delete the old period first if it is no longer needed.",
                  "danger")
            return render_template("payroll/period_form.html",
                                   form_data=request.form, today=today)
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
                           statuses=PERIOD_STATUSES,
                           accounting_status=accounting.accounting_status(period),
                           journal=accounting.journal_summary(period),
                           payment_status=payments.period_payment_status(period),
                           net_total=payments.period_net_total(period),
                           paid_total=payments.period_paid_total(period),
                           payable_balance=payments.period_payable_balance(period),
                           payment_rows=period.payments.order_by(
                               payments.PayrollPayment.payment_date).all(),
                           today=date.today())


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

    # Finalising and posting are one transaction. If the ledger refuses the
    # entry, the period must not be left finalised with nothing behind it, so
    # everything below rolls back together.
    from app import PostingError

    try:
        engine.recover_advances(period)
        period.status = "Finalized"
        period.finalized_at = datetime.utcnow()
        try:
            period.finalized_by = current_user.id
        except Exception:
            period.finalized_by = None

        entry = accounting.post_payroll_period(
            period, created_by_id=getattr(current_user, "id", None))
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"{period.name} was not finalized — accounting refused the entry: {e}",
              "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))
    except Exception:
        db.session.rollback()
        flash(f"{period.name} was not finalized — posting failed and nothing was saved.",
              "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    _audit("finalize", "payroll_period", period.id,
           f"{period.name} posted as JE#{entry.id}" if entry else period.name)
    notify_roles(("admin",), "payroll_finalized", "Payroll period finalized",
                f"{period.name} was finalized and posted to the ledger.",
                source_type="payroll_period", source_id=period.id, severity="info")
    db.session.commit()
    flash(f"{period.name} finalized"
          + (f" and posted to the ledger (entry #{entry.id})." if entry else "."),
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

    # A period with a live salary payment cannot be cancelled out from under it:
    # reversing the payroll posting alone would leave the payment's cash/bank
    # movement standing while the liability it was settling disappears, so
    # Salaries Payable would land on the wrong side of zero. The payment has to
    # be reversed first, through its own reversal route, so cash and the
    # liability move back together.
    if payments.period_paid_total(period) > 0:
        flash(f"{period.name} cannot be cancelled because salary payments exist. "
              f"Reverse the salary payment(s) first.", "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    # A posted period is cancelled by reversing its entry, never by deleting it.
    # Both rows stay in the ledger, which is the whole point of a reversal.
    from app import PostingError

    try:
        reversal = accounting.reverse_payroll_period(
            period, created_by_id=getattr(current_user, "id", None))
        period.status = "Cancelled"
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"{period.name} was not cancelled — the reversal was refused: {e}",
              "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))
    except Exception:
        db.session.rollback()
        flash(f"{period.name} was not cancelled — nothing was changed.", "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    _audit("cancel", "payroll_period", period.id,
           f"{period.name} reversed by JE#{reversal.id}" if reversal else period.name)
    notify_roles(("admin",), "payroll_cancelled", "Payroll period cancelled",
                f"{period.name} was cancelled and its posting reversed in the ledger.",
                source_type="payroll_period", source_id=period.id, severity="warning")
    db.session.commit()
    flash(f"{period.name} cancelled"
          + (f" and reversed in the ledger (entry #{reversal.id})."
             if reversal else ". Its payslips are kept for the record."),
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


# ── salary payment ────────────────────────────────────────────────────────────

@payroll_bp.route("/periods/<int:period_id>/pay", methods=["POST"])
@module_required("module_payroll")
@permission_required("payroll.post")
def period_pay(period_id):
    """Pay salary for a finalised period: Dr Salaries Payable / Cr Cash or Bank.

    Every rule is checked here, server-side. Hiding the button is not a control —
    a stale form or a crafted POST reaches this function either way.
    """
    from app import PostingError, parse_account_id

    period = db.session.get(PayrollPeriod, period_id)
    if period is None:
        abort(404)

    # Only a finalised period owes anything: draft and processing figures can
    # still change, and a cancelled period's liability has been reversed away.
    if period.status != "Finalized":
        flash(f"{period.name} is {period.status.lower()} — only a finalized "
              f"payroll can be paid.", "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    if accounting.accounting_status(period) != "POSTED":
        flash(f"{period.name} has no live posting to settle.", "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    balance = payments.period_payable_balance(period)
    if balance <= 0:
        flash(f"{period.name} is already fully paid.", "warning")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    account_id, err = parse_account_id(request.form.get("account_id"))
    if err or not account_id:
        flash(err or "Pick the cash or bank account the salary was paid from.",
              "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    # Blank amount means "settle the balance", which is what a user clicking Pay
    # Salary almost always means.
    raw_amount = (request.form.get("amount") or "").strip()
    try:
        amount = _money(raw_amount, "Payment amount") if raw_amount else balance
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    if amount <= 0:
        flash("Payment amount must be more than zero.", "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))
    if amount > balance:
        flash(f"{period.name} has {amount - balance:,.2f} less outstanding than "
              f"that — the payable balance is {balance:,.2f}.", "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    try:
        adate = (_parse_date(request.form.get("payment_date"), "Payment date")
                 if (request.form.get("payment_date") or "").strip() else date.today())
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    # The payment row and its journal entry are one transaction: a refused entry
    # must not leave a payment that was never really made.
    try:
        payment = payments.PayrollPayment(
            period_id=period.id, amount=amount, payment_date=adate,
            account_id=account_id,
            reference_no=(request.form.get("reference_no") or "").strip() or None,
            notes=(request.form.get("notes") or "").strip() or None,
            created_by_id=getattr(current_user, "id", None))
        db.session.add(payment)
        db.session.flush()

        entry = accounting.post_payroll_payment(
            payment, created_by_id=getattr(current_user, "id", None))
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"Salary was not paid — accounting refused the entry: {e}", "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))
    except Exception:
        db.session.rollback()
        flash("Salary was not paid — the payment failed and nothing was saved.",
              "danger")
        return redirect(url_for("payroll.period_detail", period_id=period.id))

    _audit("pay", "payroll_period", period.id,
           f"{period.name} paid {amount} via account {account_id}"
           + (f" as JE#{entry.id}" if entry else ""))

    remaining = payments.period_payable_balance(period)
    flash(f"Salary payment of {amount:,.2f} recorded for {period.name}"
          + (f" (entry #{entry.id})." if entry else ".")
          + (f" {remaining:,.2f} still outstanding." if remaining > 0 else ""),
          "success")
    return redirect(url_for("payroll.period_detail", period_id=period.id))


@payroll_bp.route("/payments/<int:payment_id>/reverse", methods=["POST"])
@module_required("module_payroll")
@permission_required("payroll.post")
def payment_reverse(payment_id):
    """Undo one salary payment by reversing its entry. Nothing is deleted."""
    from app import PostingError

    payment = db.session.get(payments.PayrollPayment, payment_id)
    if payment is None:
        abort(404)
    if payment.is_reversed:
        flash("That payment has already been reversed.", "warning")
        return redirect(url_for("payroll.period_detail",
                                period_id=payment.period_id))

    try:
        reversal = accounting.reverse_payroll_payment(
            payment, created_by_id=getattr(current_user, "id", None))
        payment.is_reversed = True
        payment.reversed_at = datetime.utcnow()
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"The payment was not reversed: {e}", "danger")
        return redirect(url_for("payroll.period_detail", period_id=payment.period_id))
    except Exception:
        db.session.rollback()
        flash("The payment was not reversed — nothing was changed.", "danger")
        return redirect(url_for("payroll.period_detail", period_id=payment.period_id))

    _audit("reverse", "payroll_payment", payment.id,
           f"reversed by JE#{reversal.id}" if reversal else "reversed")
    flash("Salary payment reversed"
          + (f" in the ledger (entry #{reversal.id})." if reversal else "."),
          "success")
    return redirect(url_for("payroll.period_detail", period_id=payment.period_id))


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
