"""Customer management routes."""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import Customer, CustomerLedgerEntry
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import get_paginated_results, csv_response, excel_response, valid_phone


# ─── CUSTOMER CRUD ROUTES ─────────────────────────────────────────────────


@verified_required
def customer():
    """Display customers and allow creation of new customers."""
    from app import record_audit, sync_customer_opening, post_customer_opening, get_customer_balance

    search = request.args.get("search", "")
    query = Customer.query.filter(Customer.name.ilike(f"%{search}%")) if search else Customer.query
    customers, pagination = get_paginated_results(query)
    if request.method == "POST":
        if current_user.role not in ("admin", "manager"):
            flash("You do not have permission to add customers.", "danger")
            return redirect(url_for("customer"))
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip()
        address = request.form.get("address", "").strip()
        opening_str = request.form.get("opening_balance", "0").strip()
        opening_balance = 0.0
        if opening_str:
            if not opening_str.replace("-", "", 1).replace(".", "", 1).isdigit():
                flash("Opening balance must be a valid number!", "danger")
                return render_template("customer.html", customers=customers, pagination=pagination, search=search)
            opening_balance = float(opening_str)
        if not name or not contact or not address:
            flash("All fields are required!", "danger")
        elif not valid_phone(contact):
            flash("Enter a valid phone number (7-15 digits; + - ( ) and spaces are fine).", "danger")
        elif Customer.query.filter_by(name=name, contact=contact, address=address).first():
            flash("Customer already exists!", "warning")
            return redirect(url_for("customer"))
        else:
            customer = Customer(name=name, contact=contact, address=address, opening_balance=opening_balance)
            db.session.add(customer)
            db.session.flush()
            sync_customer_opening(customer)
            post_customer_opening(customer)
            db.session.commit()
            record_audit("create", "Customer", customer.id, f"Customer '{customer.name}' added")
            flash("Customer added successfully!", "success")
            return redirect(url_for("customer"))
    return render_template("customer.html", customers=customers, pagination=pagination, search=search)


@manager_required
def edit_customer(id):
    """Edit an existing customer."""
    from app import record_audit, sync_customer_opening, post_customer_opening

    customer = db.session.get(Customer, id) or abort(404)
    if request.method == "POST":
        customer.name = request.form.get("name", "").strip()
        customer.contact = request.form.get("contact", "").strip()
        customer.address = request.form.get("address", "").strip()
        opening_str = request.form.get("opening_balance", "0").strip()
        if opening_str and not opening_str.replace("-", "", 1).replace(".", "", 1).isdigit():
            flash("Opening balance must be a valid number!", "danger")
        elif not customer.name or not customer.contact or not customer.address:
            flash("All fields are required!", "danger")
        elif not valid_phone(customer.contact):
            flash("Enter a valid phone number (7-15 digits; + - ( ) and spaces are fine).", "danger")
        else:
            customer.opening_balance = float(opening_str or 0)
            sync_customer_opening(customer)
            post_customer_opening(customer)
            db.session.commit()
            record_audit("update", "Customer", customer.id, f"Customer '{customer.name}' edited")
            flash("Customer updated successfully!", "success")
            return redirect(url_for("customer"))
    return render_template("edit_customer.html", customer=customer)


@admin_required
def delete_customer(id):
    """Delete a customer."""
    from app import record_audit

    customer = db.session.get(Customer, id) or abort(404)
    if customer.sales:
        flash("Cannot delete customer with associated sales!", "danger")
    elif customer.receipts:
        flash("Cannot delete customer with associated receipts!", "danger")
    else:
        cust_name = customer.name
        CustomerLedgerEntry.query.filter_by(customer_id=id).delete()
        db.session.delete(customer)
        db.session.commit()
        record_audit("delete", "Customer", id, f"Customer '{cust_name}' deleted")
        flash("Customer deleted successfully!", "success")
    return redirect(url_for("customer"))


@manager_required
def export_customers():
    """Export customers list (CSV)."""
    from app import get_customer_balance

    customers = Customer.query.order_by(Customer.name).all()
    rows = [
        [c.id, c.name, c.contact, c.address,
         round(float(c.opening_balance or 0), 2),
         round(get_customer_balance(c.id), 2)]
        for c in customers
    ]
    return csv_response("customers.csv", "Customers List",
                         ["ID", "Name", "Contact", "Address", "Opening Balance", "Current Balance"], rows)


@manager_required
def export_customers_excel():
    """Export customers list (XLSX)."""
    from app import get_customer_balance, get_customer_receivable, get_customer_received

    customers = Customer.query.order_by(Customer.name).all()
    rows = [
        [c.id, c.name, c.contact, c.address,
         round(float(c.opening_balance or 0), 2),
         round(get_customer_receivable(c.id), 2),
         round(get_customer_received(c.id), 2),
         round(get_customer_balance(c.id), 2)]
        for c in customers
    ]
    return excel_response(
        filename="customers.xlsx",
        title="Customers List",
        col_headers=["ID", "Name", "Contact", "Address", "Opening Balance", "Sales", "Received", "Ledger Balance"],
        rows=rows,
    )
