"""Customer management routes."""

from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from salpurflask.extensions import db
from salpurflask.models import Customer, CustomerLedgerEntry, CustomerPayment, Sale
from salpurflask.auth import verified_required, manager_required, admin_required
from salpurflask.utils import (
    get_paginated_results, csv_response, excel_response, valid_phone, now_local
)


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


# ─── CUSTOMER RECEIPT ROUTES ───────────────────────────────────────────────


@verified_required
def customer_receipt():
    """Display customer receipts and allow creation of new receipts."""
    from app import (
        record_audit, sync_customer_receipt, post_document, validate_customer_receipt,
        parse_payment_amount, parse_account_id, PAYMENT_METHODS
    )

    search = request.args.get("search", "").strip()
    query = CustomerPayment.query.join(Customer)
    if search:
        query = query.filter(
            (Customer.name.ilike(f"%{search}%"))
            | (CustomerPayment.reference_no.ilike(f"%{search}%"))
            | (CustomerPayment.notes.ilike(f"%{search}%"))
        )
    receipts, pagination = get_paginated_results(query.order_by(CustomerPayment.payment_date.desc()))
    customers = Customer.query.order_by(Customer.name).all()
    sales = Sale.query.order_by(Sale.date.desc()).all()
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "").strip()
        sale_id = request.form.get("sale_id", "").strip() or None
        amount_str = request.form.get("amount", "").strip()
        payment_date = request.form.get("payment_date", "").strip()
        payment_method = request.form.get("payment_method", "Cash").strip()
        reference_no = request.form.get("reference_no", "").strip()
        notes = request.form.get("notes", "").strip()
        amount = parse_payment_amount(amount_str)
        account_id, account_error = parse_account_id(request.form.get("account_id"))
        if not customer_id or not payment_date or amount is None:
            flash("Customer, amount and receipt date are required!", "danger")
        elif payment_method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
        elif account_error:
            flash(account_error, "danger")
        else:
            error = validate_customer_receipt(customer_id, amount, sale_id)
            if error:
                flash(error, "danger")
            else:
                receipt = CustomerPayment(
                    customer_id=int(customer_id),
                    sale_id=int(sale_id) if sale_id else None,
                    amount=amount,
                    payment_date=datetime.strptime(payment_date, "%Y-%m-%d"),
                    payment_method=payment_method,
                    account_id=account_id,
                    reference_no=reference_no or None,
                    notes=notes or None,
                )
                db.session.add(receipt)
                db.session.flush()
                sync_customer_receipt(receipt)
                post_document("receipt", receipt)
                db.session.commit()
                record_audit("create", "CustomerReceipt", receipt.id, f"Received {float(receipt.amount):,.2f} from customer #{receipt.customer_id} ({receipt.payment_method})")
                flash("Customer receipt recorded successfully!", "success")
                return redirect(url_for("customer_receipt"))
    return render_template(
        "customer_receipt.html",
        receipts=receipts,
        customers=customers,
        sales=sales,
        pagination=pagination,
        search=search,
    )


@manager_required
def edit_customer_receipt(id):
    """Edit an existing customer receipt."""
    from app import (
        record_audit, sync_customer_receipt, post_document, validate_customer_receipt,
        remove_customer_ledger_entry, recalculate_customer_ledger, assert_not_posted,
        parse_payment_amount, parse_account_id, PAYMENT_METHODS
    )

    receipt = db.session.get(CustomerPayment, id) or abort(404)
    assert_not_posted("receipt", receipt.id, f"Receipt #{receipt.id}")
    customers = Customer.query.order_by(Customer.name).all()
    sales = Sale.query.order_by(Sale.date.desc()).all()
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "").strip()
        sale_id = request.form.get("sale_id", "").strip() or None
        amount_str = request.form.get("amount", "").strip()
        payment_date = request.form.get("payment_date", "").strip()
        payment_method = request.form.get("payment_method", "Cash").strip()
        reference_no = request.form.get("reference_no", "").strip()
        notes = request.form.get("notes", "").strip()
        amount = parse_payment_amount(amount_str)
        account_id, account_error = parse_account_id(request.form.get("account_id"))
        if not customer_id or not payment_date or amount is None:
            flash("Customer, amount and receipt date are required!", "danger")
        elif payment_method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
        elif account_error:
            flash(account_error, "danger")
        else:
            error = validate_customer_receipt(customer_id, amount, sale_id, exclude_payment_id=receipt.id)
            if error:
                flash(error, "danger")
            else:
                old_customer_id = receipt.customer_id
                receipt.customer_id = int(customer_id)
                receipt.sale_id = int(sale_id) if sale_id else None
                receipt.amount = amount
                receipt.payment_date = datetime.strptime(payment_date, "%Y-%m-%d")
                receipt.payment_method = payment_method
                receipt.account_id = account_id
                receipt.reference_no = reference_no or None
                receipt.notes = notes or None
                if old_customer_id != int(customer_id):
                    remove_customer_ledger_entry("receipt", receipt.id)
                    recalculate_customer_ledger(old_customer_id)
                sync_customer_receipt(receipt)
                post_document("receipt", receipt)
                db.session.commit()
                record_audit("update", "CustomerReceipt", receipt.id, f"Customer receipt #{receipt.id} edited (amount {float(receipt.amount):,.2f})")
                flash("Customer receipt updated successfully!", "success")
                return redirect(url_for("customer_receipt"))
    return render_template(
        "edit_customer_receipt.html",
        receipt=receipt,
        customers=customers,
        sales=sales,
    )


@admin_required
def delete_customer_receipt(id):
    """Delete a customer receipt."""
    from app import (
        record_audit, remove_customer_ledger_entry, recalculate_customer_ledger, assert_not_posted
    )

    receipt = db.session.get(CustomerPayment, id) or abort(404)
    assert_not_posted("receipt", receipt.id, f"Receipt #{receipt.id}")
    audit_summary = f"Customer receipt #{receipt.id} of {float(receipt.amount):,.2f} deleted"
    customer_id = remove_customer_ledger_entry("receipt", receipt.id)
    db.session.delete(receipt)
    db.session.commit()
    if customer_id:
        recalculate_customer_ledger(customer_id)
        db.session.commit()
    record_audit("delete", "CustomerReceipt", id, audit_summary)
    flash("Customer receipt deleted successfully!", "success")
    return redirect(url_for("customer_receipt"))


@verified_required
def customer_bulk_receipt():
    """Bulk receipt processing for multiple customer invoices."""
    from app import (
        record_audit, sync_customer_receipt, post_document, validate_customer_receipt,
        sale_total, get_sale_received, parse_payment_amount, parse_account_id, PAYMENT_METHODS
    )

    customers = Customer.query.order_by(Customer.name).all()
    customer_id = request.args.get("customer_id", "").strip()
    selected_customer = None
    outstanding = []

    bulk_amount_str = request.args.get("bulk_amount", "").strip()
    bulk_amount_val = ""
    general_suggested = 0.0

    if customer_id:
        selected_customer = db.session.get(Customer, int(customer_id))
        if selected_customer:
            all_sales = Sale.query.filter_by(
                customer_id=selected_customer.id
            ).order_by(Sale.date).all()
            for s in all_sales:
                s_total    = sale_total(s)
                s_received = get_sale_received(s.id)
                s_due      = round(s_total - s_received, 2)
                if s_due > 0:
                    outstanding.append({"s": s, "total": s_total, "received": s_received, "due": s_due})

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
        cust_id      = request.form.get("customer_id", "").strip()
        date_str     = request.form.get("payment_date", "").strip()
        method       = request.form.get("payment_method", "Cash").strip()
        reference_no = request.form.get("reference_no", "").strip()
        notes        = request.form.get("notes", "").strip()
        sale_ids     = request.form.getlist("sale_id[]")
        amounts      = request.form.getlist("amount[]")
        gen_amt_str  = request.form.get("general_amount", "").strip()
        account_id, account_error = parse_account_id(request.form.get("account_id"))

        if not cust_id or not date_str:
            flash("Customer and receipt date are required!", "danger")
        elif method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
        elif account_error:
            flash(account_error, "danger")
        else:
            try:
                pay_date = datetime.strptime(date_str, "%Y-%m-%d")
                rows = []
                errors = []
                for sid, amt_s in zip(sale_ids, amounts):
                    amt_s = amt_s.strip()
                    if not amt_s or float(amt_s) <= 0:
                        continue
                    try:
                        amt = float(amt_s)
                    except ValueError:
                        errors.append(f"Invalid amount for sale #{sid}.")
                        continue
                    row_error = validate_customer_receipt(cust_id, amt, sid)
                    if row_error:
                        errors.append(f"Sale #{sid}: {row_error}")
                        continue
                    rows.append((int(sid), amt))

                gen_amt = 0.0
                if gen_amt_str:
                    try:
                        gen_amt = float(gen_amt_str)
                    except ValueError:
                        errors.append("Invalid general receipt amount.")

                if errors:
                    for e in errors:
                        flash(e, "danger")
                elif not rows and gen_amt <= 0:
                    flash("Please enter at least one receipt amount.", "danger")
                else:
                    count = 0
                    total_recv_sum = 0.0
                    for sid, amt in rows:
                        rcpt = CustomerPayment(
                            customer_id=int(cust_id),
                            sale_id=sid,
                            amount=amt,
                            payment_date=pay_date,
                            payment_method=method,
                            account_id=account_id,
                            reference_no=reference_no or None,
                            notes=notes or None,
                        )
                        db.session.add(rcpt)
                        db.session.flush()
                        sync_customer_receipt(rcpt)
                        post_document("receipt", rcpt)
                        count += 1
                        total_recv_sum += amt
                    if gen_amt > 0:
                        rcpt = CustomerPayment(
                            customer_id=int(cust_id),
                            sale_id=None,
                            amount=gen_amt,
                            payment_date=pay_date,
                            payment_method=method,
                            account_id=account_id,
                            reference_no=reference_no or None,
                            notes=notes or None,
                        )
                        db.session.add(rcpt)
                        db.session.flush()
                        sync_customer_receipt(rcpt)
                        post_document("receipt", rcpt)
                        count += 1
                        total_recv_sum += gen_amt
                    db.session.commit()
                    flash(
                        f"Bulk receipt saved: {count} receipt(s) totalling {total_recv_sum:,.2f}.",
                        "success",
                    )
                    return redirect(url_for("customer_receipt"))
            except ValueError as e:
                flash(f"Invalid data: {e}", "danger")

    return render_template(
        "customer_bulk_receipt.html",
        customers=customers,
        selected_customer=selected_customer,
        outstanding=outstanding,
        bulk_amount_val=bulk_amount_val,
        general_suggested=general_suggested,
        today=now_local().strftime("%Y-%m-%d"),
    )
