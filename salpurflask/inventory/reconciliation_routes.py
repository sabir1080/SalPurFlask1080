"""Physical count vs. system stock reconciliation routes — Phase 6.

Thin by design, the same discipline transfer_routes.py already holds itself
to: every rule that decides whether a reconciliation may be created,
counted, finalized, approved, posted or cancelled lives in
salpurflask/services/inventory_reconciliation.py. These routes parse the
form, call the service inside a try/except that mirrors transfer_routes.py's
own PostingError -> flash + rollback, bare Exception -> flash + rollback,
success -> commit shape, and render.

Create/count/finalize/cancel use manager_required, the same gate
stock_adjustment() and transfer_new()/transfer_cancel() already use.
Approve/post use admin_required — approving or posting a count is a bigger
trust step than counting it, the same reasoning transfer_reverse() already
applies to undoing a confirmed transfer.
"""

from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import (
    Item, Location, InventoryReconciliation, resolve_location_id,
    get_or_create_default_location, RECONCILIATION_STATUSES,
)
from salpurflask.auth import manager_required, admin_required
from salpurflask.utils import get_paginated_results
from salpurflask.services import inventory_reconciliation as svc
from salpurflask.services.location_permissions import (
    accessible_location_ids, require_location_access)


def _audit(action, entity, entity_id=None, summary=""):
    try:
        from app import record_audit
        record_audit(action, entity, entity_id, summary)
    except Exception:
        pass


@manager_required
def reconciliation_list():
    """List reconciliations, most recent first. Filterable by status and
    location — the same shape transfer_list() already uses.

    Location-scoped as of Phase 5: a restricted user sees only
    reconciliations at a location they hold."""
    accessible_ids = accessible_location_ids()

    status = request.args.get("status", "").strip()
    location_id = request.args.get("location_id", "").strip()

    query = InventoryReconciliation.query
    if status in RECONCILIATION_STATUSES:
        query = query.filter(InventoryReconciliation.status == status)
    if location_id.isdigit():
        require_location_access(int(location_id))
        query = query.filter(InventoryReconciliation.location_id == int(location_id))
    if accessible_ids is not None:
        query = query.filter(InventoryReconciliation.location_id.in_(accessible_ids))

    query = query.order_by(InventoryReconciliation.created_at.desc(),
                           InventoryReconciliation.id.desc())
    reconciliations, pagination = get_paginated_results(query)
    locations_query = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations_query = locations_query.filter(Location.id.in_(accessible_ids))
    locations = locations_query.order_by(Location.name).all()
    return render_template(
        "inventory_reconciliation_list.html", reconciliations=reconciliations,
        pagination=pagination, locations=locations, statuses=RECONCILIATION_STATUSES,
        filters={"status": status, "location_id": location_id})


@manager_required
def reconciliation_new():
    """Create a Draft reconciliation: pick a warehouse, pick items (manually
    or "all active stock items here"), then land on the count form.

    Location-scoped as of Phase 5: the warehouse picker lists only
    accessible locations, and a submitted location_id is refused unless the
    user is authorized for it — the same require_location_access() call
    every other Phase 5 write route already makes."""
    accessible_ids = accessible_location_ids()

    locations_query = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations_query = locations_query.filter(Location.id.in_(accessible_ids))
    locations = locations_query.order_by(Location.name).all()
    template_default_location = locations[0] if (accessible_ids is not None and locations) \
        else get_or_create_default_location()

    items = Item.query.filter_by(item_type="STOCK").order_by(Item.name).all()

    if request.method == "POST":
        try:
            location_id = resolve_location_id(request.form.get("location_id"))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("reconciliation_new"))
        require_location_access(location_id)

        if request.form.get("all_items") == "1":
            item_ids = [i.id for i in items]
        else:
            item_ids = [int(v) for v in request.form.getlist("item_id[]") if v.strip().isdigit()]

        date_str = request.form.get("date", "").strip()
        notes = request.form.get("notes", "").strip()
        try:
            recon_date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.utcnow()
        except ValueError:
            flash("Invalid date format! Use YYYY-MM-DD.", "danger")
            return redirect(url_for("reconciliation_new"))

        from app import PostingError

        try:
            reconciliation = svc.create_reconciliation(
                location_id=location_id, item_ids=item_ids, date=recon_date,
                notes=notes or None, created_by_id=getattr(current_user, "id", None))
            db.session.commit()
        except PostingError as e:
            db.session.rollback()
            flash(f"Reconciliation was not created: {e}", "danger")
            return redirect(url_for("reconciliation_new"))
        except Exception:
            db.session.rollback()
            flash("Reconciliation was not created — nothing was changed.", "danger")
            return redirect(url_for("reconciliation_new"))

        _audit("create", "inventory_reconciliation", reconciliation.id,
              f"Draft reconciliation at {reconciliation.location.name}")
        flash("Reconciliation created as Draft. Enter physical counts to continue.", "success")
        return redirect(url_for("reconciliation_detail", id=reconciliation.id))

    return render_template(
        "inventory_reconciliation_form.html", items=items, locations=locations,
        default_location=template_default_location,
        today=datetime.utcnow().strftime("%Y-%m-%d"))


def _get_reconciliation_or_403(id):
    """Fetch a reconciliation and enforce Phase 5 location access. Shared by
    every action route below so a direct URL to someone else's location's
    reconciliation is refused the same way regardless of which action was
    attempted — the same "one place, one 403" shape require_location_access()
    already gives every other Phase 5 gap point."""
    reconciliation = db.session.get(InventoryReconciliation, id) or abort(404)
    require_location_access(reconciliation.location_id)
    return reconciliation


@manager_required
def reconciliation_detail(id):
    """View one reconciliation and, on POST, save physical counts against
    its Draft lines. Counting is just repeated saves — no separate
    "Counting" state (see the service module's own reasoning)."""
    reconciliation = _get_reconciliation_or_403(id)

    if request.method == "POST":
        if reconciliation.status != "Draft":
            flash("Only a Draft reconciliation accepts counts.", "danger")
            return redirect(url_for("reconciliation_detail", id=id))

        counts = {}
        notes_by_item = {}
        for line in reconciliation.lines:
            raw = request.form.get(f"physical_quantity_{line.item_id}", "").strip()
            if raw:
                if not raw.lstrip("-").isdigit() or raw.startswith("-"):
                    flash(f"{line.item.name}: physical count must be a whole number "
                         f"of zero or more.", "danger")
                    return redirect(url_for("reconciliation_detail", id=id))
                counts[line.item_id] = int(raw)
            notes_by_item[line.item_id] = request.form.get(f"notes_{line.item_id}", "").strip()

        from app import PostingError

        try:
            svc.save_counts(reconciliation, counts, notes_by_item=notes_by_item)
            db.session.commit()
        except PostingError as e:
            db.session.rollback()
            flash(f"Counts were not saved: {e}", "danger")
            return redirect(url_for("reconciliation_detail", id=id))
        except Exception:
            db.session.rollback()
            flash("Counts were not saved — nothing was changed.", "danger")
            return redirect(url_for("reconciliation_detail", id=id))

        flash("Counts saved.", "success")
        return redirect(url_for("reconciliation_detail", id=id))

    return render_template("inventory_reconciliation_detail.html", reconciliation=reconciliation)


@manager_required
def reconciliation_finalize(id):
    """Draft -> Counted: snapshot system quantities and freeze the count."""
    reconciliation = _get_reconciliation_or_403(id)
    from app import PostingError

    try:
        svc.finalize_count(reconciliation, counted_by_id=getattr(current_user, "id", None))
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"Count was not finalized: {e}", "danger")
        return redirect(url_for("reconciliation_detail", id=id))
    except Exception:
        db.session.rollback()
        flash("Count was not finalized — nothing was changed.", "danger")
        return redirect(url_for("reconciliation_detail", id=id))

    _audit("finalize", "inventory_reconciliation", reconciliation.id, str(reconciliation.id))
    flash("Count finalized — system quantities snapshotted.", "success")
    return redirect(url_for("reconciliation_detail", id=id))


@manager_required
def reconciliation_reopen(id):
    """Counted -> Draft, for a recount."""
    reconciliation = _get_reconciliation_or_403(id)
    from app import PostingError

    try:
        svc.reopen_count(reconciliation)
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"Could not reopen for recount: {e}", "danger")
        return redirect(url_for("reconciliation_detail", id=id))
    except Exception:
        db.session.rollback()
        flash("Could not reopen for recount — nothing was changed.", "danger")
        return redirect(url_for("reconciliation_detail", id=id))

    flash("Reopened for recount.", "success")
    return redirect(url_for("reconciliation_detail", id=id))


@admin_required
def reconciliation_approve(id):
    """Counted -> Approved. Admin-only, and refused if the approver is also
    the counter — Phase 6's one segregation-of-duties rule, enforced in
    approve_reconciliation() itself, not just here."""
    reconciliation = _get_reconciliation_or_403(id)
    from app import PostingError

    try:
        svc.approve_reconciliation(reconciliation, approved_by_id=getattr(current_user, "id", None))
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"Reconciliation was not approved: {e}", "danger")
        return redirect(url_for("reconciliation_detail", id=id))
    except Exception:
        db.session.rollback()
        flash("Reconciliation was not approved — nothing was changed.", "danger")
        return redirect(url_for("reconciliation_detail", id=id))

    _audit("approve", "inventory_reconciliation", reconciliation.id, str(reconciliation.id))
    flash("Reconciliation approved.", "success")
    return redirect(url_for("reconciliation_detail", id=id))


@admin_required
def reconciliation_post(id):
    """Approved -> Posted: applies variances via item_add_stock()/
    item_remove_stock(), writes StockMovement, no GL entry (Phase 6 scope —
    see the service module's own docstring)."""
    reconciliation = _get_reconciliation_or_403(id)
    from app import PostingError

    try:
        svc.post_reconciliation(reconciliation, posted_by_id=getattr(current_user, "id", None))
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"Reconciliation was not posted: {e}", "danger")
        return redirect(url_for("reconciliation_detail", id=id))
    except Exception:
        db.session.rollback()
        flash("Reconciliation was not posted — nothing was changed.", "danger")
        return redirect(url_for("reconciliation_detail", id=id))

    _audit("post", "inventory_reconciliation", reconciliation.id,
          reconciliation.reference or str(reconciliation.id))
    flash(f"Reconciliation {reconciliation.reference} posted — stock adjusted.", "success")
    return redirect(url_for("reconciliation_detail", id=id))


@manager_required
def reconciliation_cancel(id):
    """Abandon a Draft or Counted reconciliation. Never allowed once
    Posted — Phase 6 builds no reversal path (see the service module)."""
    reconciliation = _get_reconciliation_or_403(id)
    from app import PostingError

    try:
        svc.cancel_reconciliation(reconciliation)
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"Reconciliation was not cancelled: {e}", "danger")
        return redirect(url_for("reconciliation_detail", id=id))
    except Exception:
        db.session.rollback()
        flash("Reconciliation was not cancelled — nothing was changed.", "danger")
        return redirect(url_for("reconciliation_detail", id=id))

    _audit("cancel", "inventory_reconciliation", reconciliation.id, str(reconciliation.id))
    flash("Reconciliation cancelled.", "success")
    return redirect(url_for("reconciliation_list"))
