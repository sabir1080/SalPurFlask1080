"""Sales and Point of Sale routes."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import (
    Sale, SaleItem, SaleReturn, Customer, Item, CustomerPayment, FinancialAccount,
    MONEY, resolve_item_unit, line_base_qty,
    calc_discount_tax, allocate_document_number, assert_not_posted, assert_not_numbered,
    post_document, reverse_document, Quotation,
)
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import (
    now_local, get_paginated_results, csv_response, excel_response
)
from sqlalchemy import or_ as sql_or


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


@manager_required
def pos():
    """POS main screen."""
    from app import active_accounts

    return render_template(
        "pos.html",
        customers=Customer.query.order_by(Customer.name).all(),
        accounts=active_accounts(),
        walkin=get_walkin_customer(),
        today=now_local().strftime("%Y-%m-%d"),
    )


@manager_required
def pos_lookup():
    """Item lookup for POS."""
    from app import item_unit_choices

    q = (request.args.get("q") or "").strip()
    if not q:
        return {"items": []}
    exact = Item.query.filter_by(barcode=q).first()
    if exact:
        matches = [exact]
    else:
        matches = (Item.query
                   .filter(sql_or(Item.name.ilike(f"%{q}%"), Item.barcode.ilike(f"%{q}%")))
                   .order_by(Item.name).limit(20).all())
    return {"items": [{
        "id": it.id, "name": it.name, "barcode": it.barcode or "",
        "price": float(it.sale_price or 0), "stock": it.stock,
        "unit": it.unit or "Pcs",
        "units": [{"key": u["key"], "name": u["name"], "factor": u["factor"],
                   "price": float(u["sale_price"] or 0) or float(it.sale_price or 0)}
                  for u in item_unit_choices(it)],
    } for it in matches]}


@manager_required
def pos_checkout():
    """Ring up a POS sale."""
    from app import get_item_locked, item_remove_stock, sync_customer_sale
    from app import sync_customer_receipt, post_document, record_audit
    from app import parse_account_id, PAYMENT_METHODS, PostingError

    data = request.get_json(silent=True) or {}
    lines = data.get("items") or []
    if not lines:
        return {"ok": False, "error": "The cart is empty."}, 400

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
        sal = Sale(
            customer_id=int(customer_id),
            item_id=int(first["item_id"]),
            quantity=int(first["qty"]),
            sale_price=float(first["price"]),
            cost_price=0.0,
            discount_type="percent", discount_value=0, discount_amount=0,
            tax_percent=0, tax_amount=0,
            date=now_local(), notes="POS sale",
        )
        db.session.add(sal)
        db.session.flush()

        total = Decimal("0")
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
            if item_obj.stock < base_qty:
                db.session.rollback()
                return {"ok": False,
                        "error": f"Only {item_obj.stock} {item_obj.unit} × {item_obj.name} in stock."}, 400
            net = (Decimal(str(qty_i)) * Decimal(str(price_f))).quantize(MONEY)
            total += net
            unit_cost = item_obj.avg_cost
            db.session.add(SaleItem(
                sale_id=sal.id, item_id=item_obj.id, quantity=qty_i, sale_price=price_f,
                cost_price=float(unit_cost), discount_type="percent", discount_value=0,
                discount_amount=0, tax_percent=0, tax_amount=0, amount=net,
                unit_name=unit_name, unit_factor=unit_factor))
            item_remove_stock(item_obj, base_qty, cost_total=unit_cost * Decimal(str(base_qty)))

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


@manager_required
def pos_receipt(id):
    """POS receipt display."""
    from app import get_sale_received

    sal = db.session.get(Sale, id) or abort(404)
    received = get_sale_received(sal.id)
    return render_template("pos_receipt.html", sale=sal,
                           total=sale_total(sal), received=received)


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
            [si.sale_id, si.sale_header.id_customer.name, si.item.name,
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
        date_sale_report = (
            db.session.query(
                db.func.date(Sale.date).label("sale_date"),
                db.func.sum(SaleItem.amount).label("sale_amt"),
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.unit_factor * SaleItem.cost_price).label("profit_amt"),
            )
            .select_from(SaleItem)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(db.func.date(Sale.date))
            .order_by(db.func.date(Sale.date))
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
    from salpurflask.models import Category

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
                Category.name.label("name"),
                db.func.sum(SaleItem.amount).label("sale_amt"),
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.unit_factor * SaleItem.cost_price).label("profit_amt"),
            )
            .select_from(SaleItem)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .join(Item, SaleItem.item_id == Item.id)
            .join(Category, Item.category_id == Category.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(Category.name)
            .order_by(Category.name)
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
