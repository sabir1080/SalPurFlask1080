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


@verified_required
def sale_return():
    """Display and create sale returns."""
    from app import get_item_locked, item_add_stock, item_remove_stock
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
                        if item:
                            base_qty = qty * (si.unit_factor or 1)
                            cost = (Decimal(str(si.cost_price or 0)) * Decimal(str(base_qty))).quantize(MONEY)
                            sr.cost_restored = cost
                            item_add_stock(item, base_qty, cost)
                        db.session.add(sr)
                        db.session.flush()
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
    from app import get_item_locked, item_remove_stock
    from app import remove_customer_ledger_entry, recalculate_customer_ledger

    sr = db.session.get(SaleReturn, id) or abort(404)
    assert_not_posted("sale_return", sr.id, f"Sale return #{sr.id}")
    item = get_item_locked(sr.item_id)
    if item:
        item_remove_stock(item, line_base_qty(sr), cost_total=sr.cost_restored or 0)
    customer_id = remove_customer_ledger_entry("sale_return", sr.id)
    db.session.delete(sr)
    db.session.commit()
    if customer_id:
        recalculate_customer_ledger(customer_id)
        db.session.commit()
    flash("Sale return deleted successfully!", "success")
    return redirect(url_for("sale_return"))


@verified_required
def sale_invoice(id):
    """Display sale invoice with payment details."""
    from app import get_payment_status

    sale      = db.session.get(Sale, id) or abort(404)
    received  = get_sale_received(id)
    total     = sale_total(sale)
    status    = get_payment_status(total, received)
    returned_qty = get_sale_returned_qty(id)
    return render_template(
        "invoice_sale.html",
        sale=sale,
        received=received,
        total=total,
        balance=total - received,
        status=status,
        returned_qty=returned_qty,
    )
