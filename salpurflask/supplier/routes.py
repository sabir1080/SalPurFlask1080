"""Supplier management routes."""

from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import Supplier, SupplierLedgerEntry, SupplierPayment, Purchase
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import (
    get_paginated_results, csv_response, excel_response, valid_phone, now_local
)


# ─── SUPPLIER CRUD ROUTES ─────────────────────────────────────────────────


@verified_required
def supplier():
    """Display suppliers and allow creation of new suppliers."""
    from app import record_audit, sync_supplier_opening, post_supplier_opening, get_supplier_balance

    search = request.args.get("search", "")
    query = Supplier.query.filter(Supplier.name.ilike(f"%{search}%")) if search else Supplier.query
    suppliers, pagination = get_paginated_results(query)
    if request.method == "POST":
        if current_user.role not in ("admin", "manager"):
            flash("You do not have permission to add suppliers.", "danger")
            return redirect(url_for("supplier"))
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip()
        address = request.form.get("address", "").strip()
        opening_str = request.form.get("opening_balance", "0").strip()
        opening_balance = 0.0
        if opening_str:
            if not opening_str.replace("-", "", 1).replace(".", "", 1).isdigit():
                flash("Opening balance must be a valid number!", "danger")
                return render_template("supplier.html", suppliers=suppliers, pagination=pagination, search=search)
            opening_balance = float(opening_str)
        if not name or not contact or not address:
            flash("All fields are required!", "danger")
        elif not valid_phone(contact):
            flash("Enter a valid phone number (7-15 digits; + - ( ) and spaces are fine).", "danger")
        elif Supplier.query.filter_by(name=name, contact=contact, address=address).first():
            flash("Supplier already exists!", "warning")
            return redirect(url_for("supplier"))
        else:
            supplier = Supplier(name=name, contact=contact, address=address, opening_balance=opening_balance)
            db.session.add(supplier)
            db.session.flush()
            sync_supplier_opening(supplier)
            post_supplier_opening(supplier)
            db.session.commit()
            record_audit("create", "Supplier", supplier.id, f"Supplier '{supplier.name}' added")
            flash("Supplier added successfully!", "success")
            return redirect(url_for("supplier"))
    return render_template("supplier.html", suppliers=suppliers, pagination=pagination, search=search)


@manager_required
def edit_supplier(id):
    """Edit an existing supplier."""
    from app import record_audit, sync_supplier_opening, post_supplier_opening

    supplier = db.session.get(Supplier, id) or abort(404)
    if request.method == "POST":
        supplier.name = request.form.get("name", "").strip()
        supplier.contact = request.form.get("contact", "").strip()
        supplier.address = request.form.get("address", "").strip()
        opening_str = request.form.get("opening_balance", "0").strip()
        if opening_str and not opening_str.replace("-", "", 1).replace(".", "", 1).isdigit():
            flash("Opening balance must be a valid number!", "danger")
        elif not supplier.name or not supplier.contact or not supplier.address:
            flash("All fields are required!", "danger")
        elif not valid_phone(supplier.contact):
            flash("Enter a valid phone number (7-15 digits; + - ( ) and spaces are fine).", "danger")
        else:
            supplier.opening_balance = float(opening_str or 0)
            sync_supplier_opening(supplier)
            post_supplier_opening(supplier)
            db.session.commit()
            record_audit("update", "Supplier", supplier.id, f"Supplier '{supplier.name}' edited")
            flash("Supplier updated successfully!", "success")
            return redirect(url_for("supplier"))
    return render_template("edit_supplier.html", supplier=supplier)


@admin_required
def delete_supplier(id):
    """Delete a supplier."""
    from app import record_audit

    supplier = db.session.get(Supplier, id) or abort(404)
    if supplier.purchases:
        flash("Cannot delete supplier with associated purchases!", "danger")
    elif supplier.payments:
        flash("Cannot delete supplier with associated payments!", "danger")
    else:
        sup_name = supplier.name
        SupplierLedgerEntry.query.filter_by(supplier_id=id).delete()
        db.session.delete(supplier)
        db.session.commit()
        record_audit("delete", "Supplier", id, f"Supplier '{sup_name}' deleted")
        flash("Supplier deleted successfully!", "success")
    return redirect(url_for("supplier"))


@manager_required
def export_suppliers():
    """Export suppliers list (CSV)."""
    from app import get_supplier_balance

    suppliers = Supplier.query.order_by(Supplier.name).all()
    rows = [
        [s.id, s.name, s.contact, s.address,
         round(float(s.opening_balance or 0), 2),
         round(get_supplier_balance(s.id), 2)]
        for s in suppliers
    ]
    return csv_response("suppliers.csv", "Suppliers List",
                         ["ID", "Name", "Contact", "Address", "Opening Balance", "Current Balance"], rows)


@manager_required
def export_suppliers_excel():
    """Export suppliers list (XLSX)."""
    from app import get_supplier_balance, get_supplier_payable, get_supplier_paid

    suppliers = Supplier.query.order_by(Supplier.name).all()
    rows = [
        [s.id, s.name, s.contact, s.address,
         round(float(s.opening_balance or 0), 2),
         round(get_supplier_payable(s.id), 2),
         round(get_supplier_paid(s.id), 2),
         round(get_supplier_balance(s.id), 2)]
        for s in suppliers
    ]
    return excel_response(
        filename="suppliers.xlsx",
        title="Suppliers List",
        col_headers=["ID", "Name", "Contact", "Address", "Opening Balance", "Bills", "Paid", "Ledger Balance"],
        rows=rows,
    )


# ─── SUPPLIER PAYMENT ROUTES ───────────────────────────────────────────────


@verified_required
def supplier_payment():
    """Display supplier payments and allow creation of new payments."""
    from app import (
        record_audit, sync_supplier_payment, post_document, validate_supplier_payment,
        parse_payment_amount, parse_account_id, PAYMENT_METHODS
    )

    search = request.args.get("search", "").strip()
    query = SupplierPayment.query.join(Supplier)
    if search:
        query = query.filter(
            (Supplier.name.ilike(f"%{search}%"))
            | (SupplierPayment.reference_no.ilike(f"%{search}%"))
            | (SupplierPayment.notes.ilike(f"%{search}%"))
        )
    payments, pagination = get_paginated_results(query.order_by(SupplierPayment.payment_date.desc()))
    suppliers = Supplier.query.order_by(Supplier.name).all()
    purchases = Purchase.query.order_by(Purchase.date.desc()).all()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id", "").strip()
        purchase_id = request.form.get("purchase_id", "").strip() or None
        amount_str = request.form.get("amount", "").strip()
        payment_date = request.form.get("payment_date", "").strip()
        payment_method = request.form.get("payment_method", "Cash").strip()
        reference_no = request.form.get("reference_no", "").strip()
        notes = request.form.get("notes", "").strip()
        amount = parse_payment_amount(amount_str)
        account_id, account_error = parse_account_id(request.form.get("account_id"))
        if not supplier_id or not payment_date or amount is None:
            flash("Supplier, amount and payment date are required!", "danger")
        elif payment_method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
        elif account_error:
            flash(account_error, "danger")
        else:
            error = validate_supplier_payment(supplier_id, amount, purchase_id)
            if error:
                flash(error, "danger")
            else:
                payment = SupplierPayment(
                    supplier_id=int(supplier_id),
                    purchase_id=int(purchase_id) if purchase_id else None,
                    amount=amount,
                    payment_date=datetime.strptime(payment_date, "%Y-%m-%d"),
                    payment_method=payment_method,
                    account_id=account_id,
                    reference_no=reference_no or None,
                    notes=notes or None,
                )
                db.session.add(payment)
                db.session.flush()
                sync_supplier_payment(payment)
                post_document("payment", payment)
                db.session.commit()
                record_audit("create", "SupplierPayment", payment.id, f"Paid {float(payment.amount):,.2f} to supplier #{payment.supplier_id} ({payment.payment_method})")
                flash("Supplier payment recorded successfully!", "success")
                return redirect(url_for("supplier_payment"))
    return render_template(
        "supplier_payment.html",
        payments=payments,
        suppliers=suppliers,
        purchases=purchases,
        pagination=pagination,
        search=search,
    )


@manager_required
def edit_supplier_payment(id):
    """Edit an existing supplier payment."""
    from app import (
        record_audit, sync_supplier_payment, post_document, validate_supplier_payment,
        remove_supplier_ledger_entry, recalculate_supplier_ledger, assert_not_posted,
        parse_payment_amount, parse_account_id, PAYMENT_METHODS
    )

    payment = db.session.get(SupplierPayment, id) or abort(404)
    assert_not_posted("payment", payment.id, f"Payment #{payment.id}")
    suppliers = Supplier.query.order_by(Supplier.name).all()
    purchases = Purchase.query.order_by(Purchase.date.desc()).all()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id", "").strip()
        purchase_id = request.form.get("purchase_id", "").strip() or None
        amount_str = request.form.get("amount", "").strip()
        payment_date = request.form.get("payment_date", "").strip()
        payment_method = request.form.get("payment_method", "Cash").strip()
        reference_no = request.form.get("reference_no", "").strip()
        notes = request.form.get("notes", "").strip()
        amount = parse_payment_amount(amount_str)
        account_id, account_error = parse_account_id(request.form.get("account_id"))
        if not supplier_id or not payment_date or amount is None:
            flash("Supplier, amount and payment date are required!", "danger")
        elif payment_method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
        elif account_error:
            flash(account_error, "danger")
        else:
            error = validate_supplier_payment(supplier_id, amount, purchase_id, exclude_payment_id=payment.id)
            if error:
                flash(error, "danger")
            else:
                old_supplier_id = payment.supplier_id
                payment.supplier_id = int(supplier_id)
                payment.purchase_id = int(purchase_id) if purchase_id else None
                payment.amount = amount
                payment.payment_date = datetime.strptime(payment_date, "%Y-%m-%d")
                payment.payment_method = payment_method
                payment.account_id = account_id
                payment.reference_no = reference_no or None
                payment.notes = notes or None
                if old_supplier_id != int(supplier_id):
                    remove_supplier_ledger_entry("payment", payment.id)
                    recalculate_supplier_ledger(old_supplier_id)
                sync_supplier_payment(payment)
                post_document("payment", payment)
                db.session.commit()
                record_audit("update", "SupplierPayment", payment.id, f"Supplier payment #{payment.id} edited (amount {float(payment.amount):,.2f})")
                flash("Supplier payment updated successfully!", "success")
                return redirect(url_for("supplier_payment"))
    return render_template(
        "edit_supplier_payment.html",
        payment=payment,
        suppliers=suppliers,
        purchases=purchases,
    )


@admin_required
def delete_supplier_payment(id):
    """Delete a supplier payment."""
    from app import (
        record_audit, remove_supplier_ledger_entry, recalculate_supplier_ledger, assert_not_posted
    )

    payment = db.session.get(SupplierPayment, id) or abort(404)
    assert_not_posted("payment", payment.id, f"Payment #{payment.id}")
    audit_summary = f"Supplier payment #{payment.id} of {float(payment.amount):,.2f} deleted"
    supplier_id = remove_supplier_ledger_entry("payment", payment.id)
    db.session.delete(payment)
    db.session.commit()
    if supplier_id:
        recalculate_supplier_ledger(supplier_id)
        db.session.commit()
    record_audit("delete", "SupplierPayment", id, audit_summary)
    flash("Supplier payment deleted successfully!", "success")
    return redirect(url_for("supplier_payment"))


@verified_required
def supplier_bulk_payment():
    """Bulk payment processing for multiple supplier invoices."""
    from app import (
        record_audit, sync_supplier_payment, post_document, validate_supplier_payment,
        purchase_total, get_purchase_paid, parse_payment_amount, parse_account_id, PAYMENT_METHODS
    )

    suppliers = Supplier.query.order_by(Supplier.name).all()
    supplier_id = request.args.get("supplier_id", "").strip()
    selected_supplier = None
    outstanding = []

    bulk_amount_str = request.args.get("bulk_amount", "").strip()
    bulk_amount_val = ""
    general_suggested = 0.0

    if supplier_id:
        selected_supplier = db.session.get(Supplier, int(supplier_id))
        if selected_supplier:
            all_purchases = Purchase.query.filter_by(
                supplier_id=selected_supplier.id
            ).order_by(Purchase.date).all()
            for p in all_purchases:
                p_total = purchase_total(p)
                p_paid  = get_purchase_paid(p.id)
                p_due   = round(p_total - p_paid, 2)
                if p_due > 0:
                    outstanding.append({"p": p, "total": p_total, "paid": p_paid, "due": p_due})

            if bulk_amount_str:
                try:
                    bulk_amount_val = float(bulk_amount_str)
                    remaining = bulk_amount_val
                    for row in outstanding:
                        if remaining <= 0:
                            row["suggested"] = 0.0
                        elif remaining >= row["due"]:
                            row["suggested"] = row["due"]
                            remaining -= row["due"]
                        else:
                            row["suggested"] = round(remaining, 2)
                            remaining = 0.0
                    general_suggested = round(max(0.0, remaining), 2)
                except ValueError:
                    bulk_amount_val = ""
            if not bulk_amount_val:
                for row in outstanding:
                    row["suggested"] = row["due"]

    if request.method == "POST":
        sup_id       = request.form.get("supplier_id", "").strip()
        date_str     = request.form.get("payment_date", "").strip()
        method       = request.form.get("payment_method", "Cash").strip()
        reference_no = request.form.get("reference_no", "").strip()
        notes        = request.form.get("notes", "").strip()
        purch_ids    = request.form.getlist("purchase_id[]")
        amounts      = request.form.getlist("amount[]")
        gen_amt_str  = request.form.get("general_amount", "").strip()
        account_id, account_error = parse_account_id(request.form.get("account_id"))

        if not sup_id or not date_str:
            flash("Supplier and payment date are required!", "danger")
        elif method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
        elif account_error:
            flash(account_error, "danger")
        else:
            try:
                pay_date = datetime.strptime(date_str, "%Y-%m-%d")
                rows = []
                errors = []
                for pid, amt_s in zip(purch_ids, amounts):
                    amt_s = amt_s.strip()
                    if not amt_s or float(amt_s) <= 0:
                        continue
                    try:
                        amt = float(amt_s)
                    except ValueError:
                        errors.append(f"Invalid amount for purchase #{pid}.")
                        continue
                    row_error = validate_supplier_payment(sup_id, amt, pid)
                    if row_error:
                        errors.append(f"Purchase #{pid}: {row_error}")
                        continue
                    rows.append((int(pid), amt))

                gen_amt = 0.0
                if gen_amt_str:
                    try:
                        gen_amt = float(gen_amt_str)
                    except ValueError:
                        errors.append("Invalid general payment amount.")

                if errors:
                    for e in errors:
                        flash(e, "danger")
                elif not rows and gen_amt <= 0:
                    flash("Please enter at least one payment amount.", "danger")
                else:
                    count = 0
                    total_paid_sum = 0.0
                    for pid, amt in rows:
                        pmt = SupplierPayment(
                            supplier_id=int(sup_id),
                            purchase_id=pid,
                            amount=amt,
                            payment_date=pay_date,
                            payment_method=method,
                            account_id=account_id,
                            reference_no=reference_no or None,
                            notes=notes or None,
                        )
                        db.session.add(pmt)
                        db.session.flush()
                        sync_supplier_payment(pmt)
                        post_document("payment", pmt)
                        count += 1
                        total_paid_sum += amt
                    if gen_amt > 0:
                        pmt = SupplierPayment(
                            supplier_id=int(sup_id),
                            purchase_id=None,
                            amount=gen_amt,
                            payment_date=pay_date,
                            payment_method=method,
                            account_id=account_id,
                            reference_no=reference_no or None,
                            notes=notes or None,
                        )
                        db.session.add(pmt)
                        db.session.flush()
                        sync_supplier_payment(pmt)
                        post_document("payment", pmt)
                        count += 1
                        total_paid_sum += gen_amt
                    db.session.commit()
                    flash(
                        f"Bulk payment saved: {count} payment(s) totalling {total_paid_sum:,.2f}.",
                        "success",
                    )
                    return redirect(url_for("supplier_payment"))
            except ValueError as e:
                flash(f"Invalid data: {e}", "danger")

    return render_template(
        "supplier_bulk_payment.html",
        suppliers=suppliers,
        selected_supplier=selected_supplier,
        outstanding=outstanding,
        bulk_amount_val=bulk_amount_val,
        general_suggested=general_suggested,
        today=now_local().strftime("%Y-%m-%d"),
    )
