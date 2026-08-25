"""Admin: grant/revoke location access — Phase 5.

Minimal by design: view who has what, add a grant, remove a grant. No new
permission-management framework — the same three-action shape
admin_users.html already uses for role changes, applied to a different
table.
"""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import User, Location, UserLocationAccess
from salpurflask.auth import admin_required


def _audit(action, entity, entity_id=None, summary=""):
    try:
        from app import record_audit
        record_audit(action, entity, entity_id, summary)
    except Exception:
        pass


@admin_required
def admin_location_access():
    """List every non-admin user with their current location grants."""
    users = (User.query.filter(User.role != "admin")
            .order_by(User.name).all())
    locations = Location.query.filter_by(active=True).order_by(Location.name).all()
    grants = UserLocationAccess.query.all()
    grants_by_user = {}
    for g in grants:
        grants_by_user.setdefault(g.user_id, set()).add(g.location_id)
    return render_template(
        "admin_location_access.html", users=users, locations=locations,
        grants_by_user=grants_by_user)


@admin_required
def admin_grant_location_access(user_id):
    """Grant one user access to one location. Silently no-ops on a
    duplicate grant rather than erroring — clicking "grant" twice on an
    already-held location is a no-op, not a mistake to flash a warning
    over."""
    user = db.session.get(User, user_id) or None
    if user is None:
        flash("User not found.", "danger")
        return redirect(url_for("admin_location_access"))
    if user.is_admin:
        flash(f"{user.name} is an admin — already unrestricted, no grant needed.", "warning")
        return redirect(url_for("admin_location_access"))

    location_id = request.form.get("location_id", "").strip()
    if not location_id.isdigit():
        flash("Choose a warehouse.", "danger")
        return redirect(url_for("admin_location_access"))
    location = db.session.get(Location, int(location_id))
    if location is None:
        flash("Warehouse not found.", "danger")
        return redirect(url_for("admin_location_access"))

    existing = UserLocationAccess.query.filter_by(
        user_id=user.id, location_id=location.id).first()
    if existing is None:
        db.session.add(UserLocationAccess(
            user_id=user.id, location_id=location.id,
            granted_by_id=getattr(current_user, "id", None)))
        db.session.commit()
        _audit("grant", "user_location_access", user.id,
              f"{user.name} -> {location.name}")
        flash(f"{user.name} may now access {location.name}.", "success")
    else:
        flash(f"{user.name} already has access to {location.name}.", "warning")
    return redirect(url_for("admin_location_access"))


@admin_required
def admin_revoke_location_access(user_id, location_id):
    """Revoke one user's access to one location."""
    row = UserLocationAccess.query.filter_by(
        user_id=user_id, location_id=location_id).first()
    if row is None:
        flash("That access grant no longer exists.", "warning")
        return redirect(url_for("admin_location_access"))

    user = db.session.get(User, user_id)
    location = db.session.get(Location, location_id)
    db.session.delete(row)
    db.session.commit()
    _audit("revoke", "user_location_access", user_id,
          f"{user.name if user else user_id} -/-> {location.name if location else location_id}")
    flash(f"Access revoked.", "success")
    return redirect(url_for("admin_location_access"))
