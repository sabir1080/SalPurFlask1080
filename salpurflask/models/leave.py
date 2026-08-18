"""Leave models — types, allocations and requests.

Their own tables, their own module. Attendance and payroll do not import this
package; payroll asks it a question through one function (`leave_facts`) and
gets numbers back, the same arrangement attendance already has. Turn the module
off and both work exactly as before.

Three ideas hold the design together:

  * A leave TYPE is master data, not a hard-coded enum, so a business adds
    "Hajj Leave" with a row instead of a migration.
  * A BALANCE is never stored. It is allocated days minus the days actually
    consumed by approved requests, computed on read. A stored counter drifts the
    first time a request is cancelled inside a transaction that later rolls back,
    and a leave balance that quietly disagrees with its own history is worse than
    no balance at all.
  * A REQUEST records what was asked for and what was decided. Its day count is
    computed by the module, never taken from the form, so a user cannot type
    "1 day" over a fortnight and walk off with thirteen free ones.

Nothing here posts to the general ledger. Paid and unpaid leave reach the
accounts only through the payroll engine, exactly as absence already does.
"""

from datetime import date, timedelta
from decimal import Decimal

from salpurflask.extensions import db


LEAVE_STATUSES = ("Draft", "Pending", "Approved", "Rejected", "Cancelled")

# A request in one of these states has consumed allocation. Draft, Rejected and
# Cancelled have not, which is what makes cancellation restore a balance without
# any counter being touched.
CONSUMING_STATUSES = ("Approved",)

# Requests that block the same dates being asked for twice. A rejected or
# cancelled request leaves the dates free again.
BLOCKING_STATUSES = ("Pending", "Approved")

DAY_PORTIONS = ("full", "half")

# Saturday and Sunday. Kept here rather than inline so a business whose week ends
# on Friday has one obvious place to change, and so the rule is testable.
WEEKEND_WEEKDAYS = (5, 6)


class LeaveType(db.Model):
    """A kind of leave — annual, casual, sick, unpaid.

    `paid` is the field payroll cares about and the only one it reads: paid
    leave costs the business a normal day, unpaid leave does not.
    """
    __tablename__ = "hr_leave_type"

    id   = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)

    paid = db.Column(db.Boolean, nullable=False, default=True)

    # Some leave is drawn from an allocation (annual); some is not (unpaid,
    # which is a decision rather than an entitlement).
    requires_allocation = db.Column(db.Boolean, nullable=False, default=True)
    max_days_per_year   = db.Column(db.Numeric(6, 2), nullable=True)

    carry_forward       = db.Column(db.Boolean, nullable=False, default=False)
    carry_forward_limit = db.Column(db.Numeric(6, 2), nullable=True)

    description = db.Column(db.String(255), nullable=True)
    active      = db.Column(db.Boolean, nullable=False, default=True, index=True)
    # A seeded type the module relies on; the UI refuses to delete it.
    system      = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=db.func.now())
    updated_at = db.Column(db.DateTime, nullable=False, default=db.func.now(),
                           onupdate=db.func.now())

    def __repr__(self):
        return f"<LeaveType {self.code} paid={self.paid}>"


class LeaveAllocation(db.Model):
    """How many days of one type an employee has for one year.

    Only `days` is stored. Used and remaining are derived from the requests
    themselves — see `used_days` — so the two can never disagree.
    """
    __tablename__ = "hr_leave_allocation"
    __table_args__ = (
        db.UniqueConstraint("employee_id", "leave_type_id", "year",
                            name="uq_allocation_employee_type_year"),
    )

    id            = db.Column(db.Integer, primary_key=True)
    employee_id   = db.Column(db.Integer, db.ForeignKey("hr_employee.id"),
                              nullable=False, index=True)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("hr_leave_type.id"),
                              nullable=False, index=True)
    year          = db.Column(db.Integer, nullable=False, index=True)

    days  = db.Column(db.Numeric(6, 2), nullable=False, default=0)
    notes = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=db.func.now())
    updated_at = db.Column(db.DateTime, nullable=False, default=db.func.now(),
                           onupdate=db.func.now())

    employee   = db.relationship("Employee", lazy="joined",
                                 backref=db.backref("leave_allocations",
                                                    lazy="dynamic",
                                                    cascade="all, delete-orphan"))
    leave_type = db.relationship("LeaveType", lazy="joined")

    @property
    def used_days(self):
        return used_days(self.employee_id, self.leave_type_id, self.year)

    @property
    def remaining_days(self):
        return Decimal(str(self.days or 0)) - self.used_days

    def __repr__(self):
        return f"<LeaveAllocation emp={self.employee_id} {self.year} {self.days}>"


class LeaveRequest(db.Model):
    """One request for leave, and what was decided about it.

    `days` is written by the module from the dates, never read from the form.
    """
    __tablename__ = "hr_leave_request"
    __table_args__ = (
        db.Index("ix_leave_request_emp_status", "employee_id", "status"),
        db.Index("ix_leave_request_dates", "start_date", "end_date"),
    )

    id            = db.Column(db.Integer, primary_key=True)
    employee_id   = db.Column(db.Integer, db.ForeignKey("hr_employee.id"),
                              nullable=False, index=True)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("hr_leave_type.id"),
                              nullable=False, index=True)

    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date   = db.Column(db.Date, nullable=False, index=True)
    # "full" or "half". A half day is only meaningful on a single-day request.
    day_portion = db.Column(db.String(10), nullable=False, default="full")

    days   = db.Column(db.Numeric(6, 2), nullable=False, default=0)
    reason = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="Draft", index=True)

    decided_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    decided_at    = db.Column(db.DateTime, nullable=True)
    decision_note = db.Column(db.String(300), nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at    = db.Column(db.DateTime, nullable=False, default=db.func.now())
    updated_at    = db.Column(db.DateTime, nullable=False, default=db.func.now(),
                              onupdate=db.func.now())

    employee   = db.relationship("Employee", lazy="joined",
                                 backref=db.backref("leave_requests",
                                                    lazy="dynamic",
                                                    cascade="all, delete-orphan"))
    leave_type = db.relationship("LeaveType", lazy="joined")

    @property
    def is_consuming(self):
        return self.status in CONSUMING_STATUSES

    def recalculate_days(self):
        """Recompute `days` from the dates. The only way `days` is ever set."""
        self.days = working_days(self.start_date, self.end_date,
                                 self.day_portion)
        return self.days

    def days_in(self, start, end):
        """Working days of this request that fall inside [start, end].

        This is what stops one leave being deducted twice when it spans two
        payroll periods: August asks for its own slice, September for its own,
        and the two never overlap.
        """
        if self.end_date < start or self.start_date > end:
            return Decimal("0")
        lo = max(self.start_date, start)
        hi = min(self.end_date, end)
        portion = self.day_portion if (lo == self.start_date
                                       and hi == self.end_date) else "full"
        return working_days(lo, hi, portion)

    def __repr__(self):
        return f"<LeaveRequest emp={self.employee_id} {self.start_date} {self.status}>"


# ── day counting ──────────────────────────────────────────────────────────────

def is_weekend(day):
    return day.weekday() in WEEKEND_WEEKDAYS


def working_days(start, end, day_portion="full"):
    """Working days between two dates, inclusive, skipping weekends.

    A half day is only meaningful for a single date; asking for half a fortnight
    is not a thing the form can express, so a multi-day request is always full
    days. There is no holiday calendar in TradeFlow, so none is consulted —
    when one arrives, this is the single function that needs to know.
    """
    if start is None or end is None or end < start:
        return Decimal("0")

    count = 0
    day = start
    while day <= end:
        if not is_weekend(day):
            count += 1
        day += timedelta(days=1)

    if count and day_portion == "half" and start == end:
        return Decimal("0.5")
    return Decimal(str(count))


def used_days(employee_id, leave_type_id, year):
    """Days consumed by approved requests of one type in one year.

    Derived, never stored. A request that is cancelled stops counting the moment
    its status changes, with no counter to update and no chance of drift.
    """
    rows = (LeaveRequest.query
            .filter(LeaveRequest.employee_id == employee_id,
                    LeaveRequest.leave_type_id == leave_type_id,
                    LeaveRequest.status.in_(CONSUMING_STATUSES))
            .all())
    total = Decimal("0")
    for r in rows:
        # Count only the portion falling inside the year, so a request that
        # straddles new year draws from each year's allocation fairly.
        total += r.days_in(date(year, 1, 1), date(year, 12, 31))
    return total


def allocation_for(employee_id, leave_type_id, year):
    return (LeaveAllocation.query
            .filter_by(employee_id=employee_id, leave_type_id=leave_type_id,
                       year=year).first())


def remaining_days(employee_id, leave_type_id, year):
    """What is left. Types that need no allocation are never short."""
    lt = db.session.get(LeaveType, leave_type_id)
    if lt is not None and not lt.requires_allocation:
        return None                       # not tracked against a balance
    alloc = allocation_for(employee_id, leave_type_id, year)
    allocated = Decimal(str(alloc.days)) if alloc else Decimal("0")
    return allocated - used_days(employee_id, leave_type_id, year)


def overlapping_requests(employee_id, start, end, exclude_id=None):
    """Live requests already covering any of these dates."""
    q = (LeaveRequest.query
         .filter(LeaveRequest.employee_id == employee_id,
                 LeaveRequest.status.in_(BLOCKING_STATUSES),
                 LeaveRequest.start_date <= end,
                 LeaveRequest.end_date >= start))
    if exclude_id:
        q = q.filter(LeaveRequest.id != exclude_id)
    return q.all()


# ── the payroll-facing contract ───────────────────────────────────────────────

def leave_facts(employee_id, start, end):
    """Approved leave for one employee inside one date range.

    The whole interface payroll uses. It answers in days, split by whether the
    leave is paid, and says nothing about money — the payroll engine decides
    what a day is worth, exactly as it already does for attendance.

    Only the portion of each request falling inside the range is counted, so a
    leave spanning two payroll periods is split between them and never doubled.
    """
    rows = (LeaveRequest.query
            .filter(LeaveRequest.employee_id == employee_id,
                    LeaveRequest.status.in_(CONSUMING_STATUSES),
                    LeaveRequest.start_date <= end,
                    LeaveRequest.end_date >= start)
            .all())

    paid = unpaid = Decimal("0")
    for r in rows:
        portion = r.days_in(start, end)
        if portion <= 0:
            continue
        if r.leave_type is not None and r.leave_type.paid:
            paid += portion
        else:
            unpaid += portion

    return {"paid_days": float(paid), "unpaid_days": float(unpaid),
            "total_days": float(paid + unpaid), "requests": len(rows)}


# ── seeding ───────────────────────────────────────────────────────────────────

# (code, name, paid, requires_allocation, max/yr, carry, limit, system)
DEFAULT_LEAVE_TYPES = (
    ("ANNUAL",    "Annual Leave",    True,  True,  24,   True,  10,   True),
    ("CASUAL",    "Casual Leave",    True,  True,  10,   False, None, True),
    ("SICK",      "Sick Leave",      True,  True,  10,   False, None, True),
    ("UNPAID",    "Unpaid Leave",    False, False, None, False, None, True),
    ("MATERNITY", "Maternity Leave", True,  False, 90,   False, None, False),
    ("PATERNITY", "Paternity Leave", True,  False, 10,   False, None, False),
    ("OTHER",     "Other Leave",     True,  False, None, False, None, False),
)


def seed_leave_types():
    """Create the standard leave types once. Idempotent."""
    created = 0
    for (code, name, paid, needs_alloc, max_days,
         carry, carry_limit, system) in DEFAULT_LEAVE_TYPES:
        if LeaveType.query.filter_by(code=code).first():
            continue
        db.session.add(LeaveType(
            code=code, name=name, paid=paid, requires_allocation=needs_alloc,
            max_days_per_year=max_days, carry_forward=carry,
            carry_forward_limit=carry_limit, system=system))
        created += 1
    if created:
        db.session.commit()
    return created


__all__ = ["LeaveType", "LeaveAllocation", "LeaveRequest",
           "LEAVE_STATUSES", "CONSUMING_STATUSES", "BLOCKING_STATUSES",
           "DAY_PORTIONS", "WEEKEND_WEEKDAYS", "DEFAULT_LEAVE_TYPES",
           "is_weekend", "working_days", "used_days", "allocation_for",
           "remaining_days", "overlapping_requests", "leave_facts",
           "seed_leave_types"]
