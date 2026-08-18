"""Payroll calculation engine.

All the arithmetic lives here, not in a route. A route decides who to pay and
when; this decides how much, and it is the only place that answer is computed —
so the preview a user sees and the payslip that is finalised cannot disagree.

The shape of the calculation:

    Basic Salary
  + Allowances (HRA, medical, conveyance, other)
  + Bonus
  + Overtime                       from attendance
  ---------------------------------
  = Gross Salary
  - Absence deduction              from attendance + approved unpaid leave
  - Tax
  - Loan deduction
  - Advance recovery               from open advances
  - Other deductions
  ---------------------------------
  = Net Salary

Attendance is consulted only when the attendance module is on. With it off,
every employee is paid a full period — the alternative would be to treat "no
attendance recorded" as "absent every day", which would pay a whole workforce
nothing. That decision belongs here, stated once.

Nothing in this file writes to the general ledger. Phase 3B turns a finalised
period into journal entries; until then a payroll run is an HR document.
"""

from decimal import Decimal, ROUND_HALF_UP

from salpurflask.extensions import db
from salpurflask.models.payroll import (SalaryComponent, SalaryStructure,
                                        PayrollEntry, PayrollItem,
                                        EmployeeAdvance, _dec)

MONEY = Decimal("0.01")


class PayrollError(Exception):
    """A payroll run that cannot proceed. Routes turn this into a flash."""


def _money(value):
    """Round to two places, half up — the way a payslip is read."""
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _attendance_enabled():
    from salpurflask.services.feature_flags import module_enabled
    return module_enabled("module_attendance")


def attendance_facts(employee_id, start, end):
    """What attendance says about one employee over a period.

    Returns the same keys whether or not the module is on, so every caller
    downstream is spared an `if`. With attendance off, the employee is treated
    as having worked the whole period.
    """
    blank = {"worked_days": 0.0, "absent": 0, "late": 0, "leave": 0,
             "overtime_hours": 0.0, "records": 0, "tracked": False}
    if not _attendance_enabled():
        return blank
    from salpurflask.models.attendance import Attendance
    s = Attendance.summarise(employee_id, start, end)
    return {"worked_days": s["worked_days"], "absent": s["absent"],
            "late": s["late"], "leave": s["leave"],
            "overtime_hours": s["overtime_hours"], "records": s["records"],
            "tracked": s["records"] > 0}


def _leave_enabled():
    from salpurflask.services.feature_flags import module_enabled
    return module_enabled("module_leave")


def leave_facts(employee_id, start, end):
    """Approved leave for one employee inside this period.

    Asks the leave module the one question payroll needs and gets days back,
    split paid from unpaid. Same arrangement as attendance: the other module
    owns the facts, this one decides what they are worth. With leave switched
    off the answer is zeros, so nothing downstream needs an `if`.

    Only the portion of a request falling inside the period is counted, so a
    leave running from August into September is charged to each month once.
    """
    blank = {"paid_days": 0.0, "unpaid_days": 0.0, "total_days": 0.0,
             "requests": 0, "tracked": False}
    if not _leave_enabled():
        return blank
    from salpurflask.models.leave import leave_facts as _facts
    out = _facts(employee_id, start, end)
    out["tracked"] = True
    return out


def period_days(period):
    """Calendar days in the period, inclusive. The denominator for a day rate."""
    return (period.end_date - period.start_date).days + 1


def active_structure(employee_id):
    return (SalaryStructure.query
            .filter_by(employee_id=employee_id, active=True)
            .order_by(SalaryStructure.effective_from.desc().nullslast(),
                      SalaryStructure.id.desc())
            .first())


def _structure_amounts(structure):
    """{code: Decimal} for a structure's fixed lines, plus percent-of-basic."""
    out = {}
    if structure is None:
        return out
    basic = structure.amount_for("BASIC")
    for line in structure.lines:
        comp = line.component
        if comp is None or not comp.active:
            continue
        if comp.calc_method == "percent":
            out[comp.code] = _money(basic * _dec(comp.percent_of_basic) / Decimal("100"))
        elif comp.calc_method == "fixed":
            out[comp.code] = _money(line.amount)
        # "attendance" components are computed below, never taken from the line.
    return out


def calculate(employee, period, structure=None, overrides=None):
    """Work out one employee's pay for one period.

    Pure: it reads, it computes, it returns. Nothing is written, so a preview
    screen and a real run call exactly the same code.

    `overrides` lets a user adjust a component for this period only (a one-off
    bonus, a different tax figure) without editing the contract.

    Returns a dict with the totals, the attendance facts behind them, and the
    lines that make them up.
    """
    overrides = {k: _money(v) for k, v in (overrides or {}).items()}
    structure = structure or active_structure(employee.id)

    if structure is None:
        raise PayrollError(
            f"{employee.name} has no active salary structure, so there is "
            f"nothing to calculate.")

    amounts = _structure_amounts(structure)
    amounts.update(overrides)

    basic = _money(amounts.get("BASIC", 0))
    if basic <= 0:
        raise PayrollError(f"{employee.name} has a basic salary of zero.")

    facts = attendance_facts(employee.id, period.start_date, period.end_date)
    leave = leave_facts(employee.id, period.start_date, period.end_date)
    total_days = period_days(period)
    day_rate = _money(basic / Decimal(total_days)) if total_days else Decimal("0")

    # Days not paid for. Two sources, deliberately kept apart:
    #
    #   * attendance "Absent" -- someone did not come and did not say why;
    #   * approved UNPAID leave -- someone did not come and it was agreed.
    #
    # Approved PAID leave costs a normal day, so it is not here at all: the
    # engine never deducted the attendance "Leave" status either, which is why
    # paid leave needed no change to this line.
    #
    # An absent day that is also covered by approved unpaid leave is one day
    # off, not two. Attendance marks it Leave rather than Absent in that case,
    # but the guard below holds even when a sheet says otherwise -- the two are
    # capped at the days actually in the period.
    unpaid_leave = Decimal(str(leave["unpaid_days"]))
    if facts["tracked"]:
        absent = Decimal(str(facts["absent"]))
    else:
        absent = Decimal("0")

    unworked = absent + unpaid_leave
    if unworked > Decimal(str(total_days)):
        unworked = Decimal(str(total_days))

    payable = Decimal(str(total_days)) - unworked
    if payable < 0:
        payable = Decimal("0")

    absence_deduction = _money(day_rate * unworked)

    # Overtime: hours beyond a standard day, paid at the hourly rate implied by
    # the basic salary over a standard working day.
    overtime_amount = Decimal("0")
    if facts["tracked"] and facts["overtime_hours"]:
        from salpurflask.models.attendance import STANDARD_DAY_HOURS
        hour_rate = _money(day_rate / Decimal(str(STANDARD_DAY_HOURS)))
        overtime_amount = _money(hour_rate * Decimal(str(facts["overtime_hours"])))
    if "OVERTIME" in overrides:
        overtime_amount = overrides["OVERTIME"]

    # Advance recovery: the instalment, capped at what is still outstanding.
    advance_due = Decimal("0")
    open_advances = []
    if "ADVANCE" in overrides:
        advance_due = overrides["ADVANCE"]
    else:
        for adv in EmployeeAdvance.query.filter_by(employee_id=employee.id,
                                                   status="Active").all():
            if not adv.is_open:
                continue
            instalment = _dec(adv.instalment)
            take = adv.outstanding if instalment <= 0 else min(instalment, adv.outstanding)
            take = _money(take)
            if take > 0:
                advance_due += take
                open_advances.append((adv, take))

    # ── assemble the lines ────────────────────────────────────────────────────
    components = {c.code: c for c in SalaryComponent.query.all()}
    earnings, deductions = [], []

    def add(code, amount, note=None):
        amount = _money(amount)
        if amount == 0 and code not in ("BASIC",):
            return
        comp = components.get(code)
        row = {
            "code": code,
            "name": comp.name if comp else code.replace("_", " ").title(),
            "component_type": comp.component_type if comp else "earning",
            "component_id": comp.id if comp else None,
            "sort_order": comp.sort_order if comp else 999,
            "amount": amount,
            "note": note,
        }
        (earnings if row["component_type"] == "earning" else deductions).append(row)

    # Basic is pro-rated by payable days; everything else is a period figure.
    basic_payable = _money(basic - absence_deduction)
    add("BASIC", basic_payable,
        None if not absence_deduction else
        f"{payable:g} of {total_days} days")

    for code, amount in sorted(amounts.items()):
        if code in ("BASIC", "OVERTIME", "ADVANCE", "ABSENCE"):
            continue
        comp = components.get(code)
        if comp is None or not comp.active:
            continue
        add(code, amount)

    if overtime_amount:
        add("OVERTIME", overtime_amount,
            f"{facts['overtime_hours']:g} h" if facts["overtime_hours"] else None)

    if advance_due:
        add("ADVANCE", advance_due, "advance recovery")

    total_earnings = sum((r["amount"] for r in earnings), Decimal("0"))
    total_deductions = sum((r["amount"] for r in deductions), Decimal("0"))
    net = _money(total_earnings - total_deductions)

    return {
        "employee": employee,
        "structure": structure,
        "gross_salary": _money(total_earnings),
        "total_earnings": _money(total_earnings),
        "total_deductions": _money(total_deductions),
        "net_salary": net,
        "worked_days": facts["worked_days"],
        "absent_days": facts["absent"],
        "paid_leave_days": leave["paid_days"],
        "unpaid_leave_days": leave["unpaid_days"],
        "leave_tracked": leave["tracked"],
        "late_days": facts["late"],
        "leave_days": facts["leave"],
        "overtime_hours": facts["overtime_hours"],
        "payable_days": float(payable),
        "period_days": total_days,
        "day_rate": day_rate,
        "attendance_tracked": facts["tracked"],
        "lines": sorted(earnings + deductions, key=lambda r: r["sort_order"]),
        "earnings": sorted(earnings, key=lambda r: r["sort_order"]),
        "deductions": sorted(deductions, key=lambda r: r["sort_order"]),
        "_advances": open_advances,
    }


def build_entry(employee, period, result=None):
    """Turn a calculation into a PayrollEntry with its items.

    Added to the session but not committed — the caller owns the transaction, so
    a run that fails halfway writes nothing.
    """
    result = result or calculate(employee, period)

    entry = PayrollEntry(
        period_id=period.id,
        employee_id=employee.id,
        gross_salary=result["gross_salary"],
        total_earnings=result["total_earnings"],
        total_deductions=result["total_deductions"],
        net_salary=result["net_salary"],
        worked_days=result["worked_days"],
        absent_days=result["absent_days"],
        late_days=result["late_days"],
        leave_days=result["leave_days"],
        overtime_hours=result["overtime_hours"],
        payable_days=result["payable_days"],
    )
    db.session.add(entry)
    db.session.flush()

    for row in result["lines"]:
        db.session.add(PayrollItem(
            entry_id=entry.id, component_id=row["component_id"],
            code=row["code"], name=row["name"],
            component_type=row["component_type"], amount=row["amount"],
            sort_order=row["sort_order"], note=row["note"]))

    return entry


def process_period(period, employees=None):
    """Calculate every employee into `period`.

    Recalculating replaces that period's entries rather than adding to them, so
    running twice cannot pay twice. A finalised period is refused outright.
    """
    from salpurflask.models.hr import Employee

    if period.status == "Finalized":
        raise PayrollError(
            f"{period.name} is finalized. Cancel it before recalculating.")
    if period.status == "Cancelled":
        raise PayrollError(f"{period.name} is cancelled.")

    if employees is None:
        employees = (Employee.query.filter_by(active=True)
                     .order_by(Employee.code).all())

    # Clear this period's existing entries first — the run is a replacement.
    for existing in period.entries.all():
        db.session.delete(existing)
    db.session.flush()

    processed, skipped = [], []
    for emp in employees:
        try:
            result = calculate(emp, period)
        except PayrollError as e:
            skipped.append((emp, str(e)))
            continue
        build_entry(emp, period, result)
        processed.append(emp)

    period.status = "Processing"
    return processed, skipped


def recover_advances(period):
    """Move advance recovery from 'calculated' to 'recovered'.

    Called when a period is finalised, never at calculation time: a preview must
    not change what an employee still owes. An advance whose balance reaches
    zero is marked Settled.
    """
    recovered = 0
    for entry in period.entries.all():
        for item in entry.items:
            if item.code != "ADVANCE" or not item.amount:
                continue
            remaining = _dec(item.amount)
            advances = (EmployeeAdvance.query
                        .filter_by(employee_id=entry.employee_id, status="Active")
                        .order_by(EmployeeAdvance.advance_date).all())
            for adv in advances:
                if remaining <= 0:
                    break
                take = min(remaining, adv.outstanding)
                if take <= 0:
                    continue
                adv.recovered = _dec(adv.recovered) + take
                remaining -= take
                if adv.outstanding <= 0:
                    adv.status = "Settled"
                recovered += 1
    return recovered


__all__ = ["PayrollError", "calculate", "build_entry", "process_period",
           "recover_advances", "attendance_facts", "active_structure",
           "period_days"]
