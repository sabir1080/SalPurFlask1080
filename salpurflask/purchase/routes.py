"""Purchase and Purchase Order routes."""

from datetime import datetime
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import (
    Purchase, PurchaseItem, PurchaseReturn, PurchaseOrder, PurchaseOrderItem,
    Supplier, Item, Category,
    MONEY, resolve_item_unit, save_item_units,
    item_add_stock, item_remove_stock, line_base_qty,
    calc_discount_tax, allocate_document_number, assert_not_posted, assert_not_numbered,
    post_document, posted_entry, reverse_document,
    PO_STATUSES,
)
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import (
    now_local, get_paginated_results, csv_response, excel_response, get_item_locked
)


# ─── HELPER FUNCTIONS (PURCHASE-SPECIFIC) ──────────────────────────────────


def purchase_item_total(pi):
    """Calculate total cost for a purchase line item (with tax, after discount)."""
    return pi.amount


def purchase_total(purchase):
    """Calculate total cost for an entire purchase order."""
    return float(db.session.query(PurchaseItem)
                 .filter_by(purchase_id=purchase.id)
                 .with_entities(db.func.sum(PurchaseItem.amount))
                 .scalar() or 0.0)


def get_purchase_paid(purchase_id, exclude_payment_id=None):
    """Calculate total amount paid against a purchase."""
    from salpurflask.models import SupplierPayment

    query = SupplierPayment.query.filter(
        SupplierPayment.purchase_id == purchase_id,
        SupplierPayment.is_reversed.is_(False),
    )
    if exclude_payment_id:
        query = query.filter(SupplierPayment.id != exclude_payment_id)
    return float(query.with_entities(db.func.sum(SupplierPayment.amount)).scalar() or 0.0)


def sync_supplier_purchase(purchase):
    """Sync supplier ledger after purchase is created/updated."""
    from app import sync_supplier_purchase as app_sync

    return app_sync(purchase)


def sync_supplier_purchase_return(pr):
    """Sync supplier ledger after purchase return."""
    from app import sync_supplier_purchase_return as app_sync

    return app_sync(pr)


def get_purchase_returned_qty(purchase_id):
    """Get total quantity returned for a purchase."""
    return int(db.session.query(PurchaseReturn)
               .filter_by(purchase_id=purchase_id, is_reversed=False)
               .with_entities(db.func.sum(PurchaseReturn.quantity))
               .scalar() or 0)


def get_purchase_item_returned_qty(pi):
    """Get quantity returned for a specific purchase line item."""
    return int(db.session.query(PurchaseReturn)
               .filter(PurchaseReturn.purchase_id == pi.purchase_id,
                       PurchaseReturn.item_id == pi.item_id,
                       PurchaseReturn.is_reversed.is_(False))
               .with_entities(db.func.sum(PurchaseReturn.quantity))
               .scalar() or 0)


def purchase_return_total(pr):
    """Calculate total refund for a purchase return."""
    return float(pr.quantity * pr.return_price)


def validate_supplier_payment(supplier_id, amount, purchase_id=None, exclude_payment_id=None):
    """Validate supplier payment amount."""
    from salpurflask.models import SupplierPayment

    query = SupplierPayment.query.filter(
        SupplierPayment.supplier_id == supplier_id,
        SupplierPayment.is_reversed.is_(False),
    )
    if purchase_id:
        query = query.filter(SupplierPayment.purchase_id == purchase_id)
    if exclude_payment_id:
        query = query.filter(SupplierPayment.id != exclude_payment_id)
    total_paid = float(query.with_entities(db.func.sum(SupplierPayment.amount)).scalar() or 0.0)
    return total_paid, amount


# ─── PURCHASE ROUTES ──────────────────────────────────────────────────────────


@verified_required
def purchase():
    """Display purchases and allow creation of new purchases."""
    from app import validate_line_rows, purchase_total as app_purchase_total
    from app import record_audit

    search = request.args.get("search", "").strip()
    query = Purchase.query
    if search:
        query = query.join(Supplier).filter(Supplier.name.ilike(f"%{search}%"))
    purchases, pagination = get_paginated_results(
        query.order_by(Purchase.date.desc(), Purchase.id.desc())
    )
    suppliers = Supplier.query.order_by(Supplier.name).all()
    items = Item.query.order_by(Item.name).all()
    if request.method == "POST":
        if current_user.role not in ("admin", "manager"):
            flash("Access denied. Only managers and admins can add purchases.", "danger")
            return redirect(url_for("purchase"))
        supplier_id  = request.form.get("supplier_id", "").strip()
        date_str     = request.form.get("date", "").strip()
        notes        = request.form.get("notes", "").strip()
        item_ids     = request.form.getlist("item_id[]")
        quantities   = request.form.getlist("quantity[]")
        prices       = request.form.getlist("purchase_price[]")
        disc_types   = request.form.getlist("discount_type[]")
        disc_values  = request.form.getlist("discount_value[]")
        tax_pcts     = request.form.getlist("tax_percent[]")
        unit_ids     = request.form.getlist("unit_id[]")

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
        if not supplier_id or not date_str:
            flash("Supplier and date are required!", "danger")
        elif not rows:
            flash("At least one item is required!", "danger")
        elif row_error:
            flash(row_error, "danger")
        else:
            try:
                purchase_date = datetime.strptime(date_str, "%Y-%m-%d")
                first_iid, first_qty, first_price, first_d_type, first_d_val, first_tax = rows[0][0], rows[0][1], rows[0][2], rows[0][3], rows[0][4], rows[0][5]
                gross = int(first_qty) * float(first_price)
                disc_amt, tax_amt, _ = calc_discount_tax(gross, first_d_type or "percent", float(first_d_val or 0), float(first_tax or 0))
                pur = Purchase(
                    supplier_id=int(supplier_id),
                    item_id=int(first_iid),
                    quantity=int(first_qty),
                    purchase_price=float(first_price),
                    discount_type=first_d_type or "percent", discount_value=float(first_d_val or 0), discount_amount=disc_amt,
                    tax_percent=float(first_tax or 0), tax_amount=tax_amt,
                    date=purchase_date, notes=notes or None,
                )
                db.session.add(pur)
                db.session.flush()
                for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                    item_obj = get_item_locked(int(iid)) or abort(404)
                    qty_i = int(qty)
                    price_f = float(price)
                    d_val_f = float(d_val or 0)
                    tax_f = float(tax or 0)
                    gross = qty_i * price_f
                    disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                    unit_name, unit_factor = resolve_item_unit(item_obj, unit_key)
                    pi = PurchaseItem(
                        purchase_id=pur.id, item_id=int(iid),
                        quantity=qty_i, purchase_price=price_f,
                        discount_type=d_type or "percent", discount_value=d_val_f,
                        discount_amount=disc_amt, tax_percent=tax_f,
                        tax_amount=tax_amt, amount=net,
                        unit_name=unit_name, unit_factor=unit_factor,
                    )
                    db.session.add(pi)
                    item_add_stock(item_obj, qty_i * unit_factor, net - tax_amt)
                db.session.flush()
                db.session.refresh(pur)
                pur.invoice_no = allocate_document_number("purchase", pur.date)
                sync_supplier_purchase(pur)
                post_document("purchase", pur)
                db.session.commit()
                record_audit("create", "Purchase", pur.id,
                             f"Purchase {pur.invoice_no}, total {app_purchase_total(pur):,.2f}")
                flash(f"Purchase {pur.invoice_no} added successfully!", "success")
                return redirect(url_for("purchase"))
            except ValueError as e:
                flash(f"Invalid data: {e}", "danger")
    return render_template(
        "purchase.html",
        suppliers=suppliers,
        items=items,
        purchases=purchases,
        pagination=pagination,
        search=search,
        today=now_local().strftime("%Y-%m-%d"),
    )


@manager_required
def edit_purchase(id):
    """Edit an existing purchase."""
    from app import validate_line_rows, purchase_total as app_purchase_total
    from app import record_audit, remove_supplier_ledger_entry, recalculate_supplier_ledger

    pur = db.session.get(Purchase, id) or abort(404)
    assert_not_posted("purchase", pur.id, f"Purchase #{pur.id}")
    suppliers = Supplier.query.order_by(Supplier.name).all()
    items_all = Item.query.order_by(Item.name).all()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id", "").strip()
        date_str    = request.form.get("date", "").strip()
        notes       = request.form.get("notes", "").strip()
        item_ids    = request.form.getlist("item_id[]")
        quantities  = request.form.getlist("quantity[]")
        prices      = request.form.getlist("purchase_price[]")
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
        if not supplier_id or not date_str:
            flash("Supplier and date are required!", "danger")
        elif not rows:
            flash("At least one item is required!", "danger")
        elif row_error:
            flash(row_error, "danger")
        else:
            try:
                old_supplier_id = pur.supplier_id
                touched_items = {}
                for pi in pur.line_items:
                    old_item = get_item_locked(pi.item_id)
                    if old_item:
                        cost_removed = pi.amount - pi.tax_amount
                        item_remove_stock(old_item, line_base_qty(pi), cost_total=cost_removed)
                        touched_items[old_item.id] = old_item
                PurchaseItem.query.filter_by(purchase_id=pur.id).delete()
                pur.supplier_id    = int(supplier_id)
                pur.item_id        = int(rows[0][0])
                pur.quantity       = int(rows[0][1])
                pur.purchase_price = float(rows[0][2])
                first_d_type, first_d_val, first_tax = rows[0][3], rows[0][4], rows[0][5]
                gross = int(rows[0][1]) * float(rows[0][2])
                disc_amt, tax_amt, _ = calc_discount_tax(gross, first_d_type or "percent", float(first_d_val or 0), float(first_tax or 0))
                pur.discount_type  = first_d_type or "percent"
                pur.discount_value = float(first_d_val or 0)
                pur.discount_amount= disc_amt
                pur.tax_percent = float(first_tax or 0)
                pur.tax_amount = tax_amt
                pur.date           = datetime.strptime(date_str, "%Y-%m-%d")
                pur.notes          = notes or None
                for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                    item_obj = touched_items.get(int(iid)) or get_item_locked(int(iid)) or abort(404)
                    qty_i = int(qty)
                    price_f = float(price)
                    d_val_f = float(d_val or 0)
                    tax_f = float(tax or 0)
                    gross = qty_i * price_f
                    disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                    unit_name, unit_factor = resolve_item_unit(item_obj, unit_key)
                    pi = PurchaseItem(
                        purchase_id=pur.id, item_id=int(iid),
                        quantity=qty_i, purchase_price=price_f,
                        discount_type=d_type or "percent", discount_value=d_val_f,
                        discount_amount=disc_amt, tax_percent=tax_f,
                        tax_amount=tax_amt, amount=net,
                        unit_name=unit_name, unit_factor=unit_factor,
                    )
                    db.session.add(pi)
                    item_add_stock(item_obj, qty_i * unit_factor, net - tax_amt)
                    touched_items[item_obj.id] = item_obj

                negative_items = [it for it in touched_items.values() if it.stock < 0]
                if negative_items:
                    names = ", ".join(f"{it.name} ({it.stock})" for it in negative_items)
                    db.session.rollback()
                    flash(f"Cannot save — this change would make stock negative for: {names}", "danger")
                    return render_template("edit_purchase.html", purchase=pur, suppliers=suppliers, items=items_all)

                db.session.flush()
                db.session.refresh(pur)
                if old_supplier_id != int(supplier_id):
                    remove_supplier_ledger_entry("purchase", pur.id)
                    recalculate_supplier_ledger(old_supplier_id)
                sync_supplier_purchase(pur)
                post_document("purchase", pur)
                db.session.commit()
                record_audit("update", "Purchase", pur.id, f"Purchase #{pur.id} edited")
                flash("Purchase updated successfully!", "success")
                return redirect(url_for("purchase"))
            except ValueError as e:
                flash(f"Invalid data: {e}", "danger")
    return render_template("edit_purchase.html", purchase=pur, suppliers=suppliers, items=items_all)


@admin_required
def delete_purchase(id):
    """Delete a purchase."""
    from app import remove_supplier_ledger_entry, record_audit, recalculate_supplier_ledger

    pur = db.session.get(Purchase, id) or abort(404)
    assert_not_posted("purchase", pur.id, f"Purchase #{pur.id}")
    assert_not_numbered(pur, "Purchase")
    if pur.supplier_payments:
        flash("Cannot delete purchase with associated payments! Delete payments first.", "danger")
        return redirect(url_for("purchase"))
    if pur.returns:
        flash("Cannot delete purchase with associated returns! Delete returns first.", "danger")
        return redirect(url_for("purchase"))
    linked_po = PurchaseOrder.query.filter_by(converted_purchase_id=pur.id).first()
    if linked_po:
        flash(f"Cannot delete purchase — it was created from Purchase Order #{linked_po.id}.", "danger")
        return redirect(url_for("purchase"))
    for pi in pur.line_items:
        item_obj = db.session.get(Item, pi.item_id)
        if item_obj:
            cost_removed = pi.amount - pi.tax_amount
            item_remove_stock(item_obj, line_base_qty(pi), cost_total=cost_removed)
    audit_summary = f"Purchase #{pur.id} ({pur.supplier.name if pur.supplier else 'supplier'}) deleted"
    supplier_id = remove_supplier_ledger_entry("purchase", pur.id)
    db.session.delete(pur)
    db.session.commit()
    if supplier_id:
        recalculate_supplier_ledger(supplier_id)
        db.session.commit()
    record_audit("delete", "Purchase", id, audit_summary)
    flash("Purchase deleted successfully!", "success")
    return redirect(url_for("purchase"))


# ─── PURCHASE RETURN ROUTES ───────────────────────────────────────────────────


@verified_required
def purchase_return():
    """Display purchase returns and allow creation of new returns."""
    from app import remove_supplier_ledger_entry, recalculate_supplier_ledger

    search = request.args.get("search", "").strip()
    query = PurchaseReturn.query.order_by(PurchaseReturn.date.desc())
    if search:
        query = (
            query.join(Supplier, PurchaseReturn.supplier_id == Supplier.id)
            .join(Item, PurchaseReturn.item_id == Item.id)
            .filter((Supplier.name.ilike(f"%{search}%")) | (Item.name.ilike(f"%{search}%")))
        )
    returns, pagination = get_paginated_results(query)
    all_pis = (PurchaseItem.query.join(Purchase)
               .filter(Purchase.is_reversed.is_(False))
               .order_by(Purchase.date.desc(), PurchaseItem.id).all())
    items_available = [
        {"pi": pi, "remaining": pi.quantity - get_purchase_item_returned_qty(pi)}
        for pi in all_pis
        if pi.quantity - get_purchase_item_returned_qty(pi) > 0
    ]
    if request.method == "POST":
        pi_ids        = request.form.getlist("purchase_item_id[]")
        quantities    = request.form.getlist("quantity[]")
        return_prices = request.form.getlist("return_price[]")
        reasons       = request.form.getlist("reason[]")
        date_str      = request.form.get("date", "").strip()
        if not date_str:
            flash("Date is required!", "danger")
        elif not pi_ids:
            flash("At least one item row is required!", "danger")
        else:
            try:
                ret_date = datetime.strptime(date_str, "%Y-%m-%d")
                errors = []
                rows = []
                for idx, (pi_id, qty_s, price_s, reason_s) in enumerate(zip(pi_ids, quantities, return_prices, reasons), 1):
                    if not pi_id or not qty_s or not price_s:
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
                    pi = db.session.get(PurchaseItem, int(pi_id))
                    if not pi:
                        errors.append(f"Row {idx}: purchase item not found.")
                        continue
                    remaining = pi.quantity - get_purchase_item_returned_qty(pi)
                    if int(qty_s) > remaining:
                        errors.append(f"Row {idx} ({pi.item.name}): cannot return {qty_s}, only {remaining} remaining.")
                        continue
                    if pi.item and pi.item.stock < int(qty_s) * (pi.unit_factor or 1):
                        errors.append(f"Row {idx} ({pi.item.name}): only {pi.item.stock} in current stock, cannot return {qty_s}.")
                        continue
                    rows.append((pi, int(qty_s), price_f, reason_s.strip() or None))
                if errors:
                    for e in errors:
                        flash(e, "danger")
                else:
                    for pi, qty, price, reason_val in rows:
                        purchase = pi.purchase_header
                        item = get_item_locked(pi.item_id)
                        pr = PurchaseReturn(
                            purchase_id=purchase.id,
                            supplier_id=purchase.supplier_id,
                            item_id=pi.item_id,
                            quantity=qty,
                            return_price=price,
                            date=ret_date,
                            reason=reason_val,
                            unit_name=pi.unit_name, unit_factor=pi.unit_factor or 1,
                            purchase_item_id=pi.id,
                        )
                        if item:
                            pr.cost_removed = item_remove_stock(item, qty * (pi.unit_factor or 1))
                        db.session.add(pr)
                        db.session.flush()
                        sync_supplier_purchase_return(pr)
                        post_document("purchase_return", pr)
                    db.session.commit()
                    flash(f"{len(rows)} purchase return(s) recorded successfully!", "success")
                    return redirect(url_for("purchase_return"))
            except ValueError:
                flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    return render_template(
        "purchase_return.html",
        returns=returns,
        items_available=items_available,
        pagination=pagination,
        search=search,
        today=now_local().strftime("%Y-%m-%d"),
    )


@admin_required
def delete_purchase_return(id):
    """Delete a purchase return."""
    from app import remove_supplier_ledger_entry, recalculate_supplier_ledger

    pr = db.session.get(PurchaseReturn, id) or abort(404)
    assert_not_posted("purchase_return", pr.id, f"Purchase return #{pr.id}")
    item = get_item_locked(pr.item_id)
    if item:
        item_add_stock(item, line_base_qty(pr), cost_total=pr.cost_removed or 0)
    supplier_id = remove_supplier_ledger_entry("purchase_return", pr.id)
    db.session.delete(pr)
    db.session.commit()
    if supplier_id:
        recalculate_supplier_ledger(supplier_id)
        db.session.commit()
    flash("Purchase return deleted successfully!", "success")
    return redirect(url_for("purchase_return"))


# ─── PURCHASE INVOICE ROUTE ───────────────────────────────────────────────────


@verified_required
def purchase_invoice(id):
    """Display purchase invoice with payment details."""
    from app import get_payment_status
    from salpurflask.models import Supplier

    purchase = db.session.query(Purchase).filter_by(id=id).first() or abort(404)
    supplier_row = db.session.execute(
        db.text("SELECT id, name, contact, address FROM supplier WHERE id = :sid"),
        {"sid": purchase.supplier_id}
    ).fetchone()

    # Create a simple dict-like object with the data
    class SupplierData:
        def __init__(self, row):
            if row:
                self.id = row[0]
                self.name = row[1]
                self.contact = row[2]
                self.address = row[3]
            else:
                self.id = None
                self.name = None
                self.contact = None
                self.address = None

    supplier = SupplierData(supplier_row) if supplier_row else None

    paid     = get_purchase_paid(id)
    total    = purchase_total(purchase)
    status   = get_payment_status(total, paid)
    returned_qty = get_purchase_returned_qty(id)
    return render_template(
        "invoice_purchase.html",
        purchase=purchase,
        supplier=supplier,
        paid=paid,
        total=total,
        balance=total - paid,
        status=status,
        returned_qty=returned_qty,
    )


# ─── PURCHASE ORDER ROUTES ────────────────────────────────────────────────────


@manager_required
def purchase_orders():
    """List and create purchase orders."""
    from app import validate_line_rows

    search = request.args.get("search", "").strip()
    query = PurchaseOrder.query.join(Supplier)
    if search:
        query = query.filter(Supplier.name.ilike(f"%{search}%"))
    orders, pagination = get_paginated_results(
        query.order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.id.desc())
    )
    suppliers = Supplier.query.order_by(Supplier.name).all()
    items     = Item.query.order_by(Item.name).all()
    if request.method == "POST":
        supplier_id   = request.form.get("supplier_id", "").strip()
        order_date    = request.form.get("order_date", "").strip()
        expected_date = request.form.get("expected_date", "").strip()
        notes         = request.form.get("notes", "").strip()
        item_ids      = request.form.getlist("item_id[]")
        quantities    = request.form.getlist("quantity[]")
        prices        = request.form.getlist("purchase_price[]")
        unit_ids      = request.form.getlist("unit_id[]")
        rows = [(iid.strip(), qty.strip(), pr.strip(), unit_ids[i] if i < len(unit_ids) else "")
                for i, (iid, qty, pr) in enumerate(zip(item_ids, quantities, prices))
                if iid.strip() and qty.strip() and pr.strip()]
        row_error = validate_line_rows(rows) if rows else None
        if not supplier_id or not order_date:
            flash("Supplier and order date are required.", "danger")
        elif not rows:
            flash("At least one item is required.", "danger")
        elif row_error:
            flash(row_error, "danger")
        else:
            po = PurchaseOrder(
                supplier_id=int(supplier_id),
                order_date=datetime.strptime(order_date, "%Y-%m-%d"),
                expected_date=datetime.strptime(expected_date, "%Y-%m-%d") if expected_date else None,
                notes=notes or None,
            )
            db.session.add(po)
            db.session.flush()
            for iid, qty, price, unit_key in rows:
                item_obj = db.session.get(Item, int(iid)) or abort(404)
                unit_name, unit_factor = resolve_item_unit(item_obj, unit_key)
                db.session.add(PurchaseOrderItem(
                    po_id=po.id, item_id=int(iid),
                    quantity=int(qty), purchase_price=float(price),
                    unit_name=unit_name, unit_factor=unit_factor,
                ))
            db.session.commit()
            flash(f"Purchase Order #{po.id} created.", "success")
            return redirect(url_for("purchase_orders"))
    return render_template("purchase_orders.html",
        orders=orders, suppliers=suppliers, items=items,
        pagination=pagination, search=search,
        po_statuses=PO_STATUSES,
        today=now_local().strftime("%Y-%m-%d"))


@manager_required
def purchase_order_detail(id):
    """Display purchase order details."""
    po = db.session.get(PurchaseOrder, id) or abort(404)
    return render_template("purchase_order_detail.html", po=po, po_statuses=PO_STATUSES)


@manager_required
def update_po_status(id):
    """Update purchase order status."""
    po = db.session.get(PurchaseOrder, id) or abort(404)
    new_status = request.form.get("status", "").strip()
    if new_status not in PO_STATUSES:
        flash("Invalid status.", "danger")
    elif po.status in ("Received", "Cancelled"):
        flash("Cannot change status of a Received or Cancelled order.", "warning")
    else:
        po.status = new_status
        db.session.commit()
        flash(f"PO #{po.id} status updated to {new_status}.", "success")
    return redirect(url_for("purchase_order_detail", id=id))


@manager_required
def convert_po_to_purchase(id):
    """Convert purchase order to purchase document."""
    po = db.session.get(PurchaseOrder, id) or abort(404)
    if po.status == "Cancelled":
        flash("Cancelled orders cannot be converted.", "danger")
        return redirect(url_for("purchase_order_detail", id=id))
    if po.converted_purchase_id:
        flash(f"Already converted to Purchase #{po.converted_purchase_id}.", "warning")
        return redirect(url_for("purchase_order_detail", id=id))
    date_str = request.form.get("purchase_date", "").strip()
    notes    = request.form.get("notes", "").strip()
    try:
        pur_date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else now_local()
    except ValueError:
        pur_date = now_local()
    first = po.line_items[0] if po.line_items else None
    if not first:
        flash("PO has no line items.", "danger")
        return redirect(url_for("purchase_order_detail", id=id))
    pur = Purchase(
        supplier_id=po.supplier_id,
        item_id=first.item_id, quantity=first.quantity, purchase_price=first.purchase_price,
        discount_type="percent", discount_value=0, discount_amount=0,
        tax_percent=0, tax_amount=0,
        date=pur_date, notes=notes or po.notes,
    )
    db.session.add(pur)
    db.session.flush()
    for poi in po.line_items:
        gross = poi.quantity * poi.purchase_price
        db.session.add(PurchaseItem(
            purchase_id=pur.id, item_id=poi.item_id,
            quantity=poi.quantity, purchase_price=poi.purchase_price,
            discount_type="percent", discount_value=0,
            discount_amount=0, tax_percent=0, tax_amount=0, amount=gross,
            unit_name=poi.unit_name, unit_factor=poi.unit_factor or 1,
        ))
        item_obj = db.session.get(Item, poi.item_id)
        if item_obj:
            item_add_stock(item_obj, poi.quantity * (poi.unit_factor or 1), gross)
    db.session.flush()
    db.session.refresh(pur)
    pur.invoice_no = allocate_document_number("purchase", pur.date)
    sync_supplier_purchase(pur)
    post_document("purchase", pur)
    po.status = "Received"
    po.converted_purchase_id = pur.id
    db.session.commit()
    flash(f"PO #{po.id} converted to Purchase {pur.invoice_no} successfully.", "success")
    return redirect(url_for("purchase_order_detail", id=id))


@admin_required
def delete_purchase_order(id):
    """Delete a purchase order."""
    po = db.session.get(PurchaseOrder, id) or abort(404)
    if po.status == "Received":
        flash("Cannot delete a received order.", "danger")
        return redirect(url_for("purchase_orders"))
    db.session.delete(po)
    db.session.commit()
    flash(f"Purchase Order #{id} deleted.", "success")
    return redirect(url_for("purchase_orders"))


# ─── PURCHASE EXPORT & REPORT ROUTES ──────────────────────────────────────────


@manager_required
def export_purchase_report():
    """Export purchase history as CSV or Excel."""
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        purchase_items = (
            PurchaseItem.query.join(Purchase, PurchaseItem.purchase_id == Purchase.id)
            .join(Supplier, Purchase.supplier_id == Supplier.id)
            .join(Item, PurchaseItem.item_id == Item.id)
            .filter(Purchase.date.between(start_date, end_date))
            .order_by(Purchase.id)
            .all()
        )
        col_headers = ["ID", "Supplier", "Item", "Category", "Quantity", "Purchase Price", "Total", "Date"]
        rows = [
            [pi.purchase_id, pi.purchase_header.supplier.name, pi.item.name,
             pi.item.id_category.name if pi.item.id_category else "N/A",
             pi.base_quantity, round(float(pi.purchase_price), 2),
             round(float(pi.amount), 2), pi.purchase_header.date.strftime("%Y-%m-%d")]
            for pi in purchase_items
        ]
        if request.form.get("format") == "xlsx":
            return excel_response("purchase_report.xlsx", "Purchase History", col_headers, rows, start_date_str, end_date_str)
        return csv_response("purchase_report.csv", "Purchase History", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))


@manager_required
def export_purchase_return_report():
    """Export purchase return history as CSV or Excel."""
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        returns = PurchaseReturn.query.filter(PurchaseReturn.date.between(start_date, end_date)).order_by(PurchaseReturn.date.desc()).all()
        col_headers = ["ID", "Purchase #", "Supplier", "Item", "Quantity", "Return Price", "Total", "Date", "Reason"]
        rows = [
            [r.id, r.purchase_id, r.supplier.name, r.item.name,
             r.quantity, round(r.return_price, 2), round(r.quantity * r.return_price, 2),
             r.date.strftime("%Y-%m-%d"), r.reason or ""]
            for r in returns
        ]
        if request.form.get("format") == "xlsx":
            return excel_response("purchase_return_report.xlsx", "Purchase Returns Report", col_headers, rows, start_date_str, end_date_str)
        return csv_response("purchase_return_report.csv", "Purchase Returns Report", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))


@manager_required
def export_supplier_purchase_report():
    """Export supplier-wise purchase summary as CSV or Excel."""
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        data = (
            db.session.query(
                Supplier.name.label("name"),
                db.func.count(db.func.distinct(Purchase.id)).label("bill_count"),
                db.func.sum(PurchaseItem.quantity * PurchaseItem.unit_factor).label("total_qty"),
                db.func.sum(PurchaseItem.amount).label("total_amt"),
            )
            .select_from(PurchaseItem)
            .join(Purchase, PurchaseItem.purchase_id == Purchase.id)
            .join(Supplier, Purchase.supplier_id == Supplier.id)
            .filter(Purchase.date.between(start_date, end_date))
            .group_by(Supplier.name)
            .order_by(db.func.sum(PurchaseItem.amount).desc())
            .all()
        )
        col_headers = ["Supplier", "Bills", "Total Qty", "Total Amount"]
        rows = [[row.name, row.bill_count, row.total_qty, round(row.total_amt, 2)] for row in data]
        if request.form.get("format") == "xlsx":
            return excel_response("supplier_purchase_report.xlsx", "Supplier-wise Purchase Report", col_headers, rows, start_date_str, end_date_str)
        return csv_response("supplier_purchase_report.csv", "Supplier-wise Purchase Report", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))
