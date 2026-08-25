"""Warehouse-to-warehouse stock transfer routes — Phase 3.

Thin by design: every rule that decides whether a transfer may be created,
confirmed, cancelled or reversed lives in salpurflask/services/transfers.py.
These routes parse the form, call the service inside a try/except that
mirrors payroll/routes.py's period_cancel() exactly (PostingError -> flash +
rollback, bare Exception -> flash + rollback, success -> commit), and render.

Create/confirm/cancel use manager_required, the same gate stock_adjustment()
already uses. Reverse uses admin_required, the same gate
delete_stock_adjustment() already uses for undoing a committed movement —
undoing costs more trust than doing.
"""

from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import (
    Item, Location, Transfer, resolve_location_id,
    get_or_create_default_location, TRANSFER_STATUSES,
)
from salpurflask.auth import manager_required, admin_required
from salpurflask.utils import get_paginated_results
from salpurflask.services import transfers as svc
from salpurflask.services.location_permissions import (
    accessible_location_ids, require_location_access, require_transfer_access)


def _audit(action, entity, entity_id=None, summary=""):
    try:
        from app import record_audit
        record_audit(action, entity, entity_id, summary)
    except Exception:
        pass


@manager_required
def transfer_list():
    """List transfers, most recent first. Filterable by status, source and
    destination warehouse — the minimal warehouse visibility this phase adds
    to a page that previously took no filters at all.

    Location-scoped as of Phase 5: a restricted user sees only transfers
    touching a location they hold, on EITHER side — viewing an incoming
    transfer to a warehouse they hold is legitimate even if they don't also
    hold the source, unlike creating or confirming one, which needs both
    (see transfer_new())."""
    accessible_ids = accessible_location_ids()

    status = request.args.get("status", "").strip()
    source_id = request.args.get("source_location_id", "").strip()
    destination_id = request.args.get("destination_location_id", "").strip()

    query = Transfer.query
    if status in TRANSFER_STATUSES:
        query = query.filter(Transfer.status == status)
    if source_id.isdigit():
        require_location_access(int(source_id))
        query = query.filter(Transfer.source_location_id == int(source_id))
    if destination_id.isdigit():
        require_location_access(int(destination_id))
        query = query.filter(Transfer.destination_location_id == int(destination_id))
    if accessible_ids is not None:
        query = query.filter(db.or_(
            Transfer.source_location_id.in_(accessible_ids),
            Transfer.destination_location_id.in_(accessible_ids)))

    query = query.order_by(Transfer.created_at.desc(), Transfer.id.desc())
    transfers, pagination = get_paginated_results(query)
    locations_query = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations_query = locations_query.filter(Location.id.in_(accessible_ids))
    locations = locations_query.order_by(Location.name).all()
    return render_template(
        "transfer_list.html", transfers=transfers, pagination=pagination,
        locations=locations, statuses=TRANSFER_STATUSES,
        filters={"status": status, "source_location_id": source_id,
                "destination_location_id": destination_id})


@manager_required
def transfer_new():
    """Create a Draft transfer.

    Location-scoped as of Phase 5: the source and destination pickers list
    only accessible locations, and a submitted pair is refused unless the
    user is authorized for BOTH — never because they hold just one side, so
    a transfer can never move stock into or out of a warehouse the user
    couldn't otherwise see or touch."""
    accessible_ids = accessible_location_ids()

    items = Item.query.filter_by(item_type="STOCK").order_by(Item.name).all()
    locations_query = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations_query = locations_query.filter(Location.id.in_(accessible_ids))
    locations = locations_query.order_by(Location.name).all()

    if request.method == "POST":
        try:
            source_id = resolve_location_id(request.form.get("source_location_id"))
            destination_id = resolve_location_id(request.form.get("destination_location_id"))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("transfer_new"))
        require_transfer_access(source_id, destination_id)

        item_ids = request.form.getlist("item_id[]")
        quantities = request.form.getlist("quantity[]")
        date_str = request.form.get("date", "").strip()
        notes = request.form.get("notes", "").strip()

        lines = []
        for iid, qty in zip(item_ids, quantities):
            if iid.strip() and qty.strip():
                if not qty.strip().lstrip("-").isdigit():
                    flash(f"Quantity must be a whole number (got '{qty}').", "danger")
                    return redirect(url_for("transfer_new"))
                lines.append((int(iid), int(qty)))

        try:
            transfer_date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.utcnow()
        except ValueError:
            flash("Invalid date format! Use YYYY-MM-DD.", "danger")
            return redirect(url_for("transfer_new"))

        from app import PostingError

        try:
            transfer = svc.create_transfer(
                source_location_id=source_id, destination_location_id=destination_id,
                lines=lines, date=transfer_date, notes=notes or None,
                created_by_id=getattr(current_user, "id", None))
            db.session.commit()
        except PostingError as e:
            db.session.rollback()
            flash(f"Transfer was not created: {e}", "danger")
            return redirect(url_for("transfer_new"))
        except Exception:
            db.session.rollback()
            flash("Transfer was not created — nothing was changed.", "danger")
            return redirect(url_for("transfer_new"))

        _audit("create", "transfer", transfer.id,
              f"Draft transfer {transfer.source_location.name} -> {transfer.destination_location.name}")
        flash("Transfer created as Draft. Confirm it to move stock.", "success")
        return redirect(url_for("transfer_detail", id=transfer.id))

    return render_template("transfer_form.html", items=items, locations=locations,
                          default_location=get_or_create_default_location(),
                          today=datetime.utcnow().strftime("%Y-%m-%d"))


@manager_required
def transfer_detail(id):
    """View one transfer. Location-scoped as of Phase 5: a restricted user
    may view a transfer touching a location they hold on EITHER side — the
    same "one side is enough to view" rule transfer_list() uses, not the
    stricter "both sides" rule confirm/cancel/reverse need below."""
    transfer = db.session.get(Transfer, id) or abort(404)
    accessible_ids = accessible_location_ids()
    if accessible_ids is not None and not (
        transfer.source_location_id in accessible_ids
        or transfer.destination_location_id in accessible_ids):
        abort(403)
    return render_template("transfer_detail.html", transfer=transfer)


@manager_required
def transfer_confirm(id):
    """Confirm a Draft transfer — moves stock, so both endpoints are
    required, the same rule transfer_new() enforces at creation."""
    from app import PostingError

    transfer = db.session.get(Transfer, id) or abort(404)
    require_transfer_access(transfer.source_location_id, transfer.destination_location_id)
    try:
        svc.confirm_transfer(transfer, confirmed_by_id=getattr(current_user, "id", None))
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"Transfer was not confirmed: {e}", "danger")
        return redirect(url_for("transfer_detail", id=id))
    except Exception:
        db.session.rollback()
        flash("Transfer was not confirmed — nothing was changed.", "danger")
        return redirect(url_for("transfer_detail", id=id))

    _audit("confirm", "transfer", transfer.id, transfer.transfer_no or str(transfer.id))
    flash(f"Transfer {transfer.transfer_no} confirmed — stock moved.", "success")
    return redirect(url_for("transfer_detail", id=id))


@manager_required
def transfer_cancel(id):
    """Cancel a Draft transfer — no stock has moved yet, but both endpoints
    are still required to act on the transfer at all, same as confirm."""
    from app import PostingError

    transfer = db.session.get(Transfer, id) or abort(404)
    require_transfer_access(transfer.source_location_id, transfer.destination_location_id)
    try:
        svc.cancel_transfer(transfer)
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"Transfer was not cancelled: {e}", "danger")
        return redirect(url_for("transfer_detail", id=id))
    except Exception:
        db.session.rollback()
        flash("Transfer was not cancelled — nothing was changed.", "danger")
        return redirect(url_for("transfer_detail", id=id))

    _audit("cancel", "transfer", transfer.id, str(transfer.id))
    flash("Transfer cancelled.", "success")
    return redirect(url_for("transfer_list"))


@admin_required
def transfer_reverse(id):
    """Reverse a Confirmed transfer. Already admin-only, so this check is
    always satisfied for whoever reaches it — kept for consistency with the
    other three transfer actions and as a defence-in-depth backstop, not
    because a non-admin can otherwise reach this route."""
    from app import PostingError

    transfer = db.session.get(Transfer, id) or abort(404)
    require_transfer_access(transfer.source_location_id, transfer.destination_location_id)
    try:
        svc.reverse_transfer(transfer, reversed_by_id=getattr(current_user, "id", None))
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(f"Transfer was not reversed: {e}", "danger")
        return redirect(url_for("transfer_detail", id=id))
    except Exception:
        db.session.rollback()
        flash("Transfer was not reversed — nothing was changed.", "danger")
        return redirect(url_for("transfer_detail", id=id))

    _audit("reverse", "transfer", transfer.id, transfer.transfer_no or str(transfer.id))
    flash(f"Transfer {transfer.transfer_no} reversed — stock restored.", "success")
    return redirect(url_for("transfer_detail", id=id))
