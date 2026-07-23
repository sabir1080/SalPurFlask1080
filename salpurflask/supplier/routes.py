"""Supplier management routes."""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import Supplier, SupplierLedgerEntry
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import get_paginated_results, csv_response, excel_response, valid_phone


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
