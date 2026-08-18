"""HR models — employees and the two lookups they hang off.

Kept in their own module and their own tables. Nothing here alters an existing
table, and no existing model gains a column or a relationship pointing this way,
so the core (inventory, POS, sales, purchase, accounting) is unchanged whether
these tables hold a thousand rows or none.

Money uses Numeric(14, 4) to match every other money column in the schema — a
salary added to a journal line must not arrive as a float.

Attendance and payroll models land in their own modules in later phases; this
file stays the employee record only.
"""

from datetime import datetime

from salpurflask.extensions import db


EMPLOYMENT_STATUSES = ("Permanent", "Probation", "Contract", "Intern")


class Department(db.Model):
    """A grouping for employees — Sales, Warehouse, Accounts."""
    __tablename__ = "hr_department"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    active      = db.Column(db.Boolean, nullable=False, default=True)
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                            onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Department {self.name}>"


class Designation(db.Model):
    """A job title — Cashier, Store Manager, Accountant."""
    __tablename__ = "hr_designation"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    active      = db.Column(db.Boolean, nullable=False, default=True)
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                            onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Designation {self.name}>"


class Employee(db.Model):
    """A person on the payroll.

    `active` is a flag, never a delete: an employee who leaves still owns past
    attendance and past payslips, and those payslips are already in the GL.
    Deleting the row would orphan financial history, so the UI deactivates.
    """
    __tablename__ = "hr_employee"

    id             = db.Column(db.Integer, primary_key=True)
    # Employee code: the business's own identifier, unique and used on payslips.
    code           = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name           = db.Column(db.String(120), nullable=False, index=True)

    department_id  = db.Column(db.Integer, db.ForeignKey("hr_department.id"), nullable=True, index=True)
    designation_id = db.Column(db.Integer, db.ForeignKey("hr_designation.id"), nullable=True, index=True)

    joining_date   = db.Column(db.Date, nullable=False, index=True)
    employment_status = db.Column(db.String(20), nullable=False, default="Permanent")

    # Contact
    phone   = db.Column(db.String(30), nullable=True)
    email   = db.Column(db.String(120), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    cnic    = db.Column(db.String(30), nullable=True)

    # Pay components. Payroll reads these as the starting point for a run; a
    # salary structure may override them per employee in the payroll phase.
    basic_salary = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    allowances   = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    deductions   = db.Column(db.Numeric(14, 4), nullable=False, default=0)

    # The login this employee uses, when they have one. Nullable and unique:
    # most employees never sign in, and the ones who do own exactly one account.
    # Nothing in payroll, attendance or leave reads it -- it exists so a signed-in
    # user can be resolved to their own records and to nobody else's.
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True,
                        unique=True, index=True)

    notes     = db.Column(db.Text, nullable=True)
    documents = db.Column(db.Text, nullable=True)   # free-text list of held documents

    active     = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    department  = db.relationship("Department", backref="employees", lazy="joined")
    designation = db.relationship("Designation", backref="employees", lazy="joined")
    # uselist=False on the backref: one user is at most one employee, so
    # `user.employee` is a record or None rather than a list of one.
    user        = db.relationship("User", lazy="joined",
                                  backref=db.backref("employee", uselist=False))

    @property
    def gross_salary(self):
        """Basic + allowances, before deductions. Payroll recomputes its own
        figures at run time; this is for display on lists and the profile."""
        from decimal import Decimal
        basic = Decimal(str(self.basic_salary or 0))
        allow = Decimal(str(self.allowances or 0))
        return basic + allow

    @property
    def net_salary(self):
        from decimal import Decimal
        return self.gross_salary - Decimal(str(self.deductions or 0))

    def __repr__(self):
        return f"<Employee {self.code} {self.name}>"


__all__ = ["Department", "Designation", "Employee", "EMPLOYMENT_STATUSES"]
