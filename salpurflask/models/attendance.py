"""Attendance model — one row per employee per day.

Its own table, its own module. HR owns the employee; attendance points at it and
nothing points back, so HR works exactly the same whether attendance is switched
on or off.

Built so payroll (phase 3) can read it without this file learning anything about
salary. `summarise()` returns the counts a payroll run needs — worked days,
absent days, late days, leave, overtime hours — and that is the whole contract.
No rate, no amount and no journal entry belongs here: attendance records what
happened, payroll decides what it is worth.

A device or biometric import later fills the same rows: `source` says where a
row came from ("manual" today) and `device_ref` holds whatever the device calls
that punch, so an import can be reconciled without another table.
"""

from datetime import datetime, date, time

from salpurflask.extensions import db


ATTENDANCE_STATUSES = ("Present", "Absent", "Late", "Half Day", "Leave")

# Statuses that count as the employee having worked at all. Payroll will want
# this distinction and should not have to hard-code the strings.
WORKED_STATUSES = ("Present", "Late", "Half Day")

# What a full working day is, for turning hours into day-equivalents. A half day
# counts as half regardless of the clock, which is what the status means.
STANDARD_DAY_HOURS = 8.0


class Attendance(db.Model):
    """One employee, one date.

    The unique constraint on (employee_id, date) is the point of the table: a
    second row for the same person on the same day is not extra information, it
    is a contradiction — and a payroll run that counted both would pay twice.
    """
    __tablename__ = "hr_attendance"
    __table_args__ = (
        db.UniqueConstraint("employee_id", "date", name="uq_attendance_employee_date"),
        db.Index("ix_attendance_date_status", "date", "status"),
    )

    id          = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("hr_employee.id"),
                            nullable=False, index=True)
    date        = db.Column(db.Date, nullable=False, index=True)

    check_in    = db.Column(db.Time, nullable=True)
    check_out   = db.Column(db.Time, nullable=True)

    # Stored rather than derived on read: a correction to the clock times later
    # must not silently restate a payroll run that has already been posted.
    # Kept in step by recalculate() on every write.
    working_hours = db.Column(db.Numeric(6, 2), nullable=False, default=0)

    # Hours beyond a standard day. Payroll reads it; nothing here prices it.
    overtime_hours = db.Column(db.Numeric(6, 2), nullable=False, default=0)

    status  = db.Column(db.String(20), nullable=False, default="Present", index=True)
    remarks = db.Column(db.String(255), nullable=True)

    # Where the row came from. Manual today; a biometric/device import later
    # writes "device" and keeps its own reference here.
    source     = db.Column(db.String(20), nullable=False, default="manual")
    device_ref = db.Column(db.String(64), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    employee = db.relationship("Employee",
                               backref=db.backref("attendance_records",
                                                  lazy="dynamic",
                                                  cascade="all, delete-orphan"))

    # ── derivation ────────────────────────────────────────────────────────────

    @staticmethod
    def compute_hours(check_in, check_out):
        """Hours between two clock times, as a float rounded to 2dp.

        A check-out earlier than the check-in is treated as a shift crossing
        midnight rather than an error, because a night shift is ordinary in a
        shop or a warehouse and refusing it would push the user to lie about
        the times.
        """
        if not check_in or not check_out:
            return 0.0
        start = check_in.hour * 60 + check_in.minute + check_in.second / 60
        end = check_out.hour * 60 + check_out.minute + check_out.second / 60
        minutes = end - start
        if minutes < 0:
            minutes += 24 * 60          # crossed midnight
        return round(minutes / 60.0, 2)

    def recalculate(self):
        """Refresh working_hours and overtime_hours from the clock times.

        A status that means the employee was not at work zeroes both, whatever
        times were typed — otherwise a stray check-in left on a row marked
        Absent would quietly earn overtime.
        """
        if self.status in ("Absent", "Leave"):
            self.working_hours = 0
            self.overtime_hours = 0
            return

        hours = self.compute_hours(self.check_in, self.check_out)
        self.working_hours = hours
        self.overtime_hours = round(max(0.0, hours - STANDARD_DAY_HOURS), 2)

    @property
    def worked(self):
        return self.status in WORKED_STATUSES

    @property
    def day_fraction(self):
        """How much of a working day this row represents. Payroll's unit."""
        if self.status == "Half Day":
            return 0.5
        return 1.0 if self.worked else 0.0

    # ── payroll-facing summary ────────────────────────────────────────────────

    @classmethod
    def summarise(cls, employee_id, start, end):
        """Counts for one employee over a date range, inclusive.

        This is the interface payroll will call in phase 3. It returns facts
        about attendance only — no money, no rate, no posting — so payroll can
        change how it values a day without this module changing at all.
        """
        rows = cls.query.filter(cls.employee_id == employee_id,
                                cls.date >= start,
                                cls.date <= end).all()
        out = {
            "records": len(rows),
            "present": 0, "absent": 0, "late": 0, "half_day": 0, "leave": 0,
            "worked_days": 0.0,
            "working_hours": 0.0,
            "overtime_hours": 0.0,
        }
        key = {"Present": "present", "Absent": "absent", "Late": "late",
               "Half Day": "half_day", "Leave": "leave"}
        for r in rows:
            out[key[r.status]] += 1
            out["worked_days"] += r.day_fraction
            out["working_hours"] += float(r.working_hours or 0)
            out["overtime_hours"] += float(r.overtime_hours or 0)
        out["worked_days"] = round(out["worked_days"], 2)
        out["working_hours"] = round(out["working_hours"], 2)
        out["overtime_hours"] = round(out["overtime_hours"], 2)
        return out

    def __repr__(self):
        return f"<Attendance {self.employee_id} {self.date} {self.status}>"


__all__ = ["Attendance", "ATTENDANCE_STATUSES", "WORKED_STATUSES",
           "STANDARD_DAY_HOURS"]
