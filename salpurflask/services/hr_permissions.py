"""HR / Attendance / Payroll permissions.

Built on top of the existing role system rather than beside it. TradeFlow has
three roles (admin, manager, staff) and no permission table, so adding one would
mean a new table, a migration, and an admin screen to maintain it — and every
existing user would start with nothing until someone filled it in. Instead each
named permission maps to the roles that hold it. Existing users keep working
exactly as they do today, and no role definition changes.

The map below is the single place to change who can do what. Route decorators
and template menus both read it, so they cannot drift apart.

Payroll posting is deliberately admin-only: it writes to the general ledger.
"""

from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required


# permission -> roles that hold it
PERMISSIONS = {
    # HR
    "hr.view":            ("admin", "manager", "staff"),
    "hr.create":          ("admin", "manager"),
    "hr.edit":            ("admin", "manager"),
    "hr.delete":          ("admin",),

    # Attendance
    "attendance.view":    ("admin", "manager", "staff"),
    "attendance.create":  ("admin", "manager"),
    "attendance.edit":    ("admin", "manager"),
    "attendance.delete":  ("admin",),

    # Payroll
    "payroll.view":       ("admin", "manager"),
    "payroll.create":     ("admin", "manager"),
    "payroll.edit":       ("admin", "manager"),
    "payroll.delete":     ("admin",),
    # Finalise/post writes journal entries to the GL — admin only.
    "payroll.post":       ("admin",),
    "payroll.reports":    ("admin", "manager"),

    # Leave. Staff may look at leave and raise a request for themselves; only a
    # manager or admin decides one, so nobody approves their own leave.
    "leave.view":         ("admin", "manager", "staff"),
    "leave.create":       ("admin", "manager", "staff"),
    "leave.edit":         ("admin", "manager"),
    "leave.delete":       ("admin",),
    "leave.approve":      ("admin", "manager"),
    "leave.configure":    ("admin",),
}


def has_permission(permission, user=None):
    """True when `user` (default: current_user) holds `permission`."""
    user = user if user is not None else current_user
    try:
        if not user or not user.is_authenticated:
            return False
        if not getattr(user, "verified", False):
            return False
    except Exception:
        return False
    return getattr(user, "role", None) in PERMISSIONS.get(permission, ())


def permission_required(permission):
    """Refuse a route unless the user holds `permission`.

    Reports refusal the same way role_required does, so behaviour is consistent
    with the rest of the app.
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if not getattr(current_user, "verified", False):
                flash(f"Please verify {current_user.email} to access this page.", "danger")
                return redirect(url_for("auth.signin"))
            if not has_permission(permission):
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("dashboard.index"))
            return f(*args, **kwargs)
        return decorated
    return decorator


__all__ = ["PERMISSIONS", "has_permission", "permission_required"]
