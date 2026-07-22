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
)
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import (
    now_local, get_paginated_results, csv_response, excel_response
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
    from app import validate_line_rows, get_item_locked, purchase_total as app_purchase_total
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
                first_iid, first_qty, first_price = rows[0][0], rows[0][1], rows[0][2]
                pur = Purchase(
                    supplier_id=int(supplier_id),
                    item_id=int(first_iid),
                    quantity=int(first_qty),
                    purchase_price=float(first_price),
                    discount_type="percent", discount_value=0, discount_amount=0,
                    tax_percent=0, tax_amount=0,
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
    from app import validate_line_rows, get_item_locked, purchase_total as app_purchase_total
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
                pur.discount_type  = "percent"
                pur.discount_value = 0
                pur.discount_amount= 0
                pur.tax_percent = 0
                pur.tax_amount = 0
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
    audit_summary = f"Purchase #{pur.id} ({pur.id_supplier.name if pur.id_supplier else 'supplier'}) deleted"
    supplier_id = remove_supplier_ledger_entry("purchase", pur.id)
    db.session.delete(pur)
    db.session.commit()
    if supplier_id:
        recalculate_supplier_ledger(supplier_id)
        db.session.commit()
    record_audit("delete", "Purchase", id, audit_summary)
    flash("Purchase deleted successfully!", "success")
    return redirect(url_for("purchase"))
