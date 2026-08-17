"""Payroll models — salary structures, periods, runs and advances.

Their own tables, their own module. Payroll points at `hr_employee`; nothing in
HR or attendance points back, so both work unchanged whether payroll is on or
off, and switching it off leaves every row where it is.

Components are rows, not columns. "Basic Salary", "House Rent Allowance" and
"Tax" are seeded SalaryComponent records rather than fields on a table, so a
business that needs a Fuel Allowance adds one row instead of a migration and a
rewrite of the engine. The engine only ever asks a component two things: is it
an earning or a deduction, and what does it amount to.

Money is Numeric(14, 4) throughout, matching every other money column in the
schema. Phase 3B will turn a finalised run into journal entries; nothing here
posts to the ledger, and no accounting table is touched.
"""

from datetime import datetime
from decimal import Decimal

from salpurflask.extensions import db


MONEY = Decimal("0.0001")

COMPONENT_TYPES = ("earning", "deduction")

# How a component's amount is arrived at:
#   fixed      — the amount on the structure line, as typed
#   percent    — a percentage of basic salary
#   attendance — computed by the engine from attendance (overtime, absence)
CALC_METHODS = ("fixed", "percent", "attendance")

PERIOD_STATUSES = ("Draft", "Processing", "Finalized", "Cancelled")

# A run that has been finalised is a record of what was paid. It is not edited
# afterwards -- it is cancelled and replaced, the same rule the ledger follows.
EDITABLE_PERIOD_STATUSES = ("Draft", "Processing")

ADVANCE_STATUSES = ("Active", "Settled", "Cancelled")


def _dec(value):
    """Anything money-shaped -> Decimal, quantised. None and '' become zero."""
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value)).quantize(MONEY)


class SalaryComponent(db.Model):
    """One nameable piece of pay — an allowance, a bonus, a deduction.

    Seeded with the usual set (see seed_default_components) and extensible by
    the business. `code` is the stable handle the engine uses; `name` is what a
    payslip prints, and renaming it must not change any calculation.
    """
    __tablename__ = "hr_salary_component"

    id   = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)

    component_type = db.Column(db.String(20), nullable=False, default="earning")
    calc_method    = db.Column(db.String(20), nullable=False, default="fixed")

    # For calc_method == "percent": the percentage of basic salary.
    percent_of_basic = db.Column(db.Numeric(9, 4), nullable=False, default=0)

    # Ordering on payslips and structures, so Basic prints before allowances.
    sort_order = db.Column(db.Integer, nullable=False, default=100)

    # A seeded component the engine relies on (basic, overtime, absence) cannot
    # be deleted, because a payroll run without it would silently pay nothing.
    system   = db.Column(db.Boolean, nullable=False, default=False)
    taxable  = db.Column(db.Boolean, nullable=False, default=True)
    active   = db.Column(db.Boolean, nullable=False, default=True, index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    @property
    def is_earning(self):
        return self.component_type == "earning"

    def __repr__(self):
        return f"<SalaryComponent {self.code} {self.component_type}>"


class SalaryStructure(db.Model):
    """What one employee is contracted to be paid, before a period is run.

    One active structure per employee: two would make "what does this person
    earn" ambiguous, and the engine would have to guess. Superseding a structure
    means deactivating the old one and adding a new one, which keeps the history
    of what was agreed when.
    """
    __tablename__ = "hr_salary_structure"
    __table_args__ = (
        db.Index("ix_salary_structure_employee_active", "employee_id", "active"),
    )

    id          = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("hr_employee.id"),
                            nullable=False, index=True)

    effective_from = db.Column(db.Date, nullable=True)
    active         = db.Column(db.Boolean, nullable=False, default=True, index=True)
    notes          = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    employee = db.relationship("Employee",
                               backref=db.backref("salary_structures",
                                                  lazy="dynamic",
                                                  cascade="all, delete-orphan"))
    lines = db.relationship("SalaryStructureLine", back_populates="structure",
                            cascade="all, delete-orphan", lazy="joined")

    def amount_for(self, code):
        for line in self.lines:
            if line.component and line.component.code == code:
                return _dec(line.amount)
        return Decimal("0")

    @property
    def basic(self):
        return self.amount_for("BASIC")

    def __repr__(self):
        return f"<SalaryStructure emp={self.employee_id} active={self.active}>"


class SalaryStructureLine(db.Model):
    """One component on one structure, with the amount agreed for it."""
    __tablename__ = "hr_salary_structure_line"
    __table_args__ = (
        db.UniqueConstraint("structure_id", "component_id",
                            name="uq_structure_component"),
    )

    id           = db.Column(db.Integer, primary_key=True)
    structure_id = db.Column(db.Integer, db.ForeignKey("hr_salary_structure.id"),
                             nullable=False, index=True)
    component_id = db.Column(db.Integer, db.ForeignKey("hr_salary_component.id"),
                             nullable=False, index=True)
    amount       = db.Column(db.Numeric(14, 4), nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    structure = db.relationship("SalaryStructure", back_populates="lines")
    component = db.relationship("SalaryComponent", lazy="joined")

    def __repr__(self):
        return f"<StructureLine {self.component_id}={self.amount}>"


class PayrollPeriod(db.Model):
    """One month (or any date range) that payroll is run for.

    The unique constraint on the dates stops the same month being run twice
    under two names, which would double every salary in it.
    """
    __tablename__ = "hr_payroll_period"
    __table_args__ = (
        db.UniqueConstraint("start_date", "end_date", name="uq_period_dates"),
        db.Index("ix_period_status_start", "status", "start_date"),
    )

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), unique=True, nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date   = db.Column(db.Date, nullable=False, index=True)
    status     = db.Column(db.String(20), nullable=False, default="Draft", index=True)
    notes      = db.Column(db.String(255), nullable=True)

    finalized_at = db.Column(db.DateTime, nullable=True)
    finalized_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    entries = db.relationship("PayrollEntry", back_populates="period",
                              cascade="all, delete-orphan", lazy="dynamic")

    @property
    def editable(self):
        return self.status in EDITABLE_PERIOD_STATUSES

    @property
    def totals(self):
        rows = self.entries.all()
        return {
            "employees": len(rows),
            "gross": sum((_dec(r.gross_salary) for r in rows), Decimal("0")),
            "deductions": sum((_dec(r.total_deductions) for r in rows), Decimal("0")),
            "net": sum((_dec(r.net_salary) for r in rows), Decimal("0")),
        }

    def __repr__(self):
        return f"<PayrollPeriod {self.name} {self.status}>"


class PayrollEntry(db.Model):
    """One employee's pay for one period — the payslip.

    Totals are stored rather than recomputed on read. A structure edited next
    month must not restate what was already paid, and once phase 3B posts a
    finalised run to the ledger, the payslip and the journal entry have to agree
    forever.
    """
    __tablename__ = "hr_payroll_entry"
    __table_args__ = (
        db.UniqueConstraint("period_id", "employee_id", name="uq_payroll_period_employee"),
    )

    id          = db.Column(db.Integer, primary_key=True)
    period_id   = db.Column(db.Integer, db.ForeignKey("hr_payroll_period.id"),
                            nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("hr_employee.id"),
                            nullable=False, index=True)

    gross_salary     = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    total_earnings   = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    total_deductions = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    net_salary       = db.Column(db.Numeric(14, 4), nullable=False, default=0)

    # Attendance facts the figures were derived from, copied in at calculation
    # time so a payslip explains itself even if attendance is edited later.
    worked_days    = db.Column(db.Numeric(9, 2), nullable=False, default=0)
    absent_days    = db.Column(db.Integer, nullable=False, default=0)
    late_days      = db.Column(db.Integer, nullable=False, default=0)
    leave_days     = db.Column(db.Integer, nullable=False, default=0)
    overtime_hours = db.Column(db.Numeric(9, 2), nullable=False, default=0)
    payable_days   = db.Column(db.Numeric(9, 2), nullable=False, default=0)

    remarks    = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    period   = db.relationship("PayrollPeriod", back_populates="entries")
    employee = db.relationship("Employee", lazy="joined",
                               backref=db.backref("payroll_entries", lazy="dynamic"))
    items = db.relationship("PayrollItem", back_populates="entry",
                            cascade="all, delete-orphan", lazy="joined")

    @property
    def earnings(self):
        return [i for i in self.items if i.component_type == "earning"]

    @property
    def deductions(self):
        return [i for i in self.items if i.component_type == "deduction"]

    def __repr__(self):
        return f"<PayrollEntry emp={self.employee_id} net={self.net_salary}>"


class PayrollItem(db.Model):
    """One line on one payslip.

    The component's name and type are copied onto the row, not just referenced.
    A component renamed or deactivated next year must not rewrite last year's
    payslip -- it has to keep printing what was actually paid.
    """
    __tablename__ = "hr_payroll_item"

    id       = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("hr_payroll_entry.id"),
                         nullable=False, index=True)
    component_id = db.Column(db.Integer, db.ForeignKey("hr_salary_component.id"),
                             nullable=True, index=True)

    code           = db.Column(db.String(40), nullable=False)
    name           = db.Column(db.String(100), nullable=False)
    component_type = db.Column(db.String(20), nullable=False)
    amount         = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    sort_order     = db.Column(db.Integer, nullable=False, default=100)
    note           = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    entry     = db.relationship("PayrollEntry", back_populates="items")
    component = db.relationship("SalaryComponent", lazy="joined")

    def __repr__(self):
        return f"<PayrollItem {self.code}={self.amount}>"


class EmployeeAdvance(db.Model):
    """Money lent to an employee, recovered from later payslips.

    `recovered` tracks what has already come back. The engine deducts at most
    the instalment, and never more than the outstanding balance, so an advance
    cannot over-recover and turn into a debt owed the other way.
    """
    __tablename__ = "hr_employee_advance"

    id          = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("hr_employee.id"),
                            nullable=False, index=True)

    advance_date = db.Column(db.Date, nullable=False, index=True)
    amount       = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    recovered    = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    # Per-period recovery. Zero means "take whatever is outstanding".
    instalment   = db.Column(db.Numeric(14, 4), nullable=False, default=0)

    status  = db.Column(db.String(20), nullable=False, default="Active", index=True)
    remarks = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    employee = db.relationship("Employee", lazy="joined",
                               backref=db.backref("advances", lazy="dynamic",
                                                  cascade="all, delete-orphan"))

    @property
    def outstanding(self):
        return max(Decimal("0"), _dec(self.amount) - _dec(self.recovered))

    @property
    def is_open(self):
        return self.status == "Active" and self.outstanding > 0

    def __repr__(self):
        return f"<EmployeeAdvance emp={self.employee_id} out={self.outstanding}>"


# ── seeding ───────────────────────────────────────────────────────────────────

# (code, name, type, calc_method, sort, system)
DEFAULT_COMPONENTS = (
    ("BASIC",       "Basic Salary",           "earning",   "fixed",      10,  True),
    ("HRA",         "House Rent Allowance",   "earning",   "fixed",      20,  False),
    ("MEDICAL",     "Medical Allowance",      "earning",   "fixed",      30,  False),
    ("CONVEYANCE",  "Conveyance Allowance",   "earning",   "fixed",      40,  False),
    ("OTHER_ALLOW", "Other Allowance",        "earning",   "fixed",      50,  False),
    ("BONUS",       "Bonus",                  "earning",   "fixed",      60,  False),
    ("OVERTIME",    "Overtime",               "earning",   "attendance", 70,  True),
    ("TAX",         "Tax",                    "deduction", "fixed",      110, False),
    ("LOAN",        "Loan Deduction",         "deduction", "fixed",      120, False),
    ("ADVANCE",     "Advance Deduction",      "deduction", "attendance", 130, True),
    ("ABSENCE",     "Absence Deduction",      "deduction", "attendance", 140, True),
    ("OTHER_DED",   "Other Deduction",        "deduction", "fixed",      150, False),
)


def seed_default_components():
    """Create the standard components once. Idempotent."""
    created = 0
    for code, name, ctype, method, order, system in DEFAULT_COMPONENTS:
        if SalaryComponent.query.filter_by(code=code).first():
            continue
        db.session.add(SalaryComponent(
            code=code, name=name, component_type=ctype, calc_method=method,
            sort_order=order, system=system,
            taxable=(ctype == "earning")))
        created += 1
    if created:
        db.session.commit()
    return created


__all__ = [
    "SalaryComponent", "SalaryStructure", "SalaryStructureLine",
    "PayrollPeriod", "PayrollEntry", "PayrollItem", "EmployeeAdvance",
    "COMPONENT_TYPES", "CALC_METHODS", "PERIOD_STATUSES",
    "EDITABLE_PERIOD_STATUSES", "ADVANCE_STATUSES",
    "DEFAULT_COMPONENTS", "seed_default_components",
]
