"""Sales and Point of Sale routes."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import (
    Sale, SaleItem, SaleReturn, Customer, Item,
    MONEY, resolve_item_unit, line_base_qty,
    calc_discount_tax, allocate_document_number, assert_not_posted, assert_not_numbered,
    post_document, reverse_document, Quotation,
)
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import (
    now_local, get_paginated_results, csv_response, excel_response
)


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


@verified_required
def sale():
    """Display sales and allow creation of new sales."""
    from app import validate_line_rows, get_item_locked, sale_total as app_sale_total
    from app import record_audit, sync_customer_sale, item_add_stock, item_remove_stock

    search = request.args.get("search", "").strip()
    query = Sale.query
    if search:
        query = query.join(Customer).filter(Customer.name.ilike(f"%{search}%"))
    sales, pagination = get_paginated_results(query.order_by(Sale.date.desc(), Sale.id.desc()))
    customers = Customer.query.order_by(Customer.name).all()
    items = Item.query.order_by(Item.name).all()
    if request.method == "POST":
        if current_user.role not in ("admin", "manager"):
            flash("Access denied. Only managers and admins can add sales.", "danger")
            return redirect(url_for("sale"))
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
                    if item_obj:
                        _, factor = resolve_item_unit(item_obj, unit_key)
                        if item_obj.stock < int(qty) * factor:
                            stock_errors.append(f"{item_obj.name}: only {item_obj.stock} {item_obj.unit} available")
                if stock_errors:
                    flash("Insufficient stock — " + "; ".join(stock_errors), "danger")
                else:
                    first_iid, first_qty, first_price = rows[0][0], rows[0][1], rows[0][2]
                    sal = Sale(
                        customer_id=int(customer_id),
                        item_id=int(first_iid),
                        quantity=int(first_qty),
                        sale_price=float(first_price),
                        cost_price=0.0,
                        discount_type="percent", discount_value=0, discount_amount=0,
                        tax_percent=0, tax_amount=0,
                        date=sale_date, notes=notes or None,
                    )
                    db.session.add(sal)
                    db.session.flush()
                    for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                        item_obj = get_item_locked(int(iid)) or abort(404)
                        qty_i = int(qty); price_f = float(price)
                        unit_name, unit_factor = resolve_item_unit(item_obj, unit_key)
                        base_qty = qty_i * unit_factor
                        if item_obj.stock < base_qty:
                            db.session.rollback()
                            flash(f"Insufficient stock for {item_obj.name}: only {item_obj.stock} "
                                  "available now (it changed while saving). Please try again.", "danger")
                            return redirect(url_for("sale"))
                        d_val_f = float(d_val or 0); tax_f = float(tax or 0)
                        gross = qty_i * price_f
                        disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                        unit_cost = item_obj.avg_cost
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
                        item_remove_stock(item_obj, base_qty, cost_total=unit_cost * Decimal(str(base_qty)))
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
    return render_template(
        "sale.html",
        customers=customers,
        items=items,
        sales=sales,
        pagination=pagination,
        search=search,
        today=now_local().strftime("%Y-%m-%d"),
    )


@manager_required
def edit_sale(id):
    """Edit an existing sale."""
    from app import validate_line_rows, get_item_locked, record_audit
    from app import sync_customer_sale, item_add_stock, item_remove_stock
    from app import remove_customer_ledger_entry, recalculate_customer_ledger

    sal = db.session.get(Sale, id) or abort(404)
    assert_not_posted("sale", sal.id, f"Sale #{sal.id}")
    customers = Customer.query.order_by(Customer.name).all()
    items_all = Item.query.order_by(Item.name).all()
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
                    if old_item:
                        cost_returned = si.cost_price * line_base_qty(si)
                        item_add_stock(old_item, line_base_qty(si), cost_total=cost_returned)
                stock_errors = []
                for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                    item_obj = get_item_locked(int(iid))
                    if item_obj:
                        _, factor = resolve_item_unit(item_obj, unit_key)
                        if item_obj.stock < int(qty) * factor:
                            stock_errors.append(f"{item_obj.name}: only {item_obj.stock} {item_obj.unit} available")
                if stock_errors:
                    for si in sal.line_items:
                        old_item = get_item_locked(si.item_id)
                        if old_item:
                            cost_removed = si.cost_price * line_base_qty(si)
                            item_remove_stock(old_item, line_base_qty(si), cost_total=cost_removed)
                    flash("Insufficient stock — " + "; ".join(stock_errors), "danger")
                else:
                    SaleItem.query.filter_by(sale_id=sal.id).delete()
                    first_iid, first_qty, first_price = rows[0][0], rows[0][1], rows[0][2]
                    sal.customer_id = int(customer_id)
                    sal.item_id = int(first_iid); sal.quantity = int(first_qty)
                    sal.sale_price = float(first_price); sal.cost_price = 0.0
                    sal.discount_type = "percent"; sal.discount_value = 0
                    sal.discount_amount = 0; sal.tax_percent = 0; sal.tax_amount = 0
                    sal.date = datetime.strptime(date_str, "%Y-%m-%d")
                    sal.notes = notes or None
                    for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                        item_obj = get_item_locked(int(iid)) or abort(404)
                        qty_i = int(qty); price_f = float(price)
                        d_val_f = float(d_val or 0); tax_f = float(tax or 0)
                        gross = qty_i * price_f
                        disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                        unit_name, unit_factor = resolve_item_unit(item_obj, unit_key)
                        base_qty = qty_i * unit_factor
                        unit_cost = item_obj.avg_cost
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
                        item_remove_stock(item_obj, base_qty, cost_total=unit_cost * Decimal(str(base_qty)))
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
    return render_template("edit_sale.html", sale=sal, customers=customers, items=items_all)


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
        if item_obj:
            cost_returned = si.cost_price * line_base_qty(si)
            item_add_stock(item_obj, line_base_qty(si), cost_total=cost_returned)
    audit_summary = f"Sale #{sal.id} ({sal.id_customer.name if sal.id_customer else 'customer'}) deleted"
    customer_id = remove_customer_ledger_entry("sale", sal.id)
    db.session.delete(sal)
    db.session.commit()
    if customer_id:
        recalculate_customer_ledger(customer_id)
        db.session.commit()
    record_audit("delete", "Sale", id, audit_summary)
    flash("Sale deleted successfully!", "success")
    return redirect(url_for("sale"))
