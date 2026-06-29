#app.py
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_paginate import Pagination, get_page_args
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
import click
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
from functools import wraps
import csv
import os
import secrets
from sqlalchemy.exc import IntegrityError
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from itsdangerous import URLSafeTimedSerializer
from dotenv import load_dotenv
from sqlalchemy.sql import func
from sqlalchemy import inspect, text

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "instance", "database.db").replace("\\", "/")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "your_secret_key")
app.config["SECURITY_PASSWORD_SALT"] = os.getenv("SECURITY_PASSWORD_SALT", "your_salt")
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "").strip()
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "").replace(" ", "")

# Gmail App Password: https://myaccount.google.com/apppasswords
# .env file (project root) mein MAIL_USERNAME aur MAIL_PASSWORD set karein


db = SQLAlchemy(app)  # iska matlab sqlite se connect ho raha ha
csrf = CSRFProtect(app)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "signin"

def is_signup_allowed():
    return os.getenv("ALLOW_SIGNUP", "false").lower() in ("1", "true", "yes")

# Utility Functions
def generate_verification_token(email):
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
    return serializer.dumps(email, salt=app.config["SECURITY_PASSWORD_SALT"])

def verify_token(token, expiration=3600):
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
    try:
        email = serializer.loads(token, salt=app.config["SECURITY_PASSWORD_SALT"], max_age=expiration)
        return email
    except Exception:
        return None

def _load_mail_config():
    if not app.config.get("MAIL_USERNAME") or not app.config.get("MAIL_PASSWORD"):
        load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)
        app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "").strip()
        app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "").replace(" ", "")

def send_email(to_email, subject, body):
    _load_mail_config()
    mail_user = app.config.get("MAIL_USERNAME", "").strip()
    mail_pass = app.config.get("MAIL_PASSWORD", "").strip().replace(" ", "")
    if not mail_user or not mail_pass:
        flash(
            "Email is not configured. Create a .env file with MAIL_USERNAME and MAIL_PASSWORD (Gmail App Password).",
            "danger",
        )
        print("Email error: MAIL_USERNAME or MAIL_PASSWORD missing in .env")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"SalPurFlask <{mail_user}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        text_body = body.strip()
        html_body = "<br>".join(line for line in text_body.splitlines())
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(f"<html><body><p>{html_body}</p></body></html>", "html", "utf-8"))
        with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(mail_user, mail_pass)
            server.send_message(msg)
        print(f"Email sent to {to_email}: {subject}")
        return True
    except smtplib.SMTPAuthenticationError:
        flash(
            f"Gmail login failed for {mail_user}. Use a Gmail App Password in .env (not your normal password).",
            "danger",
        )
        print("Email error: SMTP authentication failed")
        return False
    except Exception as e:
        print(f"Email error ({to_email}): {str(e)}")
        flash(f"Failed to send email to {to_email}: {str(e)}", "danger")
        return False

def verified_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.verified:
            flash(f"Please verify {current_user.email} to access this page.", "danger")
            return redirect(url_for("signin"))
        return f(*args, **kwargs)
    return decorated_function

# Models
class User(db.Model, UserMixin):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    email               = db.Column(db.String(120), unique=True, nullable=False)
    password            = db.Column(db.String(255), nullable=False)
    verified            = db.Column(db.Boolean, default=False, nullable=False)
    reset_token         = db.Column(db.String(120), nullable=True)
    reset_token_expiry  = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"User('{self.name}', '{self.email}')"

class Supplier(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    contact             = db.Column(db.String(15), nullable=False)
    address             = db.Column(db.String(200), nullable=False)
    opening_balance     = db.Column(db.Float, nullable=False, default=0.0)
    purchases           = db.relationship("Purchase", backref="id_supplier", lazy=True)

class Customer(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    contact             = db.Column(db.String(15), nullable=False)
    address             = db.Column(db.String(200), nullable=False)
    opening_balance     = db.Column(db.Float, nullable=False, default=0.0)
    sales               = db.relationship("Sale", backref="id_customer", lazy=True)

class Category(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), unique=True, nullable=False)
    items               = db.relationship("Item", backref="id_category", lazy=True)

class Item(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    category_id         = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    stock               = db.Column(db.Integer, nullable=False, default=0)
    reorder_level       = db.Column(db.Integer, nullable=False, default=50)
    purchase_price      = db.Column(db.Float, nullable=True)
    sale_price          = db.Column(db.Float, nullable=True)
    purchases           = db.relationship("Purchase", backref="id_item", lazy=True)
    sales               = db.relationship("Sale", backref="id_item", lazy=True)

class Purchase(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_id         = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    item_id             = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity            = db.Column(db.Integer, nullable=False)
    purchase_price      = db.Column(db.Float, nullable=False)
    date                = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

class Sale(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    item_id             = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity            = db.Column(db.Integer, nullable=False)
    sale_price          = db.Column(db.Float, nullable=False)
    date                = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

PAYMENT_METHODS = ("Cash", "Bank", "Cheque", "Online")

class SupplierPayment(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_id         = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    purchase_id         = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=True)
    amount              = db.Column(db.Float, nullable=False)
    payment_date        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    payment_method      = db.Column(db.String(20), nullable=False, default="Cash")
    reference_no        = db.Column(db.String(100), nullable=True)
    notes               = db.Column(db.String(300), nullable=True)
    supplier            = db.relationship("Supplier", backref="payments", lazy=True)
    purchase            = db.relationship("Purchase", backref="supplier_payments", lazy=True)

class CustomerPayment(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    sale_id             = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=True)
    amount              = db.Column(db.Float, nullable=False)
    payment_date        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    payment_method      = db.Column(db.String(20), nullable=False, default="Cash")
    reference_no        = db.Column(db.String(100), nullable=True)
    notes               = db.Column(db.String(300), nullable=True)
    customer            = db.relationship("Customer", backref="receipts", lazy=True)
    sale                = db.relationship("Sale", backref="customer_payments", lazy=True)

class PurchaseReturn(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    purchase_id  = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=False)
    supplier_id  = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    item_id      = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity     = db.Column(db.Integer, nullable=False)
    return_price = db.Column(db.Float, nullable=False)
    date         = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    reason       = db.Column(db.String(300), nullable=True)
    purchase     = db.relationship("Purchase", backref="returns", lazy=True)
    supplier     = db.relationship("Supplier", backref="purchase_returns", lazy=True)
    item         = db.relationship("Item", backref="purchase_returns", lazy=True)

class SaleReturn(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    sale_id      = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    customer_id  = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    item_id      = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity     = db.Column(db.Integer, nullable=False)
    return_price = db.Column(db.Float, nullable=False)
    date         = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    reason       = db.Column(db.String(300), nullable=True)
    sale         = db.relationship("Sale", backref="returns", lazy=True)
    customer     = db.relationship("Customer", backref="sale_returns", lazy=True)
    item         = db.relationship("Item", backref="sale_returns", lazy=True)

OPENING_LEDGER_DATE = datetime(1900, 1, 1)

class SupplierLedgerEntry(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_id         = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    entry_date          = db.Column(db.DateTime, nullable=False)
    entry_type          = db.Column(db.String(30), nullable=False)
    source_type         = db.Column(db.String(20), nullable=False)
    source_id           = db.Column(db.Integer, nullable=True)
    description         = db.Column(db.String(300), nullable=False)
    debit               = db.Column(db.Float, nullable=False, default=0.0)
    credit              = db.Column(db.Float, nullable=False, default=0.0)
    balance_after       = db.Column(db.Float, nullable=False, default=0.0)
    supplier            = db.relationship("Supplier", backref="ledger_entries", lazy=True)

class CustomerLedgerEntry(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    entry_date          = db.Column(db.DateTime, nullable=False)
    entry_type          = db.Column(db.String(30), nullable=False)
    source_type         = db.Column(db.String(20), nullable=False)
    source_id           = db.Column(db.Integer, nullable=True)
    description         = db.Column(db.String(300), nullable=False)
    debit               = db.Column(db.Float, nullable=False, default=0.0)
    credit              = db.Column(db.Float, nullable=False, default=0.0)
    balance_after       = db.Column(db.Float, nullable=False, default=0.0)
    customer            = db.relationship("Customer", backref="ledger_entries", lazy=True)

def purchase_total(purchase):
    return float(purchase.quantity * purchase.purchase_price)

def sale_total(sale):
    return float(sale.quantity * sale.sale_price)

def get_purchase_paid(purchase_id, exclude_payment_id=None):
    query = db.session.query(func.sum(SupplierPayment.amount)).filter(SupplierPayment.purchase_id == purchase_id)
    if exclude_payment_id:
        query = query.filter(SupplierPayment.id != exclude_payment_id)
    return float(query.scalar() or 0.0)

def get_sale_received(sale_id, exclude_payment_id=None):
    query = db.session.query(func.sum(CustomerPayment.amount)).filter(CustomerPayment.sale_id == sale_id)
    if exclude_payment_id:
        query = query.filter(CustomerPayment.id != exclude_payment_id)
    return float(query.scalar() or 0.0)

def get_payment_status(total, paid):
    if paid <= 0:
        return "Unpaid"
    if paid >= total:
        return "Paid"
    return "Partial"

def get_supplier_payable(supplier_id):
    return float(
        db.session.query(func.sum(Purchase.quantity * Purchase.purchase_price))
        .filter(Purchase.supplier_id == supplier_id)
        .scalar()
        or 0.0
    )

def get_supplier_paid(supplier_id, exclude_payment_id=None):
    query = db.session.query(func.sum(SupplierPayment.amount)).filter(SupplierPayment.supplier_id == supplier_id)
    if exclude_payment_id:
        query = query.filter(SupplierPayment.id != exclude_payment_id)
    return float(query.scalar() or 0.0)

def get_customer_receivable(customer_id):
    return float(
        db.session.query(func.sum(Sale.quantity * Sale.sale_price))
        .filter(Sale.customer_id == customer_id)
        .scalar()
        or 0.0
    )

def get_customer_received(customer_id, exclude_payment_id=None):
    query = db.session.query(func.sum(CustomerPayment.amount)).filter(CustomerPayment.customer_id == customer_id)
    if exclude_payment_id:
        query = query.filter(CustomerPayment.id != exclude_payment_id)
    return float(query.scalar() or 0.0)

def get_supplier_balance(supplier_id, exclude_payment_id=None):
    if exclude_payment_id:
        return get_supplier_payable(supplier_id) - get_supplier_paid(supplier_id, exclude_payment_id) + float(
            db.session.get(Supplier, supplier_id).opening_balance or 0
        )
    entry = (
        SupplierLedgerEntry.query.filter_by(supplier_id=supplier_id)
        .order_by(SupplierLedgerEntry.entry_date.desc(), SupplierLedgerEntry.id.desc())
        .first()
    )
    if entry:
        return float(entry.balance_after)
    supplier = db.session.get(Supplier, supplier_id)
    return float(supplier.opening_balance or 0) if supplier else 0.0

def get_customer_balance(customer_id, exclude_payment_id=None):
    if exclude_payment_id:
        return get_customer_receivable(customer_id) - get_customer_received(customer_id, exclude_payment_id) + float(
            db.session.get(Customer, customer_id).opening_balance or 0
        )
    entry = (
        CustomerLedgerEntry.query.filter_by(customer_id=customer_id)
        .order_by(CustomerLedgerEntry.entry_date.desc(), CustomerLedgerEntry.id.desc())
        .first()
    )
    if entry:
        return float(entry.balance_after)
    customer = db.session.get(Customer, customer_id)
    return float(customer.opening_balance or 0) if customer else 0.0

def supplier_balance_label(balance):
    if balance > 0.001:
        return "Payable"
    if balance < -0.001:
        return "Advance Paid"
    return "Settled"

def customer_balance_label(balance):
    if balance > 0.001:
        return "Receivable"
    if balance < -0.001:
        return "Advance Received"
    return "Settled"

def recalculate_supplier_ledger(supplier_id):
    entries = (
        SupplierLedgerEntry.query.filter_by(supplier_id=supplier_id)
        .order_by(SupplierLedgerEntry.entry_date.asc(), SupplierLedgerEntry.id.asc())
        .all()
    )
    balance = 0.0
    for entry in entries:
        balance += float(entry.credit) - float(entry.debit)
        entry.balance_after = balance

def recalculate_customer_ledger(customer_id):
    entries = (
        CustomerLedgerEntry.query.filter_by(customer_id=customer_id)
        .order_by(CustomerLedgerEntry.entry_date.asc(), CustomerLedgerEntry.id.asc())
        .all()
    )
    balance = 0.0
    for entry in entries:
        balance += float(entry.debit) - float(entry.credit)
        entry.balance_after = balance

def remove_supplier_ledger_entry(source_type, source_id):
    entry = SupplierLedgerEntry.query.filter_by(source_type=source_type, source_id=source_id).first()
    supplier_id = entry.supplier_id if entry else None
    if entry:
        db.session.delete(entry)
    return supplier_id

def remove_customer_ledger_entry(source_type, source_id):
    entry = CustomerLedgerEntry.query.filter_by(source_type=source_type, source_id=source_id).first()
    customer_id = entry.customer_id if entry else None
    if entry:
        db.session.delete(entry)
    return customer_id

def upsert_supplier_ledger(supplier_id, source_type, source_id, entry_date, entry_type, description, debit, credit):
    entry = SupplierLedgerEntry.query.filter_by(source_type=source_type, source_id=source_id).first()
    if entry and entry.supplier_id != supplier_id:
        old_sid = entry.supplier_id
        db.session.delete(entry)
        db.session.flush()
        recalculate_supplier_ledger(old_sid)
        entry = None
    if entry:
        entry.supplier_id = supplier_id
        entry.entry_date = entry_date
        entry.entry_type = entry_type
        entry.description = description
        entry.debit = float(debit)
        entry.credit = float(credit)
    else:
        db.session.add(
            SupplierLedgerEntry(
                supplier_id=supplier_id,
                entry_date=entry_date,
                entry_type=entry_type,
                source_type=source_type,
                source_id=source_id,
                description=description,
                debit=float(debit),
                credit=float(credit),
                balance_after=0.0,
            )
        )
    recalculate_supplier_ledger(supplier_id)

def upsert_customer_ledger(customer_id, source_type, source_id, entry_date, entry_type, description, debit, credit):
    entry = CustomerLedgerEntry.query.filter_by(source_type=source_type, source_id=source_id).first()
    if entry and entry.customer_id != customer_id:
        old_cid = entry.customer_id
        db.session.delete(entry)
        db.session.flush()
        recalculate_customer_ledger(old_cid)
        entry = None
    if entry:
        entry.customer_id = customer_id
        entry.entry_date = entry_date
        entry.entry_type = entry_type
        entry.description = description
        entry.debit = float(debit)
        entry.credit = float(credit)
    else:
        db.session.add(
            CustomerLedgerEntry(
                customer_id=customer_id,
                entry_date=entry_date,
                entry_type=entry_type,
                source_type=source_type,
                source_id=source_id,
                description=description,
                debit=float(debit),
                credit=float(credit),
                balance_after=0.0,
            )
        )
    recalculate_customer_ledger(customer_id)

def sync_supplier_opening(supplier):
    ob = float(supplier.opening_balance or 0)
    if abs(ob) < 0.001:
        remove_supplier_ledger_entry("opening", supplier.id)
        recalculate_supplier_ledger(supplier.id)
        return
    if ob > 0:
        debit, credit, desc = 0.0, ob, "Opening Balance (Payable)"
    else:
        debit, credit, desc = abs(ob), 0.0, "Opening Balance (Advance)"
    upsert_supplier_ledger(
        supplier.id, "opening", supplier.id, OPENING_LEDGER_DATE, "Opening", desc, debit, credit
    )

def sync_customer_opening(customer):
    ob = float(customer.opening_balance or 0)
    if abs(ob) < 0.001:
        remove_customer_ledger_entry("opening", customer.id)
        recalculate_customer_ledger(customer.id)
        return
    if ob > 0:
        debit, credit, desc = ob, 0.0, "Opening Balance (Receivable)"
    else:
        debit, credit, desc = 0.0, abs(ob), "Opening Balance (Advance)"
    upsert_customer_ledger(
        customer.id, "opening", customer.id, OPENING_LEDGER_DATE, "Opening", desc, debit, credit
    )

def sync_supplier_purchase(purchase):
    item = db.session.get(Item, purchase.item_id)
    item_name = item.name if item else "Item"
    total = purchase_total(purchase)
    upsert_supplier_ledger(
        purchase.supplier_id,
        "purchase",
        purchase.id,
        purchase.date,
        "Purchase",
        f"Purchase #{purchase.id} — {item_name}",
        0.0,
        total,
    )

def sync_supplier_payment(payment):
    purchase_ref = f" (Bill #{payment.purchase_id})" if payment.purchase_id else ""
    upsert_supplier_ledger(
        payment.supplier_id,
        "payment",
        payment.id,
        payment.payment_date,
        "Payment",
        f"Payment #{payment.id}{purchase_ref} — {payment.payment_method}",
        payment.amount,
        0.0,
    )

def sync_customer_sale(sale):
    item = db.session.get(Item, sale.item_id)
    item_name = item.name if item else "Item"
    total = sale_total(sale)
    upsert_customer_ledger(
        sale.customer_id,
        "sale",
        sale.id,
        sale.date,
        "Sale",
        f"Sale #{sale.id} — {item_name}",
        total,
        0.0,
    )

def sync_customer_receipt(receipt):
    sale_ref = f" (Bill #{receipt.sale_id})" if receipt.sale_id else ""
    upsert_customer_ledger(
        receipt.customer_id,
        "receipt",
        receipt.id,
        receipt.payment_date,
        "Receipt",
        f"Receipt #{receipt.id}{sale_ref} — {receipt.payment_method}",
        0.0,
        receipt.amount,
    )

def sync_supplier_purchase_return(pr):
    item = db.session.get(Item, pr.item_id)
    item_name = item.name if item else "Item"
    total = float(pr.quantity * pr.return_price)
    upsert_supplier_ledger(
        pr.supplier_id,
        "purchase_return",
        pr.id,
        pr.date,
        "Purchase Return",
        f"Return #{pr.id} — {item_name} (Bill #{pr.purchase_id})",
        total,
        0.0,
    )

def sync_customer_sale_return(sr):
    item = db.session.get(Item, sr.item_id)
    item_name = item.name if item else "Item"
    total = float(sr.quantity * sr.return_price)
    upsert_customer_ledger(
        sr.customer_id,
        "sale_return",
        sr.id,
        sr.date,
        "Sale Return",
        f"Return #{sr.id} — {item_name} (Bill #{sr.sale_id})",
        0.0,
        total,
    )

def backfill_ledgers():
    if SupplierLedgerEntry.query.first() or CustomerLedgerEntry.query.first():
        return
    for supplier in Supplier.query.all():
        sync_supplier_opening(supplier)
    for customer in Customer.query.all():
        sync_customer_opening(customer)
    for purchase in Purchase.query.all():
        sync_supplier_purchase(purchase)
    for payment in SupplierPayment.query.all():
        sync_supplier_payment(payment)
    for sale in Sale.query.all():
        sync_customer_sale(sale)
    for receipt in CustomerPayment.query.all():
        sync_customer_receipt(receipt)
    db.session.commit()

def get_total_payable():
    return float(db.session.query(func.sum(Purchase.quantity * Purchase.purchase_price)).scalar() or 0.0)

def get_total_paid_suppliers():
    return float(db.session.query(func.sum(SupplierPayment.amount)).scalar() or 0.0)

def get_total_receivable():
    return float(db.session.query(func.sum(Sale.quantity * Sale.sale_price)).scalar() or 0.0)

def get_total_received_customers():
    return float(db.session.query(func.sum(CustomerPayment.amount)).scalar() or 0.0)

def get_purchase_returned_qty(purchase_id):
    return int(db.session.query(func.sum(PurchaseReturn.quantity)).filter(
        PurchaseReturn.purchase_id == purchase_id
    ).scalar() or 0)

def get_sale_returned_qty(sale_id):
    return int(db.session.query(func.sum(SaleReturn.quantity)).filter(
        SaleReturn.sale_id == sale_id
    ).scalar() or 0)

def purchase_return_total(pr):
    return float(pr.quantity * pr.return_price)

def sale_return_total(sr):
    return float(sr.quantity * sr.return_price)

def parse_payment_amount(amount_str):
    amount_str = (amount_str or "").strip()
    if not amount_str.replace(".", "", 1).isdigit():
        return None
    amount = float(amount_str)
    return amount if amount > 0 else None

def validate_supplier_payment(supplier_id, amount, purchase_id=None, exclude_payment_id=None):
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier:
        return "Invalid supplier selected!"
    if purchase_id:
        purchase = db.session.get(Purchase, purchase_id)
        if not purchase or purchase.supplier_id != int(supplier_id):
            return "Selected purchase does not belong to this supplier!"
        purchase_balance = purchase_total(purchase) - get_purchase_paid(purchase_id, exclude_payment_id)
        if amount > purchase_balance + 0.001:
            return f"Payment exceeds purchase balance due ({purchase_balance:,.2f})!"
    return None

def validate_customer_receipt(customer_id, amount, sale_id=None, exclude_payment_id=None):
    customer = db.session.get(Customer, customer_id)
    if not customer:
        return "Invalid customer selected!"
    if sale_id:
        sale = db.session.get(Sale, sale_id)
        if not sale or sale.customer_id != int(customer_id):
            return "Selected sale does not belong to this customer!"
        sale_balance = sale_total(sale) - get_sale_received(sale_id, exclude_payment_id)
        if amount > sale_balance + 0.001:
            return f"Receipt exceeds sale balance due ({sale_balance:,.2f})!"
    return None

def migrate_database():
    db.create_all()
    inspector = inspect(db.engine)
    if "item" in inspector.get_table_names():
        item_columns = {col["name"] for col in inspector.get_columns("item")}
        if "category_id" not in item_columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE item ADD COLUMN category_id INTEGER REFERENCES category(id)"))
    for table, column in (("supplier", "opening_balance"), ("customer", "opening_balance")):
        if table in inspector.get_table_names():
            cols = {col["name"] for col in inspector.get_columns(table)}
            if column not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} FLOAT DEFAULT 0"))
    backfill_ledgers()

# Create Database
with app.app_context():
    migrate_database()

# Load user for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Custom Jinja2 filter: number ko 999,999,999,999.99 format mein dikhaye
@app.template_filter('fmt_num')
def fmt_num(value):
    try:
        value = float(value)
        return f"{value:,.2f}"
    except (TypeError, ValueError):
        return value

@app.context_processor
def inject_form_defaults():
    ctx = {
        "form_data": {},
        "payment_methods": PAYMENT_METHODS,
        "purchase_total": purchase_total,
        "sale_total": sale_total,
        "get_purchase_paid": get_purchase_paid,
        "get_sale_received": get_sale_received,
        "get_payment_status": get_payment_status,
        "get_supplier_payable": get_supplier_payable,
        "get_supplier_paid": get_supplier_paid,
        "get_supplier_balance": get_supplier_balance,
        "get_customer_receivable": get_customer_receivable,
        "get_customer_received": get_customer_received,
        "get_customer_balance": get_customer_balance,
        "supplier_balance_label": supplier_balance_label,
        "customer_balance_label": customer_balance_label,
        "get_purchase_returned_qty": get_purchase_returned_qty,
        "get_sale_returned_qty": get_sale_returned_qty,
        "purchase_return_total": purchase_return_total,
        "sale_return_total": sale_return_total,
    }
    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        data.pop("password", None)
        data.pop("confirm_password", None)
        ctx["form_data"] = data
    return ctx

# Helper function for pagination
def get_paginated_results(query, per_page=10):
    page, _, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = query.count()
    results = query.offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework="bootstrap5")
    return results, pagination

# Authentication Routes
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if not is_signup_allowed():
        flash("Registration is disabled. Contact the administrator.", "warning")
        return redirect(url_for("signin"))
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        if not name or not email or not password:
            flash("All fields are required!", "danger")
            return render_template("signup.html")
        if User.query.filter_by(email=email).first():
            flash(f"Email {email} is already registered!", "danger")
            return render_template("signup.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters!", "danger")
            return render_template("signup.html")
        hashed_password = pwd_context.hash(password)
        user = User(name=name, email=email, password=hashed_password, verified=False)
        try:
            db.session.add(user)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Database error: {str(e)}")
            flash("Failed to register user. Please try again.", "danger")
            return render_template("signup.html")
        token = generate_verification_token(email)
        verification_url = url_for("verify_email", token=token, _external=True)
        body = f"""
        Hello {name},
 
        Thank you for registering with SalPurFlask. Please click the link below to verify your email address:

        {verification_url}

        This link will expire in 1 hour.

        Regards,
        SalPurFlask Team
        """
        if send_email(email, "Verify Your Email - SalPurFlask", body):
            flash(f"A verification email has been sent to {email}. Please check your inbox.", "success")
        else:
            db.session.delete(user)
            db.session.commit()
            return render_template("signup.html")
        return redirect(url_for("signin"))
    return render_template("signup.html")

@app.route("/signin", methods=["GET", "POST"])
def signin():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        user = User.query.filter_by(email=email).first()
        if user:
            try:
                db.session.refresh(user)
                if pwd_context.verify(password, user.password):
                    if not user.verified:
                        flash(f"Please verify {email} before signing in. Check your inbox for the verification link.", "danger")
                        return render_template("signin.html", just_reset=session.get("just_reset_email"))
                    login_user(user)
                    session["user_id"] = user.id
                    session.pop("just_reset_email", None)
                    flash("Signed in successfully!", "success")
                    return redirect(url_for("index"))
                flash("Invalid email or password!", "danger")
            except Exception:
                flash("Invalid email or password!", "danger")
        else:
            flash("Invalid email or password!", "danger")
    just_reset = session.get("just_reset_email")
    return render_template("signin.html", just_reset=just_reset)

@app.route("/signout")
@login_required
def signout():
    logout_user()
    session.pop("user_id", None)
    flash("Signed out successfully!", "success")
    return redirect(url_for("signin"))

@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash(f"Email {email} not found!", "danger")
        else:
            token = secrets.token_urlsafe(32)
            expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            reset_url = url_for("reset_password", token=token, _external=True)
            body = (
                f"Dear User,\n\n"
                f"To reset your password, open this link:\n{reset_url}\n\n"
                f"This link expires in 1 hour. If you did not request this, ignore this email.\n\n"
                f"Regards,\nSalPurFlask Team"
            )
            if send_email(email, "Password Reset Request - SalPurFlask", body):
                user.reset_token = token
                user.reset_token_expiry = expiry
                db.session.commit()
                flash(
                    f"Password reset link sent to {email}! Check your inbox and spam/junk folder (Yahoo/Hotmail often filter these).",
                    "success",
                )
                print(f"PASSWORD RESET LINK for {email}: {reset_url}")
                if app.debug:
                    flash(f"Development — reset link for {email}: {reset_url}", "info")
                return redirect(url_for("signin"))
        return render_template("forgot_password.html")
    return render_template("forgot_password.html")

@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    user = User.query.filter_by(reset_token=token).first()
    if not user or user.reset_token_expiry < datetime.now(timezone.utc).replace(tzinfo=None):
        flash("Invalid or expired reset link!", "danger")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        if len(password) < 6:
            flash("Password must be at least 6 characters!", "danger")
        elif password != confirm_password:
            flash("Passwords do not match!", "danger")
        else:
            user.password = pwd_context.hash(password)
            user.reset_token = None
            user.reset_token_expiry = None
            db.session.commit()
            db.session.refresh(user)
            if not pwd_context.verify(password, user.password):
                flash("Password could not be saved. Please try again.", "danger")
                return render_template("reset_password.html", token=token)
            session["just_reset_email"] = user.email
            flash(f"Password for {user.email} reset successfully! Please sign in with your new password.", "success")
            return redirect(url_for("signin"))
    return render_template("reset_password.html", token=token)

@app.route("/verify_email/<token>")
def verify_email(token):
    email = verify_token(token)
    if not email:
        flash("Invalid or expired verification link!", "danger")
        return redirect(url_for("signin"))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash("User not found!", "danger")
        return redirect(url_for("signin"))
    if user.verified:
        flash(f"Email {email} is already verified! Please sign in.", "success")
        return redirect(url_for("signin"))
    try:
        user.verified = True
        db.session.commit()
        print(f"User verified: {user.email}")
        flash(f"Email {email} verified successfully! You can now sign in.", "success")
    except Exception as e:
        db.session.rollback()
        print(f"Verification error: {str(e)}")
        flash(f"Failed to verify email {email}. Please try again.", "danger")
    return redirect(url_for("signin"))

@app.route("/resend_verification", methods=["GET", "POST"])
def resend_verification():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash(f"Email {email} not found!", "danger")
            return render_template("resend_verification.html")
        if user.verified:
            flash(f"Email {email} is already verified! Please sign in.", "success")
            return redirect(url_for("signin"))
        token = generate_verification_token(email)
        verification_url = url_for("verify_email", token=token, _external=True)
        body = f"""
        Hello {user.name},

        Please click the link below to verify your email address:

        {verification_url}

        This link will expire in 1 hour.

        Regards,
        SalPurFlask Team
        """
        if send_email(email, "Verify Your Email - SalPurFlask", body):
            flash(f"A new verification email has been sent to {email}. Please check your inbox.", "success")
        return redirect(url_for("signin"))
    return render_template("resend_verification.html")

# Existing Routes
@app.route("/")
def index():
    if current_user.is_authenticated:
        return render_template("index.html")
    return redirect(url_for("signin"))

@app.route("/about")
def about():
    return render_template("about.html")

@app.route('/dashboard')
@verified_required
def dashboard():
    items = Item.query.all()
    purchases = Purchase.query.order_by(Purchase.date.desc()).limit(5).all()
    sales = Sale.query.order_by(Sale.date.desc()).limit(5).all()
    total_purchase_cost = db.session.query(func.sum(Purchase.quantity * Purchase.purchase_price)).scalar() or 0.0
    total_sale_revenue = db.session.query(func.sum(Sale.quantity * Sale.sale_price)).scalar() or 0.0
    total_payable = get_total_payable()
    total_paid_suppliers = get_total_paid_suppliers()
    total_receivable = get_total_receivable()
    total_received_customers = get_total_received_customers()
    total_payable_balance = sum(get_supplier_balance(s.id) for s in Supplier.query.all())
    total_receivable_balance = sum(get_customer_balance(c.id) for c in Customer.query.all())
    return render_template(
        'dashboard.html',
        items=items,
        purchases=purchases,
        sales=sales,
        total_purchase_cost=total_purchase_cost,
        total_sale_revenue=total_sale_revenue,
        total_payable=total_payable,
        total_paid_suppliers=total_paid_suppliers,
        total_payable_balance=total_payable_balance,
        total_receivable=total_receivable,
        total_received_customers=total_received_customers,
        total_receivable_balance=total_receivable_balance,
    )

@app.route("/supplier", methods=["GET", "POST"])
@verified_required
def supplier():
    search = request.args.get("search", "")
    query = Supplier.query.filter(Supplier.name.ilike(f"%{search}%")) if search else Supplier.query
    suppliers, pagination = get_paginated_results(query)
    if request.method == "POST":
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
        elif not contact.isdigit() or len(contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        elif Supplier.query.filter_by(name=name, contact=contact, address=address).first():
            flash("Supplier already exists!", "warning")
            return redirect(url_for("supplier"))
        else:
            supplier = Supplier(name=name, contact=contact, address=address, opening_balance=opening_balance)
            db.session.add(supplier)
            db.session.flush()
            sync_supplier_opening(supplier)
            db.session.commit()
            flash("Supplier added successfully!", "success")
            return redirect(url_for("supplier"))
    return render_template("supplier.html", suppliers=suppliers, pagination=pagination, search=search)

@app.route("/supplier/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_supplier(id):
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
        elif not supplier.contact.isdigit() or len(supplier.contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        else:
            supplier.opening_balance = float(opening_str or 0)
            sync_supplier_opening(supplier)
            db.session.commit()
            flash("Supplier updated successfully!", "success")
            return redirect(url_for("supplier"))
    return render_template("edit_supplier.html", supplier=supplier)

@app.route("/supplier/delete/<int:id>", methods=["POST"])
@verified_required
def delete_supplier(id):
    supplier = db.session.get(Supplier, id) or abort(404)
    if supplier.purchases:
        flash("Cannot delete supplier with associated purchases!", "danger")
    elif supplier.payments:
        flash("Cannot delete supplier with associated payments!", "danger")
    else:
        SupplierLedgerEntry.query.filter_by(supplier_id=id).delete()
        db.session.delete(supplier)
        db.session.commit()
        flash("Supplier deleted successfully!", "success")
    return redirect(url_for("supplier"))

@app.route("/export_suppliers")
@verified_required
def export_suppliers():
    suppliers = Supplier.query.all()
    with open("static/suppliers.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Name", "Contact", "Address"])
        for s in suppliers:
            writer.writerow([s.id, s.name, s.contact, s.address])
    return send_from_directory("static", "suppliers.csv")

@app.route("/customer", methods=["GET", "POST"])
@verified_required
def customer():
    search = request.args.get("search", "")
    query = Customer.query.filter(Customer.name.ilike(f"%{search}%")) if search else Customer.query
    customers, pagination = get_paginated_results(query)
    if request.method == "POST":
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
        elif not contact.isdigit() or len(contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        elif Customer.query.filter_by(name=name, contact=contact, address=address).first():
            flash("Customer already exists!", "warning")
            return redirect(url_for("customer"))
        else:
            customer = Customer(name=name, contact=contact, address=address, opening_balance=opening_balance)
            db.session.add(customer)
            db.session.flush()
            sync_customer_opening(customer)
            db.session.commit()
            flash("Customer added successfully!", "success")
            return redirect(url_for("customer"))
    return render_template("customer.html", customers=customers, pagination=pagination, search=search)

@app.route("/customer/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_customer(id):
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
        elif not customer.contact.isdigit() or len(customer.contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        else:
            customer.opening_balance = float(opening_str or 0)
            sync_customer_opening(customer)
            db.session.commit()
            flash("Customer updated successfully!", "success")
            return redirect(url_for("customer"))
    return render_template("edit_customer.html", customer=customer)

@app.route("/customer/delete/<int:id>", methods=["POST"])
@verified_required
def delete_customer(id):
    customer = db.session.get(Customer, id) or abort(404)
    if customer.sales:
        flash("Cannot delete customer with associated sales!", "danger")
    elif customer.receipts:
        flash("Cannot delete customer with associated receipts!", "danger")
    else:
        CustomerLedgerEntry.query.filter_by(customer_id=id).delete()
        db.session.delete(customer)
        db.session.commit()
        flash("Customer deleted successfully!", "success")
    return redirect(url_for("customer"))

@app.route("/export_customers")
@verified_required
def export_customers():
    customers = Customer.query.all()
    with open("static/customers.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Name", "Contact", "Address"])
        for c in customers:
            writer.writerow([c.id, c.name, c.contact, c.address])
    return send_from_directory("static", "customers.csv")

@app.route("/category", methods=["GET", "POST"])
@verified_required
def category():
    search = request.args.get("search", "")
    query = Category.query.filter(Category.name.ilike(f"%{search}%")) if search else Category.query
    categories, pagination = get_paginated_results(query)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required!", "danger")
        elif Category.query.filter_by(name=name).first():
            flash("Category already exists!", "warning")
            return redirect(url_for("category"))
        else:
            db.session.add(Category(name=name))
            db.session.commit()
            flash("Category added successfully!", "success")
            return redirect(url_for("category"))
    return render_template("category.html", categories=categories, pagination=pagination, search=search)

@app.route("/category/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_category(id):
    category = db.session.get(Category, id) or abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required!", "danger")
        elif Category.query.filter(Category.name == name, Category.id != id).first():
            flash("Category already exists!", "warning")
        else:
            category.name = name
            db.session.commit()
            flash("Category updated successfully!", "success")
            return redirect(url_for("category"))
    return render_template("edit_category.html", category=category)

@app.route("/category/delete/<int:id>", methods=["POST"])
@verified_required
def delete_category(id):
    category = db.session.get(Category, id) or abort(404)
    if category.items:
        flash("Cannot delete category with associated items!", "danger")
    else:
        db.session.delete(category)
        db.session.commit()
        flash("Category deleted successfully!", "success")
    return redirect(url_for("category"))

@app.route("/item", methods=["GET", "POST"])
@verified_required
def item():
    search = request.args.get("search", "")
    category_filter = request.args.get("category_id", "")
    query = Item.query.outerjoin(Category)
    if search:
        query = query.filter((Item.name.ilike(f"%{search}%")) | (Category.name.ilike(f"%{search}%")))
    if category_filter.isdigit():
        query = query.filter(Item.category_id == int(category_filter))
    items, pagination = get_paginated_results(query)
    categories = Category.query.order_by(Category.name).all()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id", "").strip()
        stock = request.form.get("stock", "").strip()
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        if not categories:
            flash("Please add a category first before adding items!", "danger")
        elif not name or not stock or not reorder_level or not category_id:
            flash("Name, Category, Stock, and Reorder Level are required!", "danger")
        elif not category_id.isdigit() or not db.session.get(Category, int(category_id)):
            flash("Please select a valid category!", "danger")
        elif not stock.isdigit() or not reorder_level.isdigit():
            flash("Stock and Reorder Level must be numbers!", "danger")
        elif purchase_price and (not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0):
            flash("Purchase price must be a non-negative number!", "danger")
        elif sale_price and (not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0):
            flash("Sale price must be a non-negative number!", "danger")
        else:
            item = Item(
                name=name,
                category_id=int(category_id),
                stock=int(stock),
                reorder_level=int(reorder_level),
                purchase_price=float(purchase_price) if purchase_price else None,
                sale_price=float(sale_price) if sale_price else None,
            )
            db.session.add(item)
            db.session.commit()
            flash("Item added successfully!", "success")
            return redirect(url_for("item"))
    return render_template(
        "item.html",
        items=items,
        categories=categories,
        pagination=pagination,
        search=search,
        category_filter=category_filter,
    )

@app.route("/item/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_item(id):
    item = db.session.get(Item, id) or abort(404)
    categories = Category.query.order_by(Category.name).all()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id", "").strip()
        stock = request.form.get("stock", "").strip()
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        if not categories:
            flash("Please add a category first before editing items!", "danger")
        elif not name or not stock or not reorder_level or not category_id:
            flash("Name, Category, Stock, and Reorder Level are required!", "danger")
        elif not category_id.isdigit() or not db.session.get(Category, int(category_id)):
            flash("Please select a valid category!", "danger")
        elif not stock.isdigit() or not reorder_level.isdigit():
            flash("Stock and Reorder Level must be numbers!", "danger")
        elif purchase_price and (not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0):
            flash("Purchase price must be a non-negative number!", "danger")
        elif sale_price and (not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0):
            flash("Sale price must be a non-negative number!", "danger")
        else:
            item.name = name
            item.category_id = int(category_id)
            item.stock = int(stock)
            item.reorder_level = int(reorder_level)
            item.purchase_price = float(purchase_price) if purchase_price else None
            item.sale_price = float(sale_price) if sale_price else None
            db.session.commit()
            flash("Item updated successfully!", "success")
            return redirect(url_for("item"))
    return render_template("edit_item.html", item=item, categories=categories)

@app.route("/item/delete/<int:id>", methods=["POST"])
@verified_required
def delete_item(id):
    item = db.session.get(Item, id) or abort(404)
    if item.purchases or item.sales:
        flash("Cannot delete item with associated purchases or sales!", "danger")
    else:
        db.session.delete(item)
        db.session.commit()
        flash("Item deleted successfully!", "success")
    return redirect(url_for("item"))

@app.route("/api/item/<int:id>")
@verified_required
def get_item(id):
    item = db.session.get(Item, id) or abort(404)
    return {
        "purchase_price": item.purchase_price,
        "sale_price": item.sale_price,
        "category": item.id_category.name if item.id_category else None,
    }

@app.route("/purchase", methods=["GET", "POST"])
@verified_required
def purchase():
    search = request.args.get("search", "").strip()
    query = Purchase.query
    if search:
        query = (
            query.join(Supplier)
            .join(Item)
            .outerjoin(Category, Item.category_id == Category.id)
            .filter(
                (Supplier.name.ilike(f"%{search}%"))
                | (Item.name.ilike(f"%{search}%"))
                | (Category.name.ilike(f"%{search}%"))
            )
        )
    purchases, pagination = get_paginated_results(query)
    suppliers = Supplier.query.all()
    items = Item.query.all()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id", "").strip()
        item_id = request.form.get("item_id", "").strip()
        quantity = request.form.get("quantity", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        date = request.form.get("date", "").strip()
        form_data = {
            "supplier_id": supplier_id,
            "item_id": item_id,
            "quantity": quantity,
            "purchase_price": purchase_price,
            "date": date,
        }
        if not supplier_id or not item_id or not quantity or not purchase_price or not date:
            flash("All fields are required!", "danger")
            return render_template(
                "purchase.html",
                suppliers=suppliers,
                items=items,
                purchases=purchases,
                pagination=pagination,
                search=search,
                form_data=form_data,
            )
        if not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
            return render_template(
                "purchase.html",
                suppliers=suppliers,
                items=items,
                purchases=purchases,
                pagination=pagination,
                search=search,
                form_data=form_data,
            )
        if not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0:
            flash("Purchase price must be a non-negative number!", "danger")
            return render_template(
                "purchase.html",
                suppliers=suppliers,
                items=items,
                purchases=purchases,
                pagination=pagination,
                search=search,
                form_data=form_data,
            )
        try:
            item = db.session.get(Item, item_id) or abort(404)
            item.stock += int(quantity)
            purchase = Purchase(
                supplier_id=supplier_id,
                item_id=item_id,
                quantity=int(quantity),
                purchase_price=float(purchase_price),
                date=datetime.strptime(date, "%Y-%m-%d"),
            )
            db.session.add(purchase)
            db.session.flush()
            sync_supplier_purchase(purchase)
            db.session.commit()
            flash("Purchase added successfully!", "success")
            return redirect(url_for("purchase"))
        except ValueError:
            flash("Invalid date format! Use YYYY-MM-DD.", "danger")
            return render_template(
                "purchase.html",
                suppliers=suppliers,
                items=items,
                purchases=purchases,
                pagination=pagination,
                search=search,
                form_data=form_data,
            )
    return render_template(
        "purchase.html",
        suppliers=suppliers,
        items=items,
        purchases=purchases,
        pagination=pagination,
        search=search,
        form_data={},
    )

@app.route("/purchase/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_purchase(id):
    purchase = db.session.get(Purchase, id) or abort(404)
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id", "")
        item_id = request.form.get("item_id", "")
        quantity = request.form.get("quantity", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        date = request.form.get("date", "")
        form_data = {
            "supplier_id": supplier_id,
            "item_id": item_id,
            "quantity": quantity,
            "purchase_price": purchase_price,
            "date": date,
        }
        if not supplier_id or not item_id or not quantity or not purchase_price or not date:
            flash("All fields are required!", "danger")
        elif not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
        elif not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0:
            flash("Purchase price must be a non-negative number!", "danger")
        else:
            try:
                item = db.session.get(Item, item_id) or abort(404)
                old_quantity = purchase.quantity
                old_supplier_id = purchase.supplier_id
                if purchase.item_id == int(item_id):
                    item.stock = item.stock - old_quantity + int(quantity)
                else:
                    old_item = db.session.get(Item, purchase.item_id) or abort(404)
                    old_item.stock -= old_quantity
                    item.stock += int(quantity)
                purchase.supplier_id = supplier_id
                purchase.item_id = item_id
                purchase.quantity = int(quantity)
                purchase.purchase_price = float(purchase_price)
                purchase.date = datetime.strptime(date, "%Y-%m-%d")
                if old_supplier_id != int(supplier_id):
                    remove_supplier_ledger_entry("purchase", purchase.id)
                    recalculate_supplier_ledger(old_supplier_id)
                sync_supplier_purchase(purchase)
                db.session.commit()
                flash("Purchase updated successfully!", "success")
                return redirect(url_for("purchase"))
            except ValueError:
                flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        suppliers = Supplier.query.all()
        items = Item.query.all()
        return render_template("edit_purchase.html", purchase=purchase, suppliers=suppliers, items=items, form_data=form_data)
    suppliers = Supplier.query.all()
    items = Item.query.all()
    return render_template("edit_purchase.html", purchase=purchase, suppliers=suppliers, items=items, form_data={})

@app.route("/purchase/delete/<int:id>", methods=["POST"])
@verified_required
def delete_purchase(id):
    purchase = db.session.get(Purchase, id) or abort(404)
    if purchase.supplier_payments:
        flash("Cannot delete purchase with associated payments! Delete payments first.", "danger")
        return redirect(url_for("purchase"))
    item = db.session.get(Item, purchase.item_id) or abort(404)
    item.stock -= purchase.quantity
    supplier_id = remove_supplier_ledger_entry("purchase", purchase.id)
    db.session.delete(purchase)
    db.session.commit()
    if supplier_id:
        recalculate_supplier_ledger(supplier_id)
        db.session.commit()
    flash("Purchase deleted successfully!", "success")
    return redirect(url_for("purchase"))

@app.route("/sale", methods=["GET", "POST"])
@verified_required
def sale():
    search = request.args.get("search", "").strip()
    query = Sale.query
    if search:
        query = (
            query.join(Customer)
            .join(Item)
            .outerjoin(Category, Item.category_id == Category.id)
            .filter(
                (Customer.name.ilike(f"%{search}%"))
                | (Item.name.ilike(f"%{search}%"))
                | (Category.name.ilike(f"%{search}%"))
            )
        )
    sales, pagination = get_paginated_results(query)
    customers = Customer.query.all()
    items = Item.query.all()
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "").strip()
        item_id = request.form.get("item_id", "").strip()
        quantity = request.form.get("quantity", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        date = request.form.get("date", "").strip()
        form_data = {
            "customer_id": customer_id,
            "item_id": item_id,
            "quantity": quantity,
            "sale_price": sale_price,
            "date": date,
        }
        if not customer_id or not item_id or not quantity or not sale_price or not date:
            flash("All fields are required!", "danger")
            return render_template(
                "sale.html",
                customers=customers,
                items=items,
                sales=sales,
                pagination=pagination,
                search=search,
                form_data=form_data,
            )
        if not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
            return render_template(
                "sale.html",
                customers=customers,
                items=items,
                sales=sales,
                pagination=pagination,
                search=search,
                form_data=form_data,
            )
        if not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0:
            flash("Sale price must be a non-negative number!", "danger")
            return render_template(
                "sale.html",
                customers=customers,
                items=items,
                sales=sales,
                pagination=pagination,
                search=search,
                form_data=form_data,
            )
        try:
            item = db.session.get(Item, item_id) or abort(404)
            if item.stock < int(quantity):
                flash(f"Insufficient stock! Available balance: {item.stock}", "danger")
                return render_template(
                    "sale.html",
                    customers=customers,
                    items=items,
                    sales=sales,
                    pagination=pagination,
                    search=search,
                    form_data=form_data,
                )
            item.stock -= int(quantity)
            sale = Sale(
                customer_id=customer_id,
                item_id=item_id,
                quantity=int(quantity),
                sale_price=float(sale_price),
                date=datetime.strptime(date, "%Y-%m-%d"),
            )
            db.session.add(sale)
            db.session.flush()
            sync_customer_sale(sale)
            db.session.commit()
            flash("Sale recorded successfully!", "success")
            return redirect(url_for("sale"))
        except ValueError:
            flash("Invalid date format! Use YYYY-MM-DD.", "danger")
            return render_template(
                "sale.html",
                customers=customers,
                items=items,
                sales=sales,
                pagination=pagination,
                search=search,
                form_data=form_data,
            )
    return render_template(
        "sale.html",
        customers=customers,
        items=items,
        sales=sales,
        pagination=pagination,
        search=search,
        form_data={},
    )

@app.route("/sale/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_sale(id):
    sale = db.session.get(Sale, id) or abort(404)
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "")
        item_id = request.form.get("item_id", "")
        quantity = request.form.get("quantity", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        date = request.form.get("date", "")
        form_data = {
            "customer_id": customer_id,
            "item_id": item_id,
            "quantity": quantity,
            "sale_price": sale_price,
            "date": date,
        }
        if not customer_id or not item_id or not quantity or not sale_price or not date:
            flash("All fields are required!", "danger")
        elif not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
        elif not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0:
            flash("Sale price must be a non-negative number!", "danger")
        else:
            try:
                item = db.session.get(Item, item_id) or abort(404)
                old_quantity = sale.quantity
                stock_valid = True
                if sale.item_id == int(item_id):
                    available = item.stock + old_quantity
                    if available < int(quantity):
                        flash(f"Insufficient stock! Available balance: {available}", "danger")
                        stock_valid = False
                    else:
                        item.stock = item.stock + old_quantity - int(quantity)
                else:
                    old_item = db.session.get(Item, sale.item_id) or abort(404)
                    if item.stock < int(quantity):
                        flash(f"Insufficient stock! Available balance: {item.stock}", "danger")
                        stock_valid = False
                    else:
                        old_item.stock += old_quantity
                        item.stock -= int(quantity)
                if stock_valid:
                    old_customer_id = sale.customer_id
                    sale.customer_id = customer_id
                    sale.item_id = item_id
                    sale.quantity = int(quantity)
                    sale.sale_price = float(sale_price)
                    sale.date = datetime.strptime(date, "%Y-%m-%d")
                    if old_customer_id != int(customer_id):
                        remove_customer_ledger_entry("sale", sale.id)
                        recalculate_customer_ledger(old_customer_id)
                    sync_customer_sale(sale)
                    db.session.commit()
                    flash("Sale updated successfully!", "success")
                    return redirect(url_for("sale"))
            except ValueError:
                flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        customers = Customer.query.all()
        items = Item.query.all()
        return render_template("edit_sale.html", sale=sale, customers=customers, items=items, form_data=form_data)
    customers = Customer.query.all()
    items = Item.query.all()
    return render_template("edit_sale.html", sale=sale, customers=customers, items=items, form_data={})

@app.route("/sale/delete/<int:id>", methods=["POST"])
@verified_required
def delete_sale(id):
    sale = db.session.get(Sale, id) or abort(404)
    if sale.customer_payments:
        flash("Cannot delete sale with associated receipts! Delete receipts first.", "danger")
        return redirect(url_for("sale"))
    item = db.session.get(Item, sale.item_id) or abort(404)
    item.stock += sale.quantity
    customer_id = remove_customer_ledger_entry("sale", sale.id)
    db.session.delete(sale)
    db.session.commit()
    if customer_id:
        recalculate_customer_ledger(customer_id)
        db.session.commit()
    flash("Sale deleted successfully!", "success")
    return redirect(url_for("sale"))

@app.route("/supplier_payment", methods=["GET", "POST"])
@verified_required
def supplier_payment():
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
        if not supplier_id or not payment_date or amount is None:
            flash("Supplier, amount and payment date are required!", "danger")
        elif payment_method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
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
                    reference_no=reference_no or None,
                    notes=notes or None,
                )
                db.session.add(payment)
                db.session.flush()
                sync_supplier_payment(payment)
                db.session.commit()
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

@app.route("/supplier_payment/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_supplier_payment(id):
    payment = db.session.get(SupplierPayment, id) or abort(404)
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
        if not supplier_id or not payment_date or amount is None:
            flash("Supplier, amount and payment date are required!", "danger")
        elif payment_method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
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
                payment.reference_no = reference_no or None
                payment.notes = notes or None
                if old_supplier_id != int(supplier_id):
                    remove_supplier_ledger_entry("payment", payment.id)
                    recalculate_supplier_ledger(old_supplier_id)
                sync_supplier_payment(payment)
                db.session.commit()
                flash("Supplier payment updated successfully!", "success")
                return redirect(url_for("supplier_payment"))
    return render_template(
        "edit_supplier_payment.html",
        payment=payment,
        suppliers=suppliers,
        purchases=purchases,
    )

@app.route("/supplier_payment/delete/<int:id>", methods=["POST"])
@verified_required
def delete_supplier_payment(id):
    payment = db.session.get(SupplierPayment, id) or abort(404)
    supplier_id = remove_supplier_ledger_entry("payment", payment.id)
    db.session.delete(payment)
    db.session.commit()
    if supplier_id:
        recalculate_supplier_ledger(supplier_id)
        db.session.commit()
    flash("Supplier payment deleted successfully!", "success")
    return redirect(url_for("supplier_payment"))

@app.route("/customer_receipt", methods=["GET", "POST"])
@verified_required
def customer_receipt():
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
        if not customer_id or not payment_date or amount is None:
            flash("Customer, amount and receipt date are required!", "danger")
        elif payment_method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
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
                    reference_no=reference_no or None,
                    notes=notes or None,
                )
                db.session.add(receipt)
                db.session.flush()
                sync_customer_receipt(receipt)
                db.session.commit()
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

@app.route("/customer_receipt/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_customer_receipt(id):
    receipt = db.session.get(CustomerPayment, id) or abort(404)
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
        if not customer_id or not payment_date or amount is None:
            flash("Customer, amount and receipt date are required!", "danger")
        elif payment_method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
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
                receipt.reference_no = reference_no or None
                receipt.notes = notes or None
                if old_customer_id != int(customer_id):
                    remove_customer_ledger_entry("receipt", receipt.id)
                    recalculate_customer_ledger(old_customer_id)
                sync_customer_receipt(receipt)
                db.session.commit()
                flash("Customer receipt updated successfully!", "success")
                return redirect(url_for("customer_receipt"))
    return render_template(
        "edit_customer_receipt.html",
        receipt=receipt,
        customers=customers,
        sales=sales,
    )

@app.route("/customer_receipt/delete/<int:id>", methods=["POST"])
@verified_required
def delete_customer_receipt(id):
    receipt = db.session.get(CustomerPayment, id) or abort(404)
    customer_id = remove_customer_ledger_entry("receipt", receipt.id)
    db.session.delete(receipt)
    db.session.commit()
    if customer_id:
        recalculate_customer_ledger(customer_id)
        db.session.commit()
    flash("Customer receipt deleted successfully!", "success")
    return redirect(url_for("customer_receipt"))

@app.route("/supplier/<int:id>/ledger", methods=["GET", "POST"])
@verified_required
def supplier_ledger(id):
    supplier = db.session.get(Supplier, id) or abort(404)
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    if request.method == "POST" and request.form.get("action") == "adjustment":
        adj_date = request.form.get("adj_date", "").strip()
        adj_type = request.form.get("adj_type", "").strip()
        amount_str = request.form.get("adj_amount", "").strip()
        description = request.form.get("adj_description", "").strip() or "Manual Adjustment"
        amount = parse_payment_amount(amount_str)
        if not adj_date or amount is None or adj_type not in ("debit", "credit"):
            flash("Valid date, type and amount are required for adjustment!", "danger")
        else:
            entry = SupplierLedgerEntry(
                supplier_id=supplier.id,
                entry_date=datetime.strptime(adj_date, "%Y-%m-%d"),
                entry_type="Adjustment",
                source_type="adjustment",
                source_id=None,
                description=description,
                debit=amount if adj_type == "debit" else 0.0,
                credit=amount if adj_type == "credit" else 0.0,
                balance_after=0.0,
            )
            db.session.add(entry)
            db.session.flush()
            entry.source_id = entry.id
            recalculate_supplier_ledger(supplier.id)
            db.session.commit()
            flash("Ledger adjustment added!", "success")
            return redirect(url_for("supplier_ledger", id=id))
    query = SupplierLedgerEntry.query.filter_by(supplier_id=id)
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            query = query.filter(SupplierLedgerEntry.entry_date.between(start_date, end_date))
        except ValueError:
            flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    entries = query.order_by(SupplierLedgerEntry.entry_date.asc(), SupplierLedgerEntry.id.asc()).all()
    balance = get_supplier_balance(id)
    return render_template(
        "supplier_ledger.html",
        supplier=supplier,
        entries=entries,
        balance=balance,
        start_date=start_date_str,
        end_date=end_date_str,
    )

@app.route("/supplier/<int:id>/ledger/adjustment/delete/<int:entry_id>", methods=["POST"])
@verified_required
def delete_supplier_ledger_adjustment(id, entry_id):
    entry = SupplierLedgerEntry.query.filter_by(id=entry_id, supplier_id=id, source_type="adjustment").first() or abort(404)
    db.session.delete(entry)
    recalculate_supplier_ledger(id)
    db.session.commit()
    flash("Adjustment deleted!", "success")
    return redirect(url_for("supplier_ledger", id=id))

@app.route("/supplier/<int:id>/ledger/export")
@verified_required
def export_supplier_ledger(id):
    supplier = db.session.get(Supplier, id) or abort(404)
    entries = (
        SupplierLedgerEntry.query.filter_by(supplier_id=id)
        .order_by(SupplierLedgerEntry.entry_date.asc(), SupplierLedgerEntry.id.asc())
        .all()
    )
    filename = f"supplier_ledger_{id}.csv"
    with open(f"static/{filename}", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Type", "Description", "Debit", "Credit", "Balance"])
        for e in entries:
            writer.writerow([
                e.entry_date.strftime("%Y-%m-%d"), e.entry_type, e.description,
                round(e.debit, 2), round(e.credit, 2), round(e.balance_after, 2),
            ])
    return send_from_directory("static", filename, as_attachment=True, download_name=f"{supplier.name}_ledger.csv")

@app.route("/customer/<int:id>/ledger", methods=["GET", "POST"])
@verified_required
def customer_ledger(id):
    customer = db.session.get(Customer, id) or abort(404)
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    if request.method == "POST" and request.form.get("action") == "adjustment":
        adj_date = request.form.get("adj_date", "").strip()
        adj_type = request.form.get("adj_type", "").strip()
        amount_str = request.form.get("adj_amount", "").strip()
        description = request.form.get("adj_description", "").strip() or "Manual Adjustment"
        amount = parse_payment_amount(amount_str)
        if not adj_date or amount is None or adj_type not in ("debit", "credit"):
            flash("Valid date, type and amount are required for adjustment!", "danger")
        else:
            entry = CustomerLedgerEntry(
                customer_id=customer.id,
                entry_date=datetime.strptime(adj_date, "%Y-%m-%d"),
                entry_type="Adjustment",
                source_type="adjustment",
                source_id=None,
                description=description,
                debit=amount if adj_type == "debit" else 0.0,
                credit=amount if adj_type == "credit" else 0.0,
                balance_after=0.0,
            )
            db.session.add(entry)
            db.session.flush()
            entry.source_id = entry.id
            recalculate_customer_ledger(customer.id)
            db.session.commit()
            flash("Ledger adjustment added!", "success")
            return redirect(url_for("customer_ledger", id=id))
    query = CustomerLedgerEntry.query.filter_by(customer_id=id)
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            query = query.filter(CustomerLedgerEntry.entry_date.between(start_date, end_date))
        except ValueError:
            flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    entries = query.order_by(CustomerLedgerEntry.entry_date.asc(), CustomerLedgerEntry.id.asc()).all()
    balance = get_customer_balance(id)
    return render_template(
        "customer_ledger.html",
        customer=customer,
        entries=entries,
        balance=balance,
        start_date=start_date_str,
        end_date=end_date_str,
    )

@app.route("/customer/<int:id>/ledger/adjustment/delete/<int:entry_id>", methods=["POST"])
@verified_required
def delete_customer_ledger_adjustment(id, entry_id):
    entry = CustomerLedgerEntry.query.filter_by(id=entry_id, customer_id=id, source_type="adjustment").first() or abort(404)
    db.session.delete(entry)
    recalculate_customer_ledger(id)
    db.session.commit()
    flash("Adjustment deleted!", "success")
    return redirect(url_for("customer_ledger", id=id))

@app.route("/customer/<int:id>/ledger/export")
@verified_required
def export_customer_ledger(id):
    customer = db.session.get(Customer, id) or abort(404)
    entries = (
        CustomerLedgerEntry.query.filter_by(customer_id=id)
        .order_by(CustomerLedgerEntry.entry_date.asc(), CustomerLedgerEntry.id.asc())
        .all()
    )
    filename = f"customer_ledger_{id}.csv"
    with open(f"static/{filename}", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Type", "Description", "Debit", "Credit", "Balance"])
        for e in entries:
            writer.writerow([
                e.entry_date.strftime("%Y-%m-%d"), e.entry_type, e.description,
                round(e.debit, 2), round(e.credit, 2), round(e.balance_after, 2),
            ])
    return send_from_directory("static", filename, as_attachment=True, download_name=f"{customer.name}_ledger.csv")

@app.route("/api/supplier/<int:id>/balance")
@verified_required
def api_supplier_balance(id):
    supplier = db.session.get(Supplier, id) or abort(404)
    return {
        "payable": get_supplier_payable(id),
        "paid": get_supplier_paid(id),
        "balance": get_supplier_balance(id),
    }

@app.route("/api/customer/<int:id>/balance")
@verified_required
def api_customer_balance(id):
    customer = db.session.get(Customer, id) or abort(404)
    return {
        "receivable": get_customer_receivable(id),
        "received": get_customer_received(id),
        "balance": get_customer_balance(id),
    }

@app.route("/reports", methods=["GET", "POST"])
@verified_required
def reports():
    purchase_report = sale_report = reorder_report = date_profit_report = item_profit = customer_profit = category_profit = []
    supplier_balances = customer_balances = supplier_payment_history = customer_receipt_history = []
    total_sale_amt = total_profit_amt = 0
    purchase_qty_total = purchase_amt_total = sale_qty_total = sale_amt_total = 0
    supplier_payment_total = customer_receipt_total = 0
    start_date_str = end_date_str = ""
    if request.method == "POST":
        start_date_str = request.form.get("start_date", "")
        end_date_str = request.form.get("end_date", "")
        if not start_date_str or not end_date_str:
            flash("Both dates are required!", "danger")
        else:
            try:
                start_date         = datetime.strptime(start_date_str, "%Y-%m-%d")
                end_date           = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                purchase_report    = Purchase.query.filter(Purchase.date.between(start_date, end_date)).all()
                sale_report        = Sale.query.filter(Sale.date.between(start_date, end_date)).all()
                reorder_report     = Item.query.filter(Item.stock <= Item.reorder_level).all()
                date_profit_report = (
                    db.session.query(
                        db.func.date(Sale.date).label("sale_date"),
                        db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                        db.func.sum((Sale.sale_price - Item.purchase_price) * Sale.quantity).label("profit_amt"),
                    )
                    .join(Item, Sale.item_id == Item.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(db.func.date(Sale.date))
                    .order_by(db.func.date(Sale.date))
                    .all()
                )
                item_profit = (
                    db.session.query(
                        Item.name.label("name"),
                        Category.name.label("category"),
                        db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                        db.func.sum((Sale.sale_price - Item.purchase_price) * Sale.quantity).label("profit_amt"),
                    )
                    .join(Sale, Sale.item_id == Item.id)
                    .outerjoin(Category, Item.category_id == Category.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(Item.name, Category.name)
                    .order_by(Item.name)
                    .all()
                )
                customer_profit = (
                    db.session.query(
                        Customer.name.label("name"),
                        db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                        db.func.sum((Sale.sale_price - Item.purchase_price) * Sale.quantity).label("profit_amt"),
                    )
                    .join(Sale, Sale.customer_id == Customer.id)
                    .join(Item, Sale.item_id == Item.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(Customer.name)
                    .order_by(Customer.name)
                    .all()
                )
                category_profit = (
                    db.session.query(
                        Category.name.label("name"),
                        db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                        db.func.sum((Sale.sale_price - Item.purchase_price) * Sale.quantity).label("profit_amt"),
                    )
                    .join(Item, Sale.item_id == Item.id)
                    .join(Category, Item.category_id == Category.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(Category.name)
                    .order_by(Category.name)
                    .all()
                )
                totals = (
                    db.session.query(
                        db.func.sum(Sale.quantity * Sale.sale_price).label("total_sale_amt"),
                        db.func.sum((Sale.sale_price - Item.purchase_price) * Sale.quantity).label("total_profit_amt"),
                    )
                    .join(Item, Sale.item_id == Item.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .first()
                )
                total_sale_amt = totals.total_sale_amt or 0.0
                total_profit_amt = totals.total_profit_amt or 0.0
                purchase_qty_total = sum(p.quantity for p in purchase_report)
                purchase_amt_total = sum(p.quantity * p.purchase_price for p in purchase_report)
                sale_qty_total = sum(s.quantity for s in sale_report)
                sale_amt_total = sum(s.quantity * s.sale_price for s in sale_report)
                supplier_balances = [
                    {
                        "name": s.name,
                        "opening": float(s.opening_balance or 0),
                        "payable": get_supplier_payable(s.id),
                        "paid": get_supplier_paid(s.id),
                        "balance": get_supplier_balance(s.id),
                        "label": supplier_balance_label(get_supplier_balance(s.id)),
                    }
                    for s in Supplier.query.order_by(Supplier.name).all()
                ]
                customer_balances = [
                    {
                        "name": c.name,
                        "opening": float(c.opening_balance or 0),
                        "receivable": get_customer_receivable(c.id),
                        "received": get_customer_received(c.id),
                        "balance": get_customer_balance(c.id),
                        "label": customer_balance_label(get_customer_balance(c.id)),
                    }
                    for c in Customer.query.order_by(Customer.name).all()
                ]
                supplier_payment_history = (
                    SupplierPayment.query.join(Supplier)
                    .filter(SupplierPayment.payment_date.between(start_date, end_date))
                    .order_by(SupplierPayment.payment_date.desc())
                    .all()
                )
                customer_receipt_history = (
                    CustomerPayment.query.join(Customer)
                    .filter(CustomerPayment.payment_date.between(start_date, end_date))
                    .order_by(CustomerPayment.payment_date.desc())
                    .all()
                )
                supplier_payment_total = sum(p.amount for p in supplier_payment_history)
                customer_receipt_total = sum(r.amount for r in customer_receipt_history)
            except ValueError:
                flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    return render_template(
        "reports.html",
        purchase_report=purchase_report,
        sale_report=sale_report,
        reorder_report=reorder_report,
        date_profit_report=date_profit_report,
        item_profit=item_profit,
        customer_profit=customer_profit,
        category_profit=category_profit,
        supplier_balances=supplier_balances,
        customer_balances=customer_balances,
        supplier_payment_history=supplier_payment_history,
        customer_receipt_history=customer_receipt_history,
        supplier_payment_total=supplier_payment_total,
        customer_receipt_total=customer_receipt_total,
        total_sale_amt=total_sale_amt,
        total_profit_amt=total_profit_amt,
        purchase_qty_total=purchase_qty_total,
        purchase_amt_total=purchase_amt_total,
        sale_qty_total=sale_qty_total,
        sale_amt_total=sale_amt_total,
        start_date=start_date_str,
        end_date=end_date_str,
    )

@app.route("/export_purchase_report", methods=["POST"])
@verified_required
def export_purchase_report():
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        purchases = (
            Purchase.query.join(Supplier)
            .join(Item)
            .filter(Purchase.date.between(start_date, end_date))
            .all()
        )
        with open("static/purchase_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["ID", "Supplier", "Item", "Category", "Quantity", "Purchase Price", "Total", "Date"])
            for purchase in purchases:
                writer.writerow(
                    [
                        purchase.id,
                        purchase.id_supplier.name,
                        purchase.id_item.name,
                        purchase.id_item.id_category.name if purchase.id_item.id_category else "N/A",
                        purchase.quantity,
                        round(purchase.purchase_price, 2),
                        round(purchase.quantity * purchase.purchase_price, 2),
                        purchase.date.strftime("%Y-%m-%d"),
                    ]
                )
        return send_from_directory("static", "purchase_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_sale_report", methods=["POST"])
@verified_required
def export_sale_report():
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        sales = (
            Sale.query.join(Customer)
            .join(Item)
            .filter(Sale.date.between(start_date, end_date))
            .all()
        )
        with open("static/sale_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["ID", "Customer", "Item", "Category", "Quantity", "Sale Price", "Total", "Date"])
            for sale in sales:
                writer.writerow(
                    [
                        sale.id,
                        sale.id_customer.name,
                        sale.id_item.name,
                        sale.id_item.id_category.name if sale.id_item.id_category else "N/A",
                        sale.quantity,
                        round(sale.sale_price, 2),
                        round(sale.quantity * sale.sale_price, 2),
                        sale.date.strftime("%Y-%m-%d"),
                    ]
                )
        return send_from_directory("static", "sale_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_date_sale_report", methods=["POST"])
@verified_required
def export_date_sale_report():
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
                db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                db.func.sum((Sale.sale_price - Item.purchase_price) * Sale.quantity).label("profit_amt"),
            )
            .join(Item, Sale.item_id == Item.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(db.func.date(Sale.date))
            .order_by(db.func.date(Sale.date))
            .all()
        )
        with open("static/date_sale_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Date", "Sale Amount", "Profit Amount"])
            for row in date_sale_report:
                writer.writerow([row.sale_date, round(row.sale_amt, 2), round(row.profit_amt, 2)])
        return send_from_directory("static", "date_sale_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_item_sale_report", methods=["POST"])
@verified_required
def export_item_sale_report():
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
                db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                db.func.sum((Sale.sale_price - Item.purchase_price) * Sale.quantity).label("profit_amt"),
            )
            .join(Sale, Sale.item_id == Item.id)
            .outerjoin(Category, Item.category_id == Category.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(Item.name, Category.name)
            .order_by(Item.name)
            .all()
        )
        with open("static/item_sale_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Item", "Category", "Sale Amount", "Profit Amount"])
            for row in item_sale:
                writer.writerow([row.name, row.category or "N/A", round(row.sale_amt, 2), round(row.profit_amt, 2)])
        return send_from_directory("static", "item_sale_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_customer_sale_report", methods=["POST"])
@verified_required
def export_customer_sale_report():
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
                db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                db.func.sum((Sale.sale_price - Item.purchase_price) * Sale.quantity).label("profit_amt"),
            )
            .join(Sale, Sale.customer_id == Customer.id)
            .join(Item, Sale.item_id == Item.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(Customer.name)
            .order_by(Customer.name)
            .all()
        )
        with open("static/customer_sale_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Customer", "Sale Amount", "Profit Amount"])
            for row in customer_sale:
                writer.writerow([row.name, round(row.sale_amt, 2), round(row.profit_amt, 2)])
        return send_from_directory("static", "customer_sale_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_category_sale_report", methods=["POST"])
@verified_required
def export_category_sale_report():
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
                db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                db.func.sum((Sale.sale_price - Item.purchase_price) * Sale.quantity).label("profit_amt"),
            )
            .join(Item, Sale.item_id == Item.id)
            .join(Category, Item.category_id == Category.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(Category.name)
            .order_by(Category.name)
            .all()
        )
        with open("static/category_sale_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Category", "Sale Amount", "Profit Amount"])
            for row in category_sale:
                writer.writerow([row.name, round(row.sale_amt, 2), round(row.profit_amt, 2)])
        return send_from_directory("static", "category_sale_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_supplier_payable")
@verified_required
def export_supplier_payable():
    with open("static/supplier_payable_report.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Supplier", "Opening", "Bills", "Paid", "Ledger Balance", "Status"])
        for s in Supplier.query.order_by(Supplier.name).all():
            bal = get_supplier_balance(s.id)
            writer.writerow([
                s.name,
                round(float(s.opening_balance or 0), 2),
                round(get_supplier_payable(s.id), 2),
                round(get_supplier_paid(s.id), 2),
                round(bal, 2),
                supplier_balance_label(bal),
            ])
    return send_from_directory("static", "supplier_payable_report.csv")

@app.route("/export_customer_receivable")
@verified_required
def export_customer_receivable():
    with open("static/customer_receivable_report.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Customer", "Opening", "Sales", "Received", "Ledger Balance", "Status"])
        for c in Customer.query.order_by(Customer.name).all():
            bal = get_customer_balance(c.id)
            writer.writerow([
                c.name,
                round(float(c.opening_balance or 0), 2),
                round(get_customer_receivable(c.id), 2),
                round(get_customer_received(c.id), 2),
                round(bal, 2),
                customer_balance_label(bal),
            ])
    return send_from_directory("static", "customer_receivable_report.csv")

@app.route("/export_supplier_payment_history", methods=["POST"])
@verified_required
def export_supplier_payment_history():
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        payments = (
            SupplierPayment.query.join(Supplier)
            .filter(SupplierPayment.payment_date.between(start_date, end_date))
            .order_by(SupplierPayment.payment_date.desc())
            .all()
        )
        with open("static/supplier_payment_history.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["ID", "Supplier", "Purchase ID", "Amount", "Method", "Reference", "Date", "Notes"])
            for p in payments:
                writer.writerow([
                    p.id, p.supplier.name, p.purchase_id or "General",
                    round(p.amount, 2), p.payment_method,
                    p.reference_no or "", p.payment_date.strftime("%Y-%m-%d"), p.notes or "",
                ])
        return send_from_directory("static", "supplier_payment_history.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_customer_receipt_history", methods=["POST"])
@verified_required
def export_customer_receipt_history():
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        receipts = (
            CustomerPayment.query.join(Customer)
            .filter(CustomerPayment.payment_date.between(start_date, end_date))
            .order_by(CustomerPayment.payment_date.desc())
            .all()
        )
        with open("static/customer_receipt_history.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["ID", "Customer", "Sale ID", "Amount", "Method", "Reference", "Date", "Notes"])
            for r in receipts:
                writer.writerow([
                    r.id, r.customer.name, r.sale_id or "General",
                    round(r.amount, 2), r.payment_method,
                    r.reference_no or "", r.payment_date.strftime("%Y-%m-%d"), r.notes or "",
                ])
        return send_from_directory("static", "customer_receipt_history.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/purchase_return", methods=["GET", "POST"])
@verified_required
def purchase_return():
    search = request.args.get("search", "").strip()
    query = PurchaseReturn.query.order_by(PurchaseReturn.date.desc())
    if search:
        query = (
            query.join(Supplier, PurchaseReturn.supplier_id == Supplier.id)
            .join(Item, PurchaseReturn.item_id == Item.id)
            .filter((Supplier.name.ilike(f"%{search}%")) | (Item.name.ilike(f"%{search}%")))
        )
    returns, pagination = get_paginated_results(query)
    all_purchases = Purchase.query.order_by(Purchase.date.desc()).all()
    purchases_available = [
        {"p": p, "remaining": p.quantity - get_purchase_returned_qty(p.id)}
        for p in all_purchases
        if p.quantity - get_purchase_returned_qty(p.id) > 0
    ]
    if request.method == "POST":
        purchase_id = request.form.get("purchase_id", "").strip()
        quantity    = request.form.get("quantity", "").strip()
        return_price = request.form.get("return_price", "").strip()
        date        = request.form.get("date", "").strip()
        reason      = request.form.get("reason", "").strip()
        if not purchase_id or not quantity or not return_price or not date:
            flash("Purchase, quantity, price and date are required!", "danger")
        elif not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
        elif not return_price.replace(".", "", 1).isdigit() or float(return_price) < 0:
            flash("Return price must be a non-negative number!", "danger")
        else:
            purchase = db.session.get(Purchase, int(purchase_id)) or abort(404)
            remaining = purchase.quantity - get_purchase_returned_qty(purchase.id)
            if int(quantity) > remaining:
                flash(f"Cannot return more than remaining quantity ({remaining})!", "danger")
            else:
                try:
                    item = db.session.get(Item, purchase.item_id) or abort(404)
                    pr = PurchaseReturn(
                        purchase_id=purchase.id,
                        supplier_id=purchase.supplier_id,
                        item_id=purchase.item_id,
                        quantity=int(quantity),
                        return_price=float(return_price),
                        date=datetime.strptime(date, "%Y-%m-%d"),
                        reason=reason or None,
                    )
                    item.stock -= int(quantity)
                    db.session.add(pr)
                    db.session.flush()
                    sync_supplier_purchase_return(pr)
                    db.session.commit()
                    flash("Purchase return recorded successfully!", "success")
                    return redirect(url_for("purchase_return"))
                except ValueError:
                    flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    return render_template(
        "purchase_return.html",
        returns=returns,
        purchases_available=purchases_available,
        pagination=pagination,
        search=search,
    )

@app.route("/purchase_return/delete/<int:id>", methods=["POST"])
@verified_required
def delete_purchase_return(id):
    pr = db.session.get(PurchaseReturn, id) or abort(404)
    item = db.session.get(Item, pr.item_id)
    if item:
        item.stock += pr.quantity
    supplier_id = remove_supplier_ledger_entry("purchase_return", pr.id)
    db.session.delete(pr)
    db.session.commit()
    if supplier_id:
        recalculate_supplier_ledger(supplier_id)
        db.session.commit()
    flash("Purchase return deleted successfully!", "success")
    return redirect(url_for("purchase_return"))

@app.route("/sale_return", methods=["GET", "POST"])
@verified_required
def sale_return():
    search = request.args.get("search", "").strip()
    query = SaleReturn.query.order_by(SaleReturn.date.desc())
    if search:
        query = (
            query.join(Customer, SaleReturn.customer_id == Customer.id)
            .join(Item, SaleReturn.item_id == Item.id)
            .filter((Customer.name.ilike(f"%{search}%")) | (Item.name.ilike(f"%{search}%")))
        )
    returns, pagination = get_paginated_results(query)
    all_sales = Sale.query.order_by(Sale.date.desc()).all()
    sales_available = [
        {"s": s, "remaining": s.quantity - get_sale_returned_qty(s.id)}
        for s in all_sales
        if s.quantity - get_sale_returned_qty(s.id) > 0
    ]
    if request.method == "POST":
        sale_id      = request.form.get("sale_id", "").strip()
        quantity     = request.form.get("quantity", "").strip()
        return_price = request.form.get("return_price", "").strip()
        date         = request.form.get("date", "").strip()
        reason       = request.form.get("reason", "").strip()
        if not sale_id or not quantity or not return_price or not date:
            flash("Sale, quantity, price and date are required!", "danger")
        elif not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
        elif not return_price.replace(".", "", 1).isdigit() or float(return_price) < 0:
            flash("Return price must be a non-negative number!", "danger")
        else:
            sale = db.session.get(Sale, int(sale_id)) or abort(404)
            remaining = sale.quantity - get_sale_returned_qty(sale.id)
            if int(quantity) > remaining:
                flash(f"Cannot return more than remaining quantity ({remaining})!", "danger")
            else:
                try:
                    item = db.session.get(Item, sale.item_id) or abort(404)
                    sr = SaleReturn(
                        sale_id=sale.id,
                        customer_id=sale.customer_id,
                        item_id=sale.item_id,
                        quantity=int(quantity),
                        return_price=float(return_price),
                        date=datetime.strptime(date, "%Y-%m-%d"),
                        reason=reason or None,
                    )
                    item.stock += int(quantity)
                    db.session.add(sr)
                    db.session.flush()
                    sync_customer_sale_return(sr)
                    db.session.commit()
                    flash("Sale return recorded successfully!", "success")
                    return redirect(url_for("sale_return"))
                except ValueError:
                    flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    return render_template(
        "sale_return.html",
        returns=returns,
        sales_available=sales_available,
        pagination=pagination,
        search=search,
    )

@app.route("/sale_return/delete/<int:id>", methods=["POST"])
@verified_required
def delete_sale_return(id):
    sr = db.session.get(SaleReturn, id) or abort(404)
    item = db.session.get(Item, sr.item_id)
    if item:
        item.stock -= sr.quantity
    customer_id = remove_customer_ledger_entry("sale_return", sr.id)
    db.session.delete(sr)
    db.session.commit()
    if customer_id:
        recalculate_customer_ledger(customer_id)
        db.session.commit()
    flash("Sale return deleted successfully!", "success")
    return redirect(url_for("sale_return"))

@app.route("/purchase/<int:id>/invoice")
@verified_required
def purchase_invoice(id):
    purchase = db.session.get(Purchase, id) or abort(404)
    paid     = get_purchase_paid(id)
    total    = purchase_total(purchase)
    status   = get_payment_status(total, paid)
    returned_qty = get_purchase_returned_qty(id)
    return render_template(
        "invoice_purchase.html",
        purchase=purchase,
        paid=paid,
        total=total,
        balance=total - paid,
        status=status,
        returned_qty=returned_qty,
    )

@app.route("/sale/<int:id>/invoice")
@verified_required
def sale_invoice(id):
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

@app.cli.command("create-user")
@click.option("--name", prompt="Name")
@click.option("--email", prompt="Email")
@click.option("--password", prompt="Password", hide_input=True, confirmation_prompt="Confirm password")
def create_user_cmd(name, email, password):
    """Create a verified user (administrator only)."""
    email = email.strip().lower()
    name = name.strip()
    if not name or not email:
        click.echo("Name and email are required.")
        return
    if len(password) < 6:
        click.echo("Password must be at least 6 characters.")
        return
    if User.query.filter_by(email=email).first():
        click.echo(f"Email {email} is already registered.")
        return
    user = User(name=name, email=email, password=pwd_context.hash(password), verified=True)
    db.session.add(user)
    db.session.commit()
    click.echo(f"User created: {email} (verified)")

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5172)