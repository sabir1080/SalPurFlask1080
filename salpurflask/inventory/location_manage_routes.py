"""Admin: create and manage warehouses (Location rows).

Minimal by design, same shape as location_access_routes.py: list existing
warehouses, add a new one, deactivate/reactivate one. A business always has
at least the auto-created default warehouse (see get_or_create_default_location
in inventory_location.py) — this only adds the ability to register a SECOND
(or further) one, which nothing in the app could do from the UI before.

Deactivating a warehouse never deletes it or its stock/history — `active`
just hides it from the pickers on new Purchase/Sale/Transfer forms, the same
convention FinancialAccount and Category already use.
"""

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import func

from salpurflask.extensions import db
from salpurflask.models import Branch, Location
from salpurflask.auth import admin_required


def _audit(action, entity, entity_id=None, summary=""):
    try:
        from app import record_audit
        record_audit(action, entity, entity_id, summary)
    except Exception:
        pass


def location_name_taken(name, exclude_id=None):
    """Two warehouses with the same name are indistinguishable in every
    dropdown, so names must be unique. Case- and space-insensitive — the
    same rule and reasoning as account_name_taken() in app.py."""
    q = Location.query.filter(
        func.lower(func.trim(Location.name)) == name.strip().lower())
    if exclude_id is not None:
        q = q.filter(Location.id != exclude_id)
    return db.session.query(q.exists()).scalar()


@admin_required
def admin_locations():
    """List every warehouse (active and inactive)."""
    locations = Location.query.order_by(Location.active.desc(), Location.name).all()
    return render_template("admin_locations.html", locations=locations)


@admin_required
def admin_location_new():
    """Create a new warehouse under the default branch."""
    name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip() or None

    if not name:
        flash("Warehouse name is required.", "danger")
        return redirect(url_for("admin_locations"))
    if location_name_taken(name):
        flash(f"A warehouse named \"{name}\" already exists.", "danger")
        return redirect(url_for("admin_locations"))

    branch = Branch.query.filter_by(is_default=True).first() or Branch.query.first()
    if branch is None:
        branch = Branch(name="Main Branch", is_default=True)
        db.session.add(branch)
        db.session.flush()

    location = Location(name=name, kind="warehouse", branch_id=branch.id,
                        is_default=False, active=True, address=address)
    db.session.add(location)
    db.session.commit()
    _audit("create", "location", location.id, f"Created warehouse {location.name}")
    flash(f"Warehouse \"{location.name}\" created.", "success")
    return redirect(url_for("admin_locations"))


@admin_required
def admin_location_toggle_active(id):
    """Deactivate/reactivate a warehouse. The default warehouse can never be
    deactivated — every single-location item's stock lives there, and every
    existing purchase/sale route falls back to it when no location is
    chosen, so hiding it would silently break the whole single-warehouse
    workflow this app started as."""
    location = db.session.get(Location, id)
    if location is None:
        flash("Warehouse not found.", "danger")
        return redirect(url_for("admin_locations"))
    if location.is_default and location.active:
        flash(f"\"{location.name}\" is the default warehouse and cannot be deactivated.", "danger")
        return redirect(url_for("admin_locations"))

    location.active = not location.active
    db.session.commit()
    _audit("toggle_active", "location", location.id,
          f"{location.name} -> {'active' if location.active else 'inactive'}")
    flash(f"\"{location.name}\" is now {'active' if location.active else 'inactive'}.", "success")
    return redirect(url_for("admin_locations"))
