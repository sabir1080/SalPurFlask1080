"""Sales and Point of Sale routes."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import (
    Sale, SaleItem, SaleReturn, Customer, Item, CustomerPayment, FinancialAccount, PosHold,
    MONEY, resolve_item_unit, line_base_qty,
    calc_discount_tax, allocate_document_number, assert_not_posted, assert_not_numbered,
    post_document, reverse_document, Quotation,
)
from salpurflask.models.business_config import BusinessCategory
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import (
    now_local, get_paginated_results, csv_response, excel_response, get_item_locked
)
from sqlalchemy import or_ as sql_or
from sqlalchemy.orm import joinedload

sales_bp = Blueprint('sales', __name__)

# ─── HELPER FUNCTIONS (SALES-SPECIFIC) ─────────────────────────────────────


def sale_item_total(si):
    """Calculate total cost for a sale line item (with tax, after discount)."""
    return si.amount


def sale_total(sale):
    """Calculate total revenue for an entire sale."""
    return float(db.session.query(SaleItem)
                 .filter_by(sale_id=sale.id)
                 .with_entities(db.func.sum(SaleItem.amount))
                 .scalar() or 0.0)


# ─── SALE CRUD ROUTES ──────────────────────────────────────────────────────


@sales_bp.route('/sale', methods=['GET', 'POST'])
@verified_required
def sale():
    """Display sales and allow creation of new sales."""
    from app import validate_line_rows, sale_total as app_sale_total
    from app import record_audit, sync_customer_sale, item_add_stock, item_remove_stock
    from salpurflask.models import (Location, resolve_location_id, stock_at_location,
                                    get_or_create_default_location)
    from salpurflask.services.location_permissions import (
        accessible_location_ids, require_location_access)

    accessible_ids = accessible_location_ids()

    search = request.args.get("search", "").strip()
    query = Sale.query
    if search:
        query = query.join(Customer).filter(Customer.name.ilike(f"%{search}%"))
    sales, pagination = get_paginated_results(query.order_by(Sale.date.desc(), Sale.id.desc()))
    locations_query = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations_query = locations_query.filter(Location.id.in_(accessible_ids))
    locations = locations_query.order_by(Location.name).all()
    # The form's hidden single-warehouse fallback must resolve to a location this
    # user can actually submit — Phase 5's own accessible_ids, not the unscoped
    # company default, or a user restricted to one non-default warehouse would
    # silently hand-in a location_id they aren't authorized for.
    template_default_location = locations[0] if (accessible_ids is not None and locations) \
        else get_or_create_default_location()
    if request.method == "POST":
        if current_user.role not in ("admin", "manager"):
            flash("Access denied. Only managers and admins can add sales.", "danger")
            return redirect(url_for("sale"))
        try:
            location_id = resolve_location_id(request.form.get("location_id"))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("sale"))
        require_location_access(location_id)
        customer_id = request.form.get("customer_id", "").strip()
        date_str    = request.form.get("date", "").strip()
        notes       = request.form.get("notes", "").strip()
        item_ids    = request.form.getlist("item_id[]")
        quantities  = request.form.getlist("quantity[]")
        prices      = request.form.getlist("sale_price[]")
        disc_types  = request.form.getlist("discount_type[]")
        disc_values = request.form.getlist("discount_value[]")
        tax_pcts    = request.form.getlist("tax_percent[]")
        unit_ids    = request.form.getlist("unit_id[]")

        rows = []
        for i, (iid, qty, price) in enumerate(zip(item_ids, quantities, prices)):
            if iid.strip() and qty.strip() and price.strip():
                rows.append((
                    iid.strip(), qty.strip(), price.strip(),
                    disc_types[i] if i < len(disc_types) else "percent",
                    disc_values[i] if i < len(disc_values) else "0",
                    tax_pcts[i] if i < len(tax_pcts) else "0",
                    unit_ids[i] if i < len(unit_ids) else "",
                ))

        row_error = validate_line_rows(rows) if rows else None
        if not customer_id or not date_str:
            flash("Customer and date are required!", "danger")
        elif not rows:
            flash("At least one item is required!", "danger")
        elif row_error:
            flash(row_error, "danger")
        else:
            try:
                sale_date = datetime.strptime(date_str, "%Y-%m-%d")
                stock_errors = []
                for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                    item_obj = db.session.get(Item, int(iid))
                    if item_obj and item_obj.item_type == "STOCK":
                        _, factor = resolve_item_unit(item_obj, unit_key)
                        available = stock_at_location(item_obj.id, location_id)
                        if available < int(qty) * factor:
                            stock_errors.append(
                                f"{item_obj.name}: only {available} {item_obj.unit} "
                                f"available at this warehouse")
                if stock_errors:
                    flash("Insufficient stock — " + "; ".join(stock_errors), "danger")
                else:
                    first_iid, first_qty, first_price, first_d_type, first_d_val, first_tax = rows[0][0], rows[0][1], rows[0][2], rows[0][3], rows[0][4], rows[0][5]
                    first_d_val_f = float(first_d_val or 0)
                    first_tax_f = float(first_tax or 0)
                    first_gross = int(first_qty) * float(first_price)
                    first_disc_amt, first_tax_amt, _ = calc_discount_tax(first_gross, first_d_type or "percent", first_d_val_f, first_tax_f)
                    sal = Sale(
                        customer_id=int(customer_id),
                        item_id=int(first_iid),
                        quantity=int(first_qty),
                        sale_price=float(first_price),
                        cost_price=0.0,
                        discount_type=first_d_type or "percent", discount_value=first_d_val_f, discount_amount=first_disc_amt,
                        tax_percent=first_tax_f, tax_amount=first_tax_amt,
                        date=sale_date, notes=notes or None,
                        location_id=location_id,
                    )
                    db.session.add(sal)
                    db.session.flush()
                    for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                        item_obj = get_item_locked(int(iid)) or abort(404)
                        qty_i = int(qty); price_f = float(price)
                        unit_name, unit_factor = resolve_item_unit(item_obj, unit_key)
                        base_qty = qty_i * unit_factor
                        # Stock validation and reduction only for STOCK items, not SERVICE
                        if item_obj.item_type == "STOCK":
                            available = stock_at_location(item_obj.id, location_id)
                            if available < base_qty:
                                db.session.rollback()
                                flash(f"Insufficient stock for {item_obj.name}: only {available} "
                                      "available at this warehouse now (it changed while saving). "
                                      "Please try again.", "danger")
                                return redirect(url_for("sale"))
                        d_val_f = float(d_val or 0); tax_f = float(tax or 0)
                        gross = qty_i * price_f
                        disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                        unit_cost = item_obj.avg_cost if item_obj.item_type == "STOCK" else 0
                        si = SaleItem(
                            sale_id=sal.id, item_id=int(iid),
                            quantity=qty_i, sale_price=price_f,
                            cost_price=float(unit_cost),
                            discount_type=d_type or "percent", discount_value=d_val_f,
                            discount_amount=disc_amt, tax_percent=tax_f,
                            tax_amount=tax_amt, amount=net,
                            unit_name=unit_name, unit_factor=unit_factor,
                        )
                        db.session.add(si)
                        # Only reduce stock for STOCK items, not SERVICE items
                        if item_obj.item_type == "STOCK":
                            item_remove_stock(item_obj, base_qty, cost_total=unit_cost * Decimal(str(base_qty)),
                                              location_id=location_id,
                                              movement_type="sale", source_type="sale", source_id=sal.id)
                    db.session.flush()
                    db.session.refresh(sal)
                    sal.invoice_no = allocate_document_number("sale", sal.date)
                    sync_customer_sale(sal)
                    post_document("sale", sal)
                    db.session.commit()
                    record_audit("create", "Sale", sal.id,
                                 f"Sale {sal.invoice_no}, total {sale_total(sal):,.2f}")
                    flash(f"Sale {sal.invoice_no} recorded successfully!", "success")
                    return redirect(url_for("sale"))
            except ValueError as e:
                flash(f"Invalid data: {e}", "danger")
    from app import get_standard_tax_rate
    default_tax = get_standard_tax_rate() or 0
    return render_template(
        "sale.html",
        sales=sales,
        pagination=pagination,
        search=search,
        today=now_local().strftime("%Y-%m-%d"),
        default_tax_rate=default_tax,
        locations=locations,
        default_location=template_default_location,
    )


@sales_bp.route('/sale/<int:id>/edit', methods=['GET', 'POST'])
@manager_required
def edit_sale(id):
    """Edit an existing sale."""
    from app import validate_line_rows, record_audit
    from app import sync_customer_sale, item_add_stock, item_remove_stock
    from app import remove_customer_ledger_entry, recalculate_customer_ledger

    from salpurflask.models import resolve_location_id, stock_at_location

    sal = db.session.get(Sale, id) or abort(404)
    assert_not_posted("sale", sal.id, f"Sale #{sal.id}")
    # Editing never moves a sale's stock effect to a different warehouse — the
    # sale keeps the location it was created with. Stock returned below always
    # goes back to this same location, and the new lines are deducted from it
    # too, so the two never disagree about which warehouse this sale touches.
    # (Moving a completed sale's stock between warehouses is a transfer, not
    # an edit — out of scope here.)
    location_id = sal.location_id if sal.location_id is not None else resolve_location_id(None)
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "").strip()
        date_str    = request.form.get("date", "").strip()
        notes       = request.form.get("notes", "").strip()
        item_ids    = request.form.getlist("item_id[]")
        quantities  = request.form.getlist("quantity[]")
        prices      = request.form.getlist("sale_price[]")
        disc_types  = request.form.getlist("discount_type[]")
        disc_values = request.form.getlist("discount_value[]")
        tax_pcts    = request.form.getlist("tax_percent[]")
        unit_ids    = request.form.getlist("unit_id[]")

        rows = []
        for i, (iid, qty, price) in enumerate(zip(item_ids, quantities, prices)):
            if iid.strip() and qty.strip() and price.strip():
                rows.append((
                    iid.strip(), qty.strip(), price.strip(),
                    disc_types[i] if i < len(disc_types) else "percent",
                    disc_values[i] if i < len(disc_values) else "0",
                    tax_pcts[i] if i < len(tax_pcts) else "0",
                    unit_ids[i] if i < len(unit_ids) else "",
                ))

        row_error = validate_line_rows(rows) if rows else None
        if not customer_id or not date_str:
            flash("Customer and date are required!", "danger")
        elif not rows:
            flash("At least one item is required!", "danger")
        elif row_error:
            flash(row_error, "danger")
        else:
            try:
                old_customer_id = sal.customer_id
                for si in sal.line_items:
                    old_item = get_item_locked(si.item_id)
                    if old_item and old_item.item_type == "STOCK":
                        cost_returned = si.cost_price * line_base_qty(si)
                        item_add_stock(old_item, line_base_qty(si), cost_total=cost_returned,
                                      location_id=location_id,
                                      movement_type="sale", source_type="sale", source_id=sal.id)
                stock_errors = []
                for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                    item_obj = get_item_locked(int(iid))
                    if item_obj and item_obj.item_type == "STOCK":
                        _, factor = resolve_item_unit(item_obj, unit_key)
                        available = stock_at_location(item_obj.id, location_id)
                        if available < int(qty) * factor:
                            stock_errors.append(
                                f"{item_obj.name}: only {available} {item_obj.unit} "
                                f"available at this warehouse")
                if stock_errors:
                    for si in sal.line_items:
                        old_item = get_item_locked(si.item_id)
                        if old_item and old_item.item_type == "STOCK":
                            cost_removed = si.cost_price * line_base_qty(si)
                            item_remove_stock(old_item, line_base_qty(si), cost_total=cost_removed,
                                             location_id=location_id,
                                             movement_type="sale", source_type="sale", source_id=sal.id)
                    flash("Insufficient stock — " + "; ".join(stock_errors), "danger")
                else:
                    SaleItem.query.filter_by(sale_id=sal.id).delete()
                    first_iid, first_qty, first_price, first_d_type, first_d_val, first_tax = rows[0][0], rows[0][1], rows[0][2], rows[0][3], rows[0][4], rows[0][5]
                    first_d_val_f = float(first_d_val or 0)
                    first_tax_f = float(first_tax or 0)
                    first_gross = int(first_qty) * float(first_price)
                    first_disc_amt, first_tax_amt, _ = calc_discount_tax(first_gross, first_d_type or "percent", first_d_val_f, first_tax_f)
                    sal.customer_id = int(customer_id)
                    sal.item_id = int(first_iid); sal.quantity = int(first_qty)
                    sal.sale_price = float(first_price); sal.cost_price = 0.0
                    sal.discount_type = first_d_type or "percent"; sal.discount_value = first_d_val_f
                    sal.discount_amount = first_disc_amt; sal.tax_percent = first_tax_f; sal.tax_amount = first_tax_amt
                    sal.date = datetime.strptime(date_str, "%Y-%m-%d")
                    sal.notes = notes or None
                    sal.location_id = location_id
                    for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                        item_obj = get_item_locked(int(iid)) or abort(404)
                        qty_i = int(qty); price_f = float(price)
                        d_val_f = float(d_val or 0); tax_f = float(tax or 0)
                        gross = qty_i * price_f
                        disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                        unit_name, unit_factor = resolve_item_unit(item_obj, unit_key)
                        base_qty = qty_i * unit_factor
                        unit_cost = item_obj.avg_cost if item_obj.item_type == "STOCK" else 0
                        si = SaleItem(
                            sale_id=sal.id, item_id=int(iid),
                            quantity=qty_i, sale_price=price_f,
                            cost_price=float(unit_cost),
                            discount_type=d_type or "percent", discount_value=d_val_f,
                            discount_amount=disc_amt, tax_percent=tax_f,
                            tax_amount=tax_amt, amount=net,
                            unit_name=unit_name, unit_factor=unit_factor,
                        )
                        db.session.add(si)
                        # Only reduce stock for STOCK items, not SERVICE items
                        if item_obj.item_type == "STOCK":
                            item_remove_stock(item_obj, base_qty, cost_total=unit_cost * Decimal(str(base_qty)),
                                              location_id=location_id,
                                              movement_type="sale", source_type="sale", source_id=sal.id)
                    db.session.flush()
                    db.session.refresh(sal)
                    if old_customer_id != int(customer_id):
                        remove_customer_ledger_entry("sale", sal.id)
                        recalculate_customer_ledger(old_customer_id)
                    sync_customer_sale(sal)
                    post_document("sale", sal)
                    db.session.commit()
                    record_audit("update", "Sale", sal.id, f"Sale #{sal.id} edited")
                    flash("Sale updated successfully!", "success")
                    return redirect(url_for("sale"))
            except ValueError as e:
                flash(f"Invalid data: {e}", "danger")
    from app import get_standard_tax_rate
    default_tax = get_standard_tax_rate() or 0
    return render_template("edit_sale.html", sale=sal, default_tax_rate=default_tax)


@sales_bp.route('/sale/<int:id>/delete', methods=['POST'])
@admin_required
def delete_sale(id):
    """Delete a sale."""
    from app import record_audit, item_add_stock
    from app import remove_customer_ledger_entry, recalculate_customer_ledger

    sal = db.session.get(Sale, id) or abort(404)
    assert_not_posted("sale", sal.id, f"Sale #{sal.id}")
    assert_not_numbered(sal, "Sale")
    if sal.customer_payments:
        flash("Cannot delete sale with associated receipts! Delete receipts first.", "danger")
        return redirect(url_for("sale"))
    linked_quotation = Quotation.query.filter_by(converted_sale_id=sal.id).first()
    if linked_quotation:
        flash(f"Cannot delete sale — it was created from Quotation #{linked_quotation.id}.", "danger")
        return redirect(url_for("sale"))
    for si in sal.line_items:
        item_obj = db.session.get(Item, si.item_id)
        if item_obj and item_obj.item_type == "STOCK":
            cost_returned = si.cost_price * line_base_qty(si)
            item_add_stock(item_obj, line_base_qty(si), cost_total=cost_returned,
                           location_id=sal.location_id,
                           movement_type="sale", source_type="sale", source_id=sal.id)
    audit_summary = f"Sale #{sal.id} ({sal.customer.name if sal.customer else 'customer'}) deleted"
    customer_id = remove_customer_ledger_entry("sale", sal.id)
    db.session.delete(sal)
    db.session.commit()
    if customer_id:
        recalculate_customer_ledger(customer_id)
        db.session.commit()
    record_audit("delete", "Sale", id, audit_summary)
    flash("Sale deleted successfully!", "success")
    return redirect(url_for("sale"))


# ─── SALE RETURN & INVOICE ROUTES ───────────────────────────────────────────


def get_sale_item_returned_qty(sale_item_id):
    """Total quantity already returned for a sale item."""
    return float(db.session.query(SaleReturn)
                 .filter_by(sale_item_id=sale_item_id)
                 .with_entities(db.func.sum(SaleReturn.quantity))
                 .scalar() or 0)


def get_sale_returned_qty(sale_id):
    """Total quantity returned for an entire sale."""
    return float(db.session.query(SaleReturn)
                 .filter_by(sale_id=sale_id)
                 .with_entities(db.func.sum(SaleReturn.quantity))
                 .scalar() or 0)


@sales_bp.route('/sale-return', methods=['GET', 'POST'])
@verified_required
def sale_return():
    """Display and create sale returns."""
    from app import item_add_stock, item_remove_stock
    from app import remove_customer_ledger_entry, recalculate_customer_ledger
    from app import sync_customer_sale_return, post_document

    search = request.args.get("search", "").strip()
    query = SaleReturn.query.order_by(SaleReturn.date.desc())
    if search:
        from salpurflask.models import Customer as Cust
        query = (
            query.join(Cust, SaleReturn.customer_id == Cust.id)
            .join(Item, SaleReturn.item_id == Item.id)
            .filter((Cust.name.ilike(f"%{search}%")) | (Item.name.ilike(f"%{search}%")))
        )
    returns, pagination = get_paginated_results(query)
    all_sis = (SaleItem.query.join(Sale)
               .filter(Sale.is_reversed.is_(False))
               .order_by(Sale.date.desc(), SaleItem.id).all())
    items_available = [
        {"si": si, "remaining": si.quantity - get_sale_item_returned_qty(si.id)}
        for si in all_sis
        if si.quantity - get_sale_item_returned_qty(si.id) > 0
    ]
    if request.method == "POST":
        si_ids        = request.form.getlist("sale_item_id[]")
        quantities    = request.form.getlist("quantity[]")
        return_prices = request.form.getlist("return_price[]")
        reasons       = request.form.getlist("reason[]")
        date_str      = request.form.get("date", "").strip()
        if not date_str:
            flash("Date is required!", "danger")
        elif not si_ids:
            flash("At least one item row is required!", "danger")
        else:
            try:
                ret_date = datetime.strptime(date_str, "%Y-%m-%d")
                errors = []
                rows = []
                for idx, (si_id, qty_s, price_s, reason_s) in enumerate(zip(si_ids, quantities, return_prices, reasons), 1):
                    if not si_id or not qty_s or not price_s:
                        errors.append(f"Row {idx}: item, quantity and price are required.")
                        continue
                    if not qty_s.isdigit() or int(qty_s) <= 0:
                        errors.append(f"Row {idx}: quantity must be a positive integer.")
                        continue
                    try:
                        price_f = float(price_s)
                        if price_f < 0:
                            errors.append(f"Row {idx}: return price cannot be negative.")
                            continue
                    except ValueError:
                        errors.append(f"Row {idx}: invalid return price.")
                        continue
                    si = db.session.get(SaleItem, int(si_id))
                    if not si:
                        errors.append(f"Row {idx}: sale item not found.")
                        continue
                    remaining = si.quantity - get_sale_item_returned_qty(si.id)
                    if int(qty_s) > remaining:
                        errors.append(f"Row {idx} ({si.item.name}): cannot return {qty_s}, only {remaining} remaining.")
                        continue
                    rows.append((si, int(qty_s), price_f, reason_s.strip() or None))
                if errors:
                    for e in errors:
                        flash(e, "danger")
                else:
                    for si, qty, price, reason_val in rows:
                        sale = si.sale_header
                        item = get_item_locked(si.item_id)
                        sr = SaleReturn(
                            sale_id=sale.id,
                            customer_id=sale.customer_id,
                            item_id=si.item_id,
                            quantity=qty,
                            return_price=price,
                            date=ret_date,
                            reason=reason_val,
                            unit_name=si.unit_name, unit_factor=si.unit_factor or 1,
                            sale_item_id=si.id,
                        )
                        db.session.add(sr)
                        db.session.flush()
                        if item:
                            base_qty = qty * (si.unit_factor or 1)
                            cost = (Decimal(str(si.cost_price or 0)) * Decimal(str(base_qty))).quantize(MONEY)
                            sr.cost_restored = cost
                            # Goods come back to the warehouse the sale actually left
                            # from, never wherever happens to be default.
                            item_add_stock(item, base_qty, cost, location_id=sale.location_id,
                                          movement_type="sale_return", source_type="sale_return", source_id=sr.id)
                        sync_customer_sale_return(sr)
                        post_document("sale_return", sr)
                    db.session.commit()
                    flash(f"{len(rows)} sale return(s) recorded successfully!", "success")
                    return redirect(url_for("sale_return"))
            except ValueError:
                flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    return render_template(
        "sale_return.html",
        returns=returns,
        items_available=items_available,
        pagination=pagination,
        search=search,
        today=now_local().strftime("%Y-%m-%d"),
    )


@admin_required
def delete_sale_return(id):
    """Delete a sale return."""
    from app import item_remove_stock
    from app import remove_customer_ledger_entry, recalculate_customer_ledger

    sr = db.session.get(SaleReturn, id) or abort(404)
    assert_not_posted("sale_return", sr.id, f"Sale return #{sr.id}")
    item = get_item_locked(sr.item_id)
    if item:
        parent_location = sr.sale.location_id if sr.sale else None
        item_remove_stock(item, line_base_qty(sr), cost_total=sr.cost_restored or 0,
                          location_id=parent_location,
                          movement_type="sale_return", source_type="sale_return", source_id=sr.id)
    customer_id = remove_customer_ledger_entry("sale_return", sr.id)
    db.session.delete(sr)
    db.session.commit()
    if customer_id:
        recalculate_customer_ledger(customer_id)
        db.session.commit()
    flash("Sale return deleted successfully!", "success")
    return redirect(url_for("sale_return"))


@sales_bp.route('/sale/<int:id>/invoice', methods=['GET'])
@verified_required
def sale_invoice(id):
    """Display sale invoice with payment details."""
    from app import get_payment_status, get_sale_received, get_sale_returned_qty
    from salpurflask.models import Customer, Location

    sale      = db.session.query(Sale).filter_by(id=id).first() or abort(404)
    customer  = db.session.query(Customer).filter_by(id=sale.customer_id).first()
    received  = get_sale_received(id)
    total     = sale_total(sale)
    status    = get_payment_status(total, received)
    returned_qty = get_sale_returned_qty(id)
    location  = db.session.get(Location, sale.location_id) if sale.location_id else None
    return render_template(
        "invoice_sale.html",
        sale=sale,
        customer=customer,
        received=received,
        total=total,
        balance=total - received,
        status=status,
        returned_qty=returned_qty,
        location=location,
        generated_at=now_local(),
    )


# ─── POINT OF SALE (POS) ROUTES ────────────────────────────────────────────


def get_walkin_customer():
    """The counter's default customer for cash sales."""
    c = Customer.query.filter_by(name="Walk-in Customer").first()
    if c is None:
        c = Customer(name="Walk-in Customer", contact="0000000000",
                     address="Walk-in", opening_balance=0)
        db.session.add(c)
        db.session.commit()
    return c


@sales_bp.route('/pos', methods=['GET', 'POST'])
@manager_required
def pos():
    """POS main screen."""
    from app import active_accounts, Item

    return render_template(
        "pos.html",
        accounts=active_accounts(),
        walkin=get_walkin_customer(),
        today=now_local().strftime("%Y-%m-%d"),
    )


@sales_bp.route('/pos/lookup', methods=['GET', 'POST'])
@manager_required
def pos_lookup():
    """Item lookup for POS."""
    from app import item_unit_choices

    q = (request.args.get("q") or "").strip()
    if not q:
        return {"items": []}
    # Only show items with business_category_id (new configurable system)
    exact = Item.query.filter(Item.barcode == q, Item.business_category_id != None).first()
    if exact:
        matches = [exact]
    else:
        matches = (Item.query
                   .filter(sql_or(Item.name.ilike(f"%{q}%"), Item.barcode.ilike(f"%{q}%")))
                   .filter(Item.business_category_id != None)
                   .order_by(Item.name).limit(20).all())
    from app import get_standard_tax_rate
    std_tax = get_standard_tax_rate()
    return {"items": [{
        "id": it.id, "name": it.name, "barcode": it.barcode or "",
        "price": float(it.sale_price or 0), "stock": it.stock,
        "unit": it.unit or "Pcs",
        "item_type": it.item_type or "STOCK",
        "default_tax_percent": float(it.default_tax_percent or std_tax or 0),
        "is_taxable": bool(it.is_taxable),
        "units": [{"key": u["key"], "name": u["name"], "factor": u["factor"],
                   "price": float(u["sale_price"] or 0) or float(it.sale_price or 0)}
                  for u in item_unit_choices(it)],
    } for it in matches]}


@sales_bp.route('/pos/checkout', methods=['POST'])
@manager_required
def pos_checkout():
    """Ring up a POS sale."""
    from app import item_remove_stock, sync_customer_sale
    from app import sync_customer_receipt, post_document, record_audit
    from app import parse_account_id, PAYMENT_METHODS, PostingError
    from salpurflask.models import resolve_location_id, stock_at_location

    data = request.get_json(silent=True) or {}
    lines = data.get("items") or []
    if not lines:
        return {"ok": False, "error": "The cart is empty."}, 400

    try:
        location_id = resolve_location_id(data.get("location_id"))
    except ValueError as e:
        return {"ok": False, "error": str(e)}, 400

    customer_id = data.get("customer_id") or get_walkin_customer().id
    try:
        amount_paid = Decimal(str(data.get("amount_paid") or 0)).quantize(MONEY)
    except (InvalidOperation, ValueError):
        return {"ok": False, "error": "Invalid amount paid."}, 400

    account_id, acc_err = parse_account_id(str(data.get("account_id") or "").strip())
    if acc_err:
        return {"ok": False, "error": acc_err}, 400

    try:
        first = lines[0]
        total = Decimal("0")
        first_item_data = None
        processed_lines = []

        # Validate all items, calculate totals, remove stock
        for ln in lines:
            item_obj = get_item_locked(int(ln["item_id"]))
            if item_obj is None:
                db.session.rollback()
                return {"ok": False, "error": "Item not found."}, 400
            qty_i = int(ln["qty"]); price_f = float(ln["price"])
            if qty_i <= 0 or price_f < 0:
                db.session.rollback()
                return {"ok": False, "error": f"Bad quantity or price for {item_obj.name}."}, 400
            unit_name, unit_factor = resolve_item_unit(item_obj, str(ln.get("unit_id") or ""))
            base_qty = qty_i * unit_factor
            available = stock_at_location(item_obj.id, location_id)
            if available < base_qty:
                db.session.rollback()
                return {"ok": False,
                        "error": f"Only {available} {item_obj.unit} × {item_obj.name} "
                                 f"in stock at this warehouse."}, 400

            # Calculate discount and tax
            gross = qty_i * price_f
            d_type = str(ln.get("discount_type") or "percent")
            d_val = float(ln.get("discount_value") or 0)
            tax_pct = float(ln.get("tax_percent") or 0)
            disc_amt, tax_amt, net = calc_discount_tax(gross, d_type, d_val, tax_pct)
            total += Decimal(str(net)).quantize(MONEY)

            # Store processed line data
            line_data = {
                "item_obj": item_obj,
                "qty_i": qty_i,
                "price_f": price_f,
                "unit_name": unit_name,
                "unit_factor": unit_factor,
                "base_qty": base_qty,
                "d_type": d_type,
                "d_val": d_val,
                "tax_pct": tax_pct,
                "disc_amt": disc_amt,
                "tax_amt": tax_amt,
                "net": net,
            }
            processed_lines.append(line_data)

            # Capture first item for Sale record
            if ln == first:
                first_item_data = line_data

            # Remove stock. The Sale row this belongs to does not exist yet
            # (POS validates and moves stock line-by-line, then creates the
            # Sale once every line has passed) — movement_type is passed so
            # the row exists and is correctly typed immediately, but
            # source_id is filled in right after sal.id exists, a few lines
            # below, rather than restructuring this loop's order.
            unit_cost = item_obj.avg_cost
            item_remove_stock(item_obj, base_qty, cost_total=unit_cost * Decimal(str(base_qty)),
                              location_id=location_id,
                              movement_type="sale", source_type="sale")

        # Create Sale record with first item data
        first = first_item_data
        sal = Sale(
            customer_id=int(customer_id),
            item_id=int(lines[0]["item_id"]),
            quantity=first["qty_i"],
            sale_price=float(first["price_f"]),
            cost_price=float(first["item_obj"].avg_cost),
            discount_type=first["d_type"], discount_value=float(first["d_val"]),
            discount_amount=first["disc_amt"],
            tax_percent=float(first["tax_pct"]), tax_amount=first["tax_amt"],
            date=now_local(), notes="POS sale",
            location_id=location_id,
        )
        db.session.add(sal)
        db.session.flush()

        # Fill in the source_id on the movement rows written during the
        # validation loop above, now that sal.id exists. Scoped to this
        # checkout's own still-uncommitted rows: source_id IS NULL only
        # while a "sale" movement is mid-transaction (every path either
        # fills it in here or rolls the whole transaction back), and no
        # other session can see rows this transaction hasn't committed yet.
        from salpurflask.models.inventory_location import StockMovement
        (StockMovement.query
         .filter_by(source_type="sale", source_id=None, movement_type="sale",
                   location_id=location_id)
         .filter(StockMovement.item_id.in_([ld["item_obj"].id for ld in processed_lines]))
         .update({"source_id": sal.id}, synchronize_session=False))

        # Add all SaleItems
        for line_data in processed_lines:
            unit_cost = line_data["item_obj"].avg_cost
            db.session.add(SaleItem(
                sale_id=sal.id, item_id=line_data["item_obj"].id, quantity=line_data["qty_i"],
                sale_price=line_data["price_f"], cost_price=float(unit_cost),
                discount_type=line_data["d_type"], discount_value=line_data["d_val"],
                discount_amount=line_data["disc_amt"], tax_percent=line_data["tax_pct"],
                tax_amount=line_data["tax_amt"], amount=line_data["net"],
                unit_name=line_data["unit_name"], unit_factor=line_data["unit_factor"]))

        db.session.flush()
        db.session.refresh(sal)
        sal.invoice_no = allocate_document_number("sale", sal.date)
        sync_customer_sale(sal)
        post_document("sale", sal)

        received = min(amount_paid, total) if amount_paid > 0 else Decimal("0")
        if received > 0:
            acct = db.session.get(FinancialAccount, account_id) if account_id else None
            method = acct.method if (acct and acct.method in PAYMENT_METHODS) else "Cash"
            rcpt = CustomerPayment(
                customer_id=sal.customer_id, sale_id=sal.id, amount=received,
                payment_date=now_local(), payment_method=method,
                account_id=account_id, reference_no=sal.invoice_no, notes="POS payment")
            db.session.add(rcpt)
            db.session.flush()
            sync_customer_receipt(rcpt)
            post_document("receipt", rcpt)

        db.session.commit()
        record_audit("create", "Sale", sal.id, f"POS sale {sal.invoice_no}, total {float(total):,.2f}")

        # Delete the hold record if this was a resumed hold
        hold_id = data.get("hold_id")
        if hold_id and str(hold_id).isdigit():
            hold = db.session.get(PosHold, int(hold_id))
            if hold:
                db.session.delete(hold)
                db.session.commit()

        change = (amount_paid - total) if amount_paid > total else Decimal("0")
        return {"ok": True, "sale_id": sal.id, "invoice_no": sal.invoice_no,
                "total": float(total), "paid": float(amount_paid), "change": float(change),
                "receipt_url": url_for("pos_receipt", id=sal.id)}
    except PostingError as e:
        db.session.rollback()
        return {"ok": False, "error": str(e)}, 400
    except Exception as e:
        db.session.rollback()
        import logging
        logging.exception("POS checkout failed")
        return {"ok": False, "error": f"Could not complete the sale: {e}"}, 500


@sales_bp.route('/pos/receipt/<int:id>', methods=['GET'])
@manager_required
def pos_receipt(id):
    """POS receipt display."""
    from app import get_sale_received

    sal = db.session.get(Sale, id) or abort(404)
    received = get_sale_received(sal.id)
    return render_template("pos_receipt.html", sale=sal,
                           total=sale_total(sal), received=received)


# ─── POS BILL HOLD ROUTES ──────────────────────────────────────────────────


@sales_bp.route('/pos/hold', methods=['POST'])
@manager_required
def pos_hold():
    """Hold a POS bill (save temporarily without processing). Updates existing hold if hold_id provided."""
    import json

    data = request.get_json(silent=True) or {}
    lines = data.get("items") or []

    if not lines:
        return {"ok": False, "error": "Cart is empty."}, 400

    customer_id = data.get("customer_id")
    if not customer_id:
        return {"ok": False, "error": "Customer required."}, 400

    customer = db.session.get(Customer, int(customer_id))
    if not customer:
        return {"ok": False, "error": "Customer not found."}, 400

    account_id = data.get("account_id")
    if account_id:
        account = db.session.get(FinancialAccount, int(account_id))
        if not account:
            account_id = None

    try:
        # Enrich cart data with complete unit metadata before saving
        enriched_lines = []
        for ln in lines:
            item_id = int(ln.get("item_id", 0))
            item_obj = db.session.get(Item, item_id)
            if not item_obj:
                return {"ok": False, "error": f"Item {item_id} not found."}, 400

            # Get unit metadata
            unit_id = str(ln.get("unit_id") or "").strip()
            unit_name, unit_factor = resolve_item_unit(item_obj, unit_id)

            enriched_lines.append({
                "item_id": int(ln["item_id"]),
                "qty": int(ln["qty"]),
                "price": float(ln["price"]),
                "unit_id": unit_id,  # Save the unit_id to restore it exactly
                "unit_name": unit_name,
                "unit_factor": unit_factor,
                "stock": item_obj.stock,
                "name": item_obj.name,
                # Optional discount/tax fields (for future use if added to POS)
                "discount_type": str(ln.get("discount_type") or "percent"),
                "discount_value": float(ln.get("discount_value") or 0),
                "tax_percent": float(ln.get("tax_percent") or 0),
            })

        # Check if updating existing hold (optimistic locking with version field)
        hold_id = data.get("hold_id")
        client_version = data.get("client_version")  # Version from client when resume was fetched

        if hold_id and str(hold_id).isdigit():
            hold = db.session.get(PosHold, int(hold_id))
            if hold:
                # Version conflict check: if client version differs from server, someone else updated it
                if client_version is not None and int(client_version) != hold.version:
                    # Version mismatch: hold was updated by another user
                    db.session.rollback()
                    return {
                        "ok": False,
                        "error": f"Hold was updated by another user. Please reload and try again.",
                        "conflict": True,
                        "current_version": hold.version
                    }, 409  # Conflict

                # Update existing hold with version increment
                hold.customer_id = int(customer_id)
                hold.cart_data = json.dumps(enriched_lines)
                hold.notes = data.get("notes", "").strip() or None
                hold.account_id = account_id if account_id else None
                hold.amount_paid_memo = Decimal(str(data.get("amount_paid") or 0)).quantize(MONEY) if data.get("amount_paid") else None
                hold.version += 1  # Increment for next concurrent attempt
            else:
                # Hold not found, create new (was deleted between resume and update)
                hold = PosHold(
                    customer_id=int(customer_id),
                    user_id=current_user.id,
                    cart_data=json.dumps(enriched_lines),
                    notes=data.get("notes", "").strip() or None,
                    account_id=account_id if account_id else None,
                    amount_paid_memo=Decimal(str(data.get("amount_paid") or 0)).quantize(MONEY) if data.get("amount_paid") else None,
                )
                db.session.add(hold)
        else:
            # Create new hold
            hold = PosHold(
                customer_id=int(customer_id),
                user_id=current_user.id,
                cart_data=json.dumps(enriched_lines),
                notes=data.get("notes", "").strip() or None,
                account_id=account_id if account_id else None,
                amount_paid_memo=Decimal(str(data.get("amount_paid") or 0)).quantize(MONEY) if data.get("amount_paid") else None,
            )
            db.session.add(hold)

        db.session.commit()

        return {
            "ok": True,
            "hold_id": hold.id,
            "hold_no": f"HOLD-{hold.id:05d}",
        }
    except Exception as e:
        db.session.rollback()
        import logging
        logging.exception("pos_hold failed")
        return {"ok": False, "error": f"Failed to hold bill: {e}"}, 500


@manager_required
def list_pos_holds():
    """List held POS bills."""
    from salpurflask.models import PosHold

    search = request.args.get("search", "").strip()
    sort = request.args.get("sort", "hold_time").strip()
    query = (PosHold.query
             .filter_by(status="held")
             .options(joinedload(PosHold.user), joinedload(PosHold.customer)))

    if search:
        query = query.filter(
            Customer.name.ilike(f"%{search}%")
        )

    query = query.outerjoin(Customer)

    if sort == "customer":
        query = query.order_by(Customer.name.asc())
    elif sort == "total":
        query = query.order_by(PosHold.id.desc())  # Can't order by computed property
    else:
        query = query.order_by(PosHold.hold_time.desc())

    holds, pagination = get_paginated_results(query)

    return render_template(
        "pos_held_bills.html",
        holds=holds,
        pagination=pagination,
        search=search,
        sort=sort,
    )


@manager_required
def get_pos_hold(id):
    """Fetch a held bill as JSON for resuming. Returns complete cart with unit metadata and version for optimistic locking."""
    import json

    hold = db.session.get(PosHold, id)
    if not hold:
        return {"ok": False, "error": "Hold not found."}, 404

    try:
        cart = json.loads(hold.cart_data)

        # Validate and refresh stock levels for each item (stock may have changed since hold was created)
        for line in cart:
            item = db.session.get(Item, line.get("item_id"))
            if item:
                line["stock"] = item.stock
                line["name"] = item.name

        # customer_name: the POS customer field is now a lookup-picked hidden
        # input, not a <select> with every customer's name already in an
        # <option> — resuming a hold has to be told the name explicitly to
        # show the right label instead of a stale/blank one.
        customer = db.session.get(Customer, hold.customer_id) if hold.customer_id else None
        return {
            "ok": True,
            "hold_id": hold.id,
            "hold_no": f"HOLD-{hold.id:05d}",
            "customer_id": hold.customer_id,
            "customer_name": customer.name if customer else "",
            "account_id": hold.account_id,
            "amount_paid": float(hold.amount_paid_memo or 0),
            "notes": hold.notes or "",
            "cart": cart,
            "version": hold.version,  # For optimistic locking conflict detection
        }
    except Exception as e:
        return {"ok": False, "error": f"Failed to load hold: {e}"}, 500


@manager_required
def delete_pos_hold(id):
    """Delete a held bill."""
    from salpurflask.models import PosHold

    hold = db.session.get(PosHold, id)
    if not hold:
        return redirect(url_for("list_pos_holds"))

    db.session.delete(hold)
    db.session.commit()
    flash(f"Hold HOLD-{hold.id:05d} deleted.", "info")
    return redirect(url_for("list_pos_holds"))


# ─── DELIVERY CHALLAN ROUTES ──────────────────────────────────────────────


@verified_required
def delivery_challans():
    """Display and manage delivery challans."""
    from app import CHALLAN_STATUSES

    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()
    from salpurflask.models import DeliveryChallan
    query = DeliveryChallan.query.join(Sale).join(Customer, Sale.customer_id == Customer.id)
    if search:
        query = query.filter(Customer.name.ilike(f"%{search}%"))
    if status_filter:
        query = query.filter(DeliveryChallan.status == status_filter)
    challans, pagination = get_paginated_results(
        query.order_by(DeliveryChallan.challan_date.desc(), DeliveryChallan.id.desc())
    )
    pending_sales = Sale.query.filter(
        ~Sale.id.in_(db.session.query(DeliveryChallan.sale_id))
    ).order_by(Sale.date.desc()).all()
    return render_template("delivery_challans.html",
        challans=challans, pending_sales=pending_sales,
        pagination=pagination, search=search,
        status_filter=status_filter, challan_statuses=CHALLAN_STATUSES,
        today=now_local().strftime("%Y-%m-%d"))


@manager_required
def create_delivery_challan():
    """Create a delivery challan."""
    from salpurflask.models import DeliveryChallan

    sale_id      = request.form.get("sale_id", "").strip()
    challan_date = request.form.get("challan_date", "").strip()
    transport    = request.form.get("transport", "").strip() or None
    notes        = request.form.get("notes", "").strip() or None
    if not sale_id or not challan_date:
        flash("Sale and challan date are required.", "danger")
        return redirect(url_for("delivery_challans"))
    if DeliveryChallan.query.filter_by(sale_id=int(sale_id)).first():
        flash("A challan already exists for this sale.", "warning")
        return redirect(url_for("delivery_challans"))
    dc = DeliveryChallan(
        sale_id=int(sale_id),
        challan_date=datetime.strptime(challan_date, "%Y-%m-%d"),
        transport=transport, notes=notes,
    )
    db.session.add(dc)
    db.session.commit()
    flash(f"Delivery Challan #{dc.id} created.", "success")
    return redirect(url_for("delivery_challans"))


@manager_required
def update_delivery_challan(id):
    """Update delivery challan status."""
    from app import CHALLAN_STATUSES
    from salpurflask.models import DeliveryChallan

    dc = db.session.get(DeliveryChallan, id) or abort(404)
    new_status    = request.form.get("status", "").strip()
    dispatch_date = request.form.get("dispatch_date", "").strip()
    delivery_date = request.form.get("delivery_date", "").strip()
    transport     = request.form.get("transport", "").strip() or None
    notes         = request.form.get("notes", "").strip() or None
    if new_status in CHALLAN_STATUSES:
        dc.status = new_status
    if dispatch_date:
        dc.dispatch_date = datetime.strptime(dispatch_date, "%Y-%m-%d")
    if delivery_date:
        dc.delivery_date = datetime.strptime(delivery_date, "%Y-%m-%d")
    dc.transport = transport
    dc.notes = notes
    db.session.commit()
    flash(f"Challan #{dc.id} updated.", "success")
    return redirect(url_for("delivery_challans"))


# ─── SALES EXPORT & REPORT ROUTES ─────────────────────────────────────────


@manager_required
def export_sale_report():
    """Export sales history report (CSV/XLSX)."""
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        sale_items = (
            SaleItem.query.join(Sale, SaleItem.sale_id == Sale.id)
            .join(Customer, Sale.customer_id == Customer.id)
            .join(Item, SaleItem.item_id == Item.id)
            .filter(Sale.date.between(start_date, end_date))
            .order_by(Sale.id)
            .all()
        )
        col_headers = ["ID", "Customer", "Item", "Category", "Quantity", "Sale Price", "Total", "Date"]
        rows = [
            [si.sale_id, si.sale_header.customer.name, si.item.name,
             si.item.id_category.name if si.item.id_category else "N/A",
             si.base_quantity, round(float(si.sale_price), 2),
             round(float(si.amount), 2), si.sale_header.date.strftime("%Y-%m-%d")]
            for si in sale_items
        ]
        if request.form.get("format") == "xlsx":
            return excel_response("sale_report.xlsx", "Sale History", col_headers, rows, start_date_str, end_date_str)
        return csv_response("sale_report.csv", "Sale History", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))


@manager_required
def export_date_sale_report():
    """Export date-wise profit report (CSV/XLSX)."""
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        from app import sql_date
        date_sale_report = (
            db.session.query(
                sql_date(Sale.date).label("sale_date"),
                db.func.sum(SaleItem.amount).label("sale_amt"),
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.unit_factor * SaleItem.cost_price).label("profit_amt"),
            )
            .select_from(SaleItem)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(sql_date(Sale.date))
            .order_by(sql_date(Sale.date))
            .all()
        )
        col_headers = ["Date", "Sale Amount", "Profit Amount"]
        rows = [[row.sale_date, round(row.sale_amt, 2), round(row.profit_amt, 2)] for row in date_sale_report]
        if request.form.get("format") == "xlsx":
            return excel_response("date_sale_report.xlsx", "Date-wise Profit Report", col_headers, rows, start_date_str, end_date_str)
        return csv_response("date_sale_report.csv", "Date-wise Profit Report", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))


@manager_required
def export_item_sale_report():
    """Export item-wise profit report (CSV/XLSX)."""
    from salpurflask.models import Category

    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        item_sale = (
            db.session.query(
                Item.name.label("name"),
                Category.name.label("category"),
                db.func.sum(SaleItem.amount).label("sale_amt"),
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.unit_factor * SaleItem.cost_price).label("profit_amt"),
            )
            .select_from(SaleItem)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .join(Item, SaleItem.item_id == Item.id)
            .outerjoin(Category, Item.category_id == Category.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(Item.name, Category.name)
            .order_by(Item.name)
            .all()
        )
        col_headers = ["Item", "Category", "Sale Amount", "Profit Amount"]
        rows = [[row.name, row.category or "N/A", round(row.sale_amt, 2), round(row.profit_amt, 2)] for row in item_sale]
        if request.form.get("format") == "xlsx":
            return excel_response("item_sale_report.xlsx", "Item-wise Profit Report", col_headers, rows, start_date_str, end_date_str)
        return csv_response("item_sale_report.csv", "Item-wise Profit Report", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))


@manager_required
def export_customer_sale_report():
    """Export customer-wise profit report (CSV/XLSX)."""
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        customer_sale = (
            db.session.query(
                Customer.name.label("name"),
                db.func.sum(SaleItem.amount).label("sale_amt"),
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.unit_factor * SaleItem.cost_price).label("profit_amt"),
            )
            .select_from(SaleItem)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .join(Customer, Sale.customer_id == Customer.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(Customer.name)
            .order_by(Customer.name)
            .all()
        )
        col_headers = ["Customer", "Sale Amount", "Profit Amount"]
        rows = [[row.name, round(row.sale_amt, 2), round(row.profit_amt, 2)] for row in customer_sale]
        if request.form.get("format") == "xlsx":
            return excel_response("customer_sale_report.xlsx", "Customer-wise Profit Report", col_headers, rows, start_date_str, end_date_str)
        return csv_response("customer_sale_report.csv", "Customer-wise Profit Report", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))


@manager_required
def export_category_sale_report():
    """Export category-wise profit report (CSV/XLSX)."""
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        category_sale = (
            db.session.query(
                BusinessCategory.name.label("name"),
                db.func.sum(SaleItem.amount).label("sale_amt"),
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.unit_factor * SaleItem.cost_price).label("profit_amt"),
            )
            .select_from(SaleItem)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .join(Item, SaleItem.item_id == Item.id)
            .join(BusinessCategory, Item.business_category_id == BusinessCategory.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(BusinessCategory.name)
            .order_by(BusinessCategory.name)
            .all()
        )
        col_headers = ["Category", "Sale Amount", "Profit Amount"]
        rows = [[row.name, round(row.sale_amt, 2), round(row.profit_amt, 2)] for row in category_sale]
        if request.form.get("format") == "xlsx":
            return excel_response("category_sale_report.xlsx", "Category-wise Profit Report", col_headers, rows, start_date_str, end_date_str)
        return csv_response("category_sale_report.csv", "Category-wise Profit Report", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))


@manager_required
def export_sale_return_report():
    """Export sale returns report (CSV/XLSX)."""
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        returns = SaleReturn.query.filter(SaleReturn.date.between(start_date, end_date)).order_by(SaleReturn.date.desc()).all()
        col_headers = ["ID", "Sale #", "Customer", "Item", "Quantity", "Return Price", "Total", "Date", "Reason"]
        rows = [
            [r.id, r.sale_id, r.customer.name, r.item.name,
             r.quantity, round(r.return_price, 2), round(r.quantity * r.return_price, 2),
             r.date.strftime("%Y-%m-%d"), r.reason or ""]
            for r in returns
        ]
        if request.form.get("format") == "xlsx":
            return excel_response("sale_return_report.xlsx", "Sale Returns Report", col_headers, rows, start_date_str, end_date_str)
        return csv_response("sale_return_report.csv", "Sale Returns Report", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))
