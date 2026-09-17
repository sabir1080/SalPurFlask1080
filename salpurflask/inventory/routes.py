"""Inventory routes (CRUD and read-only)."""

import json
from datetime import datetime
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import and_, or_

from salpurflask.extensions import db
from salpurflask.models import (
    Item, Category, PurchaseItem, Purchase, SaleItem, Sale,
    PurchaseReturn, SaleReturn, StockAdjustment,
    ImportLog, Customer, Supplier,
    ITEM_UNITS, MONEY, ADJUSTMENT_DIRECTIONS, ADJUSTMENT_TYPES,
    save_item_units, post_item_opening,
    post_customer_opening, post_supplier_opening,
    item_add_stock, item_remove_stock, _repost_opening, _opening_date,
    assert_not_posted, post_document,
    Batch, get_or_create_batch, enable_batch_tracking,
    item_add_stock_batched, item_remove_stock_batched,
    PostingError,
)
from salpurflask.models.business_config import BusinessCategory
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import now_local, barcode_taken, sku_taken, get_paginated_results, line_base_qty, csv_response, excel_response, parse_import_file, get_item_locked
from salpurflask.services.lookup_service import search_items, get_item_filter_fields

# Item identifiers that are real Item columns, never a per-category ProductField —
# an admin-defined field with one of these names would silently collide with (or be
# shadowed by) the core column of the same name, so it's rejected at the point a
# category field's value is extracted from the form, for both create and edit.
RESERVED_ITEM_FIELD_NAMES = {"id", "name", "sku", "barcode", "item_type", "unit",
                            "business_category_id", "category_id"}


def _first_error_tab_name(category_slug, category_field_errors):
    """Which category tab (e.g. "Batch & Expiry") the FIRST rejected field
    lives on, so the item-create form can open that tab automatically
    instead of always defaulting to whichever tab happens to be first —
    validate_product_data()'s error dict is keyed by field_name with no
    tab_name of its own, so this looks the field back up by name to read
    its tab_name off the real ProductField row. Returns None if the
    category has no fields, or none of the failing field names are found
    among them (defensive; should not happen for a genuine failure)."""
    from salpurflask.services.config_service import ConfigurationService

    fields_by_name = {f.field_name: f for f in ConfigurationService.get_category_fields(category_slug)}
    for field_name in category_field_errors:
        field = fields_by_name.get(field_name)
        if field:
            return field.tab_name or "General"
    return None


def _purchase_line_value(pi):
    """Calculate purchase line value for ledger display (simple, no discount/tax)."""
    return pi.quantity * pi.purchase_price * (pi.unit_factor or 1)


def _sale_line_value(si):
    """Calculate sale line value for ledger display (simple, no discount/tax)."""
    return si.quantity * si.sale_price * (si.unit_factor or 1)


def _batch_allocations_display(allocations):
    """Render a line's batch_allocations (PurchaseItemBatch/SaleItemBatch rows,
    one per batch — a FEFO split can leave more than one) as 'BatchNo (qty),
    BatchNo (qty)' for the Item Ledger. Descriptive only; never touches the
    ledger's own stock_in/out/value/balance math."""
    if not allocations:
        return "—"
    return ", ".join(
        f"{a.batch.batch_no or '(unknown)'} ({a.quantity})" for a in allocations
    )


@verified_required
def item_ledger(id):
    """Display item ledger with stock movements."""
    from salpurflask.models import Location, ItemStock
    from salpurflask.services.location_permissions import accessible_location_ids

    item = db.session.get(Item, id) or abort(404)
    start_date_str = request.args.get("start_date", "")
    end_date_str   = request.args.get("end_date", "")

    # Where this item actually sits right now, one row per warehouse — the
    # single place that answers "where is X currently" without making
    # someone flip through the Stock Valuation report one location at a
    # time. Scoped to accessible locations, same as report_stock()/
    # stock_movements(); only non-zero rows are shown, and only among
    # active warehouses (a deactivated one still holding stock would be
    # confusing to list here as if it were sellable from).
    accessible_ids = accessible_location_ids()
    stock_by_location_query = (
        db.session.query(Location.name, ItemStock.quantity)
        .join(ItemStock, ItemStock.location_id == Location.id)
        .filter(ItemStock.item_id == id, Location.active.is_(True), ItemStock.quantity != 0)
    )
    if accessible_ids is not None:
        stock_by_location_query = stock_by_location_query.filter(Location.id.in_(accessible_ids))
    stock_by_location = stock_by_location_query.order_by(Location.name).all()

    # A reversed document never happened, so it must not still count here — its
    # stock effect was already undone, but its row still exists for the audit trail.
    purchase_items   = (PurchaseItem.query.join(Purchase)
                        .filter(PurchaseItem.item_id == id, Purchase.is_reversed.is_(False)).all())
    sale_items       = (SaleItem.query.join(Sale)
                        .filter(SaleItem.item_id == id, Sale.is_reversed.is_(False)).all())
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id, is_reversed=False).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id, is_reversed=False).all()

    # stock_in/out are in the item's base unit — the only unit item.stock (and this
    # ledger's running balance) is ever tracked in, whatever unit the line was actually
    # bought/sold in. Rate follows it down to a per-base-unit price so it still lines
    # up with stock_in/out (Rate × qty ≈ Value); Value itself is a total and needs no
    # conversion either way.
    entries = []
    for pi in purchase_items:
        factor = pi.unit_factor or 1
        entries.append({
            "date": pi.purchase_header.date, "type": "Purchase", "badge": "success",
            "ref": f"PO #{pi.purchase_header.id}", "party": pi.purchase_header.supplier.name,
            "stock_in": line_base_qty(pi), "stock_out": 0,
            "rate": pi.purchase_price / factor, "value": _purchase_line_value(pi),
            "batch_info": _batch_allocations_display(pi.batch_allocations),
        })
    for si in sale_items:
        factor = si.unit_factor or 1
        entries.append({
            "date": si.sale_header.date, "type": "Sale", "badge": "primary",
            "ref": f"SO #{si.sale_header.id}", "party": si.sale_header.customer.name,
            "stock_in": 0, "stock_out": line_base_qty(si),
            "rate": si.sale_price / factor, "value": _sale_line_value(si),
            "batch_info": _batch_allocations_display(si.batch_allocations),
        })
    for pr in purchase_returns:
        factor = pr.unit_factor or 1
        entries.append({
            "date": pr.date, "type": "Purchase Return", "badge": "warning",
            "ref": f"PR #{pr.id}", "party": pr.supplier.name,
            "stock_in": 0, "stock_out": line_base_qty(pr),
            "rate": pr.return_price / factor, "value": round(pr.quantity * pr.return_price, 2),
            "batch_info": "—",
        })
    for sr in sale_returns:
        factor = sr.unit_factor or 1
        entries.append({
            "date": sr.date, "type": "Sale Return", "badge": "secondary",
            "ref": f"SR #{sr.id}", "party": sr.customer.name,
            "stock_in": line_base_qty(sr), "stock_out": 0,
            "rate": sr.return_price / factor, "value": round(sr.quantity * sr.return_price, 2),
            "batch_info": "—",
        })

    adjustments = StockAdjustment.query.filter_by(item_id=id).all()
    for adj in adjustments:
        stock_in = adj.quantity if adj.direction == "in" else 0
        stock_out = adj.quantity if adj.direction == "out" else 0
        entries.append({
            "date": adj.date, "type": f"Adjustment ({adj.adj_type})", "badge": "info",
            "ref": f"ADJ #{adj.id}", "party": adj.reason or "—",
            "stock_in": stock_in, "stock_out": stock_out,
            "rate": 0, "value": 0,
            "batch_info": (adj.batch.batch_no or "(unknown)") if adj.batch else "—",
        })

    entries.sort(key=lambda x: (x["date"], x["ref"]))

    date_filtered = False
    if start_date_str and end_date_str:
        try:
            sd = datetime.strptime(start_date_str, "%Y-%m-%d")
            ed = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
            entries = [e for e in entries if sd <= e["date"] <= ed]
            date_filtered = True
        except ValueError:
            flash("Invalid date format!", "danger")

    # Opening stock entry — prepend when not date-filtered (or always as starting balance)
    opening = item.opening_stock or 0
    if not date_filtered:
        opening_entry = {
            "date": None, "type": "Opening Stock", "badge": "dark",
            "ref": "—", "party": "—",
            "stock_in": opening, "stock_out": 0,
            "rate": 0, "value": 0, "batch_info": "—",
            "balance": opening, "is_opening": True,
        }
        entries = [opening_entry] + entries
        balance = opening
    else:
        balance = 0

    for e in entries:
        if e.get("is_opening"):
            continue
        balance += e["stock_in"] - e["stock_out"]
        e["balance"] = balance

    total_in  = sum(e["stock_in"]  for e in entries if not e.get("is_opening"))
    total_out = sum(e["stock_out"] for e in entries if not e.get("is_opening"))
    # The footer's closing balance must match the last transaction's running
    # balance shown in the table above it, not item.stock — item.stock can
    # differ (e.g. under a date filter, or if it's momentarily stale).
    closing_balance = entries[-1]["balance"] if entries else opening

    return render_template(
        "item_ledger.html",
        item=item,
        entries=entries,
        total_in=total_in,
        total_out=total_out,
        opening_stock=opening,
        closing_balance=closing_balance,
        current_stock=item.stock,
        stock_by_location=stock_by_location,
        start_date=start_date_str,
        end_date=end_date_str,
    )


@verified_required
def get_item(id):
    """Get item details as JSON."""
    item = db.session.get(Item, id) or abort(404)
    return {
        "purchase_price": item.purchase_price,
        "sale_price": item.sale_price,
        "unit": item.unit or "Pcs",
        "category": item.id_category.name if item.id_category else None,
    }


@manager_required
def report_stock():
    """Display stock report with valuation.

    What is in the warehouse right now, and what it cost. A snapshot, not a
    period — so unlike sales/purchase reports it takes no date range. It is an
    accounting report, not just a stock list: the total is valued at
    weighted-average cost, which is the amount the Inventory account was
    debited when the goods came in, so it is the Inventory line on the balance
    sheet and can be read straight across.

    With no location chosen this is the existing consolidated view, reading
    Item.stock exactly as it always has — a single-warehouse business sees no
    change at all. Choosing a location switches the Stock column to that
    location's ItemStock.quantity instead; valuation stays company-wide
    (Item.inventory_value is not tracked per location in this phase, see the
    architecture plan), so it is shown once, unaffected by the filter.

    Location-scoped as of Phase 5: a restricted user's location dropdown
    lists only what they may see, a location_id they aren't authorized for
    is refused (never a silently-empty page, which would still confirm the
    location exists), and the unfiltered "consolidated" total sums only
    their own accessible locations, not the whole company's — see
    location_permissions.py for the one place this decision is made.
    """
    from salpurflask.models import Location, ItemStock, Batch, BatchStock
    from salpurflask.services.location_permissions import (
        accessible_location_ids, require_location_access)

    accessible_ids = accessible_location_ids()

    items = (Item.query.outerjoin(Category, Item.category_id == Category.id)
             .order_by(Category.name, Item.name).all())
    locations_query = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations_query = locations_query.filter(Location.id.in_(accessible_ids))
    locations = locations_query.order_by(Location.name).all()

    location_id = request.args.get("location_id", "").strip()
    selected_location = None
    if location_id.isdigit():
        require_location_access(int(location_id))
        selected_location = db.session.get(Location, int(location_id))

    if selected_location:
        # One query for every item's row at this location, grouped in memory —
        # the same N+1-avoidance shape report_aging() already uses in app.py,
        # not a per-item query in the loop below.
        rows = {r.item_id: r.quantity for r in
               ItemStock.query.filter_by(location_id=selected_location.id).all()}
        stock_lookup = {it.id: rows.get(it.id, 0) for it in items}
    elif accessible_ids is not None:
        # Restricted, no specific location chosen: the "consolidated" total
        # sums only the locations this user may see, never every warehouse.
        rows = {}
        for r in ItemStock.query.filter(ItemStock.location_id.in_(accessible_ids)).all():
            rows[r.item_id] = rows.get(r.item_id, 0) + r.quantity
        stock_lookup = {it.id: rows.get(it.id, 0) for it in items}
    else:
        stock_lookup = {it.id: (it.stock or 0) for it in items}

    # Batch-wise breakdown (Phase F): one row per (batch, location) with
    # quantity > 0, for every batch-tracked item — an expandable detail under
    # each item row, not a replacement for the item-level figures above.
    # Scoped to the same accessible_ids as everything else on this page, so a
    # restricted user never sees another location's batch stock. Grouped in
    # memory by item_id, same N+1-avoidance shape as stock_lookup above.
    batch_tracked_item_ids = [it.id for it in items if it.batch_tracked]
    batch_breakdown = {}
    if batch_tracked_item_ids:
        bquery = (db.session.query(Batch, BatchStock.quantity, Location.name)
                  .join(BatchStock, BatchStock.batch_id == Batch.id)
                  .join(Location, Location.id == BatchStock.location_id)
                  .filter(Batch.item_id.in_(batch_tracked_item_ids), BatchStock.quantity > 0))
        if selected_location:
            bquery = bquery.filter(BatchStock.location_id == selected_location.id)
        elif accessible_ids is not None:
            bquery = bquery.filter(BatchStock.location_id.in_(accessible_ids))
        bquery = bquery.order_by(Batch.expiry_date.is_(None), Batch.expiry_date.asc(), Batch.id.asc())
        for batch, qty, loc_name in bquery.all():
            unit_cost = Decimal(str(batch.unit_cost or 0))
            batch_breakdown.setdefault(batch.item_id, []).append({
                "batch_no": batch.batch_no or "(unknown)",
                "expiry_date": batch.expiry_date,
                "location_name": loc_name,
                "quantity": qty,
                "unit_cost": unit_cost,
                "value": (unit_cost * Decimal(str(qty))).quantize(MONEY),
            })

    return render_template(
        "report_stock.html",
        stock_report=items,
        stock_lookup=stock_lookup,
        locations=locations,
        selected_location=selected_location,
        stock_value_total=sum(Decimal(str(i.inventory_value or 0)) for i in items),
        items_in_stock=sum(1 for i in items if stock_lookup.get(i.id, 0) > 0),
        reorder_report=[i for i in items if stock_lookup.get(i.id, 0) <= i.reorder_level],
        as_of=now_local().strftime("%d %B %Y"),
        batch_breakdown=batch_breakdown,
    )


@manager_required
def stock_movements():
    """Read-only stock movement ledger — what happened to an item, at which
    warehouse, and why. Filterable by item, location and movement type; the
    unfiltered view is every movement, most recent first, the same shape as
    the audit log page.

    Every row here was written by item_add_stock()/item_remove_stock()
    themselves (or, for opening balances, the three call sites that
    intentionally seed ItemStock outside those choke points — see the
    Phase 4 implementation note) — nothing here is computed or re-derived,
    it only ever shows what a mutation actually did.

    Location-scoped as of Phase 5: an explicit location_id the user isn't
    authorized for is refused; the unfiltered view is scoped to only the
    locations they may see, never every warehouse's history."""
    from salpurflask.models import StockMovement, Location, MOVEMENT_TYPES
    from salpurflask.services.location_permissions import (
        accessible_location_ids, require_location_access)

    accessible_ids = accessible_location_ids()

    item_id = request.args.get("item_id", "").strip()
    location_id = request.args.get("location_id", "").strip()
    movement_type = request.args.get("movement_type", "").strip()

    query = StockMovement.query
    if item_id.isdigit():
        query = query.filter(StockMovement.item_id == int(item_id))
    if location_id.isdigit():
        require_location_access(int(location_id))
        query = query.filter(StockMovement.location_id == int(location_id))
    elif accessible_ids is not None:
        query = query.filter(StockMovement.location_id.in_(accessible_ids))
    if movement_type in MOVEMENT_TYPES:
        query = query.filter(StockMovement.movement_type == movement_type)

    query = query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
    movements, pagination = get_paginated_results(query, per_page=50)

    items = Item.query.order_by(Item.name).all()
    locations_query = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations_query = locations_query.filter(Location.id.in_(accessible_ids))
    locations = locations_query.order_by(Location.name).all()

    return render_template(
        "stock_movements.html",
        movements=movements,
        pagination=pagination,
        items=items,
        locations=locations,
        movement_types=MOVEMENT_TYPES,
        filters={"item_id": item_id, "location_id": location_id, "movement_type": movement_type},
    )


@manager_required
def expiring_batches():
    """Company-wide (permission-scoped) view of batch stock by expiry status —
    the report gap Phase F closes: NEAR_EXPIRY_WARNING_DAYS previously only
    ever surfaced inside the POS batch picker's JSON response for a single
    item, never as a standalone list. Read-only; never mutates Batch or
    BatchStock.

    Only BatchStock.quantity > 0 rows are shown — a batch that's been fully
    consumed/transferred out is not "expiring stock" anymore. A NULL
    expiry_date is a real, valid state (see Batch's own docstring on Unknown
    Batch / undated receipts) and is classified as its own "no_expiry"
    bucket, never silently folded into "expired" or "near_expiry".

    Location-scoped exactly like report_stock()/stock_movements(): an
    explicit location_id the user isn't authorized for is refused, and the
    unfiltered view is scoped to only the locations they may see."""
    from salpurflask.models import Location, Batch, BatchStock, NEAR_EXPIRY_WARNING_DAYS
    from salpurflask.services.location_permissions import (
        accessible_location_ids, require_location_access)

    accessible_ids = accessible_location_ids()
    today = now_local().date()

    location_id = request.args.get("location_id", "").strip()
    status_filter = request.args.get("status", "").strip()
    try:
        threshold_days = int(request.args.get("days", "").strip() or NEAR_EXPIRY_WARNING_DAYS)
    except ValueError:
        threshold_days = NEAR_EXPIRY_WARNING_DAYS

    query = (db.session.query(Batch, BatchStock.quantity, BatchStock.location_id, Location.name, Item.name)
             .join(BatchStock, BatchStock.batch_id == Batch.id)
             .join(Location, Location.id == BatchStock.location_id)
             .join(Item, Item.id == Batch.item_id)
             .filter(BatchStock.quantity > 0))

    if location_id.isdigit():
        require_location_access(int(location_id))
        query = query.filter(BatchStock.location_id == int(location_id))
    elif accessible_ids is not None:
        query = query.filter(BatchStock.location_id.in_(accessible_ids))

    query = query.order_by(Batch.expiry_date.is_(None), Batch.expiry_date.asc(), Batch.id.asc())

    rows = []
    for batch, qty, loc_id, loc_name, item_name in query.all():
        if batch.expiry_date is None:
            status, days_to_expiry = "no_expiry", None
        else:
            days_to_expiry = (batch.expiry_date - today).days
            if batch.expiry_date < today:
                status = "expired"
            elif batch.expiry_date == today:
                status = "today"
            elif days_to_expiry <= threshold_days:
                status = "near_expiry"
            else:
                status = "ok"
        unit_cost = Decimal(str(batch.unit_cost or 0))
        rows.append({
            "item_name": item_name, "batch_no": batch.batch_no or "(unknown)",
            "location_name": loc_name, "quantity": qty,
            "expiry_date": batch.expiry_date, "days_to_expiry": days_to_expiry,
            "unit_cost": unit_cost, "value": (unit_cost * Decimal(str(qty))).quantize(MONEY),
            "status": status,
        })

    if status_filter in ("expired", "today", "near_expiry", "ok", "no_expiry"):
        rows = [r for r in rows if r["status"] == status_filter]

    locations_query = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations_query = locations_query.filter(Location.id.in_(accessible_ids))
    locations = locations_query.order_by(Location.name).all()

    return render_template(
        "expiring_batches.html",
        rows=rows,
        locations=locations,
        threshold_days=threshold_days,
        default_threshold=NEAR_EXPIRY_WARNING_DAYS,
        filters={"location_id": location_id, "status": status_filter, "days": str(threshold_days)},
        total_value=sum((r["value"] for r in rows), Decimal("0")),
        as_of=now_local().strftime("%d %B %Y"),
    )


@verified_required
def item():
    """Display items with filters and handle item creation."""
    from salpurflask.models.business_config import BusinessCategory
    from salpurflask.services.config_service import ConfigurationService

    search = request.args.get("search", "")
    business_category_filter = request.args.get("business_category_id", "")
    category_filter = request.args.get("category_id", "")
    query = Item.query.outerjoin(BusinessCategory, Item.business_category_id == BusinessCategory.id)
    if search:
        query = query.filter(
            (Item.name.ilike(f"%{search}%")) |
            (BusinessCategory.name.ilike(f"%{search}%")) |
            (Item.barcode.ilike(f"%{search}%"))
        )
    if business_category_filter.isdigit():
        query = query.filter(Item.business_category_id == int(business_category_filter))
    items, pagination = get_paginated_results(query)
    categories = Category.query.order_by(Category.name).all()
    business_categories = ConfigurationService.get_enabled_categories()
    if request.method == "POST":
        if current_user.role not in ("admin", "manager"):
            flash("You do not have permission to add items.", "danger")
            return redirect(url_for("item"))
        name = request.form.get("name", "").strip()
        business_category_id = request.form.get("business_category_id", "").strip() or None
        item_type = request.form.get("item_type", "STOCK").strip()
        unit = request.form.get("unit", "Pcs").strip()
        opening_stock = request.form.get("opening_stock", "0").strip() or "0"
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        barcode = request.form.get("barcode", "").strip() or None
        sku = request.form.get("sku", "").strip() or None
        if unit not in ITEM_UNITS:
            unit = "Pcs"
        resolved_category = ConfigurationService.resolve_enabled_category(business_category_id)
        category_field_errors = (
            ConfigurationService.validate_product_data(resolved_category.slug, request.form)
            if resolved_category else {})
        if not business_categories:
            flash("No business categories are enabled. Please enable categories in Admin Settings before adding items!", "danger")
        elif not name or not business_category_id:
            flash("Name and Category are required!", "danger")
        elif item_type == "STOCK" and not reorder_level:
            flash("Reorder Level is required for Stock items!", "danger")
        elif not resolved_category:
            # Covers a missing id, a nonexistent id, and a disabled category —
            # an enabled, valid BusinessCategory is mandatory for every Item,
            # never optional and never satisfied by the legacy Category table.
            flash("An enabled Business Category is required to create an item. Please select a valid category!", "danger")
        elif not opening_stock.lstrip("-").isdigit() or (reorder_level and not reorder_level.isdigit()):
            flash("Opening Stock and Reorder Level must be numbers!", "danger")
        elif purchase_price and (not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0):
            flash("Purchase price must be a non-negative number!", "danger")
        elif sale_price and (not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0):
            flash("Sale price must be a non-negative number!", "danger")
        elif int(opening_stock) > 0 and (not purchase_price or float(purchase_price or 0) == 0):
            flash("Opening stock requires a purchase price greater than 0!", "danger")
        elif barcode_taken(barcode):
            flash(f"Barcode '{barcode}' is already used by another item. "
                  "A code must point at one item only.", "danger")
        elif sku_taken(sku):
            flash(f"SKU '{sku}' is already used by another item. "
                  "A SKU must point at one item only.", "danger")
        elif category_field_errors:
            flash("; ".join(category_field_errors.values()), "danger")
            return render_template("item.html", items=items, categories=categories,
                                   business_categories=business_categories,
                                   pagination=pagination, search=search,
                                   category_filter=category_filter,
                                   form_data=request.form,
                                   error_tab_name=_first_error_tab_name(
                                       resolved_category.slug, category_field_errors))
        else:
            os_val = int(opening_stock)
            reorder_val = int(reorder_level) if reorder_level else 50
            item_obj = Item(
                name=name,
                category_id=None,  # Using business_category_id instead
                business_category_id=int(business_category_id),
                item_type=item_type,
                unit=unit,
                opening_stock=os_val,
                stock=os_val,
                reorder_level=reorder_val,
                purchase_price=float(purchase_price) if purchase_price else None,
                sale_price=float(sale_price) if sale_price else None,
                barcode=barcode,
                sku=sku,
            )
            db.session.add(item_obj)
            item_obj.inventory_value = (Decimal(str(item_obj.opening_stock or 0))
                                        * Decimal(str(item_obj.purchase_price or 0))).quantize(MONEY)
            db.session.flush()

            if item_obj.item_type == "STOCK":
                from salpurflask.models.inventory_location import (
                    get_or_create_default_location, ItemStock, record_stock_movement)
                default_location = get_or_create_default_location()
                db.session.add(ItemStock(item_id=item_obj.id, location_id=default_location.id,
                                         quantity=os_val))
                if os_val > 0:
                    # Bypasses item_add_stock() on purpose: this is the item's
                    # first-ever ItemStock row, not a trading transaction — see
                    # the Phase 4 implementation note. Still recorded, so the
                    # ledger's opening balance is never silently missing.
                    record_stock_movement(item_obj.id, default_location.id, "in", os_val,
                                          "opening", source_type="item", source_id=item_obj.id,
                                          created_by_id=getattr(current_user, "id", None))

            # Save category-specific field data
            if business_category_id and business_category_id.isdigit():
                from salpurflask.services.config_service import ConfigurationService
                from salpurflask.models.business_config import ProductField

                category_field_data = {}
                # Get all field names for this category
                category_fields = ProductField.query.filter_by(category_id=int(business_category_id)).all()
                field_names = {f.field_name for f in category_fields}

                # Extract category-specific fields from form. RESERVED_ITEM_FIELD_NAMES
                # guards against a same-named core column (sku, barcode, ...) ever being
                # written as EAV data even if a stale/legacy ProductField row exists with
                # that name — belt-and-braces alongside the create-time guard in
                # ConfigurationService.add_product_field.
                for key, value in request.form.items():
                    if key in field_names and key not in RESERVED_ITEM_FIELD_NAMES and value:
                        category_field_data[key] = value

                if category_field_data:
                    ConfigurationService.save_product_category_data(item_obj.id, int(business_category_id), category_field_data)

            unit_error = save_item_units(item_obj)
            if unit_error:
                db.session.rollback()
                flash(unit_error, "danger")
                return render_template("item.html", items=items, categories=categories,
                                       business_categories=business_categories,
                                       pagination=pagination, search=search,
                                       category_filter=category_filter,
                                       form_data=request.form)
            post_item_opening(item_obj)
            db.session.commit()
            # Import record_audit locally to avoid circular imports
            from app import record_audit
            record_audit("create", "Item", item_obj.id, f"Item '{item_obj.name}' added")
            flash("Item added successfully!", "success")
            return redirect(url_for("item"))
    return render_template(
        "item.html",
        items=items,
        categories=categories,
        business_categories=business_categories,
        pagination=pagination,
        search=search,
        category_filter=category_filter,
        form_data=request.form if request.method == "POST" else {},
    )


@manager_required
def edit_item(id):
    """Edit an existing item."""
    from salpurflask.models.business_config import BusinessCategory
    from salpurflask.services.config_service import ConfigurationService

    item = db.session.get(Item, id) or abort(404)
    categories = Category.query.order_by(Category.name).all()
    business_categories = ConfigurationService.get_enabled_categories()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        business_category_id = request.form.get("business_category_id", "").strip() or None
        item_type = request.form.get("item_type", "STOCK").strip()
        unit = request.form.get("unit", "Pcs").strip()
        opening_stock = request.form.get("opening_stock", str(item.opening_stock)).strip()
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        barcode = request.form.get("barcode", "").strip() or None
        sku = request.form.get("sku", "").strip() or None
        if unit not in ITEM_UNITS:
            unit = "Pcs"
        resolved_category = ConfigurationService.resolve_enabled_category(business_category_id)
        category_field_errors = (
            ConfigurationService.validate_product_data(resolved_category.slug, request.form)
            if resolved_category else {})
        if not business_categories:
            flash("No business categories are enabled. Please enable categories in Admin Settings!", "danger")
        elif not name or not business_category_id:
            flash("Name and Category are required!", "danger")
        elif item_type == "STOCK" and not reorder_level:
            flash("Reorder Level is required for Stock items!", "danger")
        elif not resolved_category:
            # Same rule as item(): an enabled, valid BusinessCategory is
            # mandatory, whether the id is missing, nonexistent, or disabled.
            flash("An enabled Business Category is required to save an item. Please select a valid category!", "danger")
        elif not opening_stock.lstrip("-").isdigit() or (reorder_level and not reorder_level.isdigit()):
            flash("Opening Stock and Reorder Level must be numbers!", "danger")
        elif purchase_price and (not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0):
            flash("Purchase price must be a non-negative number!", "danger")
        elif sale_price and (not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0):
            flash("Sale price must be a non-negative number!", "danger")
        elif int(opening_stock) > 0 and (not purchase_price or float(purchase_price or 0) == 0):
            flash("Opening stock requires a purchase price greater than 0!", "danger")
        elif barcode_taken(barcode, exclude_id=item.id):
            flash(f"Barcode '{barcode}' is already used by another item. "
                  "A code must point at one item only.", "danger")
        elif sku_taken(sku, exclude_id=item.id):
            flash(f"SKU '{sku}' is already used by another item. "
                  "A SKU must point at one item only.", "danger")
        elif category_field_errors:
            flash("; ".join(category_field_errors.values()), "danger")
        else:
            new_os = int(opening_stock)
            stock_adjustment = new_os - item.opening_stock
            # Guard: Cannot edit opening stock after transactions exist (accounting period integrity)
            if stock_adjustment != 0 and (item.purchases or item.sales):
                flash("Cannot edit opening stock after transactions have been recorded! "
                      "Create a stock adjustment instead to change inventory.", "danger")
                return render_template("edit_item.html", item=item, categories=categories,
                                       business_categories=business_categories)
            # The opening entry is about to be reversed and re-posted, so the
            # inventory value must move by the same amount the GL does.
            old_opening_value = (Decimal(str(item.opening_stock or 0))
                                 * Decimal(str(item.purchase_price or 0))).quantize(MONEY)
            item.name = name
            item.category_id = None  # Using business_category_id instead
            item.business_category_id = int(business_category_id)
            item.item_type = item_type
            item.unit = unit
            item.opening_stock = new_os
            item.reorder_level = int(reorder_level) if reorder_level else 50
            item.purchase_price = float(purchase_price) if purchase_price else None
            item.sale_price = float(sale_price) if sale_price else None
            item.barcode = barcode
            item.sku = sku
            new_opening_value = (Decimal(str(new_os)) * Decimal(str(item.purchase_price or 0))).quantize(MONEY)
            value_adjustment = new_opening_value - old_opening_value
            # Update stock and inventory_value atomically
            if stock_adjustment > 0:
                item_add_stock(item, stock_adjustment, cost_total=value_adjustment,
                               movement_type="opening", source_type="item", source_id=item.id)
            elif stock_adjustment < 0:
                item_remove_stock(item, -stock_adjustment, cost_total=-value_adjustment,
                                  movement_type="opening", source_type="item", source_id=item.id)
            unit_error = save_item_units(item)
            if unit_error:
                db.session.rollback()
                flash(unit_error, "danger")
                return render_template("edit_item.html", item=item, categories=categories,
                                       business_categories=business_categories)
            db.session.flush()

            # Save category-specific field data
            if business_category_id and business_category_id.isdigit():
                from salpurflask.models.business_config import ProductField

                category_field_data = {}
                # Get all field names for this category
                category_fields = ProductField.query.filter_by(category_id=int(business_category_id)).all()
                field_names = {f.field_name for f in category_fields}

                # Extract category-specific fields from form (see RESERVED_ITEM_FIELD_NAMES)
                for key, value in request.form.items():
                    if key in field_names and key not in RESERVED_ITEM_FIELD_NAMES and value:
                        category_field_data[key] = value

                if category_field_data:
                    ConfigurationService.save_product_category_data(item.id, int(business_category_id), category_field_data)

            post_item_opening(item)
            db.session.commit()
            # Import record_audit locally to avoid circular imports
            from app import record_audit
            record_audit("update", "Item", item.id, f"Item '{item.name}' edited")
            flash("Item updated successfully!", "success")
            return redirect(url_for("item"))
    return render_template("edit_item.html", item=item, categories=categories,
                           business_categories=business_categories)


@admin_required
def delete_item(id):
    """Delete an existing item."""
    item = db.session.get(Item, id) or abort(404)
    if item.purchases or item.sales:
        flash("Cannot delete item with associated purchases or sales!", "danger")
    else:
        item_name = item.name
        # Clear GL entries for this item's opening balance before deletion
        _repost_opening("item_opening", item.id, _opening_date(),
                       f"Opening stock — {item.name}", [])
        db.session.delete(item)
        db.session.commit()
        # Import record_audit locally to avoid circular imports
        from app import record_audit
        record_audit("delete", "Item", id, f"Item '{item_name}' deleted")
        flash("Item deleted successfully!", "success")
    return redirect(url_for("item"))


@verified_required
def category():
    """Display categories with search and handle category creation."""
    search = request.args.get("search", "")
    query = Category.query.filter(Category.name.ilike(f"%{search}%")) if search else Category.query
    categories, pagination = get_paginated_results(query)
    if request.method == "POST":
        if current_user.role not in ("admin", "manager"):
            flash("You do not have permission to add categories.", "danger")
            return redirect(url_for("category"))
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required!", "danger")
        elif Category.query.filter_by(name=name).first():
            flash("Category already exists!", "warning")
            return redirect(url_for("category"))
        else:
            db.session.add(Category(name=name))
            db.session.commit()
            flash("Category added successfully!", "success")
            return redirect(url_for("category"))
    return render_template("category.html", categories=categories, pagination=pagination, search=search)


@manager_required
def edit_category(id):
    """Edit an existing category."""
    category_obj = db.session.get(Category, id) or abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required!", "danger")
        elif Category.query.filter(Category.name == name, Category.id != id).first():
            flash("Category already exists!", "warning")
        else:
            category_obj.name = name
            db.session.commit()
            flash("Category updated successfully!", "success")
            return redirect(url_for("category"))
    return render_template("edit_category.html", category=category_obj)


@admin_required
def delete_category(id):
    """Delete an existing category."""
    category_obj = db.session.get(Category, id) or abort(404)
    if category_obj.items:
        flash("Cannot delete category with associated items!", "danger")
    else:
        db.session.delete(category_obj)
        db.session.commit()
        flash("Category deleted successfully!", "success")
    return redirect(url_for("category"))


@verified_required
def export_item_ledger(id):
    """Export item stock ledger as CSV."""
    # Import these locally to avoid circular imports (they're defined in app.py with discount/tax logic)
    from app import purchase_item_total, sale_item_total

    item = db.session.get(Item, id) or abort(404)
    # A reversed document never happened, so it must not still count here.
    purchase_items   = (PurchaseItem.query.join(Purchase)
                        .filter(PurchaseItem.item_id == id, Purchase.is_reversed.is_(False)).all())
    sale_items       = (SaleItem.query.join(Sale)
                        .filter(SaleItem.item_id == id, Sale.is_reversed.is_(False)).all())
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id, is_reversed=False).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id, is_reversed=False).all()

    # Stock In/Out are in the item's base unit, exactly like the on-screen ledger
    # (item_ledger() above) — whatever unit a line was actually transacted in.
    rows = []
    for pi in purchase_items:
        rows.append((pi.purchase_header.date, "Purchase", f"PO #{pi.purchase_header.id}", pi.purchase_header.supplier.name, line_base_qty(pi), 0, float(pi.purchase_price) / (pi.unit_factor or 1), purchase_item_total(pi)))
    for si in sale_items:
        rows.append((si.sale_header.date, "Sale", f"SO #{si.sale_header.id}", si.sale_header.customer.name, 0, line_base_qty(si), float(si.sale_price) / (si.unit_factor or 1), sale_item_total(si)))
    for pr in purchase_returns:
        rows.append((pr.date, "Purchase Return", f"PR #{pr.id}", pr.supplier.name, 0, line_base_qty(pr), float(pr.return_price) / (pr.unit_factor or 1), round(pr.quantity * pr.return_price, 2)))
    for sr in sale_returns:
        rows.append((sr.date, "Sale Return", f"SR #{sr.id}", sr.customer.name, line_base_qty(sr), 0, float(sr.return_price) / (sr.unit_factor or 1), round(sr.quantity * sr.return_price, 2)))

    rows.sort(key=lambda x: x[0])
    balance = 0
    csv_rows = []
    for date, typ, ref, party, sin, sout, rate, value in rows:
        balance += sin - sout
        csv_rows.append([date.strftime("%Y-%m-%d"), typ, ref, party, sin, sout, round(rate, 2), round(value, 2), balance])
    return csv_response(
        f"{item.name}_ledger.csv", "Item Stock Ledger",
        ["Date", "Type", "Reference", "Party", "Stock In", "Stock Out", "Rate", "Value", "Balance"],
        csv_rows, extra_info=f"Item: {item.name}",
    )


@verified_required
def export_item_ledger_excel(id):
    """Export item stock ledger as Excel."""
    # Import these locally to avoid circular imports (they're defined in app.py with discount/tax logic)
    from app import purchase_item_total, sale_item_total

    item = db.session.get(Item, id) or abort(404)
    # A reversed document never happened, so it must not still count here.
    purchase_items   = (PurchaseItem.query.join(Purchase)
                        .filter(PurchaseItem.item_id == id, Purchase.is_reversed.is_(False)).all())
    sale_items       = (SaleItem.query.join(Sale)
                        .filter(SaleItem.item_id == id, Sale.is_reversed.is_(False)).all())
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id, is_reversed=False).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id, is_reversed=False).all()

    # Stock In/Out are in the item's base unit, exactly like the on-screen ledger
    # (item_ledger() above) — whatever unit a line was actually transacted in.
    raw = []
    for pi in purchase_items:
        raw.append((pi.purchase_header.date, "Purchase", f"PO #{pi.purchase_header.id}", pi.purchase_header.supplier.name, line_base_qty(pi), 0, float(pi.purchase_price) / (pi.unit_factor or 1), purchase_item_total(pi)))
    for si in sale_items:
        raw.append((si.sale_header.date, "Sale", f"SO #{si.sale_header.id}", si.sale_header.customer.name, 0, line_base_qty(si), float(si.sale_price) / (si.unit_factor or 1), sale_item_total(si)))
    for pr in purchase_returns:
        raw.append((pr.date, "Purchase Return", f"PR #{pr.id}", pr.supplier.name, 0, line_base_qty(pr), float(pr.return_price) / (pr.unit_factor or 1), round(pr.quantity * pr.return_price, 2)))
    for sr in sale_returns:
        raw.append((sr.date, "Sale Return", f"SR #{sr.id}", sr.customer.name, line_base_qty(sr), 0, float(sr.return_price) / (sr.unit_factor or 1), round(sr.quantity * sr.return_price, 2)))

    raw.sort(key=lambda x: x[0])
    balance = item.opening_stock
    excel_rows = [["Opening", "Opening Stock", "", "", item.opening_stock, 0, 0, 0, balance]]
    for date, typ, ref, party, sin, sout, rate, value in raw:
        balance += sin - sout
        excel_rows.append([date.strftime("%Y-%m-%d"), typ, ref, party, sin, sout, round(rate, 2), round(value, 2), balance])

    return excel_response(
        filename=f"{item.name}_ledger.xlsx",
        title="Item Stock Ledger",
        col_headers=["Date", "Type", "Reference", "Party", "Stock In", "Stock Out", "Rate", "Value", "Balance"],
        rows=excel_rows,
        extra_info=f"Item: {item.name} | Unit: {item.unit or 'Pcs'}",
    )


# ─── BULK IMPORT ROUTES ────────────────────────────────────────────────────────


def import_items(data):
    """Bulk import items from parsed data. Mirrors the validation and GL/valuation
    behavior of the manual /item route so imported items are not silently missing
    their cost basis or accounting entry."""
    from salpurflask.services.config_service import ConfigurationService

    success, failed, errors = 0, 0, []

    for idx, row in enumerate(data, 1):
        try:
            name = row.get('name', '').strip()
            if not name:
                errors.append(f"Row {idx}: Missing item name")
                failed += 1
                continue

            category_name = row.get('category', '').strip()
            if not category_name:
                errors.append(f"Row {idx}: Missing category")
                failed += 1
                continue
            enabled_categories = ConfigurationService.get_enabled_categories()
            category = next((c for c in enabled_categories if c.name.lower() == category_name.lower()), None)
            if not category:
                errors.append(f"Row {idx}: Category '{category_name}' is not enabled in Business Configuration")
                failed += 1
                continue

            unit = (row.get('unit', 'Pcs') or 'Pcs').strip()
            if unit not in ITEM_UNITS:
                unit = "Pcs"

            purchase_price_raw = row.get('purchase_price', '')
            sale_price_raw = row.get('sale_price', '')
            purchase_price = float(purchase_price_raw) if purchase_price_raw else None
            sale_price = float(sale_price_raw) if sale_price_raw else None
            if purchase_price is not None and purchase_price < 0:
                errors.append(f"Row {idx}: Purchase price cannot be negative")
                failed += 1
                continue
            if sale_price is not None and sale_price < 0:
                errors.append(f"Row {idx}: Sale price cannot be negative")
                failed += 1
                continue

            barcode = row.get('barcode', '').strip() or None
            if barcode_taken(barcode):
                errors.append(f"Row {idx}: Barcode '{barcode}' is already used by another item")
                failed += 1
                continue

            # `stock` is the item's actual on-hand quantity at import time; since the
            # system has no purchase history for it yet, that quantity IS the opening
            # balance — same as the manual /item form, where opening_stock and stock
            # are always the same number.
            qty = int(row.get('stock', 0) or row.get('opening_stock', 0) or 0)

            if qty > 0 and purchase_price <= 0:
                errors.append(f"Row {idx}: Opening stock {qty} requires purchase_price > 0")
                failed += 1
                continue

            item = Item(
                name=name,
                category_id=None,
                business_category_id=category.id,
                unit=unit,
                opening_stock=qty,
                stock=qty,
                reorder_level=int(row.get('reorder_level', 0)) if row.get('reorder_level') else 0,
                purchase_price=purchase_price,
                sale_price=sale_price,
                barcode=barcode,
            )

            db.session.add(item)
            item.inventory_value = (Decimal(str(item.opening_stock or 0))
                                    * Decimal(str(item.purchase_price or 0))).quantize(MONEY)
            db.session.flush()

            if item.item_type == "STOCK":
                from salpurflask.models.inventory_location import (
                    get_or_create_default_location, ItemStock, record_stock_movement)
                default_location = get_or_create_default_location()
                db.session.add(ItemStock(item_id=item.id, location_id=default_location.id,
                                         quantity=qty))
                if qty > 0:
                    record_stock_movement(item.id, default_location.id, "in", qty,
                                          "opening", source_type="item", source_id=item.id,
                                          created_by_id=getattr(current_user, "id", None))

            # Post opening balance to GL if opening stock > 0
            if item.opening_stock > 0:
                post_item_opening(item)

            db.session.commit()
            success += 1

        except Exception as e:
            db.session.rollback()
            failed += 1
            errors.append(f"Row {idx}: {str(e)}")

    return success, failed, errors


def import_customers(data):
    """Bulk import customers from parsed data."""
    from app import sync_customer_opening

    success, failed, errors = 0, 0, []

    for idx, row in enumerate(data, 1):
        try:
            name = row.get('name', '').strip()
            if not name:
                errors.append(f"Row {idx}: Missing customer name")
                failed += 1
                continue

            customer = Customer(
                name=name,
                contact=row.get('contact', row.get('phone', '')).strip() or '',
                address=row.get('address', '').strip() or '',
                opening_balance=float(row.get('opening_balance', 0)) if row.get('opening_balance') else 0
            )

            db.session.add(customer)
            db.session.flush()
            sync_customer_opening(customer)
            if customer.opening_balance != 0:
                post_customer_opening(customer)
            db.session.commit()
            success += 1

        except Exception as e:
            db.session.rollback()
            failed += 1
            errors.append(f"Row {idx}: {str(e)}")

    return success, failed, errors


def import_suppliers(data):
    """Bulk import suppliers from parsed data."""
    from app import sync_supplier_opening

    success, failed, errors = 0, 0, []

    for idx, row in enumerate(data, 1):
        try:
            name = row.get('name', '').strip()
            if not name:
                errors.append(f"Row {idx}: Missing supplier name")
                failed += 1
                continue

            supplier = Supplier(
                name=name,
                contact=row.get('contact', row.get('phone', '')).strip() or '',
                address=row.get('address', '').strip() or '',
                opening_balance=float(row.get('opening_balance', 0)) if row.get('opening_balance') else 0
            )

            db.session.add(supplier)
            db.session.flush()
            sync_supplier_opening(supplier)
            if supplier.opening_balance != 0:
                post_supplier_opening(supplier)
            db.session.commit()
            success += 1

        except Exception as e:
            db.session.rollback()
            failed += 1
            errors.append(f"Row {idx}: {str(e)}")

    return success, failed, errors


@verified_required
def bulk_import():
    """Show bulk import form."""
    import_history = ImportLog.query.filter_by(user_id=current_user.id).order_by(ImportLog.created_at.desc()).limit(10).all()
    return render_template("bulk_import.html", import_history=import_history)


@verified_required
def process_import():
    """Process bulk import file."""
    import_type = request.form.get("import_type", "").lower()
    file = request.files.get("file")

    if not import_type or import_type not in ("items", "customers", "suppliers"):
        flash("Invalid import type selected", "danger")
        return redirect(url_for("bulk_import"))

    if not file:
        flash("No file selected", "danger")
        return redirect(url_for("bulk_import"))

    data, file_type = parse_import_file(file)
    if data is None:
        flash(file_type, "danger")
        return redirect(url_for("bulk_import"))

    if not data:
        flash("File is empty", "danger")
        return redirect(url_for("bulk_import"))

    # Create import log entry
    import_log = ImportLog(
        user_id=current_user.id,
        import_type=import_type,
        file_name=file.filename,
        file_type=file_type,
        total_records=len(data),
        status="processing"
    )
    db.session.add(import_log)
    db.session.commit()

    # Process based on type
    try:
        if import_type == "items":
            success, failed, errors = import_items(data)
        elif import_type == "customers":
            success, failed, errors = import_customers(data)
        elif import_type == "suppliers":
            success, failed, errors = import_suppliers(data)

        import_log.successful = success
        import_log.failed = failed
        import_log.status = "completed"
        if errors:
            import_log.errors = json.dumps(errors[:100])  # Store first 100 errors
        db.session.commit()

        message = f"{success} records imported successfully"
        if failed > 0:
            message += f", {failed} failed"
        flash(message, "success" if failed == 0 else "warning")

    except Exception as e:
        import_log.status = "failed"
        import_log.errors = json.dumps([str(e)])
        db.session.commit()
        flash(f"Import failed: {str(e)}", "danger")

    return redirect(url_for("bulk_import"))


# ─── STOCK ADJUSTMENT ROUTES ──────────────────────────────────────────────────────


@manager_required
def stock_adjustment():
    """List and create stock adjustments.

    Location-scoped as of Phase 5: the warehouse dropdown lists only
    accessible locations, the list itself shows only accessible locations'
    adjustments, and a submitted location_id the user isn't authorized for
    is refused before anything is validated or moved."""
    from salpurflask.models import (Location, resolve_location_id, stock_at_location,
                                    get_or_create_default_location)
    from salpurflask.services.location_permissions import (
        accessible_location_ids, require_location_access)

    accessible_ids = accessible_location_ids()

    search = request.args.get("search", "").strip()
    query = StockAdjustment.query.join(Item)
    if search:
        query = query.filter(Item.name.ilike(f"%{search}%"))
    if accessible_ids is not None:
        query = query.filter(StockAdjustment.location_id.in_(accessible_ids))
    adjustments, pagination = get_paginated_results(
        query.order_by(StockAdjustment.date.desc(), StockAdjustment.id.desc())
    )
    items = Item.query.order_by(Item.name).all()
    locations_query = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations_query = locations_query.filter(Location.id.in_(accessible_ids))
    locations = locations_query.order_by(Location.name).all()
    if request.method == "POST":
        item_id  = request.form.get("item_id", "").strip()
        adj_type = request.form.get("adj_type", "").strip()
        qty_str  = request.form.get("quantity", "").strip()
        reason   = request.form.get("reason", "").strip()
        date_str = request.form.get("date", "").strip()
        # Batch/Lot tracking — Phase E. Read regardless of whether the item
        # is batch-tracked; which of these actually gets used is decided
        # per-submission below, server-side — never trusted from a hidden/
        # disabled UI field alone (same rule Purchase/Sale/Transfer already
        # follow). batch_id: an existing batch selected for OUT, or an
        # existing batch to top up for IN. batch_no/expiry_date: a NEW
        # batch's identity for IN when no existing batch was selected —
        # blank batch_no still routes through get_or_create_batch() into
        # the item's single Unknown Batch, same as Purchase's own Draft
        # fields do.
        batch_id_raw    = request.form.get("batch_id", "").strip()
        new_batch_no    = request.form.get("batch_no", "").strip()
        new_expiry_str  = request.form.get("expiry_date", "").strip()
        try:
            location_id = resolve_location_id(request.form.get("location_id"))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("stock_adjustment"))
        require_location_access(location_id)
        if not item_id or not adj_type or not qty_str or not date_str:
            flash("Item, type, quantity and date are required.", "danger")
        elif not qty_str.isdigit() or int(qty_str) <= 0:
            flash("Quantity must be a positive integer.", "danger")
        elif not (item_obj := get_item_locked(int(item_id))):
            flash("Item not found.", "danger")
        elif adj_type not in ADJUSTMENT_DIRECTIONS:
            # Never guess. An unrecognised type used to fall through to "in" and add
            # stock that nobody asked for.
            flash("Unknown adjustment type.", "danger")
        elif item_obj.batch_tracked and ADJUSTMENT_DIRECTIONS[adj_type] == "out" and not batch_id_raw:
            # Mandatory manual selection for OUT — a damaged/lost/miscounted
            # unit is a specific physical batch, never "whichever expires
            # soonest" (FEFO does not apply to a write-off the way it does
            # to a sale), so there is deliberately no automatic fallback here.
            flash("Select which batch this adjustment applies to.", "danger")
        else:
            qty = int(qty_str)
            direction = ADJUSTMENT_DIRECTIONS[adj_type]
            available = stock_at_location(item_obj.id, location_id)
            if direction == "out" and available < qty:
                flash(f"Insufficient stock at this warehouse. Available: {available}", "danger")
            else:
                new_expiry_date = None
                if item_obj.batch_tracked and direction == "in" and not batch_id_raw and new_expiry_str:
                    try:
                        new_expiry_date = datetime.strptime(new_expiry_str, "%Y-%m-%d").date()
                    except ValueError:
                        flash(f"Invalid expiry date {new_expiry_str!r}. Use YYYY-MM-DD.", "danger")
                        return render_template("stock_adjustment.html",
                            adjustments=adjustments, items=items, pagination=pagination,
                            search=search, adj_types=ADJUSTMENT_TYPES,
                            today=now_local().strftime("%Y-%m-%d"),
                            locations=locations,
                            default_location=(locations[0] if (accessible_ids is not None and locations)
                                              else get_or_create_default_location()))
                try:
                    batch = None
                    if item_obj.batch_tracked:
                        if batch_id_raw:
                            batch = db.session.get(Batch, int(batch_id_raw)) if batch_id_raw.isdigit() else None
                            if batch is None or batch.item_id != item_obj.id:
                                flash("Selected batch does not exist or does not belong to this item.", "danger")
                                return redirect(url_for("stock_adjustment"))
                        else:
                            # direction == "in" with no batch selected: create
                            # or top up a named batch, or route into the
                            # Unknown Batch if no batch number was given —
                            # unit_cost comes from item.avg_cost, the exact
                            # figure this route already computes for every
                            # non-batch "in" adjustment today (see the
                            # comment on that original computation below) —
                            # not a new, invented pricing input.
                            batch = get_or_create_batch(
                                item_obj.id, new_batch_no or None, new_expiry_date,
                                item_obj.avg_cost, source_type="stock_adjustment",
                                created_by_id=current_user.id)

                    adj = StockAdjustment(
                        item_id=int(item_id), adj_type=adj_type, quantity=qty,
                        direction=direction,
                        date=datetime.strptime(date_str, "%Y-%m-%d"),
                        reason=reason or None,
                        location_id=location_id,
                        batch_id=batch.id if batch else None,
                    )
                    db.session.add(adj)
                    db.session.flush()
                    # Both directions are valued at the average: stock found is worth
                    # what the rest of the stock is worth, stock lost costs the same.
                    # For a batch-tracked adjustment, the SPECIFIC batch's own
                    # unit_cost is used instead — a write-off costs exactly
                    # what that lot was received at, and found stock joining
                    # an existing/new batch is valued at that batch's own cost.
                    unit = batch.unit_cost if batch else item_obj.avg_cost
                    if direction == "out":
                        if batch:
                            adj.cost_value = item_remove_stock_batched(
                                item_obj, qty, location_id=location_id, batch=batch,
                                cost_total=Decimal(str(batch.unit_cost)) * Decimal(str(qty)),
                                movement_type="adjustment",
                                source_type="stock_adjustment", source_id=adj.id)
                        else:
                            adj.cost_value = item_remove_stock(item_obj, qty, location_id=location_id,
                                                               movement_type="adjustment",
                                                               source_type="stock_adjustment", source_id=adj.id)
                    else:
                        adj.cost_value = (unit * Decimal(str(qty))).quantize(MONEY)
                        if batch:
                            item_add_stock_batched(
                                item_obj, qty, adj.cost_value, location_id=location_id,
                                batch=batch, movement_type="adjustment",
                                source_type="stock_adjustment", source_id=adj.id)
                        else:
                            item_add_stock(item_obj, qty, adj.cost_value, location_id=location_id,
                                          movement_type="adjustment",
                                          source_type="stock_adjustment", source_id=adj.id)
                except PostingError as e:
                    db.session.rollback()
                    flash(str(e), "danger")
                    return redirect(url_for("stock_adjustment"))
                db.session.flush()
                post_document("stock_adjustment", adj)
                db.session.commit()
                flash(f"Stock {'reduced' if direction=='out' else 'increased'} by {qty} for {item_obj.name}.", "success")
                return redirect(url_for("stock_adjustment"))
    # See sales/routes.py's identical fix: the form's hidden single-warehouse
    # fallback must resolve to a location this user can actually submit.
    template_default_location = locations[0] if (accessible_ids is not None and locations) \
        else get_or_create_default_location()
    return render_template("stock_adjustment.html",
        adjustments=adjustments, items=items, pagination=pagination,
        search=search, adj_types=ADJUSTMENT_TYPES,
        today=now_local().strftime("%Y-%m-%d"),
        locations=locations, default_location=template_default_location)


@admin_required
def delete_stock_adjustment(id):
    """Delete a stock adjustment and reverse its effect.

    Reachable today only for a zero-cost adjustment (post_stock_adjustment()
    posts no JournalEntry when cost_value == 0, so assert_not_posted's
    "a JournalEntry exists" check passes) — see the Phase E audit for why
    this route is not fully dead code the way its Purchase/Sale equivalents
    are. A batch-tracked adjustment can still be zero-cost (e.g. a batch
    genuinely received/costed at 0), so this must stay batch-aware: if
    adj.batch_id is set, reverse through the *_batched() wrappers so
    BatchStock moves with ItemStock, exactly as the create route already
    does — never leave the two out of sync for a batch this route can
    still reach."""
    adj = db.session.get(StockAdjustment, id) or abort(404)
    assert_not_posted("stock_adjustment", adj.id, f"Stock adjustment #{adj.id}")
    item_obj = get_item_locked(adj.item_id)
    if item_obj:
        batch = db.session.get(Batch, adj.batch_id) if adj.batch_id else None
        if adj.direction == "out":
            if batch:
                item_add_stock_batched(item_obj, adj.quantity, cost_total=adj.cost_value or 0,
                                       location_id=adj.location_id, batch=batch,
                                       movement_type="adjustment", source_type="stock_adjustment", source_id=adj.id)
            else:
                item_add_stock(item_obj, adj.quantity, cost_total=adj.cost_value or 0,
                               location_id=adj.location_id,
                               movement_type="adjustment", source_type="stock_adjustment", source_id=adj.id)
        else:
            if batch:
                item_remove_stock_batched(item_obj, adj.quantity, location_id=adj.location_id,
                                          batch=batch, cost_total=adj.cost_value or 0,
                                          movement_type="adjustment", source_type="stock_adjustment", source_id=adj.id)
            else:
                item_remove_stock(item_obj, adj.quantity, cost_total=adj.cost_value or 0,
                                  location_id=adj.location_id,
                                  movement_type="adjustment", source_type="stock_adjustment", source_id=adj.id)
    db.session.delete(adj)
    db.session.commit()
    flash("Adjustment deleted and stock reversed.", "success")
    return redirect(url_for("stock_adjustment"))


@manager_required
def stock_adjustment_item_batches(item_id):
    """Available batches for one item at one warehouse — Phase E, the Stock
    Adjustment OUT picker. Read-only, never mutates anything. Mirrors
    transfer_item_batches()'s own shape exactly (same endpoint contract,
    same NULLS-LAST expiry ordering) — Stock Adjustment gets its own route
    rather than reusing that one because it belongs to a different feature
    area with its own URL/permission surface, even though the permission
    (manager_required) happens to be the same."""
    from salpurflask.models import resolve_location_id, get_or_create_default_location
    from salpurflask.models.inventory_location import BatchStock
    from salpurflask.services.location_permissions import require_location_access

    item = db.session.get(Item, item_id) or abort(404)
    try:
        location_id = resolve_location_id(request.args.get("location_id")) \
            if request.args.get("location_id") else get_or_create_default_location().id
    except ValueError as e:
        return {"ok": False, "error": str(e)}, 400
    require_location_access(location_id)

    rows = (db.session.query(Batch, BatchStock.quantity)
            .join(BatchStock, BatchStock.batch_id == Batch.id)
            .filter(Batch.item_id == item_id,
                    BatchStock.location_id == location_id,
                    BatchStock.quantity > 0)
            .order_by(Batch.expiry_date.is_(None), Batch.expiry_date.asc(), Batch.id.asc())
            .all())
    return {"ok": True, "batches": [{
        "batch_id": b.id, "batch_no": b.batch_no, "quantity": qty,
        "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
    } for b, qty in rows]}


@admin_required
def enable_item_batch_tracking(id):
    """Turn on batch tracking for an item that already has stock — Phase G.
    Admin-only (not manager_required, unlike most Item configuration
    routes): this migrates every location's stock into BatchStock in one
    pass, with no location filter, so a manager restricted to a subset of
    warehouses must not be able to trigger it — see enable_batch_tracking()
    (models.py) for the full migration itself. This route is only the
    request/response wrapper: permission check, calling that function,
    and owning the commit/rollback boundary."""
    item = db.session.get(Item, id) or abort(404)
    if item.batch_tracked:
        flash(f"Batch tracking is already enabled for '{item.name}'.", "info")
        return redirect(url_for("edit_item", id=item.id))
    try:
        enable_batch_tracking(item.id, created_by_id=current_user.id)
        db.session.commit()
        flash(f"Batch tracking enabled for '{item.name}'. Existing stock is now "
              f"tracked under its Unknown Batch.", "success")
    except PostingError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("edit_item", id=item.id))


# ─── LABEL ROUTES ──────────────────────────────────────────────────────────────


@manager_required
def labels():
    """Printable barcode / QR labels to stick on stock.

    Pick how many of each item, barcode or QR, and print a sheet. Only items that have a
    code get a label; items without one are listed with a one-click way to assign codes.
    """
    from app import code_svg

    items = Item.query.outerjoin(Category, Item.category_id == Category.id) \
        .order_by(Category.name, Item.name).all()
    kind = "qr" if request.args.get("type") == "qr" else "barcode"
    show_price = request.args.get("price", "1") != "0"

    sheet = []
    for it in items:
        try:
            copies = int(request.args.get(f"copies_{it.id}", "0") or 0)
        except ValueError:
            copies = 0
        if copies > 0 and it.barcode:
            svg = code_svg(it.barcode, kind)
            for _ in range(min(copies, 200)):        # a sane cap per print run
                sheet.append({"name": it.name, "price": it.sale_price,
                              "code": it.barcode, "svg": svg})

    missing = [it for it in items if not it.barcode]
    return render_template("labels.html", items=items, sheet=sheet, kind=kind,
                           show_price=show_price, missing=missing)


@manager_required
def labels_assign():
    """Give every item that has no code a stable numeric one, so it can be labelled and
    scanned. Numeric (not the name) because the cheapest scanners read digits most
    reliably, and the id makes it unique and repeatable.
    """
    n = 0
    for it in Item.query.filter(or_(Item.barcode.is_(None), Item.barcode == "")).all():
        it.barcode = f"{it.id:012d}"
        n += 1
    if n:
        db.session.commit()
    flash(f"Assigned a code to {n} item(s) that had none.", "success")
    return redirect(url_for("labels"))


# ─── LOW STOCK ALERT ROUTE ────────────────────────────────────────────────────────


@manager_required
def send_low_stock_alert():
    """Send email alert for items below reorder level."""
    from app import send_email, app as flask_app

    low_items = Item.query.filter(Item.stock <= Item.reorder_level).order_by(Item.stock).all()
    if not low_items:
        flash("No items are below reorder level — no alert sent.", "info")
        return redirect(url_for("item"))
    lines = [f"LOW STOCK ALERT — {flask_app.config['COMPANY_NAME']}\n"]
    lines.append(f"Generated: {now_local().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"{'Item':<30} {'Stock':>8} {'Reorder':>8}")
    lines.append("-" * 50)
    for it in low_items:
        lines.append(f"{it.name:<30} {it.stock:>8} {it.reorder_level:>8}")
    body = "\n".join(lines)
    mail_user = flask_app.config.get("MAIL_USERNAME", "").strip()
    if not mail_user:
        flash("Email not configured — cannot send alert.", "danger")
        return redirect(url_for("item"))
    ok = send_email(mail_user, f"Low Stock Alert — {len(low_items)} items", body)
    if ok:
        flash(f"Low stock alert sent for {len(low_items)} item(s).", "success")
    return redirect(url_for("item"))


@verified_required
def get_product_category_data(product_id, category_id):
    """Get existing category-specific data for a product (AJAX endpoint)."""
    from salpurflask.models.business_config import ProductCategoryData
    from flask import jsonify

    data = ProductCategoryData.query.filter_by(
        product_id=product_id,
        category_id=category_id
    ).all()

    result = {}
    for entry in data:
        result[entry.field_name] = entry.field_value

    return jsonify(result)


@verified_required
def api_item_lookup():
    """Server-side item lookup for the universal picker (Sale/Purchase/Quotation
    item selection, and any future screen) — never the whole table, always
    paginated (see salpurflask/services/lookup_service). Category-specific
    filter values come in as filter_<field_name> query params, matched against
    that category's is_filterable ProductFields."""
    q = request.args.get("q", "")
    category_id = request.args.get("category_id", type=int)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    filters = {k[len("filter_"):]: v for k, v in request.args.items() if k.startswith("filter_")}

    rows, total, page, per_page = search_items(
        q=q, category_id=category_id, filters=filters, page=page, per_page=per_page)

    return {
        "results": [{
            "id": it.id, "name": it.name, "sku": it.sku or "", "barcode": it.barcode or "",
            "category": it.business_category.name if it.business_category else None,
            "unit": it.unit or "Pcs", "stock": it.stock,
            "sale_price": float(it.sale_price or 0),
        } for it in rows],
        "total": total, "page": page, "per_page": per_page,
    }


@verified_required
def api_item_filter_fields(category_id):
    """The filterable fields for one category, for the lookup UI to render as
    extra filter controls — data-driven off ProductField.is_filterable, so an
    admin-added field shows up here with no extra code."""
    fields = get_item_filter_fields(category_id)
    return {"fields": [f.to_dict() for f in fields]}


@verified_required
def api_item_units(id):
    """This one item's unit choices (base unit + alternates) — fetched only
    once an item is actually picked in the Universal Item Lookup, not
    precomputed for every item in the catalogue up front (that was the same
    scalability problem as embedding the whole item list: item_units_for_js()
    built this same shape for every item on the page, unconditionally)."""
    from salpurflask.models.models import item_unit_choices
    item = db.session.get(Item, id) or abort(404)
    return {"units": [{"key": c["key"], "name": c["name"], "factor": c["factor"],
                       "purchase_price": float(c["purchase_price"]) if c["purchase_price"] is not None else None,
                       "sale_price": float(c["sale_price"]) if c["sale_price"] is not None else None}
                      for c in item_unit_choices(item)],
            # Batch/Lot tracking (Phase B): lets the Purchase row show/hide its
            # Batch No / Expiry Date fields for the item actually picked --
            # a client-side convenience only; the server re-checks this same
            # flag independently before saving (see purchase()'s own gating).
            "batch_tracked": bool(item.batch_tracked)}
