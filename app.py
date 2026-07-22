#app.py
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, abort, Response
from flask_paginate import Pagination, get_page_args
from flask_login import UserMixin, login_user, logout_user, login_required, current_user
import click
from datetime import datetime, timedelta, timezone, date
from functools import wraps
import csv
from io import BytesIO, StringIO
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import os
import secrets
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from sqlalchemy.exc import IntegrityError
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from itsdangerous import URLSafeTimedSerializer
from urllib.parse import urlsplit
from dotenv import load_dotenv
from sqlalchemy.sql import func
from sqlalchemy import inspect, text, or_, and_

app = Flask(__name__)


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # Render / PostgreSQL — Render deta hai "postgres://" lekin SQLAlchemy 1.4+ ko "postgresql://" chahiye
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
else:
    # Local PC (SQLite)
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" +
        os.path.join(BASE_DIR, "instance", "database.db").replace("\\", "/")
    )

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "your_secret_key")
app.config["SECURITY_PASSWORD_SALT"] = os.getenv("SECURITY_PASSWORD_SALT", "your_salt")

_using_insecure_defaults = (
    app.config["SECRET_KEY"] == "your_secret_key"
    or app.config["SECURITY_PASSWORD_SALT"] == "your_salt"
)
if _using_insecure_defaults and DATABASE_URL:
    # DATABASE_URL only set on real deployments (e.g. Render) — never run those on default secrets.
    raise RuntimeError(
        "SECRET_KEY / SECURITY_PASSWORD_SALT are still set to their insecure defaults. "
        "Set real random values for both in your .env / environment before deploying."
    )
elif _using_insecure_defaults:
    print("WARNING: SECRET_KEY / SECURITY_PASSWORD_SALT are using insecure defaults. Set real values in .env before deploying.")

# ── Session / cookie hardening ────────────────────────────────────────────────
# Secure cookies are only sent over HTTPS, so enabling that locally (plain HTTP)
# would break login. Gate it on production, detected by DATABASE_URL being set
# (Render). HttpOnly stops JS from reading the cookie; SameSite=Lax blocks it on
# cross-site POSTs (defence-in-depth on top of CSRF tokens).
_is_production = bool(DATABASE_URL)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = _is_production
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SECURE"] = _is_production
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "").strip()
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "").replace(" ", "")
# ── Branding — single source of truth ────────────────────────────────────────
# These three come from .env ONLY. Everywhere else in the code and templates use
# app.config["APP_NAME"] / ["COMPANY_NAME"] / ["COMPANY_TAGLINE"] (or the
# app_name / company_name / company_tagline template vars) — never hardcode the
# literal text again, so the value can only ever be changed in .env.
#   APP_NAME          — the product/app name (e.g. shown on the home hero)
#   COMPANY_NAME      — the business using the app (navbar, invoices, reports)
#   COMPANY_TAGLINE   — short subtitle under the company name
#   DESIGNED_DEVELOPED— the developer credit ("Designed & Developed by ...")
app.config["APP_NAME"] = os.getenv("APP_NAME", "TradeFlow").strip()
app.config["COMPANY_NAME"] = os.getenv("COMPANY_NAME", app.config["APP_NAME"]).strip()
app.config["COMPANY_TAGLINE"] = os.getenv("COMPANY_TAGLINE", "Inventory & Accounts Management").strip()
app.config["DESIGNED_DEVELOPED"] = os.getenv("DESIGNED_DEVELOPED", "Sabir Shah").strip()

# Gmail App Password: https://myaccount.google.com/apppasswords
# .env file (project root) mein MAIL_USERNAME aur MAIL_PASSWORD set karein


# ── Logging ───────────────────────────────────────────────────────────────────
# Structured logs to stderr (captured by Render / any process manager). LOG_LEVEL
# can be overridden via env; defaults to INFO. Replaces the scattered print()s so
# there's a single, timestamped, level-tagged stream to debug production issues.
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
))
_log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
for _lg in (app.logger, logging.getLogger("werkzeug")):
    _lg.setLevel(_log_level)
if not app.logger.handlers:
    app.logger.addHandler(_log_handler)
app.logger.propagate = False

# ── Error monitoring (optional) ───────────────────────────────────────────────
# Enabled only when SENTRY_DSN is set, so it's a no-op locally and for anyone who
# hasn't set up Sentry. Reports unhandled exceptions with request context.
_sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(dsn=_sentry_dsn, integrations=[FlaskIntegration()],
                        traces_sample_rate=0.0, send_default_pii=False)
        app.logger.info("Sentry error monitoring enabled")
    except Exception as e:
        app.logger.warning("SENTRY_DSN set but Sentry could not start: %s", e)

# ── Timezone ──────────────────────────────────────────────────────────────────
# Datetimes are stored in UTC; they're displayed in APP_TIMEZONE (an IANA name
# like "Asia/Karachi"). Set it once in .env — invalid names fall back to UTC.
#   to_local(dt)   — convert a stored (UTC) datetime to the local zone for display
#   now_local()    — current time in the local zone (for "generated on" stamps)
#   {{ dt|localdt }}  — Jinja filter: format a stored datetime in the local zone
app.config["APP_TIMEZONE"] = os.getenv("APP_TIMEZONE", "UTC").strip() or "UTC"
_UTC = ZoneInfo("UTC")
try:
    APP_TZ = ZoneInfo(app.config["APP_TIMEZONE"])
except Exception:
    app.logger.warning("Invalid APP_TIMEZONE %r — falling back to UTC", app.config["APP_TIMEZONE"])
    app.config["APP_TIMEZONE"] = "UTC"
    APP_TZ = _UTC

# The month a fiscal year starts in. Pakistan runs July–June, the UK April–March, the UAE
# and the US January–December. It has to be a setting: a business cannot file its accounts
# against a year its tax authority does not recognise.
try:
    FISCAL_YEAR_START_MONTH = int(os.getenv("FISCAL_YEAR_START_MONTH", "1"))
    if not 1 <= FISCAL_YEAR_START_MONTH <= 12:
        raise ValueError
except ValueError:
    app.logger.warning("FISCAL_YEAR_START_MONTH must be 1-12 — falling back to January")
    FISCAL_YEAR_START_MONTH = 1
app.config["FISCAL_YEAR_START_MONTH"] = FISCAL_YEAR_START_MONTH

# What the money is. Shown, never converted — this system keeps one company's books in one
# currency, and printing a figure with no unit on it is how a quote in rupees gets paid in
# dollars.
app.config["CURRENCY"] = os.getenv("CURRENCY", "Rs").strip()

def to_local(dt):
    """A stored datetime (assumed UTC; naive or aware) -> aware datetime in APP_TZ."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(APP_TZ)

def now_local():
    """What time it is *for this business*, naive, in APP_TZ.

    This — not datetime.now() — is the clock the app runs on. datetime.now() reads the
    machine's clock, and the machine is a server in someone else's data centre: on Render
    it is UTC, five hours behind Karachi. Every business date derived from it is therefore
    the server's idea of today, not the user's.

    That is not a cosmetic difference. An invoice entered at 2am in Karachi lands on
    yesterday's date. A month-end entry posted on the 1st lands in the previous month. And
    a document reversed in the morning is stamped five hours in the past, so a report run
    "as of now" shows the sale and not the reversal — the cancelled invoice goes on earning
    profit that was never made.

    Timestamps are different: when a row was *saved* is a moment in time, and those stay in
    UTC and are converted for display (to_local, the localdt filter). The rule is the same
    one the bizdate filter draws: a business date is a date, not an instant.
    """
    return datetime.now(APP_TZ).replace(tzinfo=None)

@app.template_filter("localdt")
def localdt_filter(dt, fmt="%Y-%m-%d %H:%M"):
    """For a *moment*: when a row was created, when a document was reversed. These are
    instants, so they are worth showing in the reader's own time zone."""
    local = to_local(dt)
    return local.strftime(fmt) if local else ""

@app.template_filter("bizdate")
def bizdate_filter(dt, fmt="%Y-%m-%d"):
    """For a *business date*: the date a document bears. An invoice dated 31 July is
    dated 31 July in every office on earth — it is not an instant and must never be
    shifted between time zones.

    Depreciation for July is dated the last moment of July. Run it through the local
    shift and 31 July 23:59 becomes 1 August, so a July charge printed as August and
    the ledger appeared to disagree with itself."""
    return dt.strftime(fmt) if dt else ""


# Initialize extensions using the Flask Extensions Pattern
# Import extensions as standalone instances and register them with the app
from salpurflask.extensions import db, csrf, pwd_context, login_manager

# Call init_app to register extensions with Flask app
db.init_app(app)
csrf.init_app(app)
login_manager.init_app(app)

# Import all models and helpers to register them with the db instance
# Must happen AFTER db.init_app(app)
# Wildcard import is safe here because salpurflask.models explicitly defines __all__
from salpurflask.models import *

# Register blueprints
from salpurflask.routes import auth_bp, dashboard_bp
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)

def sql_date_fmt(col, fmt="%Y-%m"):
    if db.engine.dialect.name == "postgresql":
        return db.func.to_char(col, fmt.replace("%Y", "YYYY").replace("%m", "MM"))
    return db.func.strftime(fmt, col)

def is_signup_allowed():
    return os.getenv("ALLOW_SIGNUP", "false").lower() in ("1", "true", "yes")

def is_demo_mode():
    return os.getenv("DEMO_MODE", "false").lower() in ("1", "true", "yes")

def get_standard_tax_rate():
    """The single rate an admin sets on /tax_codes for their own country (17% Pakistan
    sales tax, 20% UK VAT, 8.5% a US state's sales tax, ...). Used only as a default on
    new document lines — each line can still be overridden or zeroed independently."""
    code = TaxCode.query.filter_by(name="Standard").first()
    return code.total_rate if code else 0.0

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
        msg["From"] = f"{app.config['APP_NAME']} <{mail_user}>"
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

def check_rate_limit(key, max_attempts=5, window_seconds=300):
    """Database-backed throttle. Returns False when the caller should be blocked.

    Stored in the DB (not an in-process dict) so the limit is shared across all
    gunicorn workers and survives restarts — a per-process dict silently let an
    attacker get max_attempts * worker_count tries and reset on every deploy.
    Fails open: if the rate-limit table itself errors, never lock a user out.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(seconds=window_seconds)
    try:
        # Drop this key's expired hits, plus anything older than a day globally so
        # one-off hits from many IPs can't grow the table without bound.
        RateLimitHit.query.filter(
            (RateLimitHit.created_at < now - timedelta(days=1)) |
            ((RateLimitHit.key == key) & (RateLimitHit.created_at < cutoff))
        ).delete(synchronize_session=False)
        recent = RateLimitHit.query.filter(
            RateLimitHit.key == key, RateLimitHit.created_at >= cutoff
        ).count()
        if recent >= max_attempts:
            db.session.commit()
            app.logger.warning("Rate limit hit for key=%s (%s attempts)", key, recent)
            return False
        db.session.add(RateLimitHit(key=key, created_at=now))
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        app.logger.exception("Rate-limit check failed; allowing request (fail-open)")
        return True

def verified_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.verified:
            flash(f"Please verify {current_user.email} to access this page.", "danger")
            return redirect(url_for("signin"))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*roles):
    """Decorator: user must be verified AND have one of the given roles."""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if not current_user.verified:
                flash(f"Please verify {current_user.email} to access this page.", "danger")
                return redirect(url_for("auth.signin"))
            if current_user.role not in roles:
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("purchase"))
            return f(*args, **kwargs)
        return decorated
    return decorator

def admin_required(f):
    return role_required("admin")(f)

def manager_required(f):
    return role_required("admin", "manager")(f)

# Models
ROLES = ("admin", "manager", "staff")

def record_audit(action, entity, entity_id=None, summary=""):
    """Write an audit entry in its own transaction. Called AFTER the business
    change has committed, so a failure here can never roll back or break the real
    operation — auditing is best-effort by design."""
    try:
        if current_user and current_user.is_authenticated:
            uid, uname = current_user.id, current_user.name
        else:
            uid, uname = None, "system"
        db.session.add(AuditLog(action=action, entity=entity, entity_id=entity_id,
                                summary=(summary or "")[:300], user_id=uid, user_name=uname))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to record audit entry (%s %s)", action, entity)

_PHONE_PUNCTUATION = set("+-() ./")

def valid_phone(raw):
    """A phone number, as people anywhere actually write one.

    The check used to be `contact.isdigit() and len(contact) >= 10`. That accepts
    03001234567 and rejects every other way a number is written on earth:
    +44 20 7946 0958, (212) 555-0143, +971 50 123 4567 — and +92 300 1234567, which is the
    same Karachi number with its country code on it. Anyone outside Pakistan, and anyone
    inside it dealing with a foreign supplier, simply could not save a contact.

    So: allow the punctuation numbers are written with, and count the digits. Seven at the
    least (the shortest usable subscriber numbers), fifteen at the most, which is the limit
    the ITU sets for an international number. The number is stored exactly as it was typed,
    because how a person writes their own phone number is information too.
    """
    raw = (raw or "").strip()
    digits = sum(1 for c in raw if c.isdigit())
    if not 7 <= digits <= 15:
        return False
    return all(c.isdigit() or c in _PHONE_PUNCTUATION for c in raw)

def calc_discount_tax(gross, discount_type, discount_value, tax_percent):
    """Returns (discount_amt, tax_amt, net_total). discount_type: 'percent' or 'fixed'."""
    gross = float(gross or 0)          # tolerate Decimal/str inputs
    dv = float(discount_value or 0)
    tp = float(tax_percent or 0)
    if discount_type == "fixed":
        disc = min(dv, gross)
    else:
        disc = gross * dv / 100
    taxable = gross - disc
    tax = taxable * tp / 100
    return round(disc, 4), round(tax, 4), round(taxable + tax, 4)

def calc_discount_tax(gross, discount_type, discount_value, tax_percent):
    """Returns (discount_amt, tax_amt, net_total). discount_type: 'percent' or 'fixed'."""
    gross = float(gross or 0)          # tolerate Decimal/str inputs
    dv = float(discount_value or 0)
    tp = float(tax_percent or 0)
    if discount_type == "fixed":
        disc = min(dv, gross)
    else:
        disc = gross * dv / 100
    taxable = gross - disc
    tax = taxable * tp / 100
    return round(disc, 4), round(tax, 4), round(taxable + tax, 4)

def purchase_item_total(pi):
    gross = float(pi.quantity * pi.purchase_price)
    _, _, net = calc_discount_tax(gross, pi.discount_type or "percent", pi.discount_value or 0, pi.tax_percent or 0)
    return net

def purchase_total(purchase):
    if purchase.line_items:
        return sum(purchase_item_total(pi) for pi in purchase.line_items)
    if purchase.quantity and purchase.purchase_price:
        gross = float(purchase.quantity * purchase.purchase_price)
        _, _, net = calc_discount_tax(gross, purchase.discount_type or "percent", purchase.discount_value or 0, purchase.tax_percent or 0)
        return net
    return 0.0

def sale_item_total(si):
    gross = float(si.quantity * si.sale_price)
    _, _, net = calc_discount_tax(gross, si.discount_type or "percent", si.discount_value or 0, si.tax_percent or 0)
    return net

def sale_total(sale):
    if sale.line_items:
        return sum(sale_item_total(si) for si in sale.line_items)
    if sale.quantity and sale.sale_price:
        gross = float(sale.quantity * sale.sale_price)
        _, _, net = calc_discount_tax(gross, sale.discount_type or "percent", sale.discount_value or 0, sale.tax_percent or 0)
        return net
    return 0.0

def quotation_item_net(qi):
    gross = float(qi.quantity * qi.sale_price)
    _, _, net = calc_discount_tax(gross, qi.discount_type or "percent", qi.discount_value or 0, qi.tax_percent or 0)
    return net

def quotation_total(q):
    return sum(quotation_item_net(qi) for qi in q.line_items)

def get_purchase_paid(purchase_id, exclude_payment_id=None):
    query = (db.session.query(func.sum(SupplierPayment.amount))
             .filter(SupplierPayment.purchase_id == purchase_id,
                     SupplierPayment.is_reversed.is_(False)))
    if exclude_payment_id:
        query = query.filter(SupplierPayment.id != exclude_payment_id)
    return float(query.scalar() or 0.0)

def get_sale_received(sale_id, exclude_payment_id=None):
    query = (db.session.query(func.sum(CustomerPayment.amount))
             .filter(CustomerPayment.sale_id == sale_id,
                     CustomerPayment.is_reversed.is_(False)))
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
    # A reversed purchase was cancelled — it owes the supplier nothing.
    purchases = Purchase.query.filter_by(supplier_id=supplier_id, is_reversed=False).all()
    return sum(purchase_total(p) for p in purchases)

def get_supplier_paid(supplier_id, exclude_payment_id=None):
    query = (db.session.query(func.sum(SupplierPayment.amount))
             .filter(SupplierPayment.supplier_id == supplier_id,
                     SupplierPayment.is_reversed.is_(False)))
    if exclude_payment_id:
        query = query.filter(SupplierPayment.id != exclude_payment_id)
    return float(query.scalar() or 0.0)

def get_customer_receivable(customer_id):
    sales = Sale.query.filter_by(customer_id=customer_id, is_reversed=False).all()
    return sum(sale_total(s) for s in sales)

def get_customer_received(customer_id, exclude_payment_id=None):
    query = (db.session.query(func.sum(CustomerPayment.amount))
             .filter(CustomerPayment.customer_id == customer_id,
                     CustomerPayment.is_reversed.is_(False)))
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

def _total_ledger_balance(entry_table, party_col, party_table):
    """Sum of every party's latest ledger balance in ONE query, instead of one
    query per party (the dashboard used to do N+1). Adds the opening balance of
    any party that has no ledger rows yet, to stay exactly equal to summing
    get_*_balance() over all parties."""
    latest = db.session.execute(text(
        f"SELECT COALESCE(SUM(balance_after), 0) FROM ("
        f"  SELECT balance_after, ROW_NUMBER() OVER "
        f"  (PARTITION BY {party_col} ORDER BY entry_date DESC, id DESC) AS rn "
        f"  FROM {entry_table}) t WHERE rn = 1"
    )).scalar() or 0
    orphan = db.session.execute(text(
        f"SELECT COALESCE(SUM(opening_balance), 0) FROM \"{party_table}\" p "
        f"WHERE NOT EXISTS (SELECT 1 FROM {entry_table} e WHERE e.{party_col} = p.id)"
    )).scalar() or 0
    return float(latest) + float(orphan)

def total_supplier_ledger_balance():
    return _total_ledger_balance("supplier_ledger_entry", "supplier_id", "supplier")

def total_customer_ledger_balance():
    return _total_ledger_balance("customer_ledger_entry", "customer_id", "customer")

# ── Cash / Bank accounts (derived balances) ───────────────────────────────────
# Balances come off the account's GL leaf. There used to be a second way — summing an
# account's receipts, payments and expenses directly — and it is gone rather than left
# lying around, because it looked authoritative and was not: it could not see a fixed
# asset bought with cash, or a journal entry posted to the bank, and anything built on
# it would quietly disagree with the balance sheet all over again.
#
# The rule for which movements belong to which account still exists, but only where it
# is actually needed: _resolve_financial_account(), which decides the GL leaf a movement
# posts to (explicit account_id, or the legacy payment_method match).

def get_account_balance(account):
    """The balance of the account's GL leaf: debit minus credit, which reads positive
    for cash.

    Summed from the ledger rather than from receipts, payments and expenses, because
    those three are no longer the only things that move cash. Buying a fixed asset
    does, so does a disposal, so does a manual journal entry — and none of them is a
    payment or an expense. Counting only the three left this page reading a balance
    the balance sheet disagreed with, and the balance sheet was the one telling the
    truth: it reads the ledger, where every movement lands.

    The opening balance is in the ledger too (post_account_opening), so it is not
    added again here. Read-only; touches nothing."""
    if not account.gl_account_id:
        return 0.0
    q = (db.session.query(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
         .filter(JournalLine.account_id == account.gl_account_id))
    return float(q.scalar() or 0)

def total_cash_bank_balance():
    return sum(get_account_balance(a) for a in FinancialAccount.query.all())

def get_selectable_accounts():
    """Return only leaf/subsidiary accounts (is_control=False), ordered hierarchically.
    These are the accounts shown in POS and payment forms."""
    return FinancialAccount.query.filter_by(is_control=False).order_by(FinancialAccount.parent_id, FinancialAccount.name).all()

def get_active_control_accounts():
    """Return only active control accounts (is_control=True) for admin hierarchies."""
    return FinancialAccount.query.filter_by(is_control=True, is_active=True).order_by(FinancialAccount.name).all()

def account_transactions(account):
    """Chronological list of movements for an account's ledger view, read off the
    account's GL leaf — the same place get_account_balance reads, so the rows always
    add up to the balance shown above them. Listing receipts, payments and expenses
    instead would silently drop every other thing that moves cash: a fixed asset
    bought, an asset sold, a journal entry posted straight to the bank."""
    if not account.gl_account_id:
        return []
    lines = (db.session.query(JournalLine, JournalEntry)
             .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
             .filter(JournalLine.account_id == account.gl_account_id)
             .order_by(JournalEntry.entry_date.asc(), JournalEntry.id.asc()).all())
    rows = []
    for line, entry in lines:
        debit, credit = float(line.debit or 0), float(line.credit or 0)
        rows.append({"date": entry.entry_date,
                     "desc": entry.description,
                     "inflow": debit, "outflow": credit})
    return rows

def active_accounts():
    """Return active selectable accounts (is_control=False) for POS and payment forms.
    Control accounts are headers only and not selectable."""
    return (FinancialAccount.query.filter_by(is_active=True, is_control=False)
            .order_by(FinancialAccount.parent_id, FinancialAccount.name).all())

def parse_account_id(raw):
    """Form value → (account_id or None, error or None). Blank means 'untagged',
    which stays valid so the account dropdown can be left empty."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if not raw.isdigit():
        return None, "Invalid account!"
    acct = db.session.get(FinancialAccount, int(raw))
    if acct is None or not acct.is_active or acct.is_control:
        return None, "Invalid account!"
    return acct.id, None

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
        db.session.flush()
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
        db.session.flush()
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
    if purchase.line_items:
        names = [pi.item.name for pi in purchase.line_items if pi.item]
        item_desc = names[0] if len(names) == 1 else f"{len(names)} items"
    else:
        item = db.session.get(Item, purchase.item_id)
        item_desc = item.name if item else "Item"
    total = purchase_total(purchase)
    upsert_supplier_ledger(
        purchase.supplier_id,
        "purchase",
        purchase.id,
        purchase.date,
        "Purchase",
        f"Purchase #{purchase.id} — {item_desc}",
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
    if sale.line_items:
        names = [si.item.name for si in sale.line_items if si.item]
        item_desc = names[0] if len(names) == 1 else f"{len(names)} items"
    else:
        item = db.session.get(Item, sale.item_id)
        item_desc = item.name if item else "Item"
    total = sale_total(sale)
    upsert_customer_ledger(
        sale.customer_id,
        "sale",
        sale.id,
        sale.date,
        "Sale",
        f"Sale #{sale.id} — {item_desc}",
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
    for pr in PurchaseReturn.query.all():
        sync_supplier_purchase_return(pr)
    for sr in SaleReturn.query.all():
        sync_customer_sale_return(sr)
    db.session.commit()

def get_total_payable():
    return float(db.session.query(func.sum(PurchaseItem.amount)).scalar() or 0.0)

def get_total_paid_suppliers():
    return float(db.session.query(func.sum(SupplierPayment.amount)).scalar() or 0.0)

def get_total_receivable():
    return float(db.session.query(func.sum(SaleItem.amount)).scalar() or 0.0)

def get_total_received_customers():
    return float(db.session.query(func.sum(CustomerPayment.amount)).scalar() or 0.0)

def get_purchase_returned_qty(purchase_id):
    return int(db.session.query(func.sum(PurchaseReturn.quantity)).filter(
        PurchaseReturn.purchase_id == purchase_id
    ).scalar() or 0)

def get_purchase_item_returned_qty(pi):
    """How much of this specific PurchaseItem line has already been returned.

    Keyed to the line, not just (purchase, item), because the same item can appear
    twice on one purchase in different units (5 loose Pcs and 3 Box) — grouping by
    item alone would mix their 'remaining' pools together."""
    direct = db.session.query(func.sum(PurchaseReturn.quantity)).filter(
        PurchaseReturn.purchase_item_id == pi.id
    ).scalar() or 0
    # Returns recorded before purchase_item_id existed aren't tagged to a line.
    # Only fold them in when this item appears on the purchase exactly once, so
    # there is no ambiguity about which line they belonged to.
    if PurchaseItem.query.filter_by(purchase_id=pi.purchase_id, item_id=pi.item_id).count() == 1:
        direct += db.session.query(func.sum(PurchaseReturn.quantity)).filter(
            PurchaseReturn.purchase_id == pi.purchase_id,
            PurchaseReturn.item_id == pi.item_id,
            PurchaseReturn.purchase_item_id.is_(None),
        ).scalar() or 0
    return int(direct)

def get_sale_returned_qty(sale_id):
    return int(db.session.query(func.sum(SaleReturn.quantity)).filter(
        SaleReturn.sale_id == sale_id
    ).scalar() or 0)

def get_sale_item_returned_qty(si):
    """See get_purchase_item_returned_qty — the sale-side equivalent."""
    direct = db.session.query(func.sum(SaleReturn.quantity)).filter(
        SaleReturn.sale_item_id == si.id
    ).scalar() or 0
    if SaleItem.query.filter_by(sale_id=si.sale_id, item_id=si.item_id).count() == 1:
        direct += db.session.query(func.sum(SaleReturn.quantity)).filter(
            SaleReturn.sale_id == si.sale_id,
            SaleReturn.item_id == si.item_id,
            SaleReturn.sale_item_id.is_(None),
        ).scalar() or 0
    return int(direct)

def purchase_return_total(pr):
    return float(pr.quantity * pr.return_price)

def sale_return_total(sr):
    return float(sr.quantity * sr.return_price)

def parse_payment_amount(amount_str):
    amount_str = (amount_str or "").strip().replace(",", "")   # tolerate "1,000"
    if not amount_str.replace(".", "", 1).isdigit():
        return None
    amount = float(amount_str)
    return amount if amount > 0 else None

def get_item_locked(item_id):
    """Fetch an Item row FOR UPDATE so concurrent stock changes serialize instead
    of racing (two simultaneous sales could otherwise both pass the stock check
    and oversell, or two purchases could lose one update). It's a real row lock on
    PostgreSQL; on SQLite it's a harmless no-op since SQLite serializes writes."""
    return db.session.query(Item).filter_by(id=item_id).with_for_update().first()

def validate_line_rows(rows, qty_idx=1, price_idx=2):
    """Validate quantity (positive whole number) and price (non-negative) for each
    parsed line-item row tuple. Returns an error string for the first bad row, or None."""
    for row in rows:
        qty_s, price_s = row[qty_idx], row[price_idx]
        if not qty_s.isdigit() or int(qty_s) <= 0:
            return f"Quantity must be a positive whole number (got '{qty_s}')."
        try:
            if float(price_s) < 0:
                return f"Price cannot be negative (got '{price_s}')."
        except ValueError:
            return f"Invalid price value '{price_s}'."
    return None

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
    is_postgres = db.engine.dialect.name == "postgresql"
    pk_type = "SERIAL PRIMARY KEY" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    datetime_type = "TIMESTAMP" if is_postgres else "DATETIME"
    if "item" in inspector.get_table_names():
        item_columns = {col["name"] for col in inspector.get_columns("item")}
        if "category_id" not in item_columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE item ADD COLUMN category_id INTEGER REFERENCES category(id)"))
        if "unit" not in item_columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE item ADD COLUMN unit VARCHAR(20) DEFAULT 'Pcs'"))
        if "opening_stock" not in item_columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE item ADD COLUMN opening_stock INTEGER DEFAULT 0"))
                # compute opening_stock = current_stock minus all transactions
                conn.execute(text("""
                    UPDATE item SET opening_stock = item.stock
                      - COALESCE((SELECT SUM(p.quantity) FROM purchase p WHERE p.item_id = item.id), 0)
                      + COALESCE((SELECT SUM(s.quantity) FROM sale s WHERE s.item_id = item.id), 0)
                      + COALESCE((SELECT SUM(pr.quantity) FROM purchase_return pr WHERE pr.item_id = item.id), 0)
                      - COALESCE((SELECT SUM(sr.quantity) FROM sale_return sr WHERE sr.item_id = item.id), 0)
                """))
    # Multi-unit: the unit a line was transacted in, and its factor into the item's
    # base unit. Nullable/default-1, so every existing row reads back as "the base
    # unit, factor 1" — exactly what it always implicitly was.
    for tbl in ("purchase_item", "sale_item", "quotation_item", "purchase_order_item",
                "purchase_return", "sale_return"):
        if tbl in inspector.get_table_names():
            cols = {col["name"] for col in inspector.get_columns(tbl)}
            with db.engine.begin() as conn:
                if "unit_name" not in cols:
                    conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN unit_name VARCHAR(20)"))
                if "unit_factor" not in cols:
                    conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN unit_factor INTEGER DEFAULT 1"))

    # Which specific line a return was made against — lets "how much is still
    # returnable" be tracked per line instead of mixing every unit an item was
    # ever bought/sold in on one document into a single pool.
    if "purchase_return" in inspector.get_table_names():
        cols = {col["name"] for col in inspector.get_columns("purchase_return")}
        if "purchase_item_id" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE purchase_return ADD COLUMN purchase_item_id INTEGER REFERENCES purchase_item(id)"))
    if "sale_return" in inspector.get_table_names():
        cols = {col["name"] for col in inspector.get_columns("sale_return")}
        if "sale_item_id" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE sale_return ADD COLUMN sale_item_id INTEGER REFERENCES sale_item(id)"))

    for table, column in (("supplier", "opening_balance"), ("customer", "opening_balance")):
        if table in inspector.get_table_names():
            cols = {col["name"] for col in inspector.get_columns(table)}
            if column not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} FLOAT DEFAULT 0"))
    if "user" in inspector.get_table_names():
        user_cols = {col["name"] for col in inspector.get_columns("user")}
        if "role" not in user_cols:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN role VARCHAR(20) DEFAULT \'admin\''))
                conn.execute(text('UPDATE "user" SET role = \'admin\''))
    if "sale" in inspector.get_table_names():
        sale_cols = {col["name"] for col in inspector.get_columns("sale")}
        if "cost_price" not in sale_cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE sale ADD COLUMN cost_price FLOAT DEFAULT 0.0"))
                conn.execute(text(
                    "UPDATE sale SET cost_price = "
                    "(SELECT COALESCE(purchase_price, 0) FROM item WHERE item.id = sale.item_id)"
                ))
        with db.engine.begin() as conn:
            for col, default in [("discount_type", "'percent'"), ("discount_value", "0.0"),
                                 ("tax_percent", "0.0"), ("discount_amount", "0.0"), ("tax_amount", "0.0")]:
                if col not in sale_cols:
                    conn.execute(text(f"ALTER TABLE sale ADD COLUMN {col} {'VARCHAR(10)' if col == 'discount_type' else 'FLOAT'} DEFAULT {default}"))
    if "purchase" in inspector.get_table_names():
        pur_cols = {col["name"] for col in inspector.get_columns("purchase")}
        with db.engine.begin() as conn:
            for col, default in [("discount_type", "'percent'"), ("discount_value", "0.0"),
                                 ("tax_percent", "0.0"), ("discount_amount", "0.0"), ("tax_amount", "0.0")]:
                if col not in pur_cols:
                    conn.execute(text(f"ALTER TABLE purchase ADD COLUMN {col} {'VARCHAR(10)' if col == 'discount_type' else 'FLOAT'} DEFAULT {default}"))
    # Add notes column to purchase and sale
    for tbl in ("purchase", "sale"):
        if tbl in inspector.get_table_names():
            cols = {col["name"] for col in inspector.get_columns(tbl)}
            if "notes" not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN notes VARCHAR(300)"))

    # Create purchase_item table and migrate existing single-item purchases
    with db.engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS purchase_item (
                id {pk_type},
                purchase_id INTEGER NOT NULL REFERENCES purchase(id),
                item_id INTEGER NOT NULL REFERENCES item(id),
                quantity INTEGER NOT NULL,
                purchase_price FLOAT NOT NULL,
                discount_type VARCHAR(10) NOT NULL DEFAULT 'percent',
                discount_value FLOAT NOT NULL DEFAULT 0.0,
                discount_amount FLOAT NOT NULL DEFAULT 0.0,
                tax_percent FLOAT NOT NULL DEFAULT 0.0,
                tax_amount FLOAT NOT NULL DEFAULT 0.0,
                amount FLOAT NOT NULL DEFAULT 0.0
            )
        """))
        conn.execute(text("""
            INSERT INTO purchase_item
                (purchase_id, item_id, quantity, purchase_price,
                 discount_type, discount_value, discount_amount,
                 tax_percent, tax_amount, amount)
            SELECT p.id, p.item_id, p.quantity, p.purchase_price,
                COALESCE(p.discount_type,'percent'), COALESCE(p.discount_value,0),
                COALESCE(p.discount_amount,0), COALESCE(p.tax_percent,0),
                COALESCE(p.tax_amount,0),
                COALESCE(p.quantity,0) * COALESCE(p.purchase_price,0)
            FROM purchase p
            WHERE p.item_id IS NOT NULL AND p.quantity IS NOT NULL
              AND p.id NOT IN (SELECT DISTINCT purchase_id FROM purchase_item)
        """))

    # Create sale_item table and migrate existing single-item sales
    with db.engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS sale_item (
                id {pk_type},
                sale_id INTEGER NOT NULL REFERENCES sale(id),
                item_id INTEGER NOT NULL REFERENCES item(id),
                quantity INTEGER NOT NULL,
                sale_price FLOAT NOT NULL,
                cost_price FLOAT NOT NULL DEFAULT 0.0,
                discount_type VARCHAR(10) NOT NULL DEFAULT 'percent',
                discount_value FLOAT NOT NULL DEFAULT 0.0,
                discount_amount FLOAT NOT NULL DEFAULT 0.0,
                tax_percent FLOAT NOT NULL DEFAULT 0.0,
                tax_amount FLOAT NOT NULL DEFAULT 0.0,
                amount FLOAT NOT NULL DEFAULT 0.0
            )
        """))
        conn.execute(text("""
            INSERT INTO sale_item
                (sale_id, item_id, quantity, sale_price, cost_price,
                 discount_type, discount_value, discount_amount,
                 tax_percent, tax_amount, amount)
            SELECT s.id, s.item_id, s.quantity, s.sale_price,
                COALESCE(s.cost_price,0),
                COALESCE(s.discount_type,'percent'), COALESCE(s.discount_value,0),
                COALESCE(s.discount_amount,0), COALESCE(s.tax_percent,0),
                COALESCE(s.tax_amount,0),
                COALESCE(s.quantity,0) * COALESCE(s.sale_price,0)
            FROM sale s
            WHERE s.item_id IS NOT NULL AND s.quantity IS NOT NULL
              AND s.id NOT IN (SELECT DISTINCT sale_id FROM sale_item)
        """))

    backfill_ledgers()

    # New tables for Tier-1/2 features
    with db.engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS stock_adjustment (
                id {pk_type},
                item_id INTEGER NOT NULL REFERENCES item(id),
                adj_type VARCHAR(30) NOT NULL,
                quantity INTEGER NOT NULL,
                direction VARCHAR(4) NOT NULL DEFAULT 'in',
                {datetime_type} date NOT NULL,
                reason VARCHAR(300)
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS expense_category (
                id {pk_type},
                name VARCHAR(100) NOT NULL UNIQUE
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS expense (
                id {pk_type},
                category_id INTEGER REFERENCES expense_category(id),
                description VARCHAR(300) NOT NULL,
                amount FLOAT NOT NULL,
                {datetime_type} date NOT NULL,
                payment_method VARCHAR(20) NOT NULL DEFAULT 'Cash',
                reference_no VARCHAR(100),
                notes VARCHAR(300)
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS purchase_order (
                id {pk_type},
                supplier_id INTEGER NOT NULL REFERENCES supplier(id),
                {datetime_type} order_date NOT NULL,
                expected_date {datetime_type},
                status VARCHAR(20) NOT NULL DEFAULT 'Draft',
                notes VARCHAR(300),
                converted_purchase_id INTEGER REFERENCES purchase(id)
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS purchase_order_item (
                id {pk_type},
                po_id INTEGER NOT NULL REFERENCES purchase_order(id),
                item_id INTEGER NOT NULL REFERENCES item(id),
                quantity INTEGER NOT NULL,
                purchase_price FLOAT NOT NULL
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS quotation (
                id {pk_type},
                customer_id INTEGER NOT NULL REFERENCES customer(id),
                {datetime_type} quote_date NOT NULL,
                valid_until {datetime_type},
                status VARCHAR(20) NOT NULL DEFAULT 'Draft',
                notes VARCHAR(300),
                converted_sale_id INTEGER REFERENCES sale(id)
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS quotation_item (
                id {pk_type},
                quotation_id INTEGER NOT NULL REFERENCES quotation(id),
                item_id INTEGER NOT NULL REFERENCES item(id),
                quantity INTEGER NOT NULL,
                sale_price FLOAT NOT NULL,
                discount_type VARCHAR(10) NOT NULL DEFAULT 'percent',
                discount_value FLOAT NOT NULL DEFAULT 0.0,
                tax_percent FLOAT NOT NULL DEFAULT 0.0
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS delivery_challan (
                id {pk_type},
                sale_id INTEGER NOT NULL UNIQUE REFERENCES sale(id),
                {datetime_type} challan_date NOT NULL,
                dispatch_date {datetime_type},
                delivery_date {datetime_type},
                status VARCHAR(20) NOT NULL DEFAULT 'Pending',
                transport VARCHAR(100),
                notes VARCHAR(300)
            )
        """))

    # ── Convert legacy FLOAT money columns to exact NUMERIC(14,4) ──────────────
    # Money should be stored as fixed-point decimal, not binary float, so ledger
    # balances and totals don't accumulate rounding error. SQLite is dynamically
    # typed (SQLAlchemy applies the Numeric processor on read once the models use
    # Numeric), so only PostgreSQL needs a real column-type change. Idempotent:
    # a column already stored as NUMERIC reflects as non-Float and is skipped.
    #
    # CRITICAL: ALTER COLUMN TYPE needs an ACCESS EXCLUSIVE lock and rewrites the
    # table. During a zero-downtime deploy the OLD instance is still running and
    # holding connections, so the ALTER can block indefinitely and hang the whole
    # deploy until it times out. We therefore make this best-effort: a short
    # lock_timeout means it fails fast instead of hanging, and any failure is
    # logged and skipped so the app always boots. The app works correctly on the
    # old FLOAT columns too (SQLAlchemy converts to Decimal on read); a skipped
    # column simply gets converted on a later startup once the lock is free.
    if is_postgres:
        from sqlalchemy import Float as _Float, Numeric as _Numeric
        existing_tables = set(inspector.get_table_names())
        migration_blocked = False
        for table in db.metadata.sorted_tables:
            if migration_blocked:
                break
            if table.name not in existing_tables:
                continue
            db_cols = {c["name"]: c for c in inspector.get_columns(table.name)}
            for col in table.columns:
                # our money columns are Numeric but NOT Float (Float subclasses Numeric)
                if not isinstance(col.type, _Numeric) or isinstance(col.type, _Float):
                    continue
                db_col = db_cols.get(col.name)
                if db_col is not None and isinstance(db_col["type"], _Float):
                    try:
                        with db.engine.begin() as conn:
                            conn.execute(text("SET LOCAL lock_timeout = '4s'"))
                            conn.execute(text(
                                f'ALTER TABLE "{table.name}" ALTER COLUMN "{col.name}" '
                                f'TYPE NUMERIC(14, 4) USING "{col.name}"::numeric(14,4)'
                            ))
                    except Exception as e:
                        # Almost always a lock_timeout because a previous instance is
                        # still holding the table during a zero-downtime deploy. Stop
                        # here so boot stays fast; the rest converts on a later startup
                        # when the lock is free. The app runs fine on FLOAT meanwhile.
                        app.logger.warning(
                            "Deferring money-column migration (lock busy at %s.%s); "
                            "will retry on next startup: %s", table.name, col.name, e
                        )
                        migration_blocked = True
                        break

    # Tag money movements with the cash/bank account they hit. Nullable and no
    # backfill: existing rows stay NULL and keep being matched by payment_method
    # (see FinancialAccount). Adding a nullable column takes no table rewrite on
    # either engine, so this is safe to run at startup.
    for table in ("supplier_payment", "customer_payment", "expense"):
        if table in inspector.get_table_names():
            cols = {col["name"] for col in inspector.get_columns(table)}
            if "account_id" not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN account_id INTEGER "
                        "REFERENCES financial_account(id)"
                    ))

    # Link cash/bank accounts and expense categories to their GL accounts.
    for table in ("financial_account", "expense_category"):
        if table in inspector.get_table_names():
            cols = {col["name"] for col in inspector.get_columns(table)}
            if "gl_account_id" not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN gl_account_id INTEGER "
                        "REFERENCES account(id)"
                    ))

    # Hierarchical financial accounts: control accounts (headers) with subsidiary accounts (selectable)
    if "financial_account" in inspector.get_table_names():
        cols = {col["name"] for col in inspector.get_columns("financial_account")}
        if "is_control" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE financial_account ADD COLUMN is_control BOOLEAN NOT NULL DEFAULT FALSE"))
        if "parent_id" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE financial_account ADD COLUMN parent_id INTEGER REFERENCES financial_account(id)"))

    # Weighted-average costing: what the stock on hand actually cost.
    if "item" in inspector.get_table_names():
        cols = {col["name"] for col in inspector.get_columns("item")}
        if "inventory_value" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE item ADD COLUMN inventory_value NUMERIC(14,4) NOT NULL DEFAULT 0"))
                # Seed from the only figure the old schema had: today's price.
                conn.execute(text("UPDATE item SET inventory_value = stock * COALESCE(purchase_price, 0)"))

    # Which cash-flow section an account belongs to, and what the posting layer uses
    # it for. Both nullable with no default, so the existing chart needs no backfill.
    if "account" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("account")}
        if "cash_flow_section" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE account ADD COLUMN cash_flow_section VARCHAR(12)"))
        if "role" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE account ADD COLUMN role VARCHAR(30)"))

    # Gapless invoice numbers. A plain nullable column only: adding the UNIQUE index
    # here would be blocking DDL on PostgreSQL, and a zero-downtime deploy would hang
    # on it. Uniqueness does not depend on the index anyway — one locked counter row
    # per year hands out each number exactly once (see allocate_document_number).
    for table in ("purchase", "sale"):
        if table in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns(table)}
            if "invoice_no" not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN invoice_no VARCHAR(30)"))

    # The item's barcode / QR value, for the POS counter. Nullable, so nothing about
    # existing items changes.
    if "item" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("item")}
        if "barcode" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE item ADD COLUMN barcode VARCHAR(64)"))

    # A document records the cost it moved, so its reversal undoes exactly that.
    for table, col in (("purchase_return", "cost_removed"),
                       ("sale_return", "cost_restored"),
                       ("stock_adjustment", "cost_value")):
        if table in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns(table)}
            if col not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {col} NUMERIC(14,4) NOT NULL DEFAULT 0"))

    # Documents are never deleted once posted — they are reversed and flagged.
    # DEFAULT FALSE (not 0) so the same DDL is valid on SQLite and PostgreSQL.
    for table in ("purchase", "sale", "supplier_payment", "customer_payment",
                  "expense", "purchase_return", "sale_return", "stock_adjustment"):
        if table in inspector.get_table_names():
            cols = {col["name"] for col in inspector.get_columns(table)}
            with db.engine.begin() as conn:
                if "is_reversed" not in cols:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN is_reversed BOOLEAN NOT NULL DEFAULT FALSE"))
                if "reversed_at" not in cols:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN reversed_at {datetime_type}"))

    # Seed the GL foundation. All three are idempotent, so a fresh database (local
    # or a new deploy) always boots with a usable chart of accounts.
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_tax_codes()

    # Say so before seeding a year that will overlap the ones already there.
    mismatched = fiscal_years_that_disagree_with_the_setting()
    if mismatched:
        app.logger.warning(
            "FISCAL_YEAR_START_MONTH is %d, but these fiscal years do not start in that "
            "month: %s. Their periods overlap any year seeded now, and a document dated "
            "inside the overlap will land in whichever is found first. Set the variable "
            "back, or start from a clean database — do not trade across both.",
            FISCAL_YEAR_START_MONTH, ", ".join(mismatched))

    seed_fiscal_year(now_local())

    # Seed one cash/bank account per payment method (idempotent — only if none exist)
    if FinancialAccount.query.count() == 0:
        types = {"Cash": "Cash", "Bank": "Bank", "Cheque": "Bank", "Online": "Bank"}
        for m in PAYMENT_METHODS:
            db.session.add(FinancialAccount(name=m, method=m, account_type=types.get(m, "Bank"),
                                            opening_balance=0))
        db.session.commit()

    # Nothing can be posted until every cash/bank account has a GL counterpart.
    seed_financial_account_links()

    # Cash/bank opening balances entered before they were ever posted to the GL.
    backfill_account_openings()

    # Documents raised before numbering existed still need their numbers.
    backfill_document_numbers()

    # Reversals written before reverse_entry refused to backdate.
    realign_backdated_reversals()

# Create Database
with app.app_context():
    try:
        migrate_database()
    except Exception as e:
        print(f"FATAL: database migration failed: {e}")
        raise

# Load user for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ── Custom error pages ────────────────────────────────────────────────────────
@app.errorhandler(403)
def error_403(e):
    return render_template("error.html", code=403, title="Access Denied",
                           message="You don't have permission to view this page."), 403

@app.errorhandler(404)
def error_404(e):
    return render_template("error.html", code=404, title="Page Not Found",
                           message="The page you're looking for doesn't exist or may have moved."), 404

@app.errorhandler(500)
def error_500(e):
    # A failed request can leave the session in a broken state — roll back so the
    # error page (and the next request) can still query the database.
    db.session.rollback()
    app.logger.exception("Unhandled 500 error")
    return render_template("error.html", code=500, title="Something Went Wrong",
                           message="An unexpected error occurred. Please try again, "
                                   "or contact support if it keeps happening."), 500

def _safe_referrer():
    """Return the referring URL only if it points back at this app (no open redirect)."""
    ref = request.referrer
    if ref and urlsplit(ref).netloc == urlsplit(request.host_url).netloc:
        return ref
    return url_for("index")

@app.errorhandler(400)
def error_400(e):
    # Includes CSRF failures and other bad requests — send the user back with a note
    # instead of a bare 400 page.
    app.logger.info("Bad request (400): %s", e)
    flash("Your request could not be processed. Please refresh the page and try again.", "danger")
    return redirect(_safe_referrer())

@app.errorhandler(ValueError)
def handle_value_error(e):
    # Last-resort guard: a stray numeric parse of bad form input raises ValueError.
    # Turn it into a friendly "check your numbers" message instead of a 500, and log
    # it so a genuine bug is still visible in the logs. Per-route validation
    # (validate_line_rows, parse_payment_amount, etc.) remains the first line.
    db.session.rollback()
    app.logger.warning("Invalid input rejected (ValueError): %s", e)
    flash("Some values you entered are not valid numbers. Please check them and try again.", "danger")
    return redirect(_safe_referrer())

# ── Security headers ──────────────────────────────────────────────────────────
# CSP allows the CDNs the app actually uses (Bootstrap/icons/Chart.js on jsDelivr,
# Google Fonts) plus 'unsafe-inline' for the app's inline <script>/<style> blocks
# and onclick handlers. HSTS is only sent in production (HTTPS) so local HTTP dev
# isn't pinned to https.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", _CSP)
    if _is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response

@app.errorhandler(PostingError)
def handle_posting_error(e):
    """A refused posting must never leave a half-written document behind. Rolling
    back here means the route's own db.session.add() calls are discarded too, so
    the document and its journal entry are all-or-nothing."""
    db.session.rollback()
    flash(str(e), "danger")
    app.logger.info("Posting refused: %s", e)
    return redirect(request.referrer or url_for("dashboard.dashboard"))

# ─── BULK IMPORT FUNCTIONS ─────────────────────────────────────────────────────

IMPORT_MAX_ROWS = 10000

# Allowed MIME types for bulk import
ALLOWED_MIME_TYPES = {
    'text/csv',
    'text/plain',  # CSV may be reported as plain text
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
    'application/vnd.ms-excel',  # .xls
    'application/json',
}

# Maximum file size: 50 MB (prevents DoS)
MAX_IMPORT_FILE_SIZE = 50 * 1024 * 1024

def parse_import_file(file):
    """Parse CSV/Excel/JSON file and return list of dictionaries."""
    if not file:
        return None, "No file provided"

    filename = file.filename
    file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''

    # Validate file extension
    if file_ext not in ('csv', 'xlsx', 'xls', 'json'):
        return None, f"Invalid file extension: {file_ext}. Allowed: CSV, Excel (.xlsx, .xls), JSON"

    # Validate MIME type
    mime_type = file.content_type or ''
    if mime_type not in ALLOWED_MIME_TYPES:
        # Log warning but don't block if extension is valid
        # (Some file systems may report MIME types differently)
        pass

    # Check file size (prevent DoS)
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset to beginning
    if file_size > MAX_IMPORT_FILE_SIZE:
        return None, f"File is too large ({file_size / 1024 / 1024:.1f} MB). Maximum size is 50 MB."

    try:
        if file_ext == 'csv':
            stream = StringIO(file.read().decode('utf-8'))
            reader = csv.DictReader(stream)
            data = list(reader)

        elif file_ext in ('xlsx', 'xls'):
            wb = openpyxl.load_workbook(file)
            ws = wb.active
            headers = [cell.value for cell in ws[1]]
            data = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                data.append(dict(zip(headers, row)))

        elif file_ext == 'json':
            parsed = json.loads(file.read().decode('utf-8'))
            data = parsed if isinstance(parsed, list) else [parsed]

        else:
            return None, f"Unsupported file type: {file_ext}"

        if len(data) > IMPORT_MAX_ROWS:
            return None, f"File has {len(data)} rows — the maximum is {IMPORT_MAX_ROWS} per import."
        return data, file_ext

    except Exception as e:
        return None, f"Error parsing file: {str(e)}"

def import_items(data):
    """Bulk import items from parsed data. Mirrors the validation and GL/valuation
    behavior of the manual /item route so imported items are not silently missing
    their cost basis or accounting entry."""
    success, failed, errors = 0, 0, []

    for idx, row in enumerate(data, 1):
        try:
            name = row.get('name', '').strip()
            if not name:
                errors.append(f"Row {idx}: Missing item name")
                failed += 1
                continue

            category_name = row.get('category', '').strip()
            if not category_name:
                errors.append(f"Row {idx}: Missing category")
                failed += 1
                continue
            category = Category.query.filter_by(name=category_name).first()
            if not category:
                category = Category(name=category_name)
                db.session.add(category)
                db.session.flush()

            unit = (row.get('unit', 'Pcs') or 'Pcs').strip()
            if unit not in ITEM_UNITS:
                unit = "Pcs"

            purchase_price_raw = row.get('purchase_price', '')
            sale_price_raw = row.get('sale_price', '')
            purchase_price = float(purchase_price_raw) if purchase_price_raw else None
            sale_price = float(sale_price_raw) if sale_price_raw else None
            if purchase_price is not None and purchase_price < 0:
                errors.append(f"Row {idx}: Purchase price cannot be negative")
                failed += 1
                continue
            if sale_price is not None and sale_price < 0:
                errors.append(f"Row {idx}: Sale price cannot be negative")
                failed += 1
                continue

            barcode = row.get('barcode', '').strip() or None
            if barcode_taken(barcode):
                errors.append(f"Row {idx}: Barcode '{barcode}' is already used by another item")
                failed += 1
                continue

            # `stock` is the item's actual on-hand quantity at import time; since the
            # system has no purchase history for it yet, that quantity IS the opening
            # balance — same as the manual /item form, where opening_stock and stock
            # are always the same number.
            qty = int(row.get('stock', 0) or row.get('opening_stock', 0) or 0)

            if qty > 0 and purchase_price <= 0:
                errors.append(f"Row {idx}: Opening stock {qty} requires purchase_price > 0")
                failed += 1
                continue

            item = Item(
                name=name,
                category_id=category.id,
                unit=unit,
                opening_stock=qty,
                stock=qty,
                reorder_level=int(row.get('reorder_level', 0)) if row.get('reorder_level') else 0,
                purchase_price=purchase_price,
                sale_price=sale_price,
                barcode=barcode,
            )

            db.session.add(item)
            item.inventory_value = (Decimal(str(item.opening_stock or 0))
                                    * Decimal(str(item.purchase_price or 0))).quantize(MONEY)
            db.session.flush()

            # Post opening balance to GL if opening stock > 0
            if item.opening_stock > 0:
                post_item_opening(item)

            db.session.commit()
            success += 1

        except Exception as e:
            db.session.rollback()
            failed += 1
            errors.append(f"Row {idx}: {str(e)}")

    return success, failed, errors

def import_customers(data):
    """Bulk import customers from parsed data."""
    success, failed, errors = 0, 0, []

    for idx, row in enumerate(data, 1):
        try:
            name = row.get('name', '').strip()
            if not name:
                errors.append(f"Row {idx}: Missing customer name")
                failed += 1
                continue

            customer = Customer(
                name=name,
                contact=row.get('contact', row.get('phone', '')).strip() or '',
                address=row.get('address', '').strip() or '',
                opening_balance=float(row.get('opening_balance', 0)) if row.get('opening_balance') else 0
            )

            db.session.add(customer)
            db.session.flush()
            sync_customer_opening(customer)
            if customer.opening_balance != 0:
                post_customer_opening(customer)
            db.session.commit()
            success += 1

        except Exception as e:
            db.session.rollback()
            failed += 1
            errors.append(f"Row {idx}: {str(e)}")

    return success, failed, errors

def import_suppliers(data):
    """Bulk import suppliers from parsed data."""
    success, failed, errors = 0, 0, []

    for idx, row in enumerate(data, 1):
        try:
            name = row.get('name', '').strip()
            if not name:
                errors.append(f"Row {idx}: Missing supplier name")
                failed += 1
                continue

            supplier = Supplier(
                name=name,
                contact=row.get('contact', row.get('phone', '')).strip() or '',
                address=row.get('address', '').strip() or '',
                opening_balance=float(row.get('opening_balance', 0)) if row.get('opening_balance') else 0
            )

            db.session.add(supplier)
            db.session.flush()
            sync_supplier_opening(supplier)
            if supplier.opening_balance != 0:
                post_supplier_opening(supplier)
            db.session.commit()
            success += 1

        except Exception as e:
            db.session.rollback()
            failed += 1
            errors.append(f"Row {idx}: {str(e)}")

    return success, failed, errors

@app.route("/health")
def health():
    """Lightweight, unauthenticated health check for uptime monitors and Render.
    Returns 200 only if the database is reachable, else 503."""
    try:
        db.session.execute(text("SELECT 1"))
        return {"status": "ok"}, 200
    except Exception as e:
        app.logger.error("Health check failed: %s", e)
        return {"status": "error", "detail": "database unreachable"}, 503

# Custom Jinja2 filter: number ko 999,999,999,999.99 format mein dikhaye
@app.template_filter('fmt_num')
def fmt_num(value):
    try:
        value = float(value)
        return f"{value:,.2f}"
    except (TypeError, ValueError):
        return value

@app.template_filter('fromjson')
def fromjson(value):
    """Parse JSON string to Python object."""
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, TypeError):
        return value

@app.template_filter("pct")
def pct_filter(value):
    """A rate, written the way a person writes one: 17%, 17.5%, 0.25%.

    Rates are stored as Numeric(14, 4) — exact, because a quarter of a percent of a large
    invoice is real money. But the storage is not the presentation: rendering the column
    straight prints "17.0000%" on every invoice that goes to a customer, which is the sort
    of detail that makes a system look like a school project.
    """
    try:
        d = Decimal(str(value or 0)).normalize()
    except (InvalidOperation, TypeError, ValueError):
        return value
    if d == d.to_integral_value():
        d = d.quantize(Decimal("1"))          # 17.0000 -> 17, not 1.7E+1
    return f"{d}"

@app.template_filter("money")
def money_filter(value):
    """A figure with its currency on it — for the numbers a person acts on: the total of an
    invoice, the profit for the year. A bare "1,234,567.89" on a quotation is how a price in
    rupees gets paid in dollars.

    Not for every cell in a table; a column of figures under a heading that names the
    currency does not need it repeated on each row."""
    symbol = app.config.get("CURRENCY", "")
    return f"{symbol} {fmt_num(value)}".strip()

def write_csv_header(writer, report_title, start_date_str=None, end_date_str=None, extra_info=None):
    company = app.config["COMPANY_NAME"]
    tagline = app.config["COMPANY_TAGLINE"]
    writer.writerow([company, tagline])
    writer.writerow(["Report:", report_title])
    if start_date_str and end_date_str:
        writer.writerow(["Period:", f"{start_date_str}  to  {end_date_str}"])
    if extra_info:
        writer.writerow(["Info:", extra_info])
    writer.writerow(["Generated On:", now_local().strftime("%Y-%m-%d %H:%M")])
    writer.writerow([])


def csv_response(filename, title, col_headers, rows, start_date_str=None, end_date_str=None, extra_info=None):
    """Build a CSV entirely in memory and return it as a download — no shared file on disk,
    so concurrent exports from different users/tabs can never race or overwrite each other."""
    buf = StringIO()
    writer = csv.writer(buf)
    write_csv_header(writer, title, start_date_str, end_date_str, extra_info)
    writer.writerow(col_headers)
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def excel_response(filename, title, col_headers, rows, start_date_str=None, end_date_str=None, extra_info=None):
    """Create a styled .xlsx file and return as a Flask file download response."""
    company = app.config["COMPANY_NAME"]
    tagline = app.config["COMPANY_TAGLINE"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31]

    # --- Metadata rows (row counter tracks real rows so blank row is guaranteed) ---
    r = 1
    ws.cell(row=r, column=1, value=company)
    ws.cell(row=r, column=2, value=tagline)
    ws["A1"].font = Font(bold=True, size=13, color="1E3A5F")
    r += 1

    ws.cell(row=r, column=1, value="Report:")
    ws.cell(row=r, column=2, value=title)
    r += 1

    if start_date_str and end_date_str:
        ws.cell(row=r, column=1, value="Period:")
        ws.cell(row=r, column=2, value=f"{start_date_str}  to  {end_date_str}")
        r += 1

    if extra_info:
        ws.cell(row=r, column=1, value="Info:")
        ws.cell(row=r, column=2, value=extra_info)
        r += 1

    ws.cell(row=r, column=1, value="Generated On:")
    ws.cell(row=r, column=2, value=now_local().strftime("%Y-%m-%d %H:%M"))
    r += 1

    r += 1  # genuine blank row — no cell written here

    # --- Column header row ---
    header_row_num = r
    for col_i, h in enumerate(col_headers, 1):
        cell = ws.cell(row=header_row_num, column=col_i, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1E3A5F")
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    r += 1

    # --- Data rows ---
    for row_data in rows:
        for col_i, val in enumerate(row_data, 1):
            ws.cell(row=r, column=col_i, value=val)
        r += 1

    # --- Auto-fit column widths ---
    for col in ws.columns:
        max_len = max((len(str(cell.value)) if cell.value is not None else 0) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 3, 45)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )

@app.context_processor
def inject_form_defaults():
    ctx = {
        "form_data": {},
        "payment_methods": PAYMENT_METHODS,
        "financial_accounts": active_accounts,
        "item_units": ITEM_UNITS,
        "roles": ROLES,
        "cash_flow_sections": CASH_FLOW_SECTIONS,
        "company_name": app.config["COMPANY_NAME"],
        "app_name": app.config["APP_NAME"],
        "currency": app.config["CURRENCY"],
        "company_tagline": app.config["COMPANY_TAGLINE"],
        "app_timezone": app.config["APP_TIMEZONE"],
        "designed_developed": app.config["DESIGNED_DEVELOPED"],
        "demo_mode": is_demo_mode(),
        "default_tax_rate": get_standard_tax_rate(),
        "item_units_for_js": item_units_for_js,
        "purchase_item_options_for_js": purchase_item_options_for_js,
        "sale_item_options_for_js": sale_item_options_for_js,
        "purchase_return_options_for_js": purchase_return_options_for_js,
        "sale_return_options_for_js": sale_return_options_for_js,
        "quotation_total": quotation_total,
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

# Auth routes have been moved to salpurflask/routes/auth.py blueprint


# Dashboard routes have been moved to salpurflask/routes/dashboard.py blueprint

@app.route("/supplier", methods=["GET", "POST"])
@verified_required
def supplier():
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

@app.route("/supplier/edit/<int:id>", methods=["GET", "POST"])
@manager_required
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

@app.route("/supplier/delete/<int:id>", methods=["POST"])
@admin_required
def delete_supplier(id):
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

@app.route("/export_suppliers")
@manager_required
def export_suppliers():
    suppliers = Supplier.query.order_by(Supplier.name).all()
    rows = [
        [s.id, s.name, s.contact, s.address,
         round(float(s.opening_balance or 0), 2),
         round(get_supplier_balance(s.id), 2)]
        for s in suppliers
    ]
    return csv_response("suppliers.csv", "Suppliers List",
                         ["ID", "Name", "Contact", "Address", "Opening Balance", "Current Balance"], rows)

@app.route("/export_suppliers_excel")
@manager_required
def export_suppliers_excel():
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

@app.route("/customer", methods=["GET", "POST"])
@verified_required
def customer():
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

@app.route("/customer/edit/<int:id>", methods=["GET", "POST"])
@manager_required
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

@app.route("/customer/delete/<int:id>", methods=["POST"])
@admin_required
def delete_customer(id):
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

@app.route("/export_customers")
@manager_required
def export_customers():
    customers = Customer.query.order_by(Customer.name).all()
    rows = [
        [c.id, c.name, c.contact, c.address,
         round(float(c.opening_balance or 0), 2),
         round(get_customer_balance(c.id), 2)]
        for c in customers
    ]
    return csv_response("customers.csv", "Customers List",
                         ["ID", "Name", "Contact", "Address", "Opening Balance", "Current Balance"], rows)

@app.route("/export_customers_excel")
@manager_required
def export_customers_excel():
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

@app.route("/category", methods=["GET", "POST"])
@verified_required
def category():
    search = request.args.get("search", "")
    query = Category.query.filter(Category.name.ilike(f"%{search}%")) if search else Category.query
    categories, pagination = get_paginated_results(query)
    if request.method == "POST":
        if current_user.role not in ("admin", "manager"):
            flash("You do not have permission to add categories.", "danger")
            return redirect(url_for("category"))
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
@manager_required
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
@admin_required
def delete_category(id):
    category = db.session.get(Category, id) or abort(404)
    if category.items:
        flash("Cannot delete category with associated items!", "danger")
    else:
        db.session.delete(category)
        db.session.commit()
        flash("Category deleted successfully!", "success")
    return redirect(url_for("category"))

def barcode_taken(barcode, exclude_id=None):
    """True if another item already carries this barcode.

    A barcode has to point at one item and one only — the whole point of scanning is that
    the code names the product with no ambiguity. Two items sharing a code (a mistyped
    company barcode, the same number pasted twice) would make the POS pick whichever it
    found first, silently ringing up the wrong thing. Blank is never 'taken': any number of
    items may have no code."""
    if not barcode:
        return False
    q = Item.query.filter(Item.barcode == barcode)
    if exclude_id is not None:
        q = q.filter(Item.id != exclude_id)
    return db.session.query(q.exists()).scalar()

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
        if current_user.role not in ("admin", "manager"):
            flash("You do not have permission to add items.", "danger")
            return redirect(url_for("item"))
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id", "").strip()
        unit = request.form.get("unit", "Pcs").strip()
        opening_stock = request.form.get("opening_stock", "0").strip() or "0"
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        barcode = request.form.get("barcode", "").strip() or None
        if unit not in ITEM_UNITS:
            unit = "Pcs"
        if not categories:
            flash("Please add a category first before adding items!", "danger")
        elif not name or not reorder_level or not category_id:
            flash("Name, Category, and Reorder Level are required!", "danger")
        elif not category_id.isdigit() or not db.session.get(Category, int(category_id)):
            flash("Please select a valid category!", "danger")
        elif not opening_stock.lstrip("-").isdigit() or not reorder_level.isdigit():
            flash("Opening Stock and Reorder Level must be numbers!", "danger")
        elif purchase_price and (not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0):
            flash("Purchase price must be a non-negative number!", "danger")
        elif sale_price and (not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0):
            flash("Sale price must be a non-negative number!", "danger")
        elif int(opening_stock) > 0 and (not purchase_price or float(purchase_price or 0) == 0):
            flash("Opening stock requires a purchase price greater than 0!", "danger")
        elif barcode_taken(barcode):
            flash(f"Barcode '{barcode}' is already used by another item. "
                  "A code must point at one item only.", "danger")
        else:
            os_val = int(opening_stock)
            item = Item(
                name=name,
                category_id=int(category_id),
                unit=unit,
                opening_stock=os_val,
                stock=os_val,
                reorder_level=int(reorder_level),
                purchase_price=float(purchase_price) if purchase_price else None,
                sale_price=float(sale_price) if sale_price else None,
                barcode=barcode,
            )
            db.session.add(item)
            item.inventory_value = (Decimal(str(item.opening_stock or 0))
                                    * Decimal(str(item.purchase_price or 0))).quantize(MONEY)
            db.session.flush()
            unit_error = save_item_units(item)
            if unit_error:
                db.session.rollback()
                flash(unit_error, "danger")
                return render_template("item.html", items=items, categories=categories,
                                       pagination=pagination, search=search,
                                       category_filter=category_filter)
            post_item_opening(item)
            db.session.commit()
            record_audit("create", "Item", item.id, f"Item '{item.name}' added")
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
@manager_required
def edit_item(id):
    item = db.session.get(Item, id) or abort(404)
    categories = Category.query.order_by(Category.name).all()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id", "").strip()
        unit = request.form.get("unit", "Pcs").strip()
        opening_stock = request.form.get("opening_stock", str(item.opening_stock)).strip()
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        barcode = request.form.get("barcode", "").strip() or None
        if unit not in ITEM_UNITS:
            unit = "Pcs"
        if not categories:
            flash("Please add a category first before editing items!", "danger")
        elif not name or not reorder_level or not category_id:
            flash("Name, Category, and Reorder Level are required!", "danger")
        elif not category_id.isdigit() or not db.session.get(Category, int(category_id)):
            flash("Please select a valid category!", "danger")
        elif not opening_stock.lstrip("-").isdigit() or not reorder_level.isdigit():
            flash("Opening Stock and Reorder Level must be numbers!", "danger")
        elif purchase_price and (not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0):
            flash("Purchase price must be a non-negative number!", "danger")
        elif sale_price and (not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0):
            flash("Sale price must be a non-negative number!", "danger")
        elif int(opening_stock) > 0 and (not purchase_price or float(purchase_price or 0) == 0):
            flash("Opening stock requires a purchase price greater than 0!", "danger")
        elif barcode_taken(barcode, exclude_id=item.id):
            flash(f"Barcode '{barcode}' is already used by another item. "
                  "A code must point at one item only.", "danger")
        else:
            new_os = int(opening_stock)
            stock_adjustment = new_os - item.opening_stock
            # Guard: Cannot edit opening stock after transactions exist (accounting period integrity)
            if stock_adjustment != 0 and (item.purchases or item.sales):
                flash("Cannot edit opening stock after transactions have been recorded! "
                      "Create a stock adjustment instead to change inventory.", "danger")
                return render_template("edit_item.html", item=item, categories=categories)
            # The opening entry is about to be reversed and re-posted, so the
            # inventory value must move by the same amount the GL does.
            old_opening_value = (Decimal(str(item.opening_stock or 0))
                                 * Decimal(str(item.purchase_price or 0))).quantize(MONEY)
            item.name = name
            item.category_id = int(category_id)
            item.unit = unit
            item.opening_stock = new_os
            item.reorder_level = int(reorder_level)
            item.purchase_price = float(purchase_price) if purchase_price else None
            item.sale_price = float(sale_price) if sale_price else None
            item.barcode = barcode
            new_opening_value = (Decimal(str(new_os)) * Decimal(str(item.purchase_price or 0))).quantize(MONEY)
            value_adjustment = new_opening_value - old_opening_value
            # Update stock and inventory_value atomically
            if stock_adjustment > 0:
                item_add_stock(item, stock_adjustment, cost_total=value_adjustment)
            elif stock_adjustment < 0:
                item_remove_stock(item, -stock_adjustment, cost_total=-value_adjustment)
            unit_error = save_item_units(item)
            if unit_error:
                db.session.rollback()
                flash(unit_error, "danger")
                return render_template("edit_item.html", item=item, categories=categories)
            db.session.flush()
            post_item_opening(item)          # reverses the old opening entry and re-posts
            db.session.commit()
            record_audit("update", "Item", item.id, f"Item '{item.name}' edited")
            flash("Item updated successfully!", "success")
            return redirect(url_for("item"))
    return render_template("edit_item.html", item=item, categories=categories)

@app.route("/item/delete/<int:id>", methods=["POST"])
@admin_required
def delete_item(id):
    item = db.session.get(Item, id) or abort(404)
    if item.purchases or item.sales:
        flash("Cannot delete item with associated purchases or sales!", "danger")
    else:
        item_name = item.name
        # Clear GL entries for this item's opening balance before deletion
        _repost_opening("item_opening", item.id, _opening_date(),
                       f"Opening stock — {item.name}", [])
        db.session.delete(item)
        db.session.commit()
        record_audit("delete", "Item", id, f"Item '{item_name}' deleted")
        flash("Item deleted successfully!", "success")
    return redirect(url_for("item"))

@app.route("/item/<int:id>/ledger")
@verified_required
def item_ledger(id):
    item = db.session.get(Item, id) or abort(404)
    start_date_str = request.args.get("start_date", "")
    end_date_str   = request.args.get("end_date", "")

    # A reversed document never happened, so it must not still count here — its
    # stock effect was already undone, but its row still exists for the audit trail.
    purchase_items   = (PurchaseItem.query.join(Purchase)
                        .filter(PurchaseItem.item_id == id, Purchase.is_reversed.is_(False)).all())
    sale_items       = (SaleItem.query.join(Sale)
                        .filter(SaleItem.item_id == id, Sale.is_reversed.is_(False)).all())
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id, is_reversed=False).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id, is_reversed=False).all()

    # stock_in/out are in the item's base unit — the only unit item.stock (and this
    # ledger's running balance) is ever tracked in, whatever unit the line was actually
    # bought/sold in. Rate follows it down to a per-base-unit price so it still lines
    # up with stock_in/out (Rate × qty ≈ Value); Value itself is a total and needs no
    # conversion either way.
    entries = []
    for pi in purchase_items:
        factor = pi.unit_factor or 1
        entries.append({
            "date": pi.purchase_header.date, "type": "Purchase", "badge": "success",
            "ref": f"PO #{pi.purchase_header.id}", "party": pi.purchase_header.id_supplier.name,
            "stock_in": line_base_qty(pi), "stock_out": 0,
            "rate": pi.purchase_price / factor, "value": purchase_item_total(pi),
        })
    for si in sale_items:
        factor = si.unit_factor or 1
        entries.append({
            "date": si.sale_header.date, "type": "Sale", "badge": "primary",
            "ref": f"SO #{si.sale_header.id}", "party": si.sale_header.id_customer.name,
            "stock_in": 0, "stock_out": line_base_qty(si),
            "rate": si.sale_price / factor, "value": sale_item_total(si),
        })
    for pr in purchase_returns:
        factor = pr.unit_factor or 1
        entries.append({
            "date": pr.date, "type": "Purchase Return", "badge": "warning",
            "ref": f"PR #{pr.id}", "party": pr.supplier.name,
            "stock_in": 0, "stock_out": line_base_qty(pr),
            "rate": pr.return_price / factor, "value": round(pr.quantity * pr.return_price, 2),
        })
    for sr in sale_returns:
        factor = sr.unit_factor or 1
        entries.append({
            "date": sr.date, "type": "Sale Return", "badge": "secondary",
            "ref": f"SR #{sr.id}", "party": sr.customer.name,
            "stock_in": line_base_qty(sr), "stock_out": 0,
            "rate": sr.return_price / factor, "value": round(sr.quantity * sr.return_price, 2),
        })

    adjustments = StockAdjustment.query.filter_by(item_id=id).all()
    for adj in adjustments:
        stock_in = adj.quantity if adj.direction == "in" else 0
        stock_out = adj.quantity if adj.direction == "out" else 0
        entries.append({
            "date": adj.date, "type": f"Adjustment ({adj.adj_type})", "badge": "info",
            "ref": f"ADJ #{adj.id}", "party": adj.reason or "—",
            "stock_in": stock_in, "stock_out": stock_out,
            "rate": 0, "value": 0,
        })

    entries.sort(key=lambda x: (x["date"], x["ref"]))

    date_filtered = False
    if start_date_str and end_date_str:
        try:
            sd = datetime.strptime(start_date_str, "%Y-%m-%d")
            ed = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
            entries = [e for e in entries if sd <= e["date"] <= ed]
            date_filtered = True
        except ValueError:
            flash("Invalid date format!", "danger")

    # Opening stock entry — prepend when not date-filtered (or always as starting balance)
    opening = item.opening_stock or 0
    if not date_filtered:
        opening_entry = {
            "date": None, "type": "Opening Stock", "badge": "dark",
            "ref": "—", "party": "—",
            "stock_in": opening, "stock_out": 0,
            "rate": 0, "value": 0,
            "balance": opening, "is_opening": True,
        }
        entries = [opening_entry] + entries
        balance = opening
    else:
        balance = 0

    for e in entries:
        if e.get("is_opening"):
            continue
        balance += e["stock_in"] - e["stock_out"]
        e["balance"] = balance

    total_in  = sum(e["stock_in"]  for e in entries if not e.get("is_opening"))
    total_out = sum(e["stock_out"] for e in entries if not e.get("is_opening"))

    return render_template(
        "item_ledger.html",
        item=item,
        entries=entries,
        total_in=total_in,
        total_out=total_out,
        opening_stock=opening,
        current_stock=item.stock,
        start_date=start_date_str,
        end_date=end_date_str,
    )

@app.route("/item/<int:id>/ledger/export")
@verified_required
def export_item_ledger(id):
    item = db.session.get(Item, id) or abort(404)
    # A reversed document never happened, so it must not still count here.
    purchase_items   = (PurchaseItem.query.join(Purchase)
                        .filter(PurchaseItem.item_id == id, Purchase.is_reversed.is_(False)).all())
    sale_items       = (SaleItem.query.join(Sale)
                        .filter(SaleItem.item_id == id, Sale.is_reversed.is_(False)).all())
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id, is_reversed=False).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id, is_reversed=False).all()

    # Stock In/Out are in the item's base unit, exactly like the on-screen ledger
    # (item_ledger() above) — whatever unit a line was actually transacted in.
    rows = []
    for pi in purchase_items:
        rows.append((pi.purchase_header.date, "Purchase", f"PO #{pi.purchase_header.id}", pi.purchase_header.id_supplier.name, line_base_qty(pi), 0, float(pi.purchase_price) / (pi.unit_factor or 1), purchase_item_total(pi)))
    for si in sale_items:
        rows.append((si.sale_header.date, "Sale", f"SO #{si.sale_header.id}", si.sale_header.id_customer.name, 0, line_base_qty(si), float(si.sale_price) / (si.unit_factor or 1), sale_item_total(si)))
    for pr in purchase_returns:
        rows.append((pr.date, "Purchase Return", f"PR #{pr.id}", pr.supplier.name, 0, line_base_qty(pr), float(pr.return_price) / (pr.unit_factor or 1), round(pr.quantity * pr.return_price, 2)))
    for sr in sale_returns:
        rows.append((sr.date, "Sale Return", f"SR #{sr.id}", sr.customer.name, line_base_qty(sr), 0, float(sr.return_price) / (sr.unit_factor or 1), round(sr.quantity * sr.return_price, 2)))

    rows.sort(key=lambda x: x[0])
    balance = 0
    csv_rows = []
    for date, typ, ref, party, sin, sout, rate, value in rows:
        balance += sin - sout
        csv_rows.append([date.strftime("%Y-%m-%d"), typ, ref, party, sin, sout, round(rate, 2), round(value, 2), balance])
    return csv_response(
        f"{item.name}_ledger.csv", "Item Stock Ledger",
        ["Date", "Type", "Reference", "Party", "Stock In", "Stock Out", "Rate", "Value", "Balance"],
        csv_rows, extra_info=f"Item: {item.name}",
    )

@app.route("/item/<int:id>/ledger/export/excel")
@verified_required
def export_item_ledger_excel(id):
    item = db.session.get(Item, id) or abort(404)
    # A reversed document never happened, so it must not still count here.
    purchase_items   = (PurchaseItem.query.join(Purchase)
                        .filter(PurchaseItem.item_id == id, Purchase.is_reversed.is_(False)).all())
    sale_items       = (SaleItem.query.join(Sale)
                        .filter(SaleItem.item_id == id, Sale.is_reversed.is_(False)).all())
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id, is_reversed=False).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id, is_reversed=False).all()

    # Stock In/Out are in the item's base unit, exactly like the on-screen ledger
    # (item_ledger() above) — whatever unit a line was actually transacted in.
    raw = []
    for pi in purchase_items:
        raw.append((pi.purchase_header.date, "Purchase", f"PO #{pi.purchase_header.id}", pi.purchase_header.id_supplier.name, line_base_qty(pi), 0, float(pi.purchase_price) / (pi.unit_factor or 1), purchase_item_total(pi)))
    for si in sale_items:
        raw.append((si.sale_header.date, "Sale", f"SO #{si.sale_header.id}", si.sale_header.id_customer.name, 0, line_base_qty(si), float(si.sale_price) / (si.unit_factor or 1), sale_item_total(si)))
    for pr in purchase_returns:
        raw.append((pr.date, "Purchase Return", f"PR #{pr.id}", pr.supplier.name, 0, line_base_qty(pr), float(pr.return_price) / (pr.unit_factor or 1), round(pr.quantity * pr.return_price, 2)))
    for sr in sale_returns:
        raw.append((sr.date, "Sale Return", f"SR #{sr.id}", sr.customer.name, line_base_qty(sr), 0, float(sr.return_price) / (sr.unit_factor or 1), round(sr.quantity * sr.return_price, 2)))

    raw.sort(key=lambda x: x[0])
    balance = item.opening_stock
    excel_rows = [["Opening", "Opening Stock", "", "", item.opening_stock, 0, 0, 0, balance]]
    for date, typ, ref, party, sin, sout, rate, value in raw:
        balance += sin - sout
        excel_rows.append([date.strftime("%Y-%m-%d"), typ, ref, party, sin, sout, round(rate, 2), round(value, 2), balance])

    return excel_response(
        filename=f"{item.name}_ledger.xlsx",
        title="Item Stock Ledger",
        col_headers=["Date", "Type", "Reference", "Party", "Stock In", "Stock Out", "Rate", "Value", "Balance"],
        rows=excel_rows,
        extra_info=f"Item: {item.name} | Unit: {item.unit or 'Pcs'}",
    )

# ─── BULK IMPORT ROUTES ───────────────────────────────────────────────────────

@app.route("/import", methods=["GET"])
@verified_required
def bulk_import():
    """Show bulk import form."""
    import_history = ImportLog.query.filter_by(user_id=current_user.id).order_by(ImportLog.created_at.desc()).limit(10).all()
    return render_template("bulk_import.html", import_history=import_history)

@app.route("/import/process", methods=["POST"])
@verified_required
def process_import():
    """Process bulk import file."""
    import_type = request.form.get("import_type", "").lower()
    file = request.files.get("file")

    if not import_type or import_type not in ("items", "customers", "suppliers"):
        flash("Invalid import type selected", "danger")
        return redirect(url_for("bulk_import"))

    if not file:
        flash("No file selected", "danger")
        return redirect(url_for("bulk_import"))

    data, file_type = parse_import_file(file)
    if data is None:
        flash(file_type, "danger")  # file_type contains error message
        return redirect(url_for("bulk_import"))

    if not data:
        flash("File is empty", "danger")
        return redirect(url_for("bulk_import"))

    # Create import log entry
    import_log = ImportLog(
        user_id=current_user.id,
        import_type=import_type,
        file_name=file.filename,
        file_type=file_type,
        total_records=len(data),
        status="processing"
    )
    db.session.add(import_log)
    db.session.commit()

    # Process based on type
    try:
        if import_type == "items":
            success, failed, errors = import_items(data)
        elif import_type == "customers":
            success, failed, errors = import_customers(data)
        elif import_type == "suppliers":
            success, failed, errors = import_suppliers(data)

        import_log.successful = success
        import_log.failed = failed
        import_log.status = "completed"
        if errors:
            import_log.errors = json.dumps(errors[:100])  # Store first 100 errors
        db.session.commit()

        message = f"{success} records imported successfully"
        if failed > 0:
            message += f", {failed} failed"
        flash(message, "success" if failed == 0 else "warning")

    except Exception as e:
        import_log.status = "failed"
        import_log.errors = json.dumps([str(e)])
        db.session.commit()
        flash(f"Import failed: {str(e)}", "danger")

    return redirect(url_for("bulk_import"))

@app.route("/api/item/<int:id>")
@verified_required
def get_item(id):
    item = db.session.get(Item, id) or abort(404)
    return {
        "purchase_price": item.purchase_price,
        "sale_price": item.sale_price,
        "unit": item.unit or "Pcs",
        "category": item.id_category.name if item.id_category else None,
    }

@app.route("/purchase", methods=["GET", "POST"])
@verified_required
def purchase():
    search = request.args.get("search", "").strip()
    query = Purchase.query
    if search:
        query = query.join(Supplier).filter(Supplier.name.ilike(f"%{search}%"))
    purchases, pagination = get_paginated_results(query.order_by(Purchase.date.desc(), Purchase.id.desc()))
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
                    item_obj = get_item_locked(int(iid)) or abort(404)   # lock: no lost stock updates
                    qty_i  = int(qty)
                    price_f = float(price)
                    d_val_f = float(d_val or 0)
                    tax_f   = float(tax or 0)
                    gross   = qty_i * price_f
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
                    # Tax is recoverable, so it is not part of what the goods cost.
                    # Stock moves in the item's base unit, however this line was priced.
                    item_add_stock(item_obj, qty_i * unit_factor, net - tax_amt)
                db.session.flush()
                db.session.refresh(pur)
                pur.invoice_no = allocate_document_number("purchase", pur.date)
                sync_supplier_purchase(pur)
                post_document("purchase", pur)
                db.session.commit()
                record_audit("create", "Purchase", pur.id,
                             f"Purchase {pur.invoice_no}, total {purchase_total(pur):,.2f}")
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

@app.route("/purchase/edit/<int:id>", methods=["GET", "POST"])
@manager_required
def edit_purchase(id):
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
                # Reverse old stock (with inventory_value sync)
                touched_items = {}
                for pi in pur.line_items:
                    old_item = get_item_locked(pi.item_id)
                    if old_item:
                        cost_removed = pi.amount - pi.tax_amount
                        item_remove_stock(old_item, line_base_qty(pi), cost_total=cost_removed)
                        touched_items[old_item.id] = old_item
                # Delete old line items
                PurchaseItem.query.filter_by(purchase_id=pur.id).delete()
                # Update header
                first_iid, first_qty, first_price = rows[0][0], rows[0][1], rows[0][2]
                pur.supplier_id    = int(supplier_id)
                pur.item_id        = int(first_iid)
                pur.quantity       = int(first_qty)
                pur.purchase_price = float(first_price)
                pur.discount_type  = "percent"; pur.discount_value = 0
                pur.discount_amount= 0; pur.tax_percent = 0; pur.tax_amount = 0
                pur.date           = datetime.strptime(date_str, "%Y-%m-%d")
                pur.notes          = notes or None
                # Create new line items
                for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                    item_obj = touched_items.get(int(iid)) or get_item_locked(int(iid)) or abort(404)
                    qty_i = int(qty); price_f = float(price)
                    d_val_f = float(d_val or 0); tax_f = float(tax or 0)
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

@app.route("/purchase/delete/<int:id>", methods=["POST"])
@admin_required
def delete_purchase(id):
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

@app.route("/sale", methods=["GET", "POST"])
@verified_required
def sale():
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
                # Check stock for all items first
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
                        # Authoritative check under the row lock — a concurrent sale
                        # may have reduced stock since the pre-check above.
                        if item_obj.stock < base_qty:
                            db.session.rollback()
                            flash(f"Insufficient stock for {item_obj.name}: only {item_obj.stock} "
                                  "available now (it changed while saving). Please try again.", "danger")
                            return redirect(url_for("sale"))
                        d_val_f = float(d_val or 0); tax_f = float(tax or 0)
                        gross = qty_i * price_f
                        disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                        # Cost is the weighted average at the moment of sale, captured
                        # on the line so a later purchase never re-values a past sale.
                        # avg_cost is per base unit, so the total needs the base qty.
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

# ── Point of Sale (POS) ───────────────────────────────────────────────────────
# A fast counter screen: scan or search an item, build a cart, take payment, print a
# receipt. It creates an ordinary Sale — and, when money changes hands, an ordinary
# customer receipt — through the *same* posting layer every other document uses. There
# is no second path into the ledger: a POS sale moves stock, posts COGS, updates the
# customer's ledger and hits the general ledger exactly like a sale typed on the sales
# page. That is the whole point; a till that kept its own books would defeat the system.

def get_walkin_customer():
    """The counter's default customer, for a cash sale to nobody in particular.

    A sale needs a customer (its receivable has to belong to someone), but most POS
    sales are paid on the spot and to a passer-by. So there is one shared 'Walk-in
    Customer'; the sale posts against it and the immediate receipt clears it, leaving
    its balance at zero. A named customer can still be chosen for a credit sale."""
    c = Customer.query.filter_by(name="Walk-in Customer").first()
    if c is None:
        c = Customer(name="Walk-in Customer", contact="0000000000",
                     address="Walk-in", opening_balance=0)
        db.session.add(c)
        db.session.commit()
    return c

@app.route("/pos")
@verified_required
def pos():
    if current_user.role not in ("admin", "manager"):
        flash("Access denied. Only managers and admins can use the POS.", "danger")
        return redirect(url_for("dashboard.dashboard"))
    return render_template(
        "pos.html",
        customers=Customer.query.order_by(Customer.name).all(),
        accounts=active_accounts(),
        walkin=get_walkin_customer(),
        today=now_local().strftime("%Y-%m-%d"),
    )

@app.route("/pos/lookup")
@verified_required
def pos_lookup():
    """Item lookup for the counter. An exact barcode/QR match returns that one item
    (what a scanner sends); otherwise a name search returns a short list to tap."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return {"items": []}
    exact = Item.query.filter_by(barcode=q).first()
    if exact:
        matches = [exact]
    else:
        matches = (Item.query
                   .filter(or_(Item.name.ilike(f"%{q}%"), Item.barcode.ilike(f"%{q}%")))
                   .order_by(Item.name).limit(20).all())
    return {"items": [{
        "id": it.id, "name": it.name, "barcode": it.barcode or "",
        "price": float(it.sale_price or 0), "stock": it.stock,
        "unit": it.unit or "Pcs",
        "units": [{"key": u["key"], "name": u["name"], "factor": u["factor"],
                   "price": float(u["sale_price"] or 0) or float(it.sale_price or 0)}
                  for u in item_unit_choices(it)],
    } for it in matches]}

@app.route("/pos/checkout", methods=["POST"])
@verified_required
def pos_checkout():
    """Ring up a cart: one Sale, optionally one receipt, all through the posting layer.

    Everything happens in a single transaction. If the stock check fails under the row
    lock, or anything else raises, the whole sale rolls back — the till never leaves a
    half-finished document behind."""
    if current_user.role not in ("admin", "manager"):
        return {"ok": False, "error": "Access denied."}, 403

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
            # The factor is resolved from the item's own unit definitions server-side,
            # never trusted from the client — a wrong factor would corrupt stock.
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

        # The money taken. Never receipt more than the sale is worth — an overpayment is
        # change handed back at the counter, not a credit sitting on the customer.
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
        app.logger.exception("POS checkout failed")
        return {"ok": False, "error": f"Could not complete the sale: {e}"}, 500

@app.route("/pos/receipt/<int:id>")
@verified_required
def pos_receipt(id):
    """A compact, thermal-printer-friendly receipt for a POS sale."""
    sal = db.session.get(Sale, id) or abort(404)
    received = get_sale_received(sal.id)
    return render_template("pos_receipt.html", sale=sal,
                           total=sale_total(sal), received=received)

# ── Item labels (printable barcode / QR stickers) ─────────────────────────────
def code_svg(value, kind="barcode"):
    """Inline SVG for one item's code — a Code128 barcode, or a QR.

    SVG, not a PNG, so it prints crisp at any size and needs no image files on disk or
    fetched over the network (a strict CSP would block those anyway). Returns '' if the
    value cannot be encoded, so one bad code never takes the whole label sheet down."""
    if not value:
        return ""
    from io import BytesIO
    buf = BytesIO()
    try:
        if kind == "qr":
            import qrcode
            import qrcode.image.svg
            qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage).save(buf)
        else:
            import barcode
            from barcode.writer import SVGWriter
            barcode.get("code128", str(value), writer=SVGWriter()).write(buf)
        svg = buf.getvalue().decode("utf-8")
        i = svg.find("<svg")           # drop the XML declaration so it embeds inline
        return svg[i:] if i != -1 else svg
    except Exception:
        app.logger.exception("Could not render a %s for %r", kind, value)
        return ""

@app.route("/labels")
@manager_required
def labels():
    """Printable barcode / QR labels to stick on stock.

    Pick how many of each item, barcode or QR, and print a sheet. Only items that have a
    code get a label; items without one are listed with a one-click way to assign codes."""
    items = Item.query.outerjoin(Category, Item.category_id == Category.id) \
        .order_by(Category.name, Item.name).all()
    kind = "qr" if request.args.get("type") == "qr" else "barcode"
    show_price = request.args.get("price", "1") != "0"

    sheet = []
    for it in items:
        try:
            copies = int(request.args.get(f"copies_{it.id}", "0") or 0)
        except ValueError:
            copies = 0
        if copies > 0 and it.barcode:
            svg = code_svg(it.barcode, kind)
            for _ in range(min(copies, 200)):        # a sane cap per print run
                sheet.append({"name": it.name, "price": it.sale_price,
                              "code": it.barcode, "svg": svg})

    missing = [it for it in items if not it.barcode]
    return render_template("labels.html", items=items, sheet=sheet, kind=kind,
                           show_price=show_price, missing=missing)

@app.route("/labels/assign", methods=["POST"])
@manager_required
def labels_assign():
    """Give every item that has no code a stable numeric one, so it can be labelled and
    scanned. Numeric (not the name) because the cheapest scanners read digits most
    reliably, and the id makes it unique and repeatable."""
    n = 0
    for it in Item.query.filter(or_(Item.barcode.is_(None), Item.barcode == "")).all():
        it.barcode = f"{it.id:012d}"
        n += 1
    if n:
        db.session.commit()
    flash(f"Assigned a code to {n} item(s) that had none.", "success")
    return redirect(url_for("labels"))

@app.route("/sale/edit/<int:id>", methods=["GET", "POST"])
@manager_required
def edit_sale(id):
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
                # Restore old stock (with inventory_value sync)
                for si in sal.line_items:
                    old_item = get_item_locked(si.item_id)
                    if old_item:
                        cost_returned = si.cost_price * line_base_qty(si)
                        item_add_stock(old_item, line_base_qty(si), cost_total=cost_returned)
                # Check new stock
                stock_errors = []
                for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                    item_obj = get_item_locked(int(iid))
                    if item_obj:
                        _, factor = resolve_item_unit(item_obj, unit_key)
                        if item_obj.stock < int(qty) * factor:
                            stock_errors.append(f"{item_obj.name}: only {item_obj.stock} {item_obj.unit} available")
                if stock_errors:
                    # Undo stock restoration (with inventory_value sync)
                    for si in sal.line_items:
                        old_item = get_item_locked(si.item_id)
                        if old_item:
                            cost_removed = si.cost_price * line_base_qty(si)
                            item_remove_stock(old_item, line_base_qty(si), cost_total=cost_removed)
                    flash("Insufficient stock — " + "; ".join(stock_errors), "danger")
                else:
                    # Delete old line items, update header
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
                        # Cost is the weighted average at the moment of sale, captured
                        # on the line so a later purchase never re-values a past sale.
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

@app.route("/sale/delete/<int:id>", methods=["POST"])
@admin_required
def delete_sale(id):
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

@app.route("/supplier_payment/edit/<int:id>", methods=["GET", "POST"])
@manager_required
def edit_supplier_payment(id):
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

@app.route("/supplier_payment/delete/<int:id>", methods=["POST"])
@admin_required
def delete_supplier_payment(id):
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

@app.route("/supplier_bulk_payment", methods=["GET", "POST"])
@verified_required
def supplier_bulk_payment():
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

@app.route("/customer_bulk_receipt", methods=["GET", "POST"])
@verified_required
def customer_bulk_receipt():
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

@app.route("/customer_receipt/edit/<int:id>", methods=["GET", "POST"])
@manager_required
def edit_customer_receipt(id):
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

@app.route("/customer_receipt/delete/<int:id>", methods=["POST"])
@admin_required
def delete_customer_receipt(id):
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

@app.route("/supplier/<int:id>/ledger", methods=["GET", "POST"])
@verified_required
def supplier_ledger(id):
    supplier = db.session.get(Supplier, id) or abort(404)
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    if request.method == "POST" and request.form.get("action") == "adjustment":
        if current_user.role not in ("admin", "manager"):
            flash("Access denied. Only managers and admins can add ledger adjustments.", "danger")
            return redirect(url_for("supplier_ledger", id=id))
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
@admin_required
def delete_supplier_ledger_adjustment(id, entry_id):
    entry = SupplierLedgerEntry.query.filter_by(id=entry_id, supplier_id=id, source_type="adjustment").first() or abort(404)
    db.session.delete(entry)
    recalculate_supplier_ledger(id)
    db.session.commit()
    flash("Adjustment deleted!", "success")
    return redirect(url_for("supplier_ledger", id=id))

@app.route("/supplier/<int:id>/ledger/export")
@manager_required
def export_supplier_ledger(id):
    supplier = db.session.get(Supplier, id) or abort(404)
    entries = (
        SupplierLedgerEntry.query.filter_by(supplier_id=id)
        .order_by(SupplierLedgerEntry.entry_date.asc(), SupplierLedgerEntry.id.asc())
        .all()
    )
    rows = [
        [e.entry_date.strftime("%Y-%m-%d"), e.entry_type, e.description, round(e.debit, 2), round(e.credit, 2), round(e.balance_after, 2)]
        for e in entries
    ]
    return csv_response(
        f"{supplier.name}_ledger.csv", "Supplier Ledger",
        ["Date", "Type", "Description", "Debit", "Credit", "Balance"],
        rows, extra_info=f"Supplier: {supplier.name}",
    )

@app.route("/supplier/<int:id>/ledger/export/excel")
@manager_required
def export_supplier_ledger_excel(id):
    supplier = db.session.get(Supplier, id) or abort(404)
    entries = (
        SupplierLedgerEntry.query.filter_by(supplier_id=id)
        .order_by(SupplierLedgerEntry.entry_date.asc(), SupplierLedgerEntry.id.asc())
        .all()
    )
    rows = [
        [e.entry_date.strftime("%Y-%m-%d"), e.entry_type, e.description, round(e.debit, 2), round(e.credit, 2), round(e.balance_after, 2)]
        for e in entries
    ]
    return excel_response(
        filename=f"{supplier.name}_ledger.xlsx",
        title="Supplier Ledger",
        col_headers=["Date", "Type", "Description", "Debit", "Credit", "Balance"],
        rows=rows,
        extra_info=f"Supplier: {supplier.name}",
    )

@app.route("/customer/<int:id>/ledger", methods=["GET", "POST"])
@verified_required
def customer_ledger(id):
    customer = db.session.get(Customer, id) or abort(404)
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    if request.method == "POST" and request.form.get("action") == "adjustment":
        if current_user.role not in ("admin", "manager"):
            flash("Access denied. Only managers and admins can add ledger adjustments.", "danger")
            return redirect(url_for("customer_ledger", id=id))
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
@admin_required
def delete_customer_ledger_adjustment(id, entry_id):
    entry = CustomerLedgerEntry.query.filter_by(id=entry_id, customer_id=id, source_type="adjustment").first() or abort(404)
    db.session.delete(entry)
    recalculate_customer_ledger(id)
    db.session.commit()
    flash("Adjustment deleted!", "success")
    return redirect(url_for("customer_ledger", id=id))

@app.route("/customer/<int:id>/ledger/export")
@manager_required
def export_customer_ledger(id):
    customer = db.session.get(Customer, id) or abort(404)
    entries = (
        CustomerLedgerEntry.query.filter_by(customer_id=id)
        .order_by(CustomerLedgerEntry.entry_date.asc(), CustomerLedgerEntry.id.asc())
        .all()
    )
    rows = [
        [e.entry_date.strftime("%Y-%m-%d"), e.entry_type, e.description, round(e.debit, 2), round(e.credit, 2), round(e.balance_after, 2)]
        for e in entries
    ]
    return csv_response(
        f"{customer.name}_ledger.csv", "Customer Ledger",
        ["Date", "Type", "Description", "Debit", "Credit", "Balance"],
        rows, extra_info=f"Customer: {customer.name}",
    )

@app.route("/customer/<int:id>/ledger/export/excel")
@manager_required
def export_customer_ledger_excel(id):
    customer = db.session.get(Customer, id) or abort(404)
    entries = (
        CustomerLedgerEntry.query.filter_by(customer_id=id)
        .order_by(CustomerLedgerEntry.entry_date.asc(), CustomerLedgerEntry.id.asc())
        .all()
    )
    rows = [
        [e.entry_date.strftime("%Y-%m-%d"), e.entry_type, e.description, round(e.debit, 2), round(e.credit, 2), round(e.balance_after, 2)]
        for e in entries
    ]
    return excel_response(
        filename=f"{customer.name}_ledger.xlsx",
        title="Customer Ledger",
        col_headers=["Date", "Type", "Description", "Debit", "Credit", "Balance"],
        rows=rows,
        extra_info=f"Customer: {customer.name}",
    )

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

@app.route("/api/search")
@verified_required
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return {"results": []}
    pattern = f"%{q}%"
    results = []

    for s in Supplier.query.filter(
        Supplier.name.ilike(pattern) | Supplier.contact.ilike(pattern) | Supplier.address.ilike(pattern)
    ).limit(5):
        bal = get_supplier_balance(s.id)
        results.append({
            "type": "supplier",
            "icon": "bi-truck",
            "color": "primary",
            "name": s.name,
            "detail": s.contact,
            "extra": f"Balance: {bal:,.2f}",
            "url": url_for("supplier_ledger", id=s.id),
        })

    for c in Customer.query.filter(
        Customer.name.ilike(pattern) | Customer.contact.ilike(pattern) | Customer.address.ilike(pattern)
    ).limit(5):
        bal = get_customer_balance(c.id)
        results.append({
            "type": "customer",
            "icon": "bi-person-check",
            "color": "success",
            "name": c.name,
            "detail": c.contact,
            "extra": f"Balance: {bal:,.2f}",
            "url": url_for("customer_ledger", id=c.id),
        })

    for i in Item.query.filter(
        Item.name.ilike(pattern)
    ).limit(5):
        cat = i.id_category.name if i.id_category else "—"
        results.append({
            "type": "item",
            "icon": "bi-box-seam",
            "color": "warning",
            "name": i.name,
            "detail": cat,
            "extra": f"Stock: {i.stock}",
            "url": url_for("item_ledger", id=i.id),
        })

    return {"results": results}

@app.route("/reports", methods=["GET", "POST"])
@manager_required
def reports():
    purchase_report = sale_report = date_profit_report = item_profit = customer_profit = category_profit = []
    supplier_balances = customer_balances = supplier_payment_history = customer_receipt_history = []
    purchase_return_report = sale_return_report = supplier_purchase_report = []
    total_sale_amt = total_profit_amt = total_purchase_cost = 0
    # Stock lives on its own page now — see report_stock(). It is a snapshot of what is
    # in the warehouse right now, so it never belonged behind a date range.
    total_purchase_return_amt = total_sale_return_amt = 0
    gross_profit = net_sale_amt = net_purchase_cost = 0
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
                end_date           = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
                purchase_report    = Purchase.query.filter(Purchase.date.between(start_date, end_date)).order_by(Purchase.date.desc()).all()
                sale_report        = Sale.query.filter(Sale.date.between(start_date, end_date)).order_by(Sale.date.desc()).all()
                purchase_return_report = PurchaseReturn.query.filter(PurchaseReturn.date.between(start_date, end_date)).order_by(PurchaseReturn.date.desc()).all()
                sale_return_report = SaleReturn.query.filter(SaleReturn.date.between(start_date, end_date)).order_by(SaleReturn.date.desc()).all()
                # sale_amt  = gross - discount + tax  (what customer pays = net total) = SaleItem.amount
                # profit    = gross - discount - cogs (tax excluded from profit)
                _sale_net  = SaleItem.amount
                _sale_prof = SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.unit_factor * SaleItem.cost_price
                date_profit_report = (
                    db.session.query(
                        db.func.date(Sale.date).label("sale_date"),
                        db.func.sum(_sale_net).label("sale_amt"),
                        db.func.sum(_sale_prof).label("profit_amt"),
                    )
                    .select_from(SaleItem)
                    .join(Sale, SaleItem.sale_id == Sale.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(db.func.date(Sale.date))
                    .order_by(db.func.date(Sale.date))
                    .all()
                )
                item_profit = (
                    db.session.query(
                        Item.name.label("name"),
                        Category.name.label("category"),
                        db.func.sum(_sale_net).label("sale_amt"),
                        db.func.sum(_sale_prof).label("profit_amt"),
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
                customer_profit = (
                    db.session.query(
                        Customer.name.label("name"),
                        db.func.sum(_sale_net).label("sale_amt"),
                        db.func.sum(_sale_prof).label("profit_amt"),
                    )
                    .select_from(SaleItem)
                    .join(Sale, SaleItem.sale_id == Sale.id)
                    .join(Customer, Sale.customer_id == Customer.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(Customer.name)
                    .order_by(Customer.name)
                    .all()
                )
                category_profit = (
                    db.session.query(
                        Category.name.label("name"),
                        db.func.sum(_sale_net).label("sale_amt"),
                        db.func.sum(_sale_prof).label("profit_amt"),
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
                _pur_net = PurchaseItem.amount
                supplier_purchase_report = (
                    db.session.query(
                        Supplier.name.label("name"),
                        db.func.count(db.func.distinct(Purchase.id)).label("bill_count"),
                        db.func.sum(PurchaseItem.quantity * PurchaseItem.unit_factor).label("total_qty"),
                        db.func.sum(_pur_net).label("total_amt"),
                    )
                    .select_from(PurchaseItem)
                    .join(Purchase, PurchaseItem.purchase_id == Purchase.id)
                    .join(Supplier, Purchase.supplier_id == Supplier.id)
                    .filter(Purchase.date.between(start_date, end_date))
                    .group_by(Supplier.name)
                    .order_by(db.func.sum(_pur_net).desc())
                    .all()
                )
                totals = (
                    db.session.query(
                        db.func.sum(_sale_net).label("total_sale_amt"),
                        db.func.sum(_sale_prof).label("total_profit_amt"),
                        db.func.sum(SaleItem.quantity * SaleItem.unit_factor * SaleItem.cost_price).label("total_purchase_cost"),
                    )
                    .select_from(SaleItem)
                    .join(Sale, SaleItem.sale_id == Sale.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .first()
                )
                total_sale_amt      = totals.total_sale_amt or 0.0
                total_profit_amt    = totals.total_profit_amt or 0.0
                total_purchase_cost = totals.total_purchase_cost or 0.0
                total_purchase_return_amt = sum(r.quantity * r.return_price for r in purchase_return_report)
                total_sale_return_amt     = sum(r.quantity * r.return_price for r in sale_return_report)
                net_sale_amt        = total_sale_amt - total_sale_return_amt
                net_purchase_cost   = total_purchase_cost - total_purchase_return_amt
                gross_profit        = total_profit_amt
                purchase_qty_total  = sum(pi.base_quantity for p in purchase_report for pi in p.line_items)
                purchase_amt_total  = sum(purchase_total(p) for p in purchase_report)
                sale_qty_total      = sum(si.base_quantity for s in sale_report for si in s.line_items)
                sale_amt_total      = sum(sale_total(s) for s in sale_report)
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
        purchase_return_report=purchase_return_report,
        sale_return_report=sale_return_report,
        supplier_purchase_report=supplier_purchase_report,
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
        total_purchase_cost=total_purchase_cost,
        total_purchase_return_amt=total_purchase_return_amt,
        total_sale_return_amt=total_sale_return_amt,
        net_sale_amt=net_sale_amt,
        net_purchase_cost=net_purchase_cost,
        gross_profit=gross_profit,
        purchase_qty_total=purchase_qty_total,
        purchase_amt_total=purchase_amt_total,
        sale_qty_total=sale_qty_total,
        sale_amt_total=sale_amt_total,
        start_date=start_date_str,
        end_date=end_date_str,
    )

@app.route("/export_purchase_report", methods=["POST"])
@manager_required
def export_purchase_report():
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
            [pi.purchase_id, pi.purchase_header.id_supplier.name, pi.item.name,
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

@app.route("/export_sale_report", methods=["POST"])
@manager_required
def export_sale_report():
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

@app.route("/export_date_sale_report", methods=["POST"])
@manager_required
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

@app.route("/export_item_sale_report", methods=["POST"])
@manager_required
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

@app.route("/export_customer_sale_report", methods=["POST"])
@manager_required
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

@app.route("/export_category_sale_report", methods=["POST"])
@manager_required
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

@app.route("/export_supplier_payable")
@manager_required
def export_supplier_payable():
    col_headers = ["Supplier", "Opening", "Bills", "Paid", "Ledger Balance", "Status"]
    rows = []
    for s in Supplier.query.order_by(Supplier.name).all():
        bal = get_supplier_balance(s.id)
        rows.append([s.name, round(float(s.opening_balance or 0), 2),
                     round(get_supplier_payable(s.id), 2), round(get_supplier_paid(s.id), 2),
                     round(bal, 2), supplier_balance_label(bal)])
    if request.args.get("format") == "xlsx":
        return excel_response("supplier_payable_report.xlsx", "Supplier Payable Report", col_headers, rows)
    return csv_response("supplier_payable_report.csv", "Supplier Payable Report", col_headers, rows)

@app.route("/export_customer_receivable")
@manager_required
def export_customer_receivable():
    col_headers = ["Customer", "Opening", "Sales", "Received", "Ledger Balance", "Status"]
    rows = []
    for c in Customer.query.order_by(Customer.name).all():
        bal = get_customer_balance(c.id)
        rows.append([c.name, round(float(c.opening_balance or 0), 2),
                     round(get_customer_receivable(c.id), 2), round(get_customer_received(c.id), 2),
                     round(bal, 2), customer_balance_label(bal)])
    if request.args.get("format") == "xlsx":
        return excel_response("customer_receivable_report.xlsx", "Customer Receivable Report", col_headers, rows)
    return csv_response("customer_receivable_report.csv", "Customer Receivable Report", col_headers, rows)

@app.route("/export_purchase_return_report", methods=["POST"])
@manager_required
def export_purchase_return_report():
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

@app.route("/export_sale_return_report", methods=["POST"])
@manager_required
def export_sale_return_report():
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

@app.route("/export_supplier_purchase_report", methods=["POST"])
@manager_required
def export_supplier_purchase_report():
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

@app.route("/export_stock_report")
@manager_required
def export_stock_report():
    items = Item.query.outerjoin(Category, Item.category_id == Category.id).order_by(Category.name, Item.name).all()
    col_headers = ["Item", "Category", "Stock", "Reorder Level", "Avg Cost", "Sale Price", "Stock Value", "Status"]
    rows = []
    for item in items:
        rows.append([
            item.name, item.id_category.name if item.id_category else "N/A",
            item.stock, item.reorder_level,
            round(item.avg_cost, 2) if item.stock else 0, round(item.sale_price or 0, 2),
            round(item.inventory_value or 0, 2),
            "Low Stock" if item.stock <= item.reorder_level else "OK",
        ])
    if request.args.get("format") == "xlsx":
        return excel_response("stock_report.xlsx", "Stock / Inventory Report", col_headers, rows)
    return csv_response("stock_report.csv", "Stock / Inventory Report", col_headers, rows)

@app.route("/export_supplier_payment_history", methods=["POST"])
@manager_required
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
        col_headers = ["ID", "Supplier", "Purchase ID", "Amount", "Method", "Reference", "Date", "Notes"]
        rows = [
            [p.id, p.supplier.name, p.purchase_id or "General",
             round(p.amount, 2), p.payment_method,
             p.reference_no or "", p.payment_date.strftime("%Y-%m-%d"), p.notes or ""]
            for p in payments
        ]
        if request.form.get("format") == "xlsx":
            return excel_response("supplier_payment_history.xlsx", "Supplier Payment History", col_headers, rows, start_date_str, end_date_str)
        return csv_response("supplier_payment_history.csv", "Supplier Payment History", col_headers, rows, start_date_str, end_date_str)
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_customer_receipt_history", methods=["POST"])
@manager_required
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
        col_headers = ["ID", "Customer", "Sale ID", "Amount", "Method", "Reference", "Date", "Notes"]
        rows = [
            [r.id, r.customer.name, r.sale_id or "General",
             round(r.amount, 2), r.payment_method,
             r.reference_no or "", r.payment_date.strftime("%Y-%m-%d"), r.notes or ""]
            for r in receipts
        ]
        if request.form.get("format") == "xlsx":
            return excel_response("customer_receipt_history.xlsx", "Customer Receipt History", col_headers, rows, start_date_str, end_date_str)
        return csv_response("customer_receipt_history.csv", "Customer Receipt History", col_headers, rows, start_date_str, end_date_str)
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
    # A reversed purchase never happened, so nothing can be returned against it.
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
                        # A return is always in the original line's unit, never chosen separately.
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
                            # Goods leave at what they cost us, not at the agreed
                            # credit note — the difference is a gain or loss.
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

@app.route("/purchase_return/delete/<int:id>", methods=["POST"])
@admin_required
def delete_purchase_return(id):
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
    # A reversed sale never happened, so nothing can be returned against it.
    all_sis = (SaleItem.query.join(Sale)
               .filter(Sale.is_reversed.is_(False))
               .order_by(Sale.date.desc(), SaleItem.id).all())
    items_available = [
        {"si": si, "remaining": si.quantity - get_sale_item_returned_qty(si)}
        for si in all_sis
        if si.quantity - get_sale_item_returned_qty(si) > 0
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
                    remaining = si.quantity - get_sale_item_returned_qty(si)
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
                        # A return is always in the original line's unit, never chosen separately.
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
                            # Back into stock at the cost they left at, taken from the
                            # original sale line — never at today's average. cost_price
                            # is per base unit, so the total needs the base qty.
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

@app.route("/sale_return/delete/<int:id>", methods=["POST"])
@admin_required
def delete_sale_return(id):
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

# ─── Stock Adjustment ──────────────────────────────────────────────────────────

@app.route("/stock_adjustment", methods=["GET", "POST"])
@manager_required
def stock_adjustment():
    search = request.args.get("search", "").strip()
    query = StockAdjustment.query.join(Item)
    if search:
        query = query.filter(Item.name.ilike(f"%{search}%"))
    adjustments, pagination = get_paginated_results(
        query.order_by(StockAdjustment.date.desc(), StockAdjustment.id.desc())
    )
    items = Item.query.order_by(Item.name).all()
    if request.method == "POST":
        item_id  = request.form.get("item_id", "").strip()
        adj_type = request.form.get("adj_type", "").strip()
        qty_str  = request.form.get("quantity", "").strip()
        reason   = request.form.get("reason", "").strip()
        date_str = request.form.get("date", "").strip()
        if not item_id or not adj_type or not qty_str or not date_str:
            flash("Item, type, quantity and date are required.", "danger")
        elif not qty_str.isdigit() or int(qty_str) <= 0:
            flash("Quantity must be a positive integer.", "danger")
        elif not (item_obj := get_item_locked(int(item_id))):
            flash("Item not found.", "danger")
        elif adj_type not in ADJUSTMENT_DIRECTIONS:
            # Never guess. An unrecognised type used to fall through to "in" and add
            # stock that nobody asked for.
            flash("Unknown adjustment type.", "danger")
        else:
            qty = int(qty_str)
            direction = ADJUSTMENT_DIRECTIONS[adj_type]
            if direction == "out" and item_obj.stock < qty:
                flash(f"Insufficient stock. Available: {item_obj.stock}", "danger")
            else:
                adj = StockAdjustment(
                    item_id=int(item_id), adj_type=adj_type, quantity=qty,
                    direction=direction,
                    date=datetime.strptime(date_str, "%Y-%m-%d"),
                    reason=reason or None,
                )
                db.session.add(adj)
                # Both directions are valued at the average: stock found is worth
                # what the rest of the stock is worth, stock lost costs the same.
                unit = item_obj.avg_cost
                if direction == "out":
                    adj.cost_value = item_remove_stock(item_obj, qty)
                else:
                    adj.cost_value = (unit * Decimal(str(qty))).quantize(MONEY)
                    item_add_stock(item_obj, qty, adj.cost_value)
                db.session.flush()
                post_document("stock_adjustment", adj)
                db.session.commit()
                flash(f"Stock {'reduced' if direction=='out' else 'increased'} by {qty} for {item_obj.name}.", "success")
                return redirect(url_for("stock_adjustment"))
    return render_template("stock_adjustment.html",
        adjustments=adjustments, items=items, pagination=pagination,
        search=search, adj_types=ADJUSTMENT_TYPES,
        today=now_local().strftime("%Y-%m-%d"))

@app.route("/stock_adjustment/delete/<int:id>", methods=["POST"])
@admin_required
def delete_stock_adjustment(id):
    adj = db.session.get(StockAdjustment, id) or abort(404)
    assert_not_posted("stock_adjustment", adj.id, f"Stock adjustment #{adj.id}")
    item_obj = get_item_locked(adj.item_id)
    if item_obj:
        if adj.direction == "out":
            item_add_stock(item_obj, adj.quantity, cost_total=adj.cost_value or 0)
        else:
            item_remove_stock(item_obj, adj.quantity, cost_total=adj.cost_value or 0)
    db.session.delete(adj)
    db.session.commit()
    flash("Adjustment deleted and stock reversed.", "success")
    return redirect(url_for("stock_adjustment"))

# ─── Expense Tracking ──────────────────────────────────────────────────────────

@app.route("/expenses", methods=["GET", "POST"])
@manager_required
def expenses():
    search = request.args.get("search", "").strip()
    query = Expense.query
    if search:
        query = query.filter(
            (Expense.description.ilike(f"%{search}%")) |
            (Expense.notes.ilike(f"%{search}%"))
        )
    expense_list, pagination = get_paginated_results(
        query.order_by(Expense.date.desc(), Expense.id.desc())
    )
    categories = ExpenseCategory.query.order_by(ExpenseCategory.name).all()
    if request.method == "POST":
        action = request.form.get("_action", "expense")
        if action == "add_category":
            cat_name = request.form.get("cat_name", "").strip()
            gl_id, gl_error = parse_expense_gl_account(request.form.get("gl_account_id"))
            if not cat_name:
                flash("Category name is required.", "danger")
            elif ExpenseCategory.query.filter_by(name=cat_name).first():
                flash("Category already exists.", "warning")
            elif gl_error:
                flash(gl_error, "danger")
            else:
                db.session.add(ExpenseCategory(name=cat_name, gl_account_id=gl_id))
                db.session.commit()
                flash(f"Category '{cat_name}' added.", "success")
            return redirect(url_for("expenses"))
        if action == "set_category_account":
            cat = db.session.get(ExpenseCategory, int(request.form.get("category_id", 0))) or abort(404)
            gl_id, gl_error = parse_expense_gl_account(request.form.get("gl_account_id"))
            if gl_error:
                flash(gl_error, "danger")
            else:
                cat.gl_account_id = gl_id
                db.session.commit()
                target = cat.gl_account.code if cat.gl_account else "6090 (default)"
                record_audit("update", "ExpenseCategory", cat.id,
                             f"Category '{cat.name}' now posts to {target}")
                flash(f"'{cat.name}' now posts to {target}.", "success")
            return redirect(url_for("expenses"))
        # add expense
        desc       = request.form.get("description", "").strip()
        amount_str = request.form.get("amount", "").strip()
        date_str   = request.form.get("date", "").strip()
        method     = request.form.get("payment_method", "Cash").strip()
        cat_id     = request.form.get("category_id", "").strip() or None
        ref        = request.form.get("reference_no", "").strip() or None
        notes      = request.form.get("notes", "").strip() or None
        account_id, account_error = parse_account_id(request.form.get("account_id"))
        if not desc or not amount_str or not date_str:
            flash("Description, amount and date are required.", "danger")
        elif method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
        elif account_error:
            flash(account_error, "danger")
        else:
            try:
                amount = float(amount_str)
                if amount <= 0:
                    flash("Amount must be positive.", "danger")
                else:
                    exp = Expense(
                        category_id=int(cat_id) if cat_id else None,
                        description=desc, amount=amount,
                        date=datetime.strptime(date_str, "%Y-%m-%d"),
                        payment_method=method, account_id=account_id,
                        reference_no=ref, notes=notes,
                    )
                    db.session.add(exp)
                    db.session.flush()
                    post_document("expense", exp)
                    db.session.commit()
                    flash("Expense recorded.", "success")
                    return redirect(url_for("expenses"))
            except ValueError:
                flash("Invalid amount or date.", "danger")
    total_expenses = float(db.session.query(func.sum(Expense.amount)).scalar() or 0)
    return render_template("expenses.html",
        expense_list=expense_list, categories=categories,
        pagination=pagination, search=search,
        payment_methods=PAYMENT_METHODS,
        gl_accounts=expense_gl_accounts(),
        total_expenses=total_expenses,
        today=now_local().strftime("%Y-%m-%d"))

@app.route("/expenses/delete/<int:id>", methods=["POST"])
@admin_required
def delete_expense(id):
    exp = db.session.get(Expense, id) or abort(404)
    assert_not_posted("expense", exp.id, f"Expense #{exp.id}")
    db.session.delete(exp)
    db.session.commit()
    flash("Expense deleted.", "success")
    return redirect(url_for("expenses"))

# ─── Purchase Orders ───────────────────────────────────────────────────────────

@app.route("/purchase_orders", methods=["GET", "POST"])
@manager_required
def purchase_orders():
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

@app.route("/purchase_orders/<int:id>")
@manager_required
def purchase_order_detail(id):
    po = db.session.get(PurchaseOrder, id) or abort(404)
    return render_template("purchase_order_detail.html", po=po, po_statuses=PO_STATUSES)

@app.route("/purchase_orders/<int:id>/status", methods=["POST"])
@manager_required
def update_po_status(id):
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

@app.route("/purchase_orders/<int:id>/convert", methods=["POST"])
@manager_required
def convert_po_to_purchase(id):
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
            item_add_stock(item_obj, poi.quantity * (poi.unit_factor or 1), gross)   # PO lines carry no tax
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

@app.route("/purchase_orders/<int:id>/delete", methods=["POST"])
@admin_required
def delete_purchase_order(id):
    po = db.session.get(PurchaseOrder, id) or abort(404)
    if po.status == "Received":
        flash("Cannot delete a received order.", "danger")
        return redirect(url_for("purchase_orders"))
    db.session.delete(po)
    db.session.commit()
    flash(f"Purchase Order #{id} deleted.", "success")
    return redirect(url_for("purchase_orders"))

# ─── Quotations ────────────────────────────────────────────────────────────────

@app.route("/quotations", methods=["GET", "POST"])
@manager_required
def quotations():
    search = request.args.get("search", "").strip()
    query = Quotation.query.join(Customer)
    if search:
        query = query.filter(Customer.name.ilike(f"%{search}%"))
    quotes, pagination = get_paginated_results(
        query.order_by(Quotation.quote_date.desc(), Quotation.id.desc())
    )
    customers = Customer.query.order_by(Customer.name).all()
    items     = Item.query.order_by(Item.name).all()
    if request.method == "POST":
        customer_id  = request.form.get("customer_id", "").strip()
        quote_date   = request.form.get("quote_date", "").strip()
        valid_until  = request.form.get("valid_until", "").strip()
        notes        = request.form.get("notes", "").strip()
        item_ids     = request.form.getlist("item_id[]")
        quantities   = request.form.getlist("quantity[]")
        prices       = request.form.getlist("sale_price[]")
        disc_types   = request.form.getlist("discount_type[]")
        disc_values  = request.form.getlist("discount_value[]")
        tax_pcts     = request.form.getlist("tax_percent[]")
        unit_ids     = request.form.getlist("unit_id[]")
        rows = []
        for i, (iid, qty, price) in enumerate(zip(item_ids, quantities, prices)):
            if iid.strip() and qty.strip() and price.strip():
                rows.append((iid.strip(), qty.strip(), price.strip(),
                    disc_types[i] if i < len(disc_types) else "percent",
                    disc_values[i] if i < len(disc_values) else "0",
                    tax_pcts[i] if i < len(tax_pcts) else "0",
                    unit_ids[i] if i < len(unit_ids) else ""))
        row_error = validate_line_rows(rows) if rows else None
        if not customer_id or not quote_date:
            flash("Customer and date are required.", "danger")
        elif not rows:
            flash("At least one item is required.", "danger")
        elif row_error:
            flash(row_error, "danger")
        else:
            q = Quotation(
                customer_id=int(customer_id),
                quote_date=datetime.strptime(quote_date, "%Y-%m-%d"),
                valid_until=datetime.strptime(valid_until, "%Y-%m-%d") if valid_until else None,
                notes=notes or None,
            )
            db.session.add(q)
            db.session.flush()
            for iid, qty, price, d_type, d_val, tax, unit_key in rows:
                item_obj = db.session.get(Item, int(iid)) or abort(404)
                unit_name, unit_factor = resolve_item_unit(item_obj, unit_key)
                db.session.add(QuotationItem(
                    quotation_id=q.id, item_id=int(iid),
                    quantity=int(qty), sale_price=float(price),
                    discount_type=d_type or "percent",
                    discount_value=float(d_val or 0),
                    tax_percent=float(tax or 0),
                    unit_name=unit_name, unit_factor=unit_factor,
                ))
            db.session.commit()
            flash(f"Quotation #{q.id} created.", "success")
            return redirect(url_for("quotations"))
    return render_template("quotations.html",
        quotes=quotes, customers=customers, items=items,
        pagination=pagination, search=search,
        quote_statuses=QUOTATION_STATUSES,
        today=now_local().strftime("%Y-%m-%d"))

@app.route("/quotations/<int:id>")
@manager_required
def quotation_detail(id):
    q = db.session.get(Quotation, id) or abort(404)
    total = quotation_total(q)
    return render_template("quotation_detail.html", q=q, total=total,
                           q_item_net=quotation_item_net, quote_statuses=QUOTATION_STATUSES)

@app.route("/quotations/<int:id>/status", methods=["POST"])
@manager_required
def update_quotation_status(id):
    q = db.session.get(Quotation, id) or abort(404)
    new_status = request.form.get("status", "").strip()
    if new_status not in QUOTATION_STATUSES or new_status == "Converted":
        flash("Invalid status.", "danger")
    elif q.status == "Converted":
        flash("Converted quotations cannot be changed.", "warning")
    else:
        q.status = new_status
        db.session.commit()
        flash(f"Quotation #{q.id} marked as {new_status}.", "success")
    return redirect(url_for("quotation_detail", id=id))

@app.route("/quotations/<int:id>/convert", methods=["POST"])
@manager_required
def convert_quotation_to_sale(id):
    q = db.session.get(Quotation, id) or abort(404)
    if q.converted_sale_id:
        flash(f"Already converted to Sale #{q.converted_sale_id}.", "warning")
        return redirect(url_for("quotation_detail", id=id))
    if q.status in ("Rejected", "Converted"):
        flash("Cannot convert a rejected or already-converted quotation.", "danger")
        return redirect(url_for("quotation_detail", id=id))
    date_str = request.form.get("sale_date", "").strip()
    try:
        sal_date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else now_local()
    except ValueError:
        sal_date = now_local()
    # stock check
    stock_errors = []
    for qi in q.line_items:
        item_obj = db.session.get(Item, qi.item_id)
        if item_obj and item_obj.stock < line_base_qty(qi):
            stock_errors.append(f"{item_obj.name}: only {item_obj.stock} in stock")
    if stock_errors:
        flash("Insufficient stock — " + "; ".join(stock_errors), "danger")
        return redirect(url_for("quotation_detail", id=id))
    first = q.line_items[0]
    sal = Sale(
        customer_id=q.customer_id,
        item_id=first.item_id, quantity=first.quantity, sale_price=first.sale_price,
        cost_price=0.0,
        discount_type="percent", discount_value=0, discount_amount=0,
        tax_percent=0, tax_amount=0,
        date=sal_date, notes=q.notes,
    )
    db.session.add(sal)
    db.session.flush()
    for qi in q.line_items:
        gross = qi.quantity * qi.sale_price
        disc_amt, tax_amt, net = calc_discount_tax(gross, qi.discount_type, qi.discount_value, qi.tax_percent)
        item_obj = db.session.get(Item, qi.item_id)
        unit_cost = item_obj.avg_cost if item_obj else Decimal("0")
        base_qty = line_base_qty(qi)
        db.session.add(SaleItem(
            sale_id=sal.id, item_id=qi.item_id,
            quantity=qi.quantity, sale_price=qi.sale_price,
            cost_price=float(unit_cost),
            discount_type=qi.discount_type, discount_value=qi.discount_value,
            discount_amount=disc_amt, tax_percent=qi.tax_percent,
            tax_amount=tax_amt, amount=net,
            unit_name=qi.unit_name, unit_factor=qi.unit_factor or 1,
        ))
        if item_obj:
            item_remove_stock(item_obj, base_qty,
                              cost_total=unit_cost * Decimal(str(base_qty)))
    db.session.flush()
    db.session.refresh(sal)
    sal.invoice_no = allocate_document_number("sale", sal.date)
    sync_customer_sale(sal)
    post_document("sale", sal)
    q.status = "Converted"
    q.converted_sale_id = sal.id
    db.session.commit()
    flash(f"Quotation #{q.id} converted to Sale {sal.invoice_no}.", "success")
    return redirect(url_for("quotation_detail", id=id))

@app.route("/quotations/<int:id>/delete", methods=["POST"])
@admin_required
def delete_quotation(id):
    q = db.session.get(Quotation, id) or abort(404)
    if q.status == "Converted":
        flash("Cannot delete a converted quotation.", "danger")
        return redirect(url_for("quotations"))
    db.session.delete(q)
    db.session.commit()
    flash(f"Quotation #{id} deleted.", "success")
    return redirect(url_for("quotations"))

# ─── Delivery Challan ──────────────────────────────────────────────────────────

@app.route("/delivery_challans", methods=["GET"])
@verified_required
def delivery_challans():
    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()
    query = DeliveryChallan.query.join(Sale).join(Customer, Sale.customer_id == Customer.id)
    if search:
        query = query.filter(Customer.name.ilike(f"%{search}%"))
    if status_filter:
        query = query.filter(DeliveryChallan.status == status_filter)
    challans, pagination = get_paginated_results(
        query.order_by(DeliveryChallan.challan_date.desc(), DeliveryChallan.id.desc())
    )
    # pending sales (no challan yet)
    pending_sales = Sale.query.filter(
        ~Sale.id.in_(db.session.query(DeliveryChallan.sale_id))
    ).order_by(Sale.date.desc()).all()
    return render_template("delivery_challans.html",
        challans=challans, pending_sales=pending_sales,
        pagination=pagination, search=search,
        status_filter=status_filter, challan_statuses=CHALLAN_STATUSES,
        today=now_local().strftime("%Y-%m-%d"))

@app.route("/delivery_challans/create", methods=["POST"])
@manager_required
def create_delivery_challan():
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

@app.route("/delivery_challans/<int:id>/update", methods=["POST"])
@manager_required
def update_challan_status(id):
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

# ─── Reports: AP/AR Aging, P&L, Cash Book, GST ────────────────────────────────

@app.route("/reports/aging")
@manager_required
def report_aging():
    today = now_local().date()

    def age_bucket(date_val):
        days = (today - date_val.date()).days
        if days <= 30:   return "0-30"
        elif days <= 60: return "31-60"
        elif days <= 90: return "61-90"
        else:            return "90+"

    # AP Aging (Suppliers) - Optimized: fetch all data at once to avoid N+1 queries
    suppliers = Supplier.query.order_by(Supplier.name).all()
    purchases = Purchase.query.filter_by(is_reversed=False).all()  # Fetch all at once

    # Group purchases by supplier in memory
    purchases_by_supplier = {}
    for pur in purchases:
        if pur.supplier_id not in purchases_by_supplier:
            purchases_by_supplier[pur.supplier_id] = []
        purchases_by_supplier[pur.supplier_id].append(pur)

    ap_rows = []
    for sup in suppliers:
        buckets = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        for pur in purchases_by_supplier.get(sup.id, []):
            due = purchase_total(pur) - get_purchase_paid(pur.id)
            if due > 0.01:
                buckets[age_bucket(pur.date)] += due
        total_due = sum(buckets.values())
        if total_due > 0.01:
            ap_rows.append({"name": sup.name, "buckets": buckets, "total": total_due})

    # AR Aging (Customers) - Optimized: fetch all data at once to avoid N+1 queries
    customers = Customer.query.order_by(Customer.name).all()
    sales = Sale.query.filter_by(is_reversed=False).all()  # Fetch all at once

    # Group sales by customer in memory
    sales_by_customer = {}
    for sal in sales:
        if sal.customer_id not in sales_by_customer:
            sales_by_customer[sal.customer_id] = []
        sales_by_customer[sal.customer_id].append(sal)

    ar_rows = []
    for cust in customers:
        buckets = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        for sal in sales_by_customer.get(cust.id, []):
            due = sale_total(sal) - get_sale_received(sal.id)
            if due > 0.01:
                buckets[age_bucket(sal.date)] += due
        total_due = sum(buckets.values())
        if total_due > 0.01:
            ar_rows.append({"name": cust.name, "buckets": buckets, "total": total_due})

    return render_template("report_aging.html",
        ap_rows=ap_rows, ar_rows=ar_rows, today=today)

@app.route("/reports/profit_loss")
@manager_required
def report_profit_loss():
    """Income and expense movement over a period, summed from the GL.

    Sales Returns is a contra-income account, so it carries a debit balance and
    its natural balance is negative — subtracting it from revenue happens for
    free when the income accounts are added up."""
    start_str = request.args.get("start", "")
    end_str   = request.args.get("end", "")
    today     = now_local()
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d") if start_str else datetime(today.year, 1, 1)
        end   = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_str else today
    except ValueError:
        start = datetime(today.year, 1, 1)
        end   = today

    b = gl_balances(as_of=end, start=start)
    income_rows  = accounts_by_type(b, "Income")
    expense_rows = accounts_by_type(b, "Expense")

    total_income   = sum((bal for _, bal in income_rows), Decimal("0"))
    total_expenses = sum((bal for _, bal in expense_rows), Decimal("0"))

    # Cost of Goods Sold is an expense account, but it belongs above the gross
    # profit line rather than among operating costs.
    cogs = sum((bal for acct, bal in expense_rows if acct.code == ACC_COGS), Decimal("0"))
    operating_rows = [(a, bal) for a, bal in expense_rows if a.code != ACC_COGS]
    total_operating = sum((bal for _, bal in operating_rows), Decimal("0"))
    gross_profit = total_income - cogs
    net_profit   = total_income - total_expenses

    return render_template("report_pl.html",
        start=start, end=end,
        income_rows=income_rows, total_income=total_income,
        cogs=cogs, gross_profit=gross_profit,
        operating_rows=operating_rows, total_operating=total_operating,
        total_expenses=total_expenses, net_profit=net_profit)

@app.route("/reports/cash_flow")
@manager_required
def report_cash_flow():
    """Where the cash actually came from and went, straight from the GL."""
    start_str = request.args.get("start", "")
    end_str   = request.args.get("end", "")
    today     = now_local()
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d") if start_str else datetime(today.year, 1, 1)
        end   = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_str else today
    except ValueError:
        start = datetime(today.year, 1, 1)
        end   = today

    cf = cash_flow_statement(start, end)
    return render_template("report_cash_flow.html", start=start, end=end, cf=cf,
                           sections=CASH_FLOW_SECTIONS)

@app.route("/reports/cash_book")
@manager_required
def report_cash_book():
    start_str = request.args.get("start", "")
    end_str   = request.args.get("end", "")
    method_filter = request.args.get("method", "")
    today = now_local()
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d") if start_str else datetime(today.year, today.month, 1)
        end   = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_str else today
    except ValueError:
        start = datetime(today.year, today.month, 1)
        end   = today

    # Every journal line that touches a cash or bank GL account — so a manual
    # journal entry that moves cash appears here too, which the old
    # payments-and-expenses version could never show.
    # Optimized: Use query filter instead of Python loop to avoid N+1
    cash_gl_ids = [fa.gl_account_id for fa in FinancialAccount.query.filter(FinancialAccount.gl_account_id.isnot(None)).all()]
    account_names = {fa.gl_account_id: fa.name for fa in FinancialAccount.query.all()}

    q = (db.session.query(JournalLine, JournalEntry)
         .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
         .filter(JournalLine.account_id.in_(cash_gl_ids or [-1]),
                 JournalEntry.entry_date >= start,
                 JournalEntry.entry_date <= end)
         .order_by(JournalEntry.entry_date, JournalEntry.id))

    entries = []
    for line, entry in q.all():
        name = account_names.get(line.account_id, "")
        if method_filter and name != method_filter:
            continue
        entries.append({
            "date": entry.entry_date,
            "type": entry.source_type.replace("_", " ").title(),
            "description": entry.description,
            "method": name,
            "in":  float(line.debit or 0),      # money into a cash account is a debit
            "out": float(line.credit or 0),
            "entry_id": entry.id,
        })

    total_in  = sum(e["in"]  for e in entries)
    total_out = sum(e["out"] for e in entries)
    net       = total_in - total_out

    # Filter offers the actual accounts, not the payment methods, now that a
    # business can have more than one bank.
    return render_template("report_cash_book.html",
        entries=entries, start=start, end=end,
        total_in=total_in, total_out=total_out, net=net,
        method_filter=method_filter,
        payment_methods=[fa.name for fa in active_accounts()])

# ─── Cash & Bank Accounts ───────────────────────────────────────────────────────
@app.route("/accounts")
@manager_required
def accounts():
    """Display financial accounts hierarchically with balances."""
    control_accounts = get_active_control_accounts()
    standalone_accounts = FinancialAccount.query.filter_by(is_control=False, parent_id=None, is_active=True).order_by(FinancialAccount.name).all()

    rows = []
    total = Decimal("0")

    # Add control accounts with their subsidiaries
    for control in control_accounts:
        control_balance = Decimal("0")
        control_row_idx = len(rows)  # Remember where we added the control account
        rows.append({
            "acct": control,
            "balance": None,  # Will be calculated from children
            "is_control": True,
            "level": 0
        })

        # Add subsidiaries and calculate control balance
        for subsidiary in control.children:
            if subsidiary.is_active:
                balance = get_account_balance(subsidiary)
                control_balance += Decimal(str(balance))
                rows.append({
                    "acct": subsidiary,
                    "balance": balance,
                    "is_control": False,
                    "level": 1
                })

        # Set control account balance to sum of children
        rows[control_row_idx]["balance"] = float(control_balance)
        total += control_balance

    # Add standalone accounts
    for standalone in standalone_accounts:
        balance = get_account_balance(standalone)
        rows.append({
            "acct": standalone,
            "balance": balance,
            "is_control": False,
            "level": 0
        })
        total += Decimal(str(balance))

    return render_template("accounts.html", rows=rows, total=float(total))

def account_name_taken(name, exclude_id=None):
    """Two accounts with the same name are indistinguishable in every dropdown,
    so names must be unique. Case- and space-insensitive."""
    q = FinancialAccount.query.filter(
        func.lower(func.trim(FinancialAccount.name)) == name.strip().lower())
    if exclude_id is not None:
        q = q.filter(FinancialAccount.id != exclude_id)
    return db.session.query(q.exists()).scalar()

@app.route("/accounts/new", methods=["GET", "POST"])
@admin_required
def new_account():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ob_str = request.form.get("opening_balance", "0").strip().replace(",", "")
        acc_type = request.form.get("account_type", "Bank").strip()
        if not name:
            flash("Account name is required!", "danger")
        elif account_name_taken(name):
            flash(f"An account named '{name}' already exists!", "danger")
        elif ob_str and not ob_str.replace("-", "", 1).replace(".", "", 1).isdigit():
            flash("Opening balance must be a valid number!", "danger")
        else:
            acct = FinancialAccount(
                name=name,
                method=new_account_method_token(),
                account_type=acc_type if acc_type in ("Cash", "Bank") else "Bank",
                opening_balance=float(ob_str or 0),
            )
            db.session.add(acct)
            db.session.flush()
            ensure_gl_account_for_financial(acct)     # its GL leaf, so payments can post
            post_account_opening(acct)                # and its opening money into the GL
            db.session.commit()
            record_audit("create", "Account", acct.id, f"Account '{acct.name}' created ({acct.account_type})")
            flash(f"Account '{acct.name}' created. Select it when recording payments, receipts or expenses.", "success")
            return redirect(url_for("accounts"))
    return render_template("new_account.html")

@app.route("/accounts/<int:id>/edit", methods=["GET", "POST"])
@admin_required
def edit_account(id):
    acct = db.session.get(FinancialAccount, id) or abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ob_str = request.form.get("opening_balance", "0").strip().replace(",", "")
        acc_type = request.form.get("account_type", "Cash").strip()
        if not name:
            flash("Account name is required!", "danger")
        elif account_name_taken(name, exclude_id=acct.id):
            flash(f"An account named '{name}' already exists!", "danger")
        elif ob_str and not ob_str.replace("-", "", 1).replace(".", "", 1).isdigit():
            flash("Opening balance must be a valid number!", "danger")
        else:
            acct.name = name
            acct.opening_balance = float(ob_str or 0)
            acct.account_type = acc_type if acc_type in ("Cash", "Bank") else "Cash"
            db.session.flush()
            post_account_opening(acct)      # the GL is what every report reads
            db.session.commit()
            record_audit("update", "Account", acct.id, f"Account '{acct.name}' opening balance set to {float(acct.opening_balance):,.2f}")
            flash("Account updated successfully!", "success")
            return redirect(url_for("accounts"))
    return render_template("edit_account.html", acct=acct)

@app.route("/accounts/<int:id>/ledger")
@manager_required
def account_ledger(id):
    acct = db.session.get(FinancialAccount, id) or abort(404)
    txns = account_transactions(acct)
    running = float(acct.opening_balance or 0)
    ledger = []
    for t in txns:
        running += t["inflow"] - t["outflow"]
        ledger.append({**t, "balance": running})
    return render_template("account_ledger.html", acct=acct, ledger=ledger,
                           opening=float(acct.opening_balance or 0),
                           closing=get_account_balance(acct))

def parse_as_of(default=None):
    """`?as_of=YYYY-MM-DD` → end of that day, so entries dated that day are included."""
    raw = request.args.get("as_of", "").strip()
    if not raw:
        return default or now_local()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        return default or now_local()

def accounting_position(as_of=None):
    """Point-in-time figures, summed from the general ledger.

    Equity is no longer a residual: it is the sum of the equity accounts plus the
    profit earned to date. That the sheet balances is therefore a *result* — and
    if it ever does not, something is genuinely wrong."""
    as_of = as_of or now_local()
    b = gl_balances(as_of=as_of)

    assets      = accounts_by_type(b, "Asset")
    liabilities = accounts_by_type(b, "Liability")
    equity_accs = accounts_by_type(b, "Equity")

    total_assets      = sum((bal for _, bal in assets), Decimal("0"))
    total_liabilities = sum((bal for _, bal in liabilities), Decimal("0"))
    equity_posted     = sum((bal for _, bal in equity_accs), Decimal("0"))
    profit            = retained_earnings_to_date(as_of)
    total_equity      = equity_posted + profit

    return dict(
        as_of=as_of, assets=assets, liabilities=liabilities, equity_accs=equity_accs,
        total_assets=total_assets, total_liabilities=total_liabilities,
        equity_posted=equity_posted, profit=profit, total_equity=total_equity,
        difference=total_assets - (total_liabilities + total_equity),
    )

@app.route("/reports/stock")
@manager_required
def report_stock():
    """What is in the warehouse right now, and what it cost.

    A snapshot, not a period — so unlike the sales and purchase reports it takes no
    date range. It is an accounting report, not just a stock list: the total is valued
    at weighted-average cost, which is the amount the Inventory account was debited
    when the goods came in, so it is the Inventory line on the balance sheet and can
    be read straight across."""
    items = (Item.query.outerjoin(Category, Item.category_id == Category.id)
             .order_by(Category.name, Item.name).all())
    return render_template(
        "report_stock.html",
        stock_report=items,
        stock_value_total=sum(Decimal(str(i.inventory_value or 0)) for i in items),
        items_in_stock=sum(1 for i in items if i.stock and i.stock > 0),
        reorder_report=[i for i in items if i.stock <= i.reorder_level],
        as_of=now_local().strftime("%d %B %Y"),
    )

@app.route("/reports/balance_sheet")
@manager_required
def report_balance_sheet():
    return render_template("report_balance_sheet.html", p=accounting_position(parse_as_of()))

@app.route("/reports/trial_balance")
@manager_required
def report_trial_balance():
    """Every account's balance on its natural side. Because both sides are read
    from the same journal lines, the totals agreeing is a real check that no
    entry was written half-way — not an accounting identity we imposed."""
    as_of = parse_as_of()
    balances = gl_balances(as_of=as_of)
    rows, total_dr, total_cr = [], Decimal("0"), Decimal("0")
    for acct in Account.query.filter_by(is_group=False).order_by(Account.code).all():
        raw = balances.get(acct.id, Decimal("0"))
        if not raw:
            continue
        debit  = raw if raw > 0 else Decimal("0")
        credit = -raw if raw < 0 else Decimal("0")
        rows.append((acct, debit, credit))
        total_dr += debit
        total_cr += credit
    return render_template("report_trial_balance.html", rows=rows,
                           total_dr=total_dr, total_cr=total_cr, as_of=as_of)

# ─── Manual Journal Entries ─────────────────────────────────────────────────────
@app.route("/journal")
@manager_required
def journal():
    entries = JournalEntry.query.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).all()
    # Net movement per account across every entry (read-only summary). Grouped in
    # SQL rather than in Python so a large ledger stays one query.
    summary = (db.session.query(
                    Account.code, Account.name,
                    func.sum(JournalLine.debit).label("debit"),
                    func.sum(JournalLine.credit).label("credit"))
               .join(Account, JournalLine.account_id == Account.id)
               .group_by(Account.code, Account.name)
               .order_by(Account.code).all())
    return render_template("journal.html", entries=entries, summary=summary)

@app.route("/journal/new", methods=["GET", "POST"])
@manager_required
def journal_new():
    accounts = postable_accounts()
    if request.method == "POST":
        date_str    = request.form.get("entry_date", "").strip()
        description = request.form.get("description", "").strip()
        reference   = request.form.get("reference", "").strip()
        account_ids = request.form.getlist("account_id[]")
        debits      = request.form.getlist("debit[]")
        credits     = request.form.getlist("credit[]")

        lines = [{"account_id": int(a), "debit": d.replace(",", ""), "credit": c.replace(",", "")}
                 for a, d, c in zip(account_ids, debits, credits) if a.strip().isdigit()]
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            flash("A valid date is required.", "danger")
            return render_template("journal_new.html", accounts=accounts)

        try:
            entry = post_entry(entry_date=entry_date, description=description,
                               reference=reference, lines=lines,
                               created_by_id=current_user.id)
            db.session.commit()
        except PostingError as e:
            db.session.rollback()
            flash(str(e), "danger")
            return render_template("journal_new.html", accounts=accounts)

        record_audit("create", "JournalEntry", entry.id,
                     f"Journal #{entry.id}: {entry.description} ({float(entry.total_debit):,.2f})")
        flash(f"Journal entry #{entry.id} posted.", "success")
        return redirect(url_for("journal"))
    return render_template("journal_new.html", accounts=accounts)

@app.route("/journal/<int:id>")
@manager_required
def journal_view(id):
    entry = db.session.get(JournalEntry, id) or abort(404)
    reversal = JournalEntry.query.filter_by(reversal_of_id=entry.id).first()
    return render_template("journal_view.html", entry=entry, reversal=reversal,
                           total_dr=float(entry.total_debit),
                           total_cr=float(entry.total_credit))

@app.route("/journal/<int:id>/reverse", methods=["POST"])
@admin_required
def journal_reverse(id):
    """Posted entries are immutable — correcting one means posting its mirror."""
    entry = db.session.get(JournalEntry, id) or abort(404)
    try:
        reversal = reverse_entry(entry, created_by_id=current_user.id)
        # The GL is not the whole story for a fixed asset: its register has to be put
        # back too, or the two drift apart.
        unwind_asset_entry(entry)
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(str(e), "danger")
        return redirect(url_for("journal_view", id=id))

    record_audit("reverse", "JournalEntry", entry.id,
                 f"Journal #{entry.id} reversed by #{reversal.id}")
    flash(f"Entry #{entry.id} reversed by #{reversal.id}. Both remain in the ledger.", "success")
    return redirect(url_for("journal_view", id=reversal.id))

# ─── Document reversal ─────────────────────────────────────────────────────────
DOCUMENT_MODELS = {
    "purchase":         (lambda: Purchase,        "purchase"),
    "sale":             (lambda: Sale,            "sale"),
    "payment":          (lambda: SupplierPayment, "supplier_payment"),
    "receipt":          (lambda: CustomerPayment, "customer_receipt"),
    "expense":          (lambda: Expense,         "expenses"),
    "purchase_return":  (lambda: PurchaseReturn,  "purchase_return"),
    "sale_return":      (lambda: SaleReturn,      "sale_return"),
    "stock_adjustment": (lambda: StockAdjustment, "stock_adjustment"),
}

@app.route("/document/<kind>/<int:id>/reverse", methods=["POST"])
@admin_required
def reverse_document_route(kind, id):
    """One route for every document type — the shape of a reversal is the same
    whatever the document, and the differences live in reverse_document()."""
    if kind not in DOCUMENT_MODELS:
        abort(404)
    model_fn, list_endpoint = DOCUMENT_MODELS[kind]
    doc = db.session.get(model_fn(), id) or abort(404)

    reversal = reverse_document(kind, doc)
    db.session.commit()

    label = DOCUMENT_LABELS[kind]
    record_audit("reverse", label.replace(" ", ""), doc.id,
                 f"{label} #{doc.id} reversed by journal entry #{reversal.id}")
    flash(f"{label} #{doc.id} reversed. Journal entry #{reversal.id} cancels it; "
          f"stock and ledgers have been corrected.", "success")
    return redirect(request.referrer or url_for(list_endpoint))

# ─── Fixed assets ──────────────────────────────────────────────────────────────
@app.route("/fixed_assets")
@manager_required
def fixed_assets():
    assets = FixedAsset.query.order_by(FixedAsset.acquisition_date.desc(),
                                       FixedAsset.id.desc()).all()
    live = [a for a in assets if a.status != "Disposed"]
    totals = {
        "cost":  sum((Decimal(str(a.cost)) for a in live), Decimal("0")),
        "accum": sum((a.accumulated for a in live), Decimal("0")),
    }
    totals["nbv"] = totals["cost"] - totals["accum"]
    last_run = (JournalEntry.query.filter_by(source_type="depreciation")
                .order_by(JournalEntry.entry_date.desc()).first())
    return render_template("fixed_assets.html", assets=assets, totals=totals,
                           last_run=last_run, today=now_local())

@app.route("/fixed_assets/new", methods=["GET", "POST"])
@manager_required
def fixed_asset_new():
    accounts = postable_accounts()
    if request.method == "POST":
        name    = request.form.get("name", "").strip()
        tag     = request.form.get("tag", "").strip()
        method  = request.form.get("method", "Straight Line").strip()
        notes   = request.form.get("notes", "").strip()
        credit_raw = request.form.get("credit_account_id", "").strip()

        def _num(field, default="0"):
            return Decimal((request.form.get(field, default) or default).strip().replace(",", "") or default)

        try:
            acq_date = datetime.strptime(request.form.get("acquisition_date", "").strip(), "%Y-%m-%d")
            cost     = _num("cost")
            salvage  = _num("salvage_value")
            life     = int(request.form.get("useful_life_months", "0") or 0)
            rate     = _num("rate_percent")
        except (ValueError, ArithmeticError):
            flash("Check the date and the amounts — they must be valid numbers.", "danger")
            return render_template("fixed_asset_new.html", accounts=accounts,
                                   methods=DEPRECIATION_METHODS, form_data=request.form)

        error = None
        if not name:
            error = "The asset needs a name."
        elif cost <= 0:
            error = "Cost must be greater than zero."
        elif salvage < 0 or salvage >= cost:
            error = "Salvage value must be zero or more, and less than the cost."
        elif method == "Straight Line" and life <= 0:
            error = "Straight Line needs a useful life in months."
        elif method == "Reducing Balance" and rate <= 0:
            error = "Reducing Balance needs a yearly rate."
        elif not credit_raw.isdigit():
            error = "Choose the account that paid for the asset."
        if error:
            flash(error, "danger")
            return render_template("fixed_asset_new.html", accounts=accounts,
                                   methods=DEPRECIATION_METHODS, form_data=request.form)

        asset = FixedAsset(
            name=name, tag=tag or None, acquisition_date=acq_date, cost=cost,
            salvage_value=salvage, method=method,
            useful_life_months=life if method == "Straight Line" else None,
            rate_percent=rate if method == "Reducing Balance" else None,
            notes=notes or None)
        db.session.add(asset)
        db.session.flush()
        post_asset_acquisition(asset, int(credit_raw), created_by_id=current_user.id)
        db.session.commit()

        record_audit("create", "FixedAsset", asset.id,
                     f"Fixed asset '{asset.name}' acquired for {float(asset.cost):,.2f}")
        flash(f"Fixed asset '{asset.name}' recorded and posted.", "success")
        return redirect(url_for("fixed_assets"))

    return render_template("fixed_asset_new.html", accounts=accounts,
                           methods=DEPRECIATION_METHODS, form_data={})

@app.route("/fixed_assets/<int:id>")
@manager_required
def fixed_asset_view(id):
    asset = db.session.get(FixedAsset, id) or abort(404)
    charges = sorted(asset.charges, key=lambda c: c.period_end)
    fin_accounts = (FinancialAccount.query.filter_by(is_active=True)
                    .order_by(FinancialAccount.name).all())
    return render_template("fixed_asset_view.html", asset=asset, charges=charges,
                           fin_accounts=fin_accounts, today=now_local())

@app.route("/fixed_assets/depreciation", methods=["POST"])
@manager_required
def fixed_assets_depreciation():
    """Post one month's depreciation across every eligible asset."""
    try:
        when = datetime.strptime(request.form.get("month", "").strip(), "%Y-%m")
    except ValueError:
        flash("Pick the month to depreciate.", "danger")
        return redirect(url_for("fixed_assets"))

    period_end = month_end(when)
    entry, total, count = run_depreciation(period_end, created_by_id=current_user.id)
    db.session.commit()

    record_audit("create", "Depreciation", entry.id,
                 f"Depreciation for {period_end:%B %Y}: {float(total):,.2f} over {count} asset(s)")
    flash(f"Depreciation for {period_end:%B %Y} posted — {float(total):,.2f} "
          f"across {count} asset(s).", "success")
    return redirect(url_for("fixed_assets"))

@app.route("/fixed_assets/<int:id>/dispose", methods=["POST"])
@admin_required
def fixed_asset_dispose(id):
    asset = db.session.get(FixedAsset, id) or abort(404)
    fa_raw = request.form.get("financial_account_id", "").strip()
    try:
        disposal_date = datetime.strptime(request.form.get("disposal_date", "").strip(), "%Y-%m-%d")
        proceeds = Decimal((request.form.get("proceeds", "0") or "0").strip().replace(",", "") or "0")
    except (ValueError, ArithmeticError):
        flash("Check the disposal date and the proceeds.", "danger")
        return redirect(url_for("fixed_asset_view", id=id))
    if proceeds < 0:
        flash("Proceeds cannot be negative.", "danger")
        return redirect(url_for("fixed_asset_view", id=id))

    fin = db.session.get(FinancialAccount, int(fa_raw)) if fa_raw.isdigit() else None
    if proceeds > 0 and fin is None:
        flash("Choose the account the money was received into.", "danger")
        return redirect(url_for("fixed_asset_view", id=id))

    entry, gain = post_asset_disposal(asset, disposal_date, proceeds,
                                      _cash_gl(fin) if fin else None,
                                      created_by_id=current_user.id)
    db.session.commit()

    outcome = (f"gain of {float(gain):,.2f}" if gain > 0
               else f"loss of {float(-gain):,.2f}" if gain < 0 else "no gain or loss")
    record_audit("update", "FixedAsset", asset.id,
                 f"Fixed asset '{asset.name}' disposed — {outcome}")
    flash(f"'{asset.name}' disposed — {outcome}. Journal entry #{entry.id} posted.", "success")
    return redirect(url_for("fixed_assets"))

# ─── Fiscal years and period close ─────────────────────────────────────────────
@app.route("/periods")
@manager_required
def periods():
    years = FiscalYear.query.order_by(FiscalYear.start_date.desc()).all()
    return render_template("periods.html", years=years, today=now_local().date())

@app.route("/periods/<int:id>/toggle", methods=["POST"])
@admin_required
def toggle_period(id):
    """Closing a month stops backdated postings into it. Unlike a year close it
    posts nothing, so it is safe to reopen."""
    period = db.session.get(AccountingPeriod, id) or abort(404)
    if period.fiscal_year.is_closed:
        raise PostingError(f"Fiscal year {period.fiscal_year.name} is closed; "
                           f"its periods cannot be reopened individually.")
    period.is_closed = not period.is_closed
    db.session.commit()
    state = "closed" if period.is_closed else "reopened"
    record_audit("update", "AccountingPeriod", period.id, f"Period {period.name} {state}")
    flash(f"{period.name} {state}.", "success")
    return redirect(url_for("periods"))

@app.route("/fiscal_years/<int:id>/close", methods=["POST"])
@admin_required
def close_year(id):
    fy = db.session.get(FiscalYear, id) or abort(404)
    profit = close_fiscal_year(fy, created_by_id=current_user.id)
    db.session.commit()
    record_audit("close", "FiscalYear", fy.id,
                 f"Fiscal year {fy.name} closed, {float(profit):,.2f} moved to Retained Earnings")
    flash(f"Fiscal year {fy.name} closed. {float(profit):,.2f} "
          f"{'profit' if profit >= 0 else 'loss'} moved to Retained Earnings. "
          f"This cannot be undone.", "success")
    return redirect(url_for("periods"))

@app.route("/fiscal_years/new", methods=["POST"])
@admin_required
def new_fiscal_year():
    try:
        year = int(request.form.get("year", "").strip())
    except ValueError:
        flash("Enter a valid year.", "danger")
        return redirect(url_for("periods"))
    if not 1900 <= year <= 2200:
        flash("Enter a valid year.", "danger")
        return redirect(url_for("periods"))
    created = seed_fiscal_year(year)
    if created:
        record_audit("create", "FiscalYear", 0, f"Fiscal year {year} created")
        flash(f"Fiscal year {year} created with 12 periods.", "success")
    else:
        flash(f"Fiscal year {year} already exists.", "warning")
    return redirect(url_for("periods"))

# ─── Subledger ↔ control account reconciliation ────────────────────────────────
@app.route("/reports/reconciliation")
@manager_required
def report_reconciliation():
    """Prove that each control account in the GL equals the subledger that owns it.

    These are two independent records of the same money: the GL adds up journal
    lines, the subledgers add up documents. They are kept in step by the posting
    layer, never copied from one another — so if they agree, both are almost
    certainly right, and if they differ, the difference is a real bug, not a
    rounding artefact. This page is the check that makes the other reports
    trustworthy."""
    balances = gl_balances()

    def gl_of(code):
        acct = get_account(code)
        return acct, natural_balance(acct, balances.get(acct.id, Decimal("0")))

    ar_acct, gl_ar   = gl_of(ACC_AR)
    ap_acct, gl_ap   = gl_of(ACC_AP)
    inv_acct, gl_inv = gl_of(ACC_INVENTORY)

    # Subledger side. Customer/supplier balances are net of advances, which is
    # exactly what the control account holds.
    sub_ar = sum(Decimal(str(get_customer_balance(c.id))) for c in Customer.query.all())
    sub_ap = sum(Decimal(str(get_supplier_balance(s.id))) for s in Supplier.query.all())
    # Weighted-average cost of the stock on hand — the same amounts the Inventory
    # control account was posted, arrived at independently.
    sub_inv = Decimal(str(db.session.query(func.sum(Item.inventory_value)).scalar() or 0))

    rows = [
        {"acct": ar_acct,  "gl": gl_ar,  "sub": sub_ar,
         "sub_label": "Sum of customer ledger balances"},
        {"acct": ap_acct,  "gl": gl_ap,  "sub": sub_ap,
         "sub_label": "Sum of supplier ledger balances"},
        {"acct": inv_acct, "gl": gl_inv, "sub": sub_inv,
         "sub_label": "Stock on hand × cost"},
    ]
    for r in rows:
        r["diff"] = Decimal(str(r["gl"])) - Decimal(str(r["sub"]))
        r["ok"] = abs(r["diff"]) < Decimal("0.01")

    # Per-party detail, so a mismatch can be traced rather than merely reported.
    customers = [(c, get_customer_balance(c.id)) for c in Customer.query.order_by(Customer.name).all()]
    suppliers = [(s, get_supplier_balance(s.id)) for s in Supplier.query.order_by(Supplier.name).all()]

    return render_template("report_reconciliation.html", rows=rows, as_of=now_local(),
                           customers=[c for c in customers if abs(c[1]) > 0.001],
                           suppliers=[s for s in suppliers if abs(s[1]) > 0.001],
                           all_ok=all(r["ok"] for r in rows))

# ─── Chart of Accounts & Tax Codes ─────────────────────────────────────────────
@app.route("/chart_of_accounts")
@manager_required
def chart_of_accounts():
    as_of = parse_as_of()
    accounts = Account.query.order_by(Account.code).all()
    balances = gl_balances(as_of=as_of)

    # Depth from the parent chain, walked over the rows already fetched rather
    # than by following .parent, which would be one query per account.
    parent_of = {a.id: a.parent_id for a in accounts}
    def depth(a):
        d, pid = 1, a.parent_id
        while pid is not None:
            d, pid = d + 1, parent_of.get(pid)
        return d

    # A heading holds no balance of its own; what it shows is the sum of the
    # accounts beneath it. Roll each leaf's raw (debit-minus-credit) figure up
    # its ancestors, then convert once at the end — signs only make sense on a
    # single account's own natural side.
    rolled = {a.id: balances.get(a.id, Decimal("0")) for a in accounts}
    for a in accounts:
        if a.is_group:
            continue
        pid = a.parent_id
        while pid is not None:
            rolled[pid] = rolled.get(pid, Decimal("0")) + balances.get(a.id, Decimal("0"))
            pid = parent_of.get(pid)

    rows = [(a, depth(a), natural_balance(a, rolled.get(a.id, Decimal("0")))) for a in accounts]
    return render_template("chart_of_accounts.html", rows=rows, as_of=as_of,
                           system_codes=SYSTEM_ACCOUNT_CODES)

def _validate_account_form(code, name, type_, parent, is_group, exclude_id=None):
    """Shared by create and edit. Returns an error string, or None."""
    if not code or not code.isalnum():
        return "Account code is required and must be letters or digits only."
    if len(code) > 20:
        return "Account code is too long (20 characters maximum)."
    clash = Account.query.filter(Account.code == code)
    if exclude_id:
        clash = clash.filter(Account.id != exclude_id)
    if db.session.query(clash.exists()).scalar():
        return f"Account code {code} is already in use."
    if not name:
        return "Account name is required."
    if type_ not in ACCOUNT_TYPES:
        return "Choose a valid account type."
    if parent is not None:
        if not parent.is_group:
            return f"{parent.code} {parent.name} is not a heading, so it cannot hold sub-accounts."
        if parent.type != type_:
            return (f"A sub-account of {parent.code} {parent.name} must be of type "
                    f"{parent.type}, not {type_}.")
        # A heading may sit at level 1, 2 or 3. Refusing a fourth keeps the chart
        # readable; postable accounts may still hang off a level-3 heading.
        if is_group and parent.parent_id and parent.parent.parent_id:
            return ("Headings can be nested three levels deep at most. "
                    "You can still add postable accounts under this heading.")
    return None

@app.route("/chart_of_accounts/new", methods=["GET", "POST"])
@admin_required
def new_gl_account():
    """Create a main account (a heading) or a subsidiary one (a postable leaf).

    Control accounts cannot be created here: they only mean something if a
    subledger maintains them, and nothing in the app would maintain a new one."""
    groups = Account.query.filter_by(is_group=True, is_active=True).order_by(Account.code).all()
    if request.method == "POST":
        code      = request.form.get("code", "").strip()
        name      = request.form.get("name", "").strip()
        type_     = request.form.get("type", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        is_group  = request.form.get("is_group") == "1"
        parent    = db.session.get(Account, int(parent_id)) if parent_id.isdigit() else None

        if parent is not None and not type_:
            type_ = parent.type            # a sub-account inherits its parent's type

        error = _validate_account_form(code, name, type_, parent, is_group)
        if error:
            flash(error, "danger")
            return render_template("gl_account_new.html", groups=groups,
                                   types=ACCOUNT_TYPES, form_data=request.form)

        acct = Account(code=code, name=name, type=type_,
                       parent_id=parent.id if parent else None,
                       is_group=is_group, is_control=False,
                       cash_flow_section=parse_cf_section(request.form.get("cash_flow_section")))
        db.session.add(acct)
        db.session.commit()
        record_audit("create", "Account", acct.id,
                     f"Account {acct.code} {acct.name} created ({'heading' if is_group else acct.type})")
        flash(f"Account {acct.code} — {acct.name} created.", "success")
        return redirect(url_for("chart_of_accounts"))
    return render_template("gl_account_new.html", groups=groups, types=ACCOUNT_TYPES,
                           form_data={})

@app.route("/chart_of_accounts/<int:id>/edit", methods=["GET", "POST"])
@admin_required
def edit_gl_account(id):
    acct = db.session.get(Account, id) or abort(404)
    groups = (Account.query.filter(Account.is_group.is_(True), Account.is_active.is_(True),
                                   Account.id != acct.id)
              .order_by(Account.code).all())
    is_system = account_is_system(acct)

    if request.method == "POST":
        name      = request.form.get("name", "").strip()
        code      = acct.code if is_system else request.form.get("code", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        is_active = request.form.get("is_active") == "1"
        parent    = db.session.get(Account, int(parent_id)) if parent_id.isdigit() else None

        error = _validate_account_form(code, name, acct.type, parent, acct.is_group,
                                       exclude_id=acct.id)
        if error is None and parent is not None and parent.id == acct.id:
            error = "An account cannot be its own parent."
        if error is None and not is_active:
            if account_has_activity(acct):
                error = "This account has journal entries; it cannot be deactivated."
            elif acct.is_group and any(c.is_active for c in acct.children):
                error = "This heading still has active sub-accounts."
        if error:
            flash(error, "danger")
            return redirect(url_for("edit_gl_account", id=id))

        acct.code, acct.name = code, name
        acct.parent_id = parent.id if parent else None
        acct.is_active = is_active
        acct.cash_flow_section = parse_cf_section(request.form.get("cash_flow_section"))
        db.session.commit()
        record_audit("update", "Account", acct.id, f"Account {acct.code} {acct.name} edited")
        flash(f"Account {acct.code} — {acct.name} updated.", "success")
        return redirect(url_for("chart_of_accounts"))

    return render_template("gl_account_edit.html", acct=acct, groups=groups, is_system=is_system)

@app.route("/chart_of_accounts/<int:id>/delete", methods=["POST"])
@admin_required
def delete_gl_account(id):
    """Only an unused account can go. Once a journal line points at it, deleting
    it would orphan a posting that really happened — deactivate it instead."""
    acct = db.session.get(Account, id) or abort(404)
    if account_is_system(acct):
        flash(f"{acct.code} {acct.name} is used by the posting layer and cannot be deleted.", "danger")
    elif account_has_activity(acct):
        flash(f"{acct.code} {acct.name} has journal entries. Deactivate it instead.", "danger")
    elif acct.children:
        flash(f"{acct.code} {acct.name} still has sub-accounts.", "danger")
    elif FinancialAccount.query.filter_by(gl_account_id=acct.id).first():
        flash(f"{acct.code} {acct.name} belongs to a cash/bank account.", "danger")
    elif ExpenseCategory.query.filter_by(gl_account_id=acct.id).first():
        flash(f"{acct.code} {acct.name} is used by an expense category.", "danger")
    elif TaxComponent.query.filter((TaxComponent.input_account_id == acct.id) |
                                   (TaxComponent.output_account_id == acct.id)).first():
        flash(f"{acct.code} {acct.name} is used by a tax code.", "danger")
    else:
        code, name = acct.code, acct.name
        db.session.delete(acct)
        db.session.commit()
        record_audit("delete", "Account", id, f"Account {code} {name} deleted")
        flash(f"Account {code} — {name} deleted.", "success")
    return redirect(url_for("chart_of_accounts"))

@app.route("/tax_codes", methods=["GET", "POST"])
@admin_required
def tax_codes():
    """Rates live on components, so one code can be a single tax (Pakistan, UK)
    or a split one (India's CGST + SGST) without the rest of the app knowing."""
    if request.method == "POST":
        try:
            for comp in TaxComponent.query.all():
                raw = request.form.get(f"rate_{comp.id}")
                if raw is None:
                    continue
                rate = Decimal(str(raw).strip() or 0)
                if rate < 0 or rate > 100:
                    raise PostingError(f"Rate for {comp.name} must be between 0 and 100.")
                comp.rate = rate
            db.session.commit()
        except (InvalidOperation, PostingError) as e:
            db.session.rollback()
            flash(str(e) if isinstance(e, PostingError) else "Rates must be valid numbers.", "danger")
            return redirect(url_for("tax_codes"))
        record_audit("update", "TaxCode", 0, "Tax rates updated")
        flash("Tax rates updated.", "success")
        return redirect(url_for("tax_codes"))
    return render_template("tax_codes.html", codes=TaxCode.query.order_by(TaxCode.name).all())

@app.route("/reports/gst")
@manager_required
def report_gst():
    start_str = request.args.get("start", "")
    end_str   = request.args.get("end", "")
    today = now_local()
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d") if start_str else datetime(today.year, today.month, 1)
        end   = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_str else today
    except ValueError:
        start = datetime(today.year, today.month, 1)
        end   = today

    # Input tax (purchases)
    input_rows = []
    input_total = 0.0
    for pur in Purchase.query.filter(Purchase.date >= start, Purchase.date <= end).order_by(Purchase.date).all():
        tax = sum(float(pi.tax_amount or 0) for pi in pur.line_items)
        if tax > 0:
            input_rows.append({"id": pur.id, "date": pur.date,
                "party": pur.id_supplier.name, "tax": tax})
            input_total += tax

    # Output tax (sales)
    output_rows = []
    output_total = 0.0
    for sal in Sale.query.filter(Sale.date >= start, Sale.date <= end).order_by(Sale.date).all():
        tax = sum(float(si.tax_amount or 0) for si in sal.line_items)
        if tax > 0:
            output_rows.append({"id": sal.id, "date": sal.date,
                "party": sal.id_customer.name, "tax": tax})
            output_total += tax

    net_gst = output_total - input_total  # positive = payable to govt

    return render_template("report_gst.html",
        start=start, end=end,
        input_rows=input_rows, input_total=input_total,
        output_rows=output_rows, output_total=output_total,
        net_gst=net_gst)

# ─── Low Stock Alert ───────────────────────────────────────────────────────────

@app.route("/low_stock_alert", methods=["POST"])
@manager_required
def send_low_stock_alert():
    low_items = Item.query.filter(Item.stock <= Item.reorder_level).order_by(Item.stock).all()
    if not low_items:
        flash("No items are below reorder level — no alert sent.", "info")
        return redirect(url_for("item"))
    lines = [f"LOW STOCK ALERT — {app.config['COMPANY_NAME']}\n"]
    lines.append(f"Generated: {now_local().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"{'Item':<30} {'Stock':>8} {'Reorder':>8}")
    lines.append("-" * 50)
    for it in low_items:
        lines.append(f"{it.name:<30} {it.stock:>8} {it.reorder_level:>8}")
    body = "\n".join(lines)
    mail_user = app.config.get("MAIL_USERNAME", "").strip()
    if not mail_user:
        flash("Email not configured — cannot send alert.", "danger")
        return redirect(url_for("item"))
    ok = send_email(mail_user, f"Low Stock Alert — {len(low_items)} items", body)
    if ok:
        flash(f"Low stock alert sent for {len(low_items)} item(s).", "success")
    return redirect(url_for("item"))

@app.cli.command("seed-accounting")
def seed_accounting_cmd():
    """Seed the chart of accounts, tax codes and the current fiscal year. Safe to re-run."""
    n = seed_chart_of_accounts()
    click.echo(f"Chart of accounts: {n} account(s) created, {Account.query.count()} total.")
    t = seed_tax_codes()
    click.echo(f"Tax codes: {t} total.")
    p = seed_fiscal_year(now_local())
    click.echo(f"Fiscal year {now_local().year}: {p} period(s) created.")

@app.cli.command("reset-db")
@click.option("--yes", is_flag=True, help="Required. Drops every table.")
@click.option("--i-understand-this-wipes-the-deployed-database", "wipe_remote", is_flag=True,
              help="Also allow this when DATABASE_URL is set (Render). Irreversible.")
def reset_db_cmd(yes, wipe_remote):
    """Drop and recreate every table, then seed accounting. Destroys all data."""
    if not yes:
        click.echo("Refusing without --yes. This drops every table.")
        return
    if DATABASE_URL and not wipe_remote:
        # DATABASE_URL is only set on real deployments (Render). Wiping one has to
        # be a sentence someone typed on purpose, not a flag they reached for.
        click.echo("Refusing: DATABASE_URL is set, so this is a deployed database.")
        click.echo("If you really mean it, add:")
        click.echo("    --i-understand-this-wipes-the-deployed-database")
        return
    if DATABASE_URL:
        click.echo(f"About to wipe the DEPLOYED database at "
                   f"{urlsplit(DATABASE_URL).hostname or 'unknown host'}.")
        if not click.confirm("There is no undo. Continue?"):
            click.echo("Aborted.")
            return

    db.drop_all()
    db.create_all()
    click.echo("All tables dropped and recreated.")
    seed_chart_of_accounts()
    seed_tax_codes()
    seed_fiscal_year(now_local())
    # drop_all took the seeded cash/bank accounts with it.
    types = {"Cash": "Cash", "Bank": "Bank", "Cheque": "Bank", "Online": "Bank"}
    for m in PAYMENT_METHODS:
        db.session.add(FinancialAccount(name=m, method=m, account_type=types[m], opening_balance=0))
    db.session.commit()
    seed_financial_account_links()
    click.echo(f"Seeded {Account.query.count()} accounts, {TaxCode.query.count()} tax codes, "
               f"{AccountingPeriod.query.count()} periods.")
    click.echo("Now run: flask create-user, then: flask seed-data --yes")

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
    user = User(name=name, email=email, password=pwd_context.hash(password), verified=True, role="admin")
    db.session.add(user)
    db.session.commit()
    click.echo(f"User created: {email} (verified, role=admin)")

# ─── Admin: User Management ────────────────────────────────────────────────

@app.route("/admin/users")
@admin_required
def admin_users():
    users = User.query.order_by(User.name).all()
    return render_template("admin_users.html", users=users)

@app.route("/admin/users/create", methods=["GET", "POST"])
@admin_required
def admin_create_user():
    if request.method == "POST":
        name             = request.form.get("name", "").strip()
        email            = request.form.get("email", "").strip().lower()
        password         = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        role             = request.form.get("role", "staff").strip()
        verified         = request.form.get("verified") == "1"

        if not name or not email or not password:
            flash("Name, email, and password are required.", "danger")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
        elif password != confirm_password:
            flash("Passwords do not match. Please retype carefully.", "danger")
        elif role not in ROLES:
            flash("Invalid role selected.", "danger")
        elif User.query.filter_by(email=email).first():
            flash(f"Email {email} is already registered.", "warning")
        else:
            user = User(name=name, email=email,
                        password=pwd_context.hash(password),
                        role=role, verified=verified)
            db.session.add(user)
            db.session.commit()
            flash(f"User '{name}' created successfully.", "success")
            return redirect(url_for("admin_users"))

    return render_template("admin_create_user.html")

@app.route("/admin/users/edit/<int:id>", methods=["GET", "POST"])
@admin_required
def admin_edit_user(id):
    user = db.session.get(User, id) or abort(404)
    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip().lower()
        role     = request.form.get("role", "staff").strip()
        verified = request.form.get("verified") == "1"
        new_pw   = request.form.get("password", "").strip()

        if not name or not email:
            flash("Name and email are required.", "danger")
        elif role not in ROLES:
            flash("Invalid role selected.", "danger")
        elif email != user.email and User.query.filter_by(email=email).first():
            flash(f"Email {email} is already in use.", "warning")
        else:
            user.name     = name
            user.email    = email
            user.role     = role
            user.verified = verified
            if new_pw:
                if len(new_pw) < 6:
                    flash("Password must be at least 6 characters.", "danger")
                    return render_template("admin_edit_user.html", user=user)
                user.password = pwd_context.hash(new_pw)
            db.session.commit()
            flash(f"User '{name}' updated successfully.", "success")
            return redirect(url_for("admin_users"))

    return render_template("admin_edit_user.html", user=user)

@app.route("/admin/users/change-role/<int:id>", methods=["POST"])
@admin_required
def admin_change_role(id):
    user = db.session.get(User, id) or abort(404)
    if user.id == current_user.id:
        flash("You cannot change your own role.", "danger")
        return redirect(url_for("admin_users"))
    role = request.form.get("role", "").strip()
    if role not in ROLES:
        flash("Invalid role.", "danger")
        return redirect(url_for("admin_users"))
    old_role = user.role
    user.role = role
    db.session.commit()
    record_audit("update", "User", user.id, f"Role of '{user.name}' changed from '{old_role}' to '{role}'")
    flash(f"{user.name}'s role changed from '{old_role}' to '{role}'.", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/delete/<int:id>", methods=["POST"])
@admin_required
def admin_delete_user(id):
    user = db.session.get(User, id) or abort(404)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("admin_users"))
    user_name = user.name
    db.session.delete(user)
    db.session.commit()
    record_audit("delete", "User", id, f"User '{user_name}' deleted")
    flash(f"User '{user_name}' deleted.", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/toggle-verify/<int:id>", methods=["POST"])
@admin_required
def admin_toggle_verify(id):
    user = db.session.get(User, id) or abort(404)
    user.verified = not user.verified
    db.session.commit()
    state = "verified" if user.verified else "unverified"
    record_audit("update", "User", user.id, f"User '{user.name}' set to {state}")
    flash(f"User '{user.name}' is now {state}.", "success")
    return redirect(url_for("admin_users"))

# ─── Financial Account Hierarchy Management ────────────────────────────────────
@app.route("/admin/financial-accounts")
@admin_required
def admin_financial_accounts():
    """List all financial accounts in hierarchical view."""
    control_accounts = get_active_control_accounts()
    standalone_accounts = FinancialAccount.query.filter_by(is_control=False, parent_id=None, is_active=True).order_by(FinancialAccount.name).all()
    inactive_accounts = FinancialAccount.query.filter_by(is_active=False).order_by(FinancialAccount.name).all()
    return render_template("admin_financial_accounts.html",
                         control_accounts=control_accounts,
                         standalone_accounts=standalone_accounts,
                         inactive_accounts=inactive_accounts)

@app.route("/admin/financial-accounts/create", methods=["GET", "POST"])
@admin_required
def admin_create_financial_account():
    """Create a new financial account (control or subsidiary)."""
    if request.method == "POST":
        account_type = request.form.get("account_type", "").strip()  # "control" or "subsidiary" or "standalone"
        name = request.form.get("name", "").strip()
        opening_balance_str = request.form.get("opening_balance", "0").strip()
        parent_raw = request.form.get("parent_id", "").strip()
        account_subtype = request.form.get("account_subtype", "Cash").strip()
        is_control = (account_type == "control")
        needs_parent = (account_type == "subsidiary")

        parent = None
        if not name:
            flash("Account name is required!", "danger")
        elif account_name_taken(name):
            flash(f"An account named '{name}' already exists!", "danger")
        elif needs_parent and not parent_raw.isdigit():
            flash("Please select a valid control account for this subsidiary!", "danger")
        elif needs_parent and not (parent := db.session.get(FinancialAccount, int(parent_raw))):
            flash("Selected control account was not found!", "danger")
        elif needs_parent and not parent.is_control:
            flash("A subsidiary account must belong to a control account!", "danger")
        else:
            try:
                opening_balance = Decimal(opening_balance_str) if opening_balance_str else Decimal("0")
            except (InvalidOperation, ValueError):
                flash("Invalid opening balance!", "danger")
                return render_template("admin_create_financial_account.html",
                                      control_accounts=get_active_control_accounts())

            account = FinancialAccount(
                name=name,
                method=new_account_method_token(),
                account_type=account_subtype,
                opening_balance=opening_balance,
                is_control=is_control,
                parent_id=parent.id if needs_parent else None,
                is_active=True
            )
            db.session.add(account)
            db.session.flush()
            ensure_gl_account_for_financial(account)
            post_account_opening(account)
            db.session.commit()
            record_audit("create", "FinancialAccount", account.id, f"Created {'control account' if is_control else 'account'} '{name}'")
            flash(f"{'Control account' if is_control else 'Account'} '{name}' created successfully!", "success")
            return redirect(url_for("admin_financial_accounts"))

    parent_id_prefill = request.args.get("parent_id", "")
    account_type_prefill = request.args.get("type", "")
    control_accounts = get_active_control_accounts()
    return render_template("admin_create_financial_account.html",
                         control_accounts=control_accounts,
                         parent_id_prefill=parent_id_prefill,
                         account_type_prefill=account_type_prefill)

@app.route("/admin/financial-accounts/<int:id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_financial_account(id):
    """Edit an existing financial account."""
    account = FinancialAccount.query.get_or_404(id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        opening_balance_str = request.form.get("opening_balance", "0").strip()
        is_active = request.form.get("is_active", "").strip() == "on"
        parent_raw = request.form.get("parent_id", "").strip()

        parent = None
        if not name:
            flash("Account name is required!", "danger")
        elif account_name_taken(name, exclude_id=account.id):
            flash(f"An account named '{name}' already exists!", "danger")
        elif not account.is_control and parent_raw and not parent_raw.isdigit():
            flash("Please select a valid control account!", "danger")
        elif not account.is_control and parent_raw and not (parent := db.session.get(FinancialAccount, int(parent_raw))):
            flash("Selected control account was not found!", "danger")
        elif not account.is_control and parent_raw and parent and not parent.is_control:
            flash("A subsidiary account must belong to a control account!", "danger")
        elif not account.is_control and parent_raw and parent and parent.id == account.id:
            flash("An account cannot be its own parent!", "danger")
        else:
            try:
                opening_balance = Decimal(opening_balance_str) if opening_balance_str else Decimal("0")
            except (InvalidOperation, ValueError):
                flash("Invalid opening balance!", "danger")
                return render_template("admin_edit_financial_account.html",
                                      account=account,
                                      control_accounts=get_active_control_accounts())

            account.name = name
            account.opening_balance = opening_balance
            account.is_active = is_active
            if not account.is_control:
                account.parent_id = parent.id if parent_raw else None

            db.session.flush()
            post_account_opening(account)
            db.session.commit()
            record_audit("edit", "FinancialAccount", account.id, f"Updated account '{name}'")
            flash(f"Account '{name}' updated successfully!", "success")
            return redirect(url_for("admin_financial_accounts"))

    control_accounts = get_active_control_accounts()
    return render_template("admin_edit_financial_account.html",
                         account=account,
                         control_accounts=control_accounts)

@app.route("/admin/financial-accounts/<int:id>/delete", methods=["POST"])
@admin_required
def admin_delete_financial_account(id):
    """Delete a financial account (only if it has no transactions)."""
    account = FinancialAccount.query.get_or_404(id)

    # Check if account has any transactions
    has_children = FinancialAccount.query.filter_by(parent_id=id).count() > 0
    has_transactions = (
        SupplierPayment.query.filter_by(account_id=id).count() > 0 or
        CustomerPayment.query.filter_by(account_id=id).count() > 0 or
        Expense.query.filter_by(account_id=id).count() > 0
    )

    if has_children:
        flash(f"Cannot delete account '{account.name}' - it has subsidiary accounts!", "danger")
    elif has_transactions:
        flash(f"Cannot delete account '{account.name}' - it has transactions!", "danger")
    else:
        name = account.name
        db.session.delete(account)
        db.session.commit()
        record_audit("delete", "FinancialAccount", id, f"Deleted account '{name}'")
        flash(f"Account '{name}' deleted successfully!", "success")

    return redirect(url_for("admin_financial_accounts"))

@app.route("/admin/audit")
@admin_required
def admin_audit():
    action = request.args.get("action", "").strip()
    query = AuditLog.query
    if action:
        query = query.filter(AuditLog.action == action)
    query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    entries, pagination = get_paginated_results(query)
    actions = [a[0] for a in db.session.query(AuditLog.action).distinct().all()]
    return render_template("admin_audit.html", entries=entries, pagination=pagination,
                           actions=sorted(actions), current_action=action)

# ─── Backup & Restore ──────────────────────────────────────────────────────────
# Backups use a portable JSON dump of every table so they work identically on
# SQLite (local) and PostgreSQL (production) — the old SQLite-file copy only
# worked locally and did nothing useful on the deployed Postgres database.
BACKUP_FORMAT_VERSION = 1

def _json_default(v):
    if isinstance(v, Decimal):
        return str(v)               # preserve exact precision
    # datetime is a subclass of date, so it has to be tested first or a timestamp
    # would be flattened to its day. Fiscal years and periods use plain dates.
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("latin1")
    raise TypeError(f"Not JSON serializable: {type(v)}")

def _coerce_value(value, column):
    """Convert a JSON-decoded value back to the Python type the column expects."""
    if value is None:
        return None
    from sqlalchemy import DateTime, Date, Numeric, Float, Boolean, Integer
    coltype = column.type
    if isinstance(coltype, DateTime):
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(coltype, Date):
        # A Date column must get a date, not a string, or the restore writes junk
        # that the next query cannot compare against.
        return date.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(coltype, Float):
        return float(value)
    if isinstance(coltype, Numeric):
        return Decimal(str(value))
    if isinstance(coltype, Integer):
        return int(value)
    if isinstance(coltype, Boolean):
        return bool(value)
    return value

def export_database_dict():
    """Serialize every table to a plain dict suitable for json.dumps."""
    data = {
        "_meta": {
            "app": app.config["APP_NAME"],
            "format_version": BACKUP_FORMAT_VERSION,
            "created": datetime.now(timezone.utc).isoformat(),
            "dialect": db.engine.dialect.name,
        },
        "tables": {},
    }
    for table in db.metadata.sorted_tables:
        result = db.session.execute(table.select())
        cols = list(result.keys())
        data["tables"][table.name] = [dict(zip(cols, row)) for row in result]
    return data

def import_database_dict(data):
    """Replace all data with the contents of a backup dict (atomic transaction)."""
    tables_data = data.get("tables", {})
    is_postgres = db.engine.dialect.name == "postgresql"
    # Wipe children first, then insert parents first (FK-safe order).
    for table in reversed(db.metadata.sorted_tables):
        db.session.execute(table.delete())
    for table in db.metadata.sorted_tables:
        rows = tables_data.get(table.name)
        if not rows:
            continue
        columns = {c.name: c for c in table.columns}
        cleaned = [
            {k: _coerce_value(v, columns[k]) for k, v in row.items() if k in columns}
            for row in rows
        ]
        if cleaned:
            db.session.execute(table.insert(), cleaned)
    # Postgres SERIAL sequences don't advance on explicit-id inserts — realign them.
    if is_postgres:
        for table in db.metadata.sorted_tables:
            if "id" in {c.name for c in table.columns}:
                db.session.execute(text(
                    "SELECT setval(pg_get_serial_sequence(:t, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM \"%s\"), 1), true)" % table.name
                ), {"t": table.name})
    db.session.commit()

@app.route("/admin/system")
@admin_required
def admin_system():
    dialect = db.engine.dialect.name
    db_path = os.path.join(BASE_DIR, "instance", "database.db")
    db_size_kb = round(os.path.getsize(db_path) / 1024, 1) if os.path.exists(db_path) else 0
    tables = inspect(db.engine).get_table_names()
    table_counts = {}
    for t in sorted(tables):
        try:
            table_counts[t] = db.session.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
        except Exception:
            table_counts[t] = "?"
    return render_template("admin_system.html",
        dialect=dialect,
        db_size_kb=db_size_kb,
        table_counts=table_counts,
        backup_name=f"backup_{now_local().strftime('%Y%m%d_%H%M%S')}.json",
    )

FACTORY_RESET_PHRASE = "DELETE ALL DATA"
TRANSACTION_RESET_PHRASE = "DELETE ALL TRANSACTIONS"

# Both resets name the tables they *keep* and delete the rest, rather than listing what
# to delete. A list of things to delete rots: add a model, forget to add it here, and
# rows of it survive a wipe that claimed to be total — silently, and only noticed when
# the numbers do not add up. Naming what survives fails the safe way instead.
_KEEP_ON_FACTORY_RESET = {"user"}
_KEEP_ON_TRANSACTION_RESET = _KEEP_ON_FACTORY_RESET | {
    "supplier", "customer", "item", "category", "expense_category",
    "account", "tax_code", "tax_component",
    "fiscal_year", "accounting_period", "financial_account",
}

def _wipe_except(keep):
    """Delete every table not in `keep`, children first."""
    for table in reversed(db.metadata.sorted_tables):
        if table.name not in keep:
            db.session.execute(table.delete())
    db.session.commit()

def _seed_fresh_ledger():
    """The chart, the tax codes, this year's periods, the four cash/bank accounts —
    everything a first boot puts there so the system is usable the moment it is empty."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_tax_codes()
    seed_fiscal_year(now_local())
    # Only if there are none — a transaction reset keeps the cash/bank accounts, and
    # `method` is UNIQUE, so seeding them again unconditionally would fail the whole
    # reset on the four accounts it was supposed to leave alone.
    if FinancialAccount.query.count() == 0:
        types = {"Cash": "Cash", "Bank": "Bank", "Cheque": "Bank", "Online": "Bank"}
        for m in PAYMENT_METHODS:
            db.session.add(FinancialAccount(name=m, method=m, account_type=types.get(m, "Bank"),
                                            opening_balance=0))
        db.session.commit()
    seed_financial_account_links()

def factory_reset():
    """Empty the system of every business record and leave it as a fresh install.

    For handing a tested system to whoever will actually use it: the trial suppliers,
    invoices and journal entries have to go, and they have to go *completely* — an
    invoice sequence left mid-count would hand the new owner INV-2026-000007 as their
    first invoice, and a stale opening balance would sit in the ledger for ever.

    Users are kept. Wiping them would lock the administrator out of the machine
    half-way through the request that wiped them.
    """
    _wipe_except(_KEEP_ON_FACTORY_RESET)
    _seed_fresh_ledger()

def reset_transactions():
    """Clear the trading history but keep who you trade with and what you sell.

    What a client actually asks for at the start of a year, or when a trial period ends:
    the suppliers, customers and items took real work to enter and are still correct —
    it is the transactions that have to go.

    Their opening balances go too, and that is the part that is easy to get wrong. An
    opening balance is not a static field: it is *posted* to the ledger, so leaving it
    behind would leave a receivable in the accounts with no customer ledger under it.
    Item stock is the same — the Inventory account is emptied with the journals, so any
    stock left on an item would immediately contradict it. Everything a party or an item
    carries is therefore zeroed, not merely the rows around it deleted.

    A closed fiscal year is reopened: its closing entry has just been deleted, so
    "closed" would only mean "nothing can ever be posted again".
    """
    _wipe_except(_KEEP_ON_TRANSACTION_RESET)

    for s in Supplier.query.all():
        s.opening_balance = 0
    for c in Customer.query.all():
        c.opening_balance = 0
    for i in Item.query.all():
        i.stock = 0
        i.inventory_value = 0
    for fa in FinancialAccount.query.all():
        fa.opening_balance = 0
    for fy in FiscalYear.query.all():
        fy.is_closed = False
        fy.closed_at = None
    for p in AccountingPeriod.query.all():
        p.is_closed = False
    db.session.commit()

    # Idempotent, and it puts back anything a half-set-up system was missing.
    _seed_fresh_ledger()

def _confirmed(phrase):
    if request.form.get("confirm", "").strip() == phrase:
        return True
    flash(f'Type "{phrase}" exactly to confirm. Nothing was deleted.', "danger")
    return False

@app.route("/admin/reset", methods=["POST"])
@admin_required
def admin_reset():
    """Wipe the trial data before the system changes hands. Irreversible, so it asks for
    the phrase to be typed rather than settling for a button someone can hit by
    accident."""
    if not _confirmed(FACTORY_RESET_PHRASE):
        return redirect(url_for("admin_system"))
    try:
        factory_reset()
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Factory reset failed")
        flash(f"Reset failed, nothing was changed: {e}", "danger")
        return redirect(url_for("admin_system"))

    # The audit log was wiped with everything else, so this becomes its first entry —
    # which is the right first thing for it to say.
    record_audit("reset", "Database", None,
                 "All business data deleted; chart of accounts and fiscal year re-seeded")
    flash("All business data deleted. The system is empty and ready to be set up.",
          "success")
    return redirect(url_for("admin_system"))

@app.route("/admin/reset_transactions", methods=["POST"])
@admin_required
def admin_reset_transactions():
    if not _confirmed(TRANSACTION_RESET_PHRASE):
        return redirect(url_for("admin_system"))
    try:
        reset_transactions()
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Transaction reset failed")
        flash(f"Reset failed, nothing was changed: {e}", "danger")
        return redirect(url_for("admin_system"))

    kept = (f"{Supplier.query.count()} suppliers, {Customer.query.count()} customers, "
            f"{Item.query.count()} items")
    record_audit("reset", "Database", None,
                 f"All transactions deleted; opening balances cleared. Kept {kept}.")
    flash(f"All transactions deleted. {kept} kept, with their opening balances and "
          f"stock cleared to zero.", "success")
    return redirect(url_for("admin_system"))

@app.route("/admin/backup")
@admin_required
def admin_backup():
    try:
        payload = json.dumps(export_database_dict(), default=_json_default, indent=1)
    except Exception as e:
        app.logger.exception("Backup failed")
        flash(f"Backup failed: {e}", "danger")
        return redirect(url_for("admin_system"))
    filename = f"backup_{now_local().strftime('%Y%m%d_%H%M%S')}.json"
    return send_file(
        BytesIO(payload.encode("utf-8")),
        as_attachment=True, download_name=filename, mimetype="application/json",
    )

@app.route("/admin/restore", methods=["POST"])
@admin_required
def admin_restore():
    uploaded = request.files.get("db_file")
    if not uploaded or not uploaded.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("admin_system"))
    raw = uploaded.read()

    # Legacy path: a raw SQLite .db file can still be restored on a local SQLite
    # install by swapping the file (this never worked on Postgres anyway).
    if raw[:16].startswith(b"SQLite format 3"):
        if db.engine.dialect.name != "sqlite":
            flash("This looks like a SQLite .db file, but the server uses "
                  f"{db.engine.dialect.name}. Please upload a .json backup instead.", "danger")
            return redirect(url_for("admin_system"))
        db_path = os.path.join(BASE_DIR, "instance", "database.db")
        try:
            import shutil
            if os.path.exists(db_path):
                shutil.copy2(db_path, db_path + ".bak")
            db.session.remove()
            db.engine.dispose()
            with open(db_path, "wb") as f:
                f.write(raw)
            with app.app_context():
                migrate_database()
            record_audit("restore", "Database", None, "Database restored from uploaded SQLite file")
            flash("Database restored from SQLite file. Previous copy saved as database.db.bak.", "success")
        except Exception as e:
            app.logger.exception("SQLite restore failed")
            flash(f"Restore failed: {e}", "danger")
        return redirect(url_for("admin_system"))

    # Portable JSON restore (works on both SQLite and Postgres).
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        flash("Invalid file — not a valid JSON backup.", "danger")
        return redirect(url_for("admin_system"))
    if not isinstance(data, dict) or "tables" not in data:
        flash("Invalid backup file — missing table data.", "danger")
        return redirect(url_for("admin_system"))
    try:
        import_database_dict(data)
        n = sum(len(v) for v in data.get("tables", {}).values())
        record_audit("restore", "Database", None, f"Database restored from JSON backup ({n} rows)")
        flash(f"Database restored successfully from backup ({n:,} rows). "
              "All previous data was replaced.", "success")
    except Exception as e:
        db.session.rollback()
        app.logger.exception("JSON restore failed")
        flash(f"Restore failed (no changes were applied): {e}", "danger")
    return redirect(url_for("admin_system"))

@app.cli.command("backup-db")
@click.argument("path", required=False)
def backup_db_cmd(path):
    """Write a full JSON backup of the database to PATH.

    Schedulable for automated backups, e.g. a daily cron:
        flask backup-db /backups/salpur_$(date +%F).json
    Defaults to ./backups/backup_<timestamp>.json when PATH is omitted.
    """
    data = export_database_dict()
    if not path:
        os.makedirs("backups", exist_ok=True)
        path = os.path.join("backups", f"backup_{now_local().strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=_json_default, indent=1)
    rows = sum(len(v) for v in data["tables"].values())
    click.echo(f"Backup written to {path} ({rows} rows, {os.path.getsize(path)//1024} KB).")

DEMO_ITEMS = [
    # (name, category, unit, opening_stock, reorder, purchase_price, sale_price)
    ("Samsung A15 Mobile",   "Electronics",     "Pcs",    12,  5, 42000, 49000),
    ("HP Laptop 15s",        "Electronics",     "Pcs",     6,  2, 98000, 118000),
    ("USB-C Cable 2m",       "Electronics",     "Pcs",   150, 40,   320,    650),
    ("Wireless Mouse",       "Electronics",     "Pcs",    60, 15,   850,   1500),
    ("Cotton Fabric",        "Fabric & Textile","Meter", 400, 100,  210,    310),
    ("Polyester Fabric",     "Fabric & Textile","Meter", 250,  80,  160,    240),
    ("Office Chair",         "Furniture",       "Pcs",    18,   5, 7200,  11500),
    ("Steel Almirah",        "Furniture",       "Pcs",     4,   2,24000,  33000),
]

DEMO_SUPPLIERS = [
    ("Shaheen Electronics", "03211234567", "Hall Road, Lahore",      35000),
    ("Karimi Cloth House",  "03337654321", "Bolton Market, Karachi", 12000),
    ("National Traders",    "03459876543", "Blue Area, Islamabad",       0),
    ("Meezan Furniture",    "03018889999", "Ferozepur Road, Lahore", -8000),   # advance paid
]

DEMO_CUSTOMERS = [
    ("Ahmed Brothers",     "03121111111", "Karkhana Bazaar, Faisalabad", 18000),
    ("Zafar Retail Store", "03222222222", "Hussain Agahi, Multan",           0),
    ("City Electronics",   "03003333333", "Saddar, Rawalpindi",          25000),
    ("Gulberg Interiors",  "03334444444", "Gulberg III, Lahore",         -5000),  # advance received
    ("Rehman Traders",     "03015555555", "University Road, Peshawar",       0),
]

# The seeder rebuilds the master data too, so it keeps less than a transaction reset does.
# Derived from that set rather than written out again: a list of tables to delete rots —
# fixed assets and the invoice-number counter were both added after these two commands were
# written, and both quietly survived a wipe that claimed to be total, until a demo turned up
# holding six vans.
_KEEP_ON_SEED = _KEEP_ON_TRANSACTION_RESET - {
    "supplier", "customer", "item", "category", "expense_category",
}

def _wipe_transactional_data():
    """Everything except the users and the ledger's foundations."""
    _wipe_except(_KEEP_ON_SEED)
    for fa in FinancialAccount.query.all():
        fa.opening_balance = 0        # cash/bank accounts survive; their balances are data
    db.session.commit()

@app.cli.command("clear-transactions")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def clear_transactions_cmd(yes):
    """Delete every transaction, keeping the chart of accounts and master data.

    Kept: chart of accounts, tax codes, fiscal years, cash/bank accounts,
    suppliers, customers, items, categories, expense categories, users.

    Deleted: purchases, sales, payments, receipts, expenses, returns, stock
    adjustments, purchase orders, quotations, challans, and every journal entry.

    Opening balances survive as *values* on the master records, but the journal
    entries that carried them into the ledger do not — so they are posted again
    afterwards, and stock is reset to opening stock. Without that the general
    ledger would be empty while the subledgers still showed balances."""
    if not yes:
        click.echo("This deletes ALL transactions. Chart of accounts and master data are kept.")
        if DATABASE_URL:
            click.echo(f"Target: DEPLOYED database at "
                       f"{urlsplit(DATABASE_URL).hostname or 'unknown host'}")
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    # Name what survives, and delete the rest. This used to be a list of models to delete,
    # and it had rotted: fixed assets, their depreciation charges and the invoice-number
    # counter were all added afterwards and none of them was in it, so they survived a
    # command whose whole job was to remove them.
    before = {t.name: db.session.execute(
                  db.select(func.count()).select_from(t)).scalar()
              for t in db.metadata.sorted_tables if t.name not in _KEEP_ON_TRANSACTION_RESET}
    _wipe_except(_KEEP_ON_TRANSACTION_RESET)
    for name, n in sorted(before.items()):
        if n:
            click.echo(f"  removed {n:>5} {name}")

    # A closed year's closing entry has just been deleted with everything else,
    # so leaving the year closed would lock a ledger that no longer has one.
    reopened = 0
    for fy in FiscalYear.query.all():
        if fy.is_closed:
            fy.is_closed, fy.closed_at = False, None
            reopened += 1
        for p in fy.periods:
            p.is_closed = False
    if reopened:
        click.echo(f"  reopened {reopened} closed fiscal year(s)")

    # Stock goes back to what the item started with.
    for it in Item.query.all():
        it.stock = it.opening_stock
        it.inventory_value = (Decimal(str(it.opening_stock or 0))
                              * Decimal(str(it.purchase_price or 0))).quantize(MONEY)
    db.session.commit()

    # Re-lay the opening balances into both the subledgers and the GL.
    for sup in Supplier.query.all():
        sync_supplier_opening(sup)
        post_supplier_opening(sup)
    for cus in Customer.query.all():
        sync_customer_opening(cus)
        post_customer_opening(cus)
    for it in Item.query.all():
        post_item_opening(it)
    db.session.commit()

    entries = JournalEntry.query.count()
    click.echo(f"  re-posted opening balances: {entries} journal entr{'y' if entries == 1 else 'ies'}")

    # Same checks the seeder runs — an empty ledger must reconcile too.
    b = gl_balances()

    def gl_of(code):
        a = get_account(code)
        return natural_balance(a, b.get(a.id, Decimal("0")))

    sub_ar = sum(Decimal(str(get_customer_balance(c.id))) for c in Customer.query.all())
    sub_ap = sum(Decimal(str(get_supplier_balance(s.id))) for s in Supplier.query.all())
    sub_inv = Decimal(str(db.session.query(func.sum(Item.inventory_value)).scalar() or 0))
    dr = db.session.query(func.sum(JournalLine.debit)).scalar() or 0
    cr = db.session.query(func.sum(JournalLine.credit)).scalar() or 0

    def check(label, left, right):
        ok = abs(Decimal(str(left)) - Decimal(str(right))) < Decimal("0.01")
        click.echo(f"  {'OK  ' if ok else 'FAIL'} {label:<34} {float(left):>14,.2f}  vs {float(right):>14,.2f}")
        return ok

    click.echo("")
    click.echo("Verification")
    all_ok = check("Journal debits = credits", dr, cr)
    all_ok &= check("AR: ledger = customer subledger", gl_of(ACC_AR), sub_ar)
    all_ok &= check("AP: ledger = supplier subledger", gl_of(ACC_AP), sub_ap)
    all_ok &= check("Inventory: ledger = stock value", gl_of(ACC_INVENTORY), sub_inv)
    click.echo("")
    click.echo(f"  Chart of accounts kept: {Account.query.count()} accounts, "
               f"{Item.query.count()} items, {Supplier.query.count()} suppliers, "
               f"{Customer.query.count()} customers")
    click.echo("")
    click.echo("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED - investigate before using this data")

@app.cli.command("seed-data")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--demo-user", is_flag=True,
              help="Also create demo@demo.com / demo1234 as a MANAGER, for a public demo.")
def seed_data_cmd(yes, demo_user):
    """Wipe all non-user data and build a realistic demo company.

    Everything goes through the same posting layer the web app uses, so the
    general ledger, the subledgers and the stock all end up consistent — the
    reconciliation report passes on the data this produces, and the command
    checks that itself before it finishes."""
    # Always say which database is about to be emptied — including under --yes, which is how
    # it will actually be run. The demo database, the local one and a client's live books are
    # told apart by one environment variable, and this command deletes everything it finds.
    # A wipe that does not name its target is one mistyped export away from a disaster that
    # cannot be undone.
    target = (f"DEPLOYED database at {urlsplit(DATABASE_URL).hostname or 'unknown host'}"
              if DATABASE_URL else "local SQLite database (instance/database.db)")
    click.echo(f"Target: {target}")

    if not yes:
        click.echo("WARNING: deletes ALL data except user accounts, then inserts demo data.")
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    if demo_user:
        # Manager, never admin. A public demo login that can reverse documents, wipe the
        # database or read the audit log is not a demo, it is an open door — the first
        # visitor to find Admin → Start Fresh would empty the thing you are showing people.
        existing = User.query.filter_by(email="demo@demo.com").first()
        if existing:
            existing.role, existing.verified = "manager", True
            existing.password = pwd_context.hash("demo1234")
        else:
            db.session.add(User(name="Demo User", email="demo@demo.com",
                                password=pwd_context.hash("demo1234"),
                                verified=True, role="manager"))
        db.session.commit()
        click.echo("Demo login: demo@demo.com / demo1234  (manager — cannot reset or reverse)")

    year = now_local().year
    # Everything a first boot puts there. The seeder builds a demo from nothing and must not
    # assume the app has been booted against this database first — it did, and fell over on
    # a missing fixed-asset account the moment it was pointed at a fresh one.
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_tax_codes()

    click.echo("Clearing existing data ...")
    _wipe_transactional_data()

    # Clear the years out before seeding new ones, not after. A fiscal year left over from
    # a different FISCAL_YEAR_START_MONTH does not just sit there: its periods overlap the
    # ones about to be created, and a demo document dated inside the overlap lands in
    # whichever is found first. Re-running the seeder with the setting changed would quietly
    # produce a company trading in two fiscal years at once.
    #
    # Safe here, and only here: every posting has just been deleted, so nothing points at
    # these periods any more. The web resets cannot do this — they are aimed at real books.
    stale = [fy for fy in FiscalYear.query.all()
             if fy.start_date.month != FISCAL_YEAR_START_MONTH]
    for fy in stale:
        db.session.delete(fy)              # cascades to its periods
    if stale:
        db.session.commit()
        click.echo(f"  dropped {len(stale)} fiscal year(s) that started in another month: "
                   f"{', '.join(fy.name for fy in stale)}")

    # The demo trades across a whole calendar year. Unless the fiscal year starts in
    # January, that spans two of them, and a document cannot be posted on a date no open
    # period covers. Both are seeded; seeding is idempotent, so on a January year the
    # second call does nothing.
    seed_fiscal_year(datetime(year, 1, 1))
    seed_fiscal_year(datetime(year, 12, 31))

    # The seeder builds a demo company from nothing, so it cannot assume the cash and bank
    # accounts are already there — it used to, and died on a KeyError against a database
    # that did not happen to have been booted first.
    # Hierarchical structure: control accounts (headers) with subsidiary accounts (selectable)

    # Check if hierarchical structure exists (by looking for Cash control account)
    if not FinancialAccount.query.filter_by(name="Cash", is_control=True).first():
        # Clear old flat accounts if they exist (migration from old structure)
        FinancialAccount.query.delete()
        db.session.commit()

        # Cash Control Account
        cash_control = FinancialAccount(
            name="Cash", method="cash", account_type="Cash",
            opening_balance=0, is_control=True
        )
        db.session.add(cash_control)
        db.session.flush()  # Need ID before using it as parent

        # Cash subsidiaries
        cash_subsidiaries = [
            ("Cash in Hand", 0),
            ("Cash at Cashier", 0),
        ]
        for sub_name, opening_bal in cash_subsidiaries:
            subsidiary = FinancialAccount(
                name=sub_name, method=new_account_method_token(),
                account_type="Cash", opening_balance=opening_bal,
                is_control=False, parent_id=cash_control.id
            )
            db.session.add(subsidiary)

        # Banks Control Account
        banks_control = FinancialAccount(
            name="Banks", method="bank", account_type="Bank",
            opening_balance=0, is_control=True
        )
        db.session.add(banks_control)
        db.session.flush()  # Need ID before using it as parent

        # Bank subsidiaries
        bank_subsidiaries = [
            ("HBL", 0),
            ("UBL", 0),
            ("ABL", 0),
            ("MCB", 0),
        ]
        for bank_name, opening_bal in bank_subsidiaries:
            subsidiary = FinancialAccount(
                name=bank_name, method=new_account_method_token(),
                account_type="Bank", opening_balance=opening_bal,
                is_control=False, parent_id=banks_control.id
            )
            db.session.add(subsidiary)

        # Cheque (standalone for legacy compatibility)
        cheque = FinancialAccount(
            name="Cheque", method="cheque", account_type="Bank",
            opening_balance=0, is_control=False
        )
        db.session.add(cheque)

        # Online (standalone for legacy compatibility)
        online = FinancialAccount(
            name="Online", method="online", account_type="Bank",
            opening_balance=0, is_control=False
        )
        db.session.add(online)
        db.session.commit()

        # Card Control Account
        card_control = FinancialAccount(
            name="Card", method="card", account_type="Bank",
            opening_balance=0, is_control=True
        )
        db.session.add(card_control)
        db.session.flush()  # Need ID before using it as parent

        # Add a default Visa subsidiary
        visa = FinancialAccount(
            name="Visa Card", method=new_account_method_token(),
            account_type="Bank", opening_balance=0,
            is_control=False, parent_id=card_control.id
        )
        db.session.add(visa)
        db.session.commit()

    # Create GL accounts for all financial accounts at once (idempotent)
    seed_financial_account_links()

    closed = [fy.name for fy in FiscalYear.query.filter_by(is_closed=True).all()]
    if closed:
        click.echo(f"Fiscal year(s) {', '.join(closed)} are closed; demo data cannot be "
                   f"posted into them.")
        return

    def D(month, day):
        """A date inside the current fiscal year, so every posting is accepted."""
        return datetime(year, month, day)

    # -- Master data ----------------------------------------------------------
    cats = {}
    for name in ("Electronics", "Fabric & Textile", "Furniture"):
        c = Category(name=name)
        db.session.add(c)
        cats[name] = c
    db.session.flush()

    items = {}
    for name, cat, unit, opening, reorder, pp, sp in DEMO_ITEMS:
        it = Item(name=name, category_id=cats[cat].id, unit=unit,
                  opening_stock=opening, stock=opening, reorder_level=reorder,
                  purchase_price=pp, sale_price=sp,
                  inventory_value=Decimal(str(opening)) * Decimal(str(pp)))
        db.session.add(it)
        items[name] = it
    db.session.flush()
    for it in items.values():
        post_item_opening(it)

    suppliers = {}
    for name, contact, address, ob in DEMO_SUPPLIERS:
        sup = Supplier(name=name, contact=contact, address=address, opening_balance=ob)
        db.session.add(sup)
        suppliers[name] = sup
    db.session.flush()
    for sup in suppliers.values():
        sync_supplier_opening(sup)
        post_supplier_opening(sup)

    customers = {}
    for name, contact, address, ob in DEMO_CUSTOMERS:
        cus = Customer(name=name, contact=contact, address=address, opening_balance=ob)
        db.session.add(cus)
        customers[name] = cus
    db.session.flush()
    for cus in customers.values():
        sync_customer_opening(cus)
        post_customer_opening(cus)

    for name in ("Rent", "Salaries & Wages", "Utilities", "Freight"):
        db.session.add(ExpenseCategory(name=name))
    db.session.commit()
    click.echo(f"  {len(cats)} categories, {len(items)} items, "
               f"{len(suppliers)} suppliers, {len(customers)} customers.")

    # Build account lookup - use name for hierarchical structure (not method, which is now unique per account)
    accounts = {fa.name: fa for fa in FinancialAccount.query.all()}

    # -- What was already in the till and the bank on day one ------------------
    # A funded business, so the demo does not open on an overdraft. These post to the
    # ledger like any other opening balance (Dr the account, Cr Opening Balance Equity),
    # which is also the only reason they show up on the balance sheet at all.
    # Use the first subsidiary of each control account for opening balance
    for acct_name, opening in (("Cash in Hand", 400000), ("HBL", 4500000)):
        fa = accounts.get(acct_name)
        if fa:
            fa.opening_balance = Decimal(str(opening))
            post_account_opening(fa)
    db.session.commit()

    # -- Capital injection, so the ledger has a manual journal entry in it ------
    post_entry(entry_date=D(1, 2), description="Owner's capital introduced",
               reference="JV-001", lines=[
                   {"code": ACC_CASH_IN_HAND, "debit": 300000, "credit": 0},
                   {"code": ACC_CAPITAL, "debit": 0, "credit": 300000}])
    db.session.commit()

    # -- Purchases ------------------------------------------------------------
    def purchase(supplier, date, rows, tax=0):
        """rows: [(item_name, qty, unit_price)]"""
        first = rows[0]
        pur = Purchase(supplier_id=suppliers[supplier].id, item_id=items[first[0]].id,
                       quantity=first[1], purchase_price=first[2],
                       discount_type="percent", discount_value=0, discount_amount=0,
                       tax_percent=0, tax_amount=0, date=date)
        db.session.add(pur)
        db.session.flush()
        for iname, qty, price in rows:
            it = items[iname]
            gross = qty * price
            disc, tax_amt, net = calc_discount_tax(gross, "percent", 0, tax)
            db.session.add(PurchaseItem(purchase_id=pur.id, item_id=it.id,
                                        quantity=qty, purchase_price=price,
                                        discount_type="percent", discount_value=0,
                                        discount_amount=disc, tax_percent=tax,
                                        tax_amount=tax_amt, amount=net))
            item_add_stock(it, qty, net - tax_amt)
        db.session.flush()
        db.session.refresh(pur)
        sync_supplier_purchase(pur)
        post_document("purchase", pur)
        db.session.commit()
        return pur

    purchases = [
        purchase("Shaheen Electronics", D(1, 12), [("Samsung A15 Mobile", 20, 41500),
                                                   ("USB-C Cable 2m", 200, 300)], tax=17),
        purchase("Karimi Cloth House",  D(1, 25), [("Cotton Fabric", 600, 205),
                                                   ("Polyester Fabric", 400, 158)]),
        purchase("Shaheen Electronics", D(2, 14), [("HP Laptop 15s", 10, 97000)], tax=17),
        purchase("Meezan Furniture",    D(3, 3),  [("Office Chair", 25, 7000),
                                                   ("Steel Almirah", 8, 23500)]),
        purchase("Shaheen Electronics", D(4, 8),  [("Samsung A15 Mobile", 15, 43000),
                                                   ("Wireless Mouse", 80, 820)], tax=17),
        purchase("National Traders",    D(5, 19), [("USB-C Cable 2m", 300, 290)]),
        purchase("Karimi Cloth House",  D(6, 6),  [("Cotton Fabric", 400, 215)]),
    ]
    click.echo(f"  {len(purchases)} purchases.")

    # -- Sales ----------------------------------------------------------------
    def sale(customer, date, rows, tax=0):
        first = rows[0]
        sal = Sale(customer_id=customers[customer].id, item_id=items[first[0]].id,
                   quantity=first[1], sale_price=first[2], cost_price=0,
                   discount_type="percent", discount_value=0, discount_amount=0,
                   tax_percent=0, tax_amount=0, date=date)
        db.session.add(sal)
        db.session.flush()
        for iname, qty, price in rows:
            it = items[iname]
            gross = qty * price
            disc, tax_amt, net = calc_discount_tax(gross, "percent", 0, tax)
            unit_cost = it.avg_cost
            db.session.add(SaleItem(sale_id=sal.id, item_id=it.id,
                                    quantity=qty, sale_price=price,
                                    cost_price=float(unit_cost),
                                    discount_type="percent", discount_value=0,
                                    discount_amount=disc, tax_percent=tax,
                                    tax_amount=tax_amt, amount=net))
            item_remove_stock(it, qty, cost_total=unit_cost * Decimal(str(qty)))
        db.session.flush()
        db.session.refresh(sal)
        sync_customer_sale(sal)
        post_document("sale", sal)
        db.session.commit()
        return sal

    sales = [
        sale("Ahmed Brothers",     D(2, 3),  [("Samsung A15 Mobile", 6, 49000)], tax=17),
        sale("City Electronics",   D(2, 20), [("USB-C Cable 2m", 80, 650),
                                              ("Wireless Mouse", 20, 1500)], tax=17),
        sale("Zafar Retail Store", D(3, 9),  [("Cotton Fabric", 250, 310)]),
        sale("Ahmed Brothers",     D(3, 22), [("HP Laptop 15s", 4, 118000)], tax=17),
        sale("Gulberg Interiors",  D(4, 2),  [("Office Chair", 10, 11500),
                                              ("Steel Almirah", 3, 33000)]),
        sale("City Electronics",   D(4, 27), [("Samsung A15 Mobile", 9, 48500)], tax=17),
        sale("Rehman Traders",     D(5, 11), [("Polyester Fabric", 300, 240)]),
        sale("Zafar Retail Store", D(6, 1),  [("Cotton Fabric", 350, 315)]),
        sale("Ahmed Brothers",     D(6, 18), [("Wireless Mouse", 30, 1450),
                                              ("USB-C Cable 2m", 120, 640)], tax=17),
        sale("City Electronics",   D(7, 4),  [("HP Laptop 15s", 3, 120000)], tax=17),
        sale("Rehman Traders",     D(2, 25), [("Samsung A15 Mobile", 5, 49500)], tax=17),
        sale("Gulberg Interiors",  D(3, 28), [("Office Chair", 8, 11800)]),
        sale("Zafar Retail Store", D(4, 18), [("USB-C Cable 2m", 200, 660)], tax=17),
        sale("City Electronics",   D(5, 2),  [("HP Laptop 15s", 2, 121000)], tax=17),
        sale("Ahmed Brothers",     D(5, 27), [("Steel Almirah", 4, 33500)]),
        sale("Rehman Traders",     D(6, 25), [("Cotton Fabric", 250, 320)]),
    ]
    click.echo(f"  {len(sales)} sales.")

    # -- Payments and receipts, across all four methods ----------------------
    # Map old payment method names to actual account names (for hierarchical structure)
    method_to_account = {
        "Bank": "HBL",           # Bank payments go to HBL subsidiary
        "Cash": "Cash in Hand",  # Cash payments go to Cash in Hand subsidiary
        "Cheque": "Cheque",      # Standalone
        "Online": "Online"       # Standalone
    }

    def pay(supplier, date, amount, method="Bank"):
        acct_name = method_to_account.get(method, method)
        acct = accounts.get(acct_name)
        if not acct:
            click.echo(f"WARNING: Account '{acct_name}' not found for payment method '{method}', skipping payment")
            return
        p = SupplierPayment(supplier_id=suppliers[supplier].id, amount=amount,
                            payment_date=date, payment_method=method,
                            account_id=acct.id, reference_no=f"PAY-{date:%m%d}")
        db.session.add(p); db.session.flush()
        sync_supplier_payment(p); post_document("payment", p); db.session.commit()

    def receipt(customer, date, amount, method="Cash"):
        acct_name = method_to_account.get(method, method)
        acct = accounts.get(acct_name)
        if not acct:
            click.echo(f"WARNING: Account '{acct_name}' not found for payment method '{method}', skipping receipt")
            return
        r = CustomerPayment(customer_id=customers[customer].id, amount=amount,
                            payment_date=date, payment_method=method,
                            account_id=acct.id, reference_no=f"RCT-{date:%m%d}")
        db.session.add(r); db.session.flush()
        sync_customer_receipt(r); post_document("receipt", r); db.session.commit()

    pay("Shaheen Electronics", D(2, 1),  500000)
    pay("Karimi Cloth House",  D(2, 10), 150000, method="Cheque")
    pay("Shaheen Electronics", D(3, 5),  600000)
    pay("Meezan Furniture",    D(4, 1),  200000, method="Online")
    pay("National Traders",    D(6, 2),   50000, method="Cash")

    receipt("Ahmed Brothers",     D(2, 12), 300000, method="Bank")
    receipt("City Electronics",   D(3, 1),   80000)
    receipt("Zafar Retail Store", D(3, 30),  70000, method="Bank")
    receipt("Ahmed Brothers",     D(4, 15), 400000, method="Online")
    receipt("Gulberg Interiors",  D(5, 5),  150000, method="Cheque")
    receipt("Rehman Traders",     D(6, 10),  60000, method="Bank")
    receipt("City Electronics",   D(5, 20), 250000, method="Bank")
    receipt("Gulberg Interiors",  D(6, 22), 120000)
    click.echo("  5 supplier payments, 8 customer receipts.")

    # -- Expenses, each category wired to its GL account ----------------------
    exp_cats = {c.name: c for c in ExpenseCategory.query.all()}
    for cname, code in (("Rent", "6010"), ("Salaries & Wages", "6020"),
                        ("Utilities", "6030"), ("Freight", "6040")):
        exp_cats[cname].gl_account_id = get_account(code).id
    db.session.commit()

    demo_expenses = [
        ("Rent",             "Shop rent - January",  30000, D(1, 31), "Cash"),
        ("Salaries & Wages", "Staff salaries - Jan", 62000, D(1, 31), "Bank"),
        ("Utilities",        "Electricity bill",     18500, D(2, 8),  "Cash"),
        ("Rent",             "Shop rent - February", 30000, D(2, 28), "Cash"),
        ("Freight",          "Delivery charges",     12000, D(3, 15), "Cash"),
        ("Salaries & Wages", "Staff salaries - Mar", 64000, D(3, 31), "Bank"),
        ("Utilities",        "Internet & phone",      9500, D(4, 10), "Online"),
        ("Rent",             "Shop rent - April",    30000, D(4, 30), "Cash"),
        ("Freight",          "Courier - Multan",      7800, D(5, 20), "Cash"),
        ("Salaries & Wages", "Staff salaries - Jun", 66000, D(6, 30), "Bank"),
    ]
    for cat, desc, amount, date, method in demo_expenses:
        acct_name = method_to_account.get(method, method)
        acct = accounts.get(acct_name)
        if acct:
            e = Expense(category_id=exp_cats[cat].id, description=desc, amount=amount,
                        date=date, payment_method=method, account_id=acct.id)
            db.session.add(e); db.session.flush()
            post_document("expense", e); db.session.commit()
    click.echo(f"  {len(demo_expenses)} expenses.")

    # -- Fixed assets, and depreciation charged every month so far -------------
    # Both methods, so the register shows what each one does to a book value.
    # Sized for a business this size. A 2m-revenue trader does not run an 1.8m van, and a
    # demo whose depreciation swallows its whole profit shows a loss to everyone you show it to.
    demo_assets = [
        ("Delivery Van",    "VAN-01", 1_200_000, 200_000, "Straight Line",    60, None, D(1, 15), "Bank"),
        ("Office Laptops",  "LAP-01",   240_000,       0, "Straight Line",    36, None, D(3, 10), "Bank"),
        ("Shop Fit-out",    "FIT-01",   400_000,       0, "Reducing Balance", None, 15, D(2, 5),  "Bank"),
    ]
    for name, tag, cost, salvage, method, life, rate, acq, pay_method in demo_assets:
        acct_name = method_to_account.get(pay_method, pay_method)
        acct = accounts.get(acct_name)
        if acct:
            asset = FixedAsset(name=name, tag=tag, acquisition_date=acq, cost=cost,
                               salvage_value=salvage, method=method,
                               useful_life_months=life, rate_percent=rate)
            db.session.add(asset)
            db.session.flush()
            post_asset_acquisition(asset, acct.gl_account_id)
            db.session.commit()

    charged = 0
    for m in range(1, now_local().month + 1):
        try:
            entry, total, count = run_depreciation(month_end(D(m, 1)))
            db.session.commit()
            if entry:
                charged += 1
        except PostingError:
            db.session.rollback()          # nothing eligible that month
    click.echo(f"  {len(demo_assets)} fixed assets, depreciation posted for {charged} month(s).")

    # -- Returns --------------------------------------------------------------
    pi = PurchaseItem.query.filter_by(purchase_id=purchases[4].id).first()
    item = db.session.get(Item, pi.item_id)
    pr = PurchaseReturn(purchase_id=pi.purchase_id, supplier_id=purchases[4].supplier_id,
                        item_id=pi.item_id, quantity=2, return_price=43000,
                        date=D(4, 20), reason="Damaged in transit")
    pr.cost_removed = item_remove_stock(item, 2)
    db.session.add(pr); db.session.flush()
    sync_supplier_purchase_return(pr); post_document("purchase_return", pr); db.session.commit()

    si = SaleItem.query.filter_by(sale_id=sales[2].id).first()
    item = db.session.get(Item, si.item_id)
    cost = (Decimal(str(si.cost_price)) * Decimal("40")).quantize(MONEY)
    sr = SaleReturn(sale_id=si.sale_id, customer_id=sales[2].customer_id,
                    item_id=si.item_id, quantity=40, return_price=310,
                    date=D(3, 18), reason="Colour mismatch", cost_restored=cost)
    item_add_stock(item, 40, cost)
    db.session.add(sr); db.session.flush()
    sync_customer_sale_return(sr); post_document("sale_return", sr); db.session.commit()
    click.echo("  1 purchase return, 1 sale return.")

    # -- Stock adjustments ----------------------------------------------------
    # Direction comes from ADJUSTMENT_DIRECTIONS, the same map the form uses. It used to be
    # worked out here by matching the label against a list of the outbound ones — a second
    # copy of a rule that has since been fixed once, in one place.
    for iname, qty, adj_type, date in (("USB-C Cable 2m", 5, "Damage Write-off", D(5, 6)),
                                       ("Wireless Mouse", 3, "Count Correction (Decrease)", D(6, 21))):
        it = items[iname]
        direction = ADJUSTMENT_DIRECTIONS[adj_type]
        adj = StockAdjustment(item_id=it.id, adj_type=adj_type, quantity=qty,
                              direction=direction, date=date, reason="Demo data")
        db.session.add(adj)
        if direction == "out":
            adj.cost_value = item_remove_stock(it, qty)
        else:
            adj.cost_value = (it.avg_cost * Decimal(str(qty))).quantize(MONEY)
            item_add_stock(it, qty, adj.cost_value)
        db.session.flush()
        post_document("stock_adjustment", adj)
        db.session.commit()
    click.echo("  2 stock adjustments.")

    # -- Manual journal entries, one of them reversed --------------------------
    post_entry(entry_date=D(5, 31), description="Owner's drawings", reference="JV-002",
               lines=[{"code": ACC_DRAWINGS, "debit": 50000, "credit": 0},
                      {"code": ACC_CASH_IN_HAND, "debit": 0, "credit": 50000}])
    mistake = post_entry(entry_date=D(6, 15), description="Misposted utilities",
                         reference="JV-003",
                         lines=[{"code": "6030", "debit": 5000, "credit": 0},
                                {"code": ACC_CASH_IN_HAND, "debit": 0, "credit": 5000}])
    db.session.commit()
    reverse_entry(mistake)                 # leaves a visible correction in the ledger
    db.session.commit()
    click.echo("  3 manual journal entries (one reversed).")

    # -- A reversed sale, so the reversal flow has demo data too ---------------
    reverse_document("sale", sales[6])
    db.session.commit()
    click.echo("  1 reversed sale.")

    # -- Prove the result -----------------------------------------------------
    dr = db.session.query(func.sum(JournalLine.debit)).scalar() or 0
    cr = db.session.query(func.sum(JournalLine.credit)).scalar() or 0
    b = gl_balances()

    def gl_of(code):
        a = get_account(code)
        return natural_balance(a, b.get(a.id, Decimal("0")))

    sub_ar = sum(Decimal(str(get_customer_balance(c.id))) for c in Customer.query.all())
    sub_ap = sum(Decimal(str(get_supplier_balance(s.id))) for s in Supplier.query.all())
    sub_inv = Decimal(str(db.session.query(func.sum(Item.inventory_value)).scalar() or 0))

    def check(label, left, right):
        ok = abs(Decimal(str(left)) - Decimal(str(right))) < Decimal("0.01")
        click.echo(f"  {'OK  ' if ok else 'FAIL'} {label:<34} {float(left):>14,.2f}  vs {float(right):>14,.2f}")
        return ok

    click.echo("")
    click.echo("Verification")
    all_ok = check("Journal debits = credits", dr, cr)
    all_ok &= check("AR: ledger = customer subledger", gl_of(ACC_AR), sub_ar)
    all_ok &= check("AP: ledger = supplier subledger", gl_of(ACC_AP), sub_ap)
    all_ok &= check("Inventory: ledger = stock value", gl_of(ACC_INVENTORY), sub_inv)

    income, expense = gl_profit(None, now_local())
    click.echo("")
    click.echo(f"  Journal entries {JournalEntry.query.count()}, lines {JournalLine.query.count()}")
    click.echo(f"  Revenue {float(income):,.2f}   Expenses {float(expense):,.2f}   "
               f"Profit {float(income - expense):,.2f}")
    click.echo("")
    click.echo("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED - do not trust this data")
    click.echo("Seed complete. Log in and explore.")


if __name__ == "__main__":
    # Debugger stays off in production. FLASK_DEBUG overrides explicitly;
    # otherwise default to on only for local SQLite dev (no DATABASE_URL) so a
    # stray `python app.py` against a real database never exposes the debugger.
    _debug_env = os.getenv("FLASK_DEBUG")
    if _debug_env is not None:
        debug_mode = _debug_env.lower() in ("1", "true", "yes", "on")
    else:
        debug_mode = not DATABASE_URL
    # On Render, listen on PORT env var and 0.0.0.0. Local dev: port 5172 on localhost
    port = int(os.getenv("PORT", 5172))
    host = "0.0.0.0" if DATABASE_URL else "127.0.0.1"
    app.run(debug=debug_mode, host=host, port=port)