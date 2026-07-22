"""Inventory routes (CRUD and read-only)."""

from datetime import datetime
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import and_

from salpurflask.extensions import db
from salpurflask.models import (
    Item, Category, PurchaseItem, Purchase, SaleItem, Sale,
    PurchaseReturn, SaleReturn, StockAdjustment,
    ITEM_UNITS, MONEY, save_item_units, post_item_opening,
    item_add_stock, item_remove_stock, _repost_opening, _opening_date,
)
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import now_local, barcode_taken, get_paginated_results, line_base_qty, csv_response, excel_response


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
            "ref": f"PO #{pi.purchase_header.id}", "party": pi.purchase_header.id_supplier.name,
            "stock_in": line_base_qty(pi), "stock_out": 0,
            "rate": pi.purchase_price / factor, "value": _purchase_line_value(pi),
        })
    for si in sale_items:
        factor = si.unit_factor or 1
        entries.append({
            "date": si.sale_header.date, "type": "Sale", "badge": "primary",
            "ref": f"SO #{si.sale_header.id}", "party": si.sale_header.id_customer.name,
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
    search = request.args.get("search", "")
    category_filter = request.args.get("category_id", "")
    query = Item.query.outerjoin(Category)
    if search:
        query = query.filter((Item.name.ilike(f"%{search}%")) | (Category.name.ilike(f"%{search}%")))
    if category_filter.isdigit():
        query = query.filter(Item.category_id == int(category_filter))
    items, pagination = get_paginated_results(query)
    categories = Category.query.order_by(Category.name).all()
    if request.method == "POST":
        if current_user.role not in ("admin", "manager"):
            flash("You do not have permission to add items.", "danger")
            return redirect(url_for("item"))
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id", "").strip()
        unit = request.form.get("unit", "Pcs").strip()
        opening_stock = request.form.get("opening_stock", "0").strip() or "0"
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        barcode = request.form.get("barcode", "").strip() or None
        if unit not in ITEM_UNITS:
            unit = "Pcs"
        if not categories:
            flash("Please add a category first before adding items!", "danger")
        elif not name or not reorder_level or not category_id:
            flash("Name, Category, and Reorder Level are required!", "danger")
        elif not category_id.isdigit() or not db.session.get(Category, int(category_id)):
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
                category_id=int(category_id),
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
            unit_error = save_item_units(item_obj)
            if unit_error:
                db.session.rollback()
                flash(unit_error, "danger")
                return render_template("item.html", items=items, categories=categories,
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
        pagination=pagination,
        search=search,
        category_filter=category_filter,
    )


@manager_required
def edit_item(id):
    """Edit an existing item."""
    item = db.session.get(Item, id) or abort(404)
    categories = Category.query.order_by(Category.name).all()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id", "").strip()
        unit = request.form.get("unit", "Pcs").strip()
        opening_stock = request.form.get("opening_stock", str(item.opening_stock)).strip()
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        barcode = request.form.get("barcode", "").strip() or None
        if unit not in ITEM_UNITS:
            unit = "Pcs"
        if not categories:
            flash("Please add a category first before editing items!", "danger")
        elif not name or not reorder_level or not category_id:
            flash("Name, Category, and Reorder Level are required!", "danger")
        elif not category_id.isdigit() or not db.session.get(Category, int(category_id)):
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
                return render_template("edit_item.html", item=item, categories=categories)
            # The opening entry is about to be reversed and re-posted, so the
            # inventory value must move by the same amount the GL does.
            old_opening_value = (Decimal(str(item.opening_stock or 0))
                                 * Decimal(str(item.purchase_price or 0))).quantize(MONEY)
            item.name = name
            item.category_id = int(category_id)
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
                return render_template("edit_item.html", item=item, categories=categories)
            db.session.flush()
            post_item_opening(item)
            db.session.commit()
            # Import record_audit locally to avoid circular imports
            from app import record_audit
            record_audit("update", "Item", item.id, f"Item '{item.name}' edited")
            flash("Item updated successfully!", "success")
            return redirect(url_for("item"))
    return render_template("edit_item.html", item=item, categories=categories)


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
        rows.append((pi.purchase_header.date, "Purchase", f"PO #{pi.purchase_header.id}", pi.purchase_header.id_supplier.name, line_base_qty(pi), 0, float(pi.purchase_price) / (pi.unit_factor or 1), purchase_item_total(pi)))
    for si in sale_items:
        rows.append((si.sale_header.date, "Sale", f"SO #{si.sale_header.id}", si.sale_header.id_customer.name, 0, line_base_qty(si), float(si.sale_price) / (si.unit_factor or 1), sale_item_total(si)))
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
        raw.append((pi.purchase_header.date, "Purchase", f"PO #{pi.purchase_header.id}", pi.purchase_header.id_supplier.name, line_base_qty(pi), 0, float(pi.purchase_price) / (pi.unit_factor or 1), purchase_item_total(pi)))
    for si in sale_items:
        raw.append((si.sale_header.date, "Sale", f"SO #{si.sale_header.id}", si.sale_header.id_customer.name, 0, line_base_qty(si), float(si.sale_price) / (si.unit_factor or 1), sale_item_total(si)))
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
