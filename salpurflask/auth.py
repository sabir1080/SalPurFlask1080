"""Authentication decorators and access control."""

from functools import wraps
from flask import flash, redirect, url_for
from flask_login import login_required, current_user


def verified_required(f):
    """Decorator: user must be logged in AND verified."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.verified:
            flash(f"Please verify {current_user.email} to access this page.", "danger")
            return redirect(url_for("auth.signin"))
        return f(*args, **kwargs)
    return decorated_function


def role_required(*roles):
    """Decorator: user must be verified AND have one of the given roles."""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if not current_user.verified:
                flash(f"Please verify {current_user.email} to access this page.", "danger")
                return redirect(url_for("auth.signin"))
            if current_user.role not in roles:
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("purchase"))
            return f(*args, **kwargs)
        return decorated
    return decorator


def admin_required(f):
    """Decorator: user must be an admin."""
    return role_required("admin")(f)


def manager_required(f):
    """Decorator: user must be an admin or manager."""
    return role_required("admin", "manager")(f)


__all__ = ['verified_required', 'role_required', 'admin_required', 'manager_required']
