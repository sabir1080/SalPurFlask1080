"""Who the signed-in user is, as an employee — and nothing beyond that.

Every self-service page answers one question first: which employee record does
this login own? That question is answered here, once, from the session — never
from a URL, a form field or a query parameter. A route that takes an employee id
from the request and trusts it is one crafted link away from showing somebody
else's salary.

The rule the whole portal rests on:

    the employee is derived from current_user, and the only rows a page may
    show are the ones whose employee_id equals that employee's id.

`owns()` is the guard for any record reached by id. It compares the record's
employee against the session's employee and refuses anything else, so changing
the number in the address bar returns 404 rather than another person's payslip.
"""

from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user, login_required


def current_employee():
    """The Employee linked to the signed-in user, or None.

    None is an ordinary state, not an error: most users are not employees, and
    an admin running payroll has no employee record of their own.
    """
    from salpurflask.models.hr import Employee

    try:
        if not current_user or not current_user.is_authenticated:
            return None
        uid = getattr(current_user, "id", None)
    except Exception:
        return None
    if uid is None:
        return None
    # One indexed lookup by user_id -- never a scan over all employees.
    return Employee.query.filter_by(user_id=uid).first()


def owns(record, employee=None):
    """True when `record` belongs to the signed-in user's employee record.

    Works for anything carrying an `employee_id` (attendance, leave request,
    payslip) and for an Employee row itself.
    """
    employee = employee if employee is not None else current_employee()
    if employee is None or record is None:
        return False
    owner_id = getattr(record, "employee_id", None)
    if owner_id is None:
        owner_id = getattr(record, "id", None) if _is_employee(record) else None
    return owner_id is not None and owner_id == employee.id


def _is_employee(record):
    from salpurflask.models.hr import Employee
    return isinstance(record, Employee)


def require_own(record, employee=None):
    """Abort 404 unless the record belongs to this user.

    404 rather than 403 on purpose: a 403 confirms the record exists, which
    tells a prober that employee 7 has a payslip 12 even though they cannot read
    it. 404 says nothing at all.
    """
    if not owns(record, employee):
        abort(404)
    return record


def employee_required(f):
    """Refuse a self-service page to a user with no employee record.

    Reports refusal the way the rest of the app does -- flash and redirect --
    so a manager who clicks a bookmarked link gets an explanation rather than a
    blank 404.
    """
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not getattr(current_user, "verified", False):
            flash(f"Please verify {current_user.email} to access this page.",
                  "danger")
            return redirect(url_for("auth.signin"))
        if current_employee() is None:
            flash("Your login is not linked to an employee record yet. "
                  "Ask an administrator to link it.", "warning")
            return redirect(url_for("dashboard.index"))
        return f(*args, **kwargs)
    return decorated


def linkable_users():
    """Verified users not already linked to an employee.

    The admin link form offers only these, so the unique constraint on
    `user_id` is never the thing that reports a double-link to the user.
    """
    from app import User
    from salpurflask.models.hr import Employee

    taken = {e.user_id for e in
             Employee.query.filter(Employee.user_id.isnot(None)).all()}
    return [u for u in User.query.filter_by(verified=True)
            .order_by(User.name).all() if u.id not in taken]


__all__ = ["current_employee", "owns", "require_own", "employee_required",
           "linkable_users"]
