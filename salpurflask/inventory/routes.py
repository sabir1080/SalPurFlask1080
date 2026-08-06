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
)
from salpurflask.models.business_config import BusinessCategory
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import now_local, barcode_taken, get_paginated_results, line_base_qty, csv_response, excel_response, parse_import_file, get_item_locked


def _purchase_line_value(pi):
    """Calculate purchase line value for ledger display (simple, no discount/tax)."""
    return pi.quantity * pi.purchase_price * (pi.unit_factor or 1)


def _sale_line_value(si):
    """Calculate sale line value for ledger display (simple, no discount/tax)."""
    return si.quantity * si.sale_price * (si.unit_factor or 1)


@verified_required
def item_ledger(id):
    """Display item ledger with stock movements."""
    item = db.session.get(Item, id) or abort(404)
    start_date_str = request.args.get("start_date", "")
    end_date_str   = request.args.get("end_date", "")

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
        })
    for si in sale_items:
        factor = si.unit_factor or 1
        entries.append({
            "date": si.sale_header.date, "type": "Sale", "badge": "primary",
            "ref": f"SO #{si.sale_header.id}", "party": si.sale_header.customer.name,
            "stock_in": 0, "stock_out": line_base_qty(si),
            "rate": si.sale_price / factor, "value": _sale_line_value(si),
        })
    for pr in purchase_returns:
        factor = pr.unit_factor or 1
        entries.append({
            "date": pr.date, "type": "Purchase Return", "badge": "warning",
            "ref": f"PR #{pr.id}", "party": pr.supplier.name,
            "stock_in": 0, "stock_out": line_base_qty(pr),
            "rate": pr.return_price / factor, "value": round(pr.quantity * pr.return_price, 2),
        })
    for sr in sale_returns:
        factor = sr.unit_factor or 1
        entries.append({
            "date": sr.date, "type": "Sale Return", "badge": "secondary",
            "ref": f"SR #{sr.id}", "party": sr.customer.name,
            "stock_in": line_base_qty(sr), "stock_out": 0,
            "rate": sr.return_price / factor, "value": round(sr.quantity * sr.return_price, 2),
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
            "rate": 0, "value": 0,
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

    return render_template(
        "item_ledger.html",
        item=item,
        entries=entries,
        total_in=total_in,
        total_out=total_out,
        opening_stock=opening,
        current_stock=item.stock,
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
    """
    items = (Item.query.outerjoin(Category, Item.category_id == Category.id)
             .order_by(Category.name, Item.name).all())
    return render_template(
        "report_stock.html",
        stock_report=items,
        stock_value_total=sum(Decimal(str(i.inventory_value or 0)) for i in items),
        items_in_stock=sum(1 for i in items if i.stock and i.stock > 0),
        reorder_report=[i for i in items if i.stock <= i.reorder_level],
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
        query = query.filter((Item.name.ilike(f"%{search}%")) | (BusinessCategory.name.ilike(f"%{search}%")))
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
        unit = request.form.get("unit", "Pcs").strip()
        opening_stock = request.form.get("opening_stock", "0").strip() or "0"
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        barcode = request.form.get("barcode", "").strip() or None
        if unit not in ITEM_UNITS:
            unit = "Pcs"
        if not business_categories:
            flash("No business categories are enabled. Please enable categories in Admin Settings before adding items!", "danger")
        elif not name or not reorder_level or not business_category_id:
            flash("Name, Category, and Reorder Level are required!", "danger")
        elif not business_category_id.isdigit() or not db.session.get(BusinessCategory, int(business_category_id)):
            flash("Please select a valid category!", "danger")
        elif not opening_stock.lstrip("-").isdigit() or not reorder_level.isdigit():
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
        else:
            os_val = int(opening_stock)
            item_obj = Item(
                name=name,
                category_id=None,  # Using business_category_id instead
                business_category_id=int(business_category_id),
                unit=unit,
                opening_stock=os_val,
                stock=os_val,
                reorder_level=int(reorder_level),
                purchase_price=float(purchase_price) if purchase_price else None,
                sale_price=float(sale_price) if sale_price else None,
                barcode=barcode,
            )
            db.session.add(item_obj)
            item_obj.inventory_value = (Decimal(str(item_obj.opening_stock or 0))
                                        * Decimal(str(item_obj.purchase_price or 0))).quantize(MONEY)
            db.session.flush()

            # Save category-specific field data
            if business_category_id and business_category_id.isdigit():
                from salpurflask.services.config_service import ConfigurationService
                from salpurflask.models.business_config import ProductField

                category_field_data = {}
                # Get all field names for this category
                category_fields = ProductField.query.filter_by(category_id=int(business_category_id)).all()
                field_names = {f.field_name for f in category_fields}

                # Extract category-specific fields from form
                for key, value in request.form.items():
                    if key in field_names and value:
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
                                       category_filter=category_filter)
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
        unit = request.form.get("unit", "Pcs").strip()
        opening_stock = request.form.get("opening_stock", str(item.opening_stock)).strip()
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        barcode = request.form.get("barcode", "").strip() or None
        if unit not in ITEM_UNITS:
            unit = "Pcs"
        if not business_categories:
            flash("No business categories are enabled. Please enable categories in Admin Settings!", "danger")
        elif not name or not reorder_level or not business_category_id:
            flash("Name, Category, and Reorder Level are required!", "danger")
        elif not business_category_id.isdigit() or not db.session.get(BusinessCategory, int(business_category_id)):
            flash("Please select a valid category!", "danger")
        elif not opening_stock.lstrip("-").isdigit() or not reorder_level.isdigit():
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
            item.unit = unit
            item.opening_stock = new_os
            item.reorder_level = int(reorder_level)
            item.purchase_price = float(purchase_price) if purchase_price else None
            item.sale_price = float(sale_price) if sale_price else None
            item.barcode = barcode
            new_opening_value = (Decimal(str(new_os)) * Decimal(str(item.purchase_price or 0))).quantize(MONEY)
            value_adjustment = new_opening_value - old_opening_value
            # Update stock and inventory_value atomically
            if stock_adjustment > 0:
                item_add_stock(item, stock_adjustment, cost_total=value_adjustment)
            elif stock_adjustment < 0:
                item_remove_stock(item, -stock_adjustment, cost_total=-value_adjustment)
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

                # Extract category-specific fields from form
                for key, value in request.form.items():
                    if key in field_names and value:
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
    """List and create stock adjustments."""
    search = request.args.get("search", "").strip()
    query = StockAdjustment.query.join(Item)
    if search:
        query = query.filter(Item.name.ilike(f"%{search}%"))
    adjustments, pagination = get_paginated_results(
        query.order_by(StockAdjustment.date.desc(), StockAdjustment.id.desc())
    )
    items = Item.query.order_by(Item.name).all()
    if request.method == "POST":
        item_id  = request.form.get("item_id", "").strip()
        adj_type = request.form.get("adj_type", "").strip()
        qty_str  = request.form.get("quantity", "").strip()
        reason   = request.form.get("reason", "").strip()
        date_str = request.form.get("date", "").strip()
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
        else:
            qty = int(qty_str)
            direction = ADJUSTMENT_DIRECTIONS[adj_type]
            if direction == "out" and item_obj.stock < qty:
                flash(f"Insufficient stock. Available: {item_obj.stock}", "danger")
            else:
                adj = StockAdjustment(
                    item_id=int(item_id), adj_type=adj_type, quantity=qty,
                    direction=direction,
                    date=datetime.strptime(date_str, "%Y-%m-%d"),
                    reason=reason or None,
                )
                db.session.add(adj)
                # Both directions are valued at the average: stock found is worth
                # what the rest of the stock is worth, stock lost costs the same.
                unit = item_obj.avg_cost
                if direction == "out":
                    adj.cost_value = item_remove_stock(item_obj, qty)
                else:
                    adj.cost_value = (unit * Decimal(str(qty))).quantize(MONEY)
                    item_add_stock(item_obj, qty, adj.cost_value)
                db.session.flush()
                post_document("stock_adjustment", adj)
                db.session.commit()
                flash(f"Stock {'reduced' if direction=='out' else 'increased'} by {qty} for {item_obj.name}.", "success")
                return redirect(url_for("stock_adjustment"))
    return render_template("stock_adjustment.html",
        adjustments=adjustments, items=items, pagination=pagination,
        search=search, adj_types=ADJUSTMENT_TYPES,
        today=now_local().strftime("%Y-%m-%d"))


@admin_required
def delete_stock_adjustment(id):
    """Delete a stock adjustment and reverse its effect."""
    adj = db.session.get(StockAdjustment, id) or abort(404)
    assert_not_posted("stock_adjustment", adj.id, f"Stock adjustment #{adj.id}")
    item_obj = get_item_locked(adj.item_id)
    if item_obj:
        if adj.direction == "out":
            item_add_stock(item_obj, adj.quantity, cost_total=adj.cost_value or 0)
        else:
            item_remove_stock(item_obj, adj.quantity, cost_total=adj.cost_value or 0)
    db.session.delete(adj)
    db.session.commit()
    flash("Adjustment deleted and stock reversed.", "success")
    return redirect(url_for("stock_adjustment"))


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
