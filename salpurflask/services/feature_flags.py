"""Central module enable/disable switches.

One place decides whether an optional module is on. Routes ask for it through
`module_required`, templates through `module_enabled` (injected by the context
processor), and the Developer Setup page flips it.

Turning a module off only hides its menus and refuses its routes. It never
deletes a row: HR records, attendance and — most importantly — payroll journal
entries already posted to the GL stay exactly where they are, because the
accounting history of a business is not a UI preference. Turn the module back
on and everything is where it was left.

Flags live in `app_configuration` (category "modules"), so adding a module later
is one entry in MODULES below plus a checkbox on the setup page.
"""

from functools import wraps

from flask import flash, redirect, url_for


# key -> (label, default_on, description)
# Adding a future module means adding a line here. Nothing else in this file
# needs to change.
MODULES = {
    "module_hr": (
        "HR Module",
        False,
        "Employees, departments, designations",
    ),
    "module_attendance": (
        "Attendance Module",
        False,
        "Daily attendance, monthly summary (needs HR)",
    ),
    "module_payroll": (
        "Payroll Module",
        False,
        "Salary structures, payroll runs, payslips (needs HR)",
    ),
    "module_leave": (
        "Leave Module",
        False,
        "Leave types, allocations and requests (needs HR)",
    ),
}

# A module that cannot work without another. Attendance and Payroll both hang
# off Employees, so with HR off they are off too whatever their own flag says.
MODULE_REQUIRES = {
    "module_attendance": "module_hr",
    "module_payroll": "module_hr",
    "module_leave": "module_hr",
}


def _raw_flag(key):
    """This module's own flag, ignoring dependencies."""
    from salpurflask.models import AppConfiguration

    label, default, _desc = MODULES.get(key, (key, False, ""))
    try:
        value = AppConfiguration.get_value(key, None)
    except Exception:
        # Table missing (fresh DB, mid-migration) — fall back to the default
        # rather than breaking every page that renders a menu.
        return default
    if value is None:
        return default
    return bool(value)


def module_enabled(key):
    """True when `key` is on AND everything it depends on is on."""
    if key not in MODULES:
        return False
    if not _raw_flag(key):
        return False
    parent = MODULE_REQUIRES.get(key)
    if parent and not _raw_flag(parent):
        return False
    return True


def set_module(key, enabled, updated_by=None):
    """Flip a flag. No data is touched — see the module docstring."""
    from salpurflask.models import AppConfiguration

    if key not in MODULES:
        raise ValueError(f"Unknown module flag: {key}")
    label, _default, desc = MODULES[key]
    AppConfiguration.set_value(
        key,
        "true" if enabled else "false",
        updated_by=updated_by,
        description=desc,
        data_type="boolean",
        category="modules",
    )
    return bool(enabled)


def all_modules():
    """[(key, label, description, enabled, blocked_by)] for the setup page."""
    out = []
    for key, (label, _default, desc) in MODULES.items():
        parent = MODULE_REQUIRES.get(key)
        blocked = parent if (parent and not _raw_flag(parent)) else None
        out.append({
            "key": key,
            "label": label,
            "description": desc,
            "enabled": _raw_flag(key),
            "effective": module_enabled(key),
            "requires": parent,
            "blocked_by": blocked,
        })
    return out


def module_required(key):
    """Refuse a route while its module is off.

    Mirrors how role_required reports refusal — flash plus redirect — so a user
    who follows a stale bookmark gets the same experience as hitting a page they
    lack the role for, not a 404 that looks like breakage.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not module_enabled(key):
                label = MODULES.get(key, (key,))[0]
                flash(f"{label} is turned off. Enable it in Developer Setup.", "warning")
                return redirect(url_for("dashboard.index"))
            return f(*args, **kwargs)
        return decorated
    return decorator


__all__ = [
    "MODULES",
    "MODULE_REQUIRES",
    "module_enabled",
    "set_module",
    "all_modules",
    "module_required",
]
