#app.py
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, abort, Response
from flask_sqlalchemy import SQLAlchemy
from flask_paginate import Pagination, get_page_args
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
import click
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
from functools import wraps
import csv
from io import BytesIO, StringIO
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import os
import secrets
import json
import logging
from decimal import Decimal
from zoneinfo import ZoneInfo
from sqlalchemy.exc import IntegrityError
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from itsdangerous import URLSafeTimedSerializer
from urllib.parse import urlsplit
from dotenv import load_dotenv
from sqlalchemy.sql import func
from sqlalchemy import inspect, text

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

def to_local(dt):
    """A stored datetime (assumed UTC; naive or aware) -> aware datetime in APP_TZ."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(APP_TZ)

def now_local():
    """Current wall-clock time in APP_TZ, naive (for display / 'generated on')."""
    return datetime.now(APP_TZ).replace(tzinfo=None)

@app.template_filter("localdt")
def localdt_filter(dt, fmt="%Y-%m-%d %H:%M"):
    local = to_local(dt)
    return local.strftime(fmt) if local else ""


db = SQLAlchemy(app)  # iska matlab sqlite se connect ho raha ha
csrf = CSRFProtect(app)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "signin"

def sql_date_fmt(col, fmt="%Y-%m"):
    if db.engine.dialect.name == "postgresql":
        return db.func.to_char(col, fmt.replace("%Y", "YYYY").replace("%m", "MM"))
    return db.func.strftime(fmt, col)

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
                return redirect(url_for("signin"))
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

class User(db.Model, UserMixin):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    email               = db.Column(db.String(120), unique=True, nullable=False)
    password            = db.Column(db.String(255), nullable=False)
    verified            = db.Column(db.Boolean, default=False, nullable=False)
    role                = db.Column(db.String(20), nullable=False, default="staff")
    reset_token         = db.Column(db.String(120), nullable=True)
    reset_token_expiry  = db.Column(db.DateTime, nullable=True)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_manager(self):
        return self.role in ("admin", "manager")

    def __repr__(self):
        return f"User('{self.name}', '{self.email}')"

class Supplier(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    contact             = db.Column(db.String(15), nullable=False)
    address             = db.Column(db.String(200), nullable=False)
    opening_balance     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    purchases           = db.relationship("Purchase", backref="id_supplier", lazy=True)

class Customer(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    contact             = db.Column(db.String(15), nullable=False)
    address             = db.Column(db.String(200), nullable=False)
    opening_balance     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    sales               = db.relationship("Sale", backref="id_customer", lazy=True)

class Category(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), unique=True, nullable=False)
    items               = db.relationship("Item", backref="id_category", lazy=True)

class Item(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    category_id         = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    unit                = db.Column(db.String(20), nullable=False, default="Pcs")
    opening_stock       = db.Column(db.Integer, nullable=False, default=0)
    stock               = db.Column(db.Integer, nullable=False, default=0)
    reorder_level       = db.Column(db.Integer, nullable=False, default=50)
    purchase_price      = db.Column(db.Numeric(14, 4), nullable=True)
    sale_price          = db.Column(db.Numeric(14, 4), nullable=True)
    purchases           = db.relationship("Purchase", backref="id_item", lazy=True)
    sales               = db.relationship("Sale", backref="id_item", lazy=True)

class Purchase(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_id         = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    item_id             = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity            = db.Column(db.Integer, nullable=False)
    purchase_price      = db.Column(db.Numeric(14, 4), nullable=False)
    discount_type       = db.Column(db.String(10), nullable=False, default="percent")
    discount_value      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent         = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_amount     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_amount          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    date                = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    notes               = db.Column(db.String(300), nullable=True)
    line_items          = db.relationship("PurchaseItem", backref="purchase_header", lazy=True, cascade="all,delete-orphan")

class Sale(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    item_id             = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity            = db.Column(db.Integer, nullable=False)
    sale_price          = db.Column(db.Numeric(14, 4), nullable=False)
    cost_price          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_type       = db.Column(db.String(10), nullable=False, default="percent")
    discount_value      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent         = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_amount     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_amount          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    date                = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    notes               = db.Column(db.String(300), nullable=True)
    line_items          = db.relationship("SaleItem", backref="sale_header", lazy=True, cascade="all,delete-orphan")

class PurchaseItem(db.Model):
    __tablename__   = "purchase_item"
    id              = db.Column(db.Integer, primary_key=True)
    purchase_id     = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=False)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    purchase_price  = db.Column(db.Numeric(14, 4), nullable=False)
    discount_type   = db.Column(db.String(10), nullable=False, default="percent")
    discount_value  = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_amount = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_amount      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    amount          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    item            = db.relationship("Item", foreign_keys=[item_id])

class SaleItem(db.Model):
    __tablename__   = "sale_item"
    id              = db.Column(db.Integer, primary_key=True)
    sale_id         = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    sale_price      = db.Column(db.Numeric(14, 4), nullable=False)
    cost_price      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_type   = db.Column(db.String(10), nullable=False, default="percent")
    discount_value  = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_amount = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_amount      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    amount          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    item            = db.relationship("Item", foreign_keys=[item_id])

PAYMENT_METHODS = ("Cash", "Bank", "Cheque", "Online")
ITEM_UNITS = ("Pcs", "Dozen", "Meter", "Kg", "Gram", "Liter", "Box", "Carton", "Bag", "Yard", "Foot", "Set", "Pair", "Roll", "Sheet", "Pack")

class SupplierPayment(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_id         = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    purchase_id         = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=True)
    amount              = db.Column(db.Numeric(14, 4), nullable=False)
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
    amount              = db.Column(db.Numeric(14, 4), nullable=False)
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
    return_price = db.Column(db.Numeric(14, 4), nullable=False)
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
    return_price = db.Column(db.Numeric(14, 4), nullable=False)
    date         = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    reason       = db.Column(db.String(300), nullable=True)
    sale         = db.relationship("Sale", backref="returns", lazy=True)
    customer     = db.relationship("Customer", backref="sale_returns", lazy=True)
    item         = db.relationship("Item", backref="sale_returns", lazy=True)

# ── Stock Adjustment ──────────────────────────────────────────────────────────
ADJUSTMENT_TYPES = ("Stock In", "Stock Out", "Damage Write-off", "Count Correction", "Sample / Free Issue")

class StockAdjustment(db.Model):
    __tablename__ = "stock_adjustment"
    id              = db.Column(db.Integer, primary_key=True)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    adj_type        = db.Column(db.String(30), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    direction       = db.Column(db.String(4), nullable=False, default="in")   # "in" or "out"
    date            = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    reason          = db.Column(db.String(300), nullable=True)
    item            = db.relationship("Item", backref="adjustments", lazy=True)

# ── Expense Tracking ──────────────────────────────────────────────────────────
class ExpenseCategory(db.Model):
    __tablename__ = "expense_category"
    id      = db.Column(db.Integer, primary_key=True)
    name    = db.Column(db.String(100), unique=True, nullable=False)
    expenses = db.relationship("Expense", backref="category", lazy=True)

class Expense(db.Model):
    __tablename__ = "expense"
    id              = db.Column(db.Integer, primary_key=True)
    category_id     = db.Column(db.Integer, db.ForeignKey("expense_category.id"), nullable=True)
    description     = db.Column(db.String(300), nullable=False)
    amount          = db.Column(db.Numeric(14, 4), nullable=False)
    date            = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    payment_method  = db.Column(db.String(20), nullable=False, default="Cash")
    reference_no    = db.Column(db.String(100), nullable=True)
    notes           = db.Column(db.String(300), nullable=True)

class FinancialAccount(db.Model):
    """A cash or bank account. Its live balance is derived from the existing
    payment_method-tagged movements (customer receipts in, supplier payments and
    expenses out) plus an opening balance — no changes to those records needed."""
    __tablename__ = "financial_account"
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(80), nullable=False)          # display name
    method          = db.Column(db.String(20), nullable=False, unique=True)  # payment_method it tracks
    account_type    = db.Column(db.String(10), nullable=False, default="Cash")  # Cash / Bank
    opening_balance = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    is_active       = db.Column(db.Boolean, nullable=False, default=True)

# ── Purchase Order ────────────────────────────────────────────────────────────
PO_STATUSES = ("Draft", "Confirmed", "Received", "Cancelled")

class PurchaseOrder(db.Model):
    __tablename__ = "purchase_order"
    id              = db.Column(db.Integer, primary_key=True)
    supplier_id     = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    order_date      = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    expected_date   = db.Column(db.DateTime, nullable=True)
    status          = db.Column(db.String(20), nullable=False, default="Draft")
    notes           = db.Column(db.String(300), nullable=True)
    converted_purchase_id = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=True)
    supplier        = db.relationship("Supplier", backref="purchase_orders", lazy=True)
    line_items      = db.relationship("PurchaseOrderItem", backref="order", lazy=True,
                                      cascade="all,delete-orphan")

class PurchaseOrderItem(db.Model):
    __tablename__ = "purchase_order_item"
    id              = db.Column(db.Integer, primary_key=True)
    po_id           = db.Column(db.Integer, db.ForeignKey("purchase_order.id"), nullable=False)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    purchase_price  = db.Column(db.Numeric(14, 4), nullable=False)
    item            = db.relationship("Item", foreign_keys=[item_id])

# ── Quotation ─────────────────────────────────────────────────────────────────
QUOTATION_STATUSES = ("Draft", "Sent", "Accepted", "Rejected", "Converted")

class Quotation(db.Model):
    __tablename__ = "quotation"
    id              = db.Column(db.Integer, primary_key=True)
    customer_id     = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    quote_date      = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    valid_until     = db.Column(db.DateTime, nullable=True)
    status          = db.Column(db.String(20), nullable=False, default="Draft")
    notes           = db.Column(db.String(300), nullable=True)
    converted_sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=True)
    customer        = db.relationship("Customer", backref="quotations", lazy=True)
    line_items      = db.relationship("QuotationItem", backref="quotation", lazy=True,
                                      cascade="all,delete-orphan")

class QuotationItem(db.Model):
    __tablename__ = "quotation_item"
    id              = db.Column(db.Integer, primary_key=True)
    quotation_id    = db.Column(db.Integer, db.ForeignKey("quotation.id"), nullable=False)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    sale_price      = db.Column(db.Numeric(14, 4), nullable=False)
    discount_type   = db.Column(db.String(10), nullable=False, default="percent")
    discount_value  = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    item            = db.relationship("Item", foreign_keys=[item_id])

# ── Delivery Challan ──────────────────────────────────────────────────────────
CHALLAN_STATUSES = ("Pending", "Dispatched", "Delivered", "Cancelled")

class DeliveryChallan(db.Model):
    __tablename__ = "delivery_challan"
    id              = db.Column(db.Integer, primary_key=True)
    sale_id         = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False, unique=True)
    challan_date    = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    dispatch_date   = db.Column(db.DateTime, nullable=True)
    delivery_date   = db.Column(db.DateTime, nullable=True)
    status          = db.Column(db.String(20), nullable=False, default="Pending")
    transport       = db.Column(db.String(100), nullable=True)
    notes           = db.Column(db.String(300), nullable=True)
    sale            = db.relationship("Sale", backref=db.backref("delivery_challan", uselist=False), lazy=True)

OPENING_LEDGER_DATE = datetime(1900, 1, 1)

class SupplierLedgerEntry(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_id         = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    entry_date          = db.Column(db.DateTime, nullable=False)
    entry_type          = db.Column(db.String(30), nullable=False)
    source_type         = db.Column(db.String(20), nullable=False)
    source_id           = db.Column(db.Integer, nullable=True)
    description         = db.Column(db.String(300), nullable=False)
    debit               = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    credit              = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    balance_after       = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    supplier            = db.relationship("Supplier", backref="ledger_entries", lazy=True)

class CustomerLedgerEntry(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    entry_date          = db.Column(db.DateTime, nullable=False)
    entry_type          = db.Column(db.String(30), nullable=False)
    source_type         = db.Column(db.String(20), nullable=False)
    source_id           = db.Column(db.Integer, nullable=True)
    description         = db.Column(db.String(300), nullable=False)
    debit               = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    credit              = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    balance_after       = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    customer            = db.relationship("Customer", backref="ledger_entries", lazy=True)

class RateLimitHit(db.Model):
    """One row per throttled action attempt (login, password reset). Shared across
    workers so brute-force limits actually hold. See check_rate_limit()."""
    __tablename__ = "rate_limit_hit"
    id         = db.Column(db.Integer, primary_key=True)
    key        = db.Column(db.String(200), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, index=True,
                           default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class AuditLog(db.Model):
    """Who did what, when — an append-only activity trail for accountability.
    user_name is denormalized so the record still makes sense if the user is
    later deleted. Written via record_audit()."""
    __tablename__ = "audit_log"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, index=True,
                           default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    user_id    = db.Column(db.Integer, nullable=True)
    user_name  = db.Column(db.String(100), nullable=False, default="system")
    action     = db.Column(db.String(20), nullable=False)   # create / update / delete / login / restore
    entity     = db.Column(db.String(50), nullable=False)   # Purchase / Sale / Supplier / ...
    entity_id  = db.Column(db.Integer, nullable=True)
    summary    = db.Column(db.String(300), nullable=False, default="")

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
    purchases = Purchase.query.filter_by(supplier_id=supplier_id).all()
    return sum(purchase_total(p) for p in purchases)

def get_supplier_paid(supplier_id, exclude_payment_id=None):
    query = db.session.query(func.sum(SupplierPayment.amount)).filter(SupplierPayment.supplier_id == supplier_id)
    if exclude_payment_id:
        query = query.filter(SupplierPayment.id != exclude_payment_id)
    return float(query.scalar() or 0.0)

def get_customer_receivable(customer_id):
    sales = Sale.query.filter_by(customer_id=customer_id).all()
    return sum(sale_total(s) for s in sales)

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
def _sum_amount(model, method):
    return float(db.session.query(func.sum(model.amount))
                 .filter(model.payment_method == method).scalar() or 0)

def get_account_balance(account):
    """opening + customer receipts (in) − supplier payments (out) − expenses (out),
    all matched by this account's payment_method. Read-only; touches nothing."""
    m = account.method
    inflow  = _sum_amount(CustomerPayment, m)
    outflow = _sum_amount(SupplierPayment, m) + _sum_amount(Expense, m)
    return float(account.opening_balance or 0) + inflow - outflow

def total_cash_bank_balance():
    return sum(get_account_balance(a) for a in FinancialAccount.query.all())

def account_transactions(account):
    """Chronological list of movements for an account's ledger view."""
    m = account.method
    rows = []
    for r in CustomerPayment.query.filter_by(payment_method=m).all():
        rows.append({"date": r.payment_date, "desc": f"Receipt #{r.id} — {r.customer.name if r.customer else ''}",
                     "inflow": float(r.amount), "outflow": 0.0})
    for p in SupplierPayment.query.filter_by(payment_method=m).all():
        rows.append({"date": p.payment_date, "desc": f"Payment #{p.id} — {p.supplier.name if p.supplier else ''}",
                     "inflow": 0.0, "outflow": float(p.amount)})
    for e in Expense.query.filter_by(payment_method=m).all():
        rows.append({"date": e.date, "desc": f"Expense — {e.description}",
                     "inflow": 0.0, "outflow": float(e.amount)})
    rows.sort(key=lambda x: (x["date"] or datetime.min))
    return rows

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

def get_purchase_item_returned_qty(purchase_id, item_id):
    return int(db.session.query(func.sum(PurchaseReturn.quantity)).filter(
        PurchaseReturn.purchase_id == purchase_id,
        PurchaseReturn.item_id == item_id,
    ).scalar() or 0)

def get_sale_returned_qty(sale_id):
    return int(db.session.query(func.sum(SaleReturn.quantity)).filter(
        SaleReturn.sale_id == sale_id
    ).scalar() or 0)

def get_sale_item_returned_qty(sale_id, item_id):
    return int(db.session.query(func.sum(SaleReturn.quantity)).filter(
        SaleReturn.sale_id == sale_id,
        SaleReturn.item_id == item_id,
    ).scalar() or 0)

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

    # Seed one cash/bank account per payment method (idempotent — only if none exist)
    if FinancialAccount.query.count() == 0:
        types = {"Cash": "Cash", "Bank": "Bank", "Cheque": "Bank", "Online": "Bank"}
        for m in PAYMENT_METHODS:
            db.session.add(FinancialAccount(name=m, method=m, account_type=types.get(m, "Bank"),
                                            opening_balance=0))
        db.session.commit()

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
    ws.cell(row=r, column=2, value=datetime.now().strftime("%Y-%m-%d %H:%M"))
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
        "item_units": ITEM_UNITS,
        "roles": ROLES,
        "company_name": app.config["COMPANY_NAME"],
        "app_name": app.config["APP_NAME"],
        "company_tagline": app.config["COMPANY_TAGLINE"],
        "app_timezone": app.config["APP_TIMEZONE"],
        "designed_developed": app.config["DESIGNED_DEVELOPED"],
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
 
        Thank you for registering with {app.config['APP_NAME']}. Please click the link below to verify your email address:

        {verification_url}

        This link will expire in 1 hour.

        Regards,
        {app.config['APP_NAME']} Team
        """
        if send_email(email, f"Verify Your Email - {app.config['APP_NAME']}", body):
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
        if not check_rate_limit(f"signin:{request.remote_addr}"):
            flash("Too many login attempts. Please try again in a few minutes.", "danger")
            return render_template("signin.html", just_reset=session.get("just_reset_email"))
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
                    record_audit("login", "User", user.id, f"Signed in from {request.remote_addr}")
                    app.logger.info("Login OK: %s (role=%s) from %s", email, user.role, request.remote_addr)
                    flash("Signed in successfully!", "success")
                    return redirect(url_for("index"))
                app.logger.warning("Login FAILED (bad password): %s from %s", email, request.remote_addr)
                flash("Invalid email or password!", "danger")
            except Exception as e:
                app.logger.exception("Login error for %s: %s", email, e)
                flash("Invalid email or password!", "danger")
        else:
            app.logger.warning("Login FAILED (unknown email): %s from %s", email, request.remote_addr)
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
        if not check_rate_limit(f"forgot_password:{request.remote_addr}"):
            flash("Too many attempts. Please try again in a few minutes.", "danger")
            return render_template("forgot_password.html")
        user = User.query.filter_by(email=email).first()
        generic_msg = "If that email is registered, a password reset link has been sent. Check your inbox and spam/junk folder."
        if not user:
            flash(generic_msg, "success")
        else:
            token = secrets.token_urlsafe(32)
            expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            reset_url = url_for("reset_password", token=token, _external=True)
            body = (
                f"Dear User,\n\n"
                f"To reset your password, open this link:\n{reset_url}\n\n"
                f"This link expires in 1 hour. If you did not request this, ignore this email.\n\n"
                f"Regards,\n{app.config['APP_NAME']} Team"
            )
            if send_email(email, f"Password Reset Request - {app.config['APP_NAME']}", body):
                user.reset_token = token
                user.reset_token_expiry = expiry
                db.session.commit()
                flash(generic_msg, "success")
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
        {app.config['APP_NAME']} Team
        """
        if send_email(email, f"Verify Your Email - {app.config['APP_NAME']}", body):
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
    return render_template("about2.html")

@app.route('/dashboard')
@manager_required
def dashboard():
    items = Item.query.all()
    purchases = Purchase.query.order_by(Purchase.date.desc()).limit(5).all()
    sales = Sale.query.order_by(Sale.date.desc()).limit(5).all()
    total_purchase_cost = db.session.query(func.sum(PurchaseItem.amount)).scalar() or 0.0
    total_sale_revenue = db.session.query(func.sum(SaleItem.amount)).scalar() or 0.0
    _profit_expr = SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.cost_price
    total_gross_profit = db.session.query(func.sum(_profit_expr)).scalar() or 0.0
    total_purchase_returns = db.session.query(func.sum(PurchaseReturn.quantity * PurchaseReturn.return_price)).scalar() or 0.0
    total_sale_returns = db.session.query(func.sum(SaleReturn.quantity * SaleReturn.return_price)).scalar() or 0.0
    low_stock_count = Item.query.filter(Item.stock <= Item.reorder_level).count()
    total_payable = get_total_payable()
    total_paid_suppliers = get_total_paid_suppliers()
    total_receivable = get_total_receivable()
    total_received_customers = get_total_received_customers()
    total_payable_balance = total_supplier_ledger_balance()
    total_receivable_balance = total_customer_ledger_balance()
    monthly_sales = (
        db.session.query(
            sql_date_fmt(Sale.date).label("month"),
            db.func.sum(SaleItem.amount).label("sale_amt"),
            db.func.sum(_profit_expr).label("profit_amt"),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .group_by(sql_date_fmt(Sale.date))
        .order_by(sql_date_fmt(Sale.date))
        .limit(12)
        .all()
    )
    monthly_purchases = (
        db.session.query(
            sql_date_fmt(Purchase.date).label("month"),
            db.func.sum(PurchaseItem.amount).label("purchase_amt"),
        )
        .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
        .group_by(sql_date_fmt(Purchase.date))
        .order_by(sql_date_fmt(Purchase.date))
        .limit(12)
        .all()
    )
    return render_template(
        'dashboard.html',
        items=items,
        purchases=purchases,
        sales=sales,
        total_purchase_cost=total_purchase_cost,
        total_sale_revenue=total_sale_revenue,
        total_gross_profit=total_gross_profit,
        total_purchase_returns=total_purchase_returns,
        total_sale_returns=total_sale_returns,
        low_stock_count=low_stock_count,
        total_payable=total_payable,
        total_paid_suppliers=total_paid_suppliers,
        total_payable_balance=total_payable_balance,
        total_receivable=total_receivable,
        total_received_customers=total_received_customers,
        total_receivable_balance=total_receivable_balance,
        monthly_sales=monthly_sales,
        monthly_purchases=monthly_purchases,
    )

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
        elif not supplier.contact.isdigit() or len(supplier.contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        else:
            supplier.opening_balance = float(opening_str or 0)
            sync_supplier_opening(supplier)
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
        elif not customer.contact.isdigit() or len(customer.contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        else:
            customer.opening_balance = float(opening_str or 0)
            sync_customer_opening(customer)
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
            )
            db.session.add(item)
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
        else:
            new_os = int(opening_stock)
            stock_adjustment = new_os - item.opening_stock
            item.name = name
            item.category_id = int(category_id)
            item.unit = unit
            item.opening_stock = new_os
            item.stock = item.stock + stock_adjustment
            item.reorder_level = int(reorder_level)
            item.purchase_price = float(purchase_price) if purchase_price else None
            item.sale_price = float(sale_price) if sale_price else None
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

    purchase_items   = PurchaseItem.query.filter_by(item_id=id).all()
    sale_items       = SaleItem.query.filter_by(item_id=id).all()
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id).all()

    entries = []
    for pi in purchase_items:
        entries.append({
            "date": pi.purchase_header.date, "type": "Purchase", "badge": "success",
            "ref": f"PO #{pi.purchase_header.id}", "party": pi.purchase_header.id_supplier.name,
            "stock_in": pi.quantity, "stock_out": 0,
            "rate": pi.purchase_price, "value": purchase_item_total(pi),
        })
    for si in sale_items:
        entries.append({
            "date": si.sale_header.date, "type": "Sale", "badge": "primary",
            "ref": f"SO #{si.sale_header.id}", "party": si.sale_header.id_customer.name,
            "stock_in": 0, "stock_out": si.quantity,
            "rate": si.sale_price, "value": sale_item_total(si),
        })
    for pr in purchase_returns:
        entries.append({
            "date": pr.date, "type": "Purchase Return", "badge": "warning",
            "ref": f"PR #{pr.id}", "party": pr.supplier.name,
            "stock_in": 0, "stock_out": pr.quantity,
            "rate": pr.return_price, "value": round(pr.quantity * pr.return_price, 2),
        })
    for sr in sale_returns:
        entries.append({
            "date": sr.date, "type": "Sale Return", "badge": "secondary",
            "ref": f"SR #{sr.id}", "party": sr.customer.name,
            "stock_in": sr.quantity, "stock_out": 0,
            "rate": sr.return_price, "value": round(sr.quantity * sr.return_price, 2),
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
    purchase_items   = PurchaseItem.query.filter_by(item_id=id).all()
    sale_items       = SaleItem.query.filter_by(item_id=id).all()
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id).all()

    rows = []
    for pi in purchase_items:
        rows.append((pi.purchase_header.date, "Purchase", f"PO #{pi.purchase_header.id}", pi.purchase_header.id_supplier.name, pi.quantity, 0, pi.purchase_price, purchase_item_total(pi)))
    for si in sale_items:
        rows.append((si.sale_header.date, "Sale", f"SO #{si.sale_header.id}", si.sale_header.id_customer.name, 0, si.quantity, si.sale_price, sale_item_total(si)))
    for pr in purchase_returns:
        rows.append((pr.date, "Purchase Return", f"PR #{pr.id}", pr.supplier.name, 0, pr.quantity, pr.return_price, round(pr.quantity * pr.return_price, 2)))
    for sr in sale_returns:
        rows.append((sr.date, "Sale Return", f"SR #{sr.id}", sr.customer.name, sr.quantity, 0, sr.return_price, round(sr.quantity * sr.return_price, 2)))

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
    purchase_items   = PurchaseItem.query.filter_by(item_id=id).all()
    sale_items       = SaleItem.query.filter_by(item_id=id).all()
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id).all()

    raw = []
    for pi in purchase_items:
        raw.append((pi.purchase_header.date, "Purchase", f"PO #{pi.purchase_header.id}", pi.purchase_header.id_supplier.name, pi.quantity, 0, pi.purchase_price, purchase_item_total(pi)))
    for si in sale_items:
        raw.append((si.sale_header.date, "Sale", f"SO #{si.sale_header.id}", si.sale_header.id_customer.name, 0, si.quantity, si.sale_price, sale_item_total(si)))
    for pr in purchase_returns:
        raw.append((pr.date, "Purchase Return", f"PR #{pr.id}", pr.supplier.name, 0, pr.quantity, pr.return_price, round(pr.quantity * pr.return_price, 2)))
    for sr in sale_returns:
        raw.append((sr.date, "Sale Return", f"SR #{sr.id}", sr.customer.name, sr.quantity, 0, sr.return_price, round(sr.quantity * sr.return_price, 2)))

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

        rows = []
        for i, (iid, qty, price) in enumerate(zip(item_ids, quantities, prices)):
            if iid.strip() and qty.strip() and price.strip():
                rows.append((
                    iid.strip(), qty.strip(), price.strip(),
                    disc_types[i] if i < len(disc_types) else "percent",
                    disc_values[i] if i < len(disc_values) else "0",
                    tax_pcts[i] if i < len(tax_pcts) else "0",
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
                for iid, qty, price, d_type, d_val, tax in rows:
                    item_obj = get_item_locked(int(iid)) or abort(404)   # lock: no lost stock updates
                    qty_i  = int(qty)
                    price_f = float(price)
                    d_val_f = float(d_val or 0)
                    tax_f   = float(tax or 0)
                    gross   = qty_i * price_f
                    disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                    pi = PurchaseItem(
                        purchase_id=pur.id, item_id=int(iid),
                        quantity=qty_i, purchase_price=price_f,
                        discount_type=d_type or "percent", discount_value=d_val_f,
                        discount_amount=disc_amt, tax_percent=tax_f,
                        tax_amount=tax_amt, amount=net,
                    )
                    db.session.add(pi)
                    item_obj.stock += qty_i
                db.session.flush()
                db.session.refresh(pur)
                sync_supplier_purchase(pur)
                db.session.commit()
                record_audit("create", "Purchase", pur.id, f"Purchase #{pur.id}, total {purchase_total(pur):,.2f}")
                flash("Purchase added successfully!", "success")
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
        today=datetime.now().strftime("%Y-%m-%d"),
    )

@app.route("/purchase/edit/<int:id>", methods=["GET", "POST"])
@manager_required
def edit_purchase(id):
    pur = db.session.get(Purchase, id) or abort(404)
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

        rows = []
        for i, (iid, qty, price) in enumerate(zip(item_ids, quantities, prices)):
            if iid.strip() and qty.strip() and price.strip():
                rows.append((
                    iid.strip(), qty.strip(), price.strip(),
                    disc_types[i] if i < len(disc_types) else "percent",
                    disc_values[i] if i < len(disc_values) else "0",
                    tax_pcts[i] if i < len(tax_pcts) else "0",
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
                # Reverse old stock
                touched_items = {}
                for pi in pur.line_items:
                    old_item = db.session.get(Item, pi.item_id)
                    if old_item:
                        old_item.stock -= pi.quantity
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
                for iid, qty, price, d_type, d_val, tax in rows:
                    item_obj = db.session.get(Item, int(iid)) or abort(404)
                    qty_i = int(qty); price_f = float(price)
                    d_val_f = float(d_val or 0); tax_f = float(tax or 0)
                    gross = qty_i * price_f
                    disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                    pi = PurchaseItem(
                        purchase_id=pur.id, item_id=int(iid),
                        quantity=qty_i, purchase_price=price_f,
                        discount_type=d_type or "percent", discount_value=d_val_f,
                        discount_amount=disc_amt, tax_percent=tax_f,
                        tax_amount=tax_amt, amount=net,
                    )
                    db.session.add(pi)
                    item_obj.stock += qty_i
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
            item_obj.stock -= pi.quantity
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

        rows = []
        for i, (iid, qty, price) in enumerate(zip(item_ids, quantities, prices)):
            if iid.strip() and qty.strip() and price.strip():
                rows.append((
                    iid.strip(), qty.strip(), price.strip(),
                    disc_types[i] if i < len(disc_types) else "percent",
                    disc_values[i] if i < len(disc_values) else "0",
                    tax_pcts[i] if i < len(tax_pcts) else "0",
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
                for iid, qty, price, *_ in rows:
                    item_obj = db.session.get(Item, int(iid))
                    if item_obj and item_obj.stock < int(qty):
                        stock_errors.append(f"{item_obj.name}: only {item_obj.stock} available")
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
                    for iid, qty, price, d_type, d_val, tax in rows:
                        item_obj = get_item_locked(int(iid)) or abort(404)
                        qty_i = int(qty); price_f = float(price)
                        # Authoritative check under the row lock — a concurrent sale
                        # may have reduced stock since the pre-check above.
                        if item_obj.stock < qty_i:
                            db.session.rollback()
                            flash(f"Insufficient stock for {item_obj.name}: only {item_obj.stock} "
                                  "available now (it changed while saving). Please try again.", "danger")
                            return redirect(url_for("sale"))
                        d_val_f = float(d_val or 0); tax_f = float(tax or 0)
                        gross = qty_i * price_f
                        disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                        si = SaleItem(
                            sale_id=sal.id, item_id=int(iid),
                            quantity=qty_i, sale_price=price_f,
                            cost_price=float(item_obj.purchase_price or 0),
                            discount_type=d_type or "percent", discount_value=d_val_f,
                            discount_amount=disc_amt, tax_percent=tax_f,
                            tax_amount=tax_amt, amount=net,
                        )
                        db.session.add(si)
                        item_obj.stock -= qty_i
                    db.session.flush()
                    db.session.refresh(sal)
                    sync_customer_sale(sal)
                    db.session.commit()
                    record_audit("create", "Sale", sal.id, f"Sale #{sal.id}, total {sale_total(sal):,.2f}")
                    flash("Sale recorded successfully!", "success")
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
        today=datetime.now().strftime("%Y-%m-%d"),
    )

@app.route("/sale/edit/<int:id>", methods=["GET", "POST"])
@manager_required
def edit_sale(id):
    sal = db.session.get(Sale, id) or abort(404)
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

        rows = []
        for i, (iid, qty, price) in enumerate(zip(item_ids, quantities, prices)):
            if iid.strip() and qty.strip() and price.strip():
                rows.append((
                    iid.strip(), qty.strip(), price.strip(),
                    disc_types[i] if i < len(disc_types) else "percent",
                    disc_values[i] if i < len(disc_values) else "0",
                    tax_pcts[i] if i < len(tax_pcts) else "0",
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
                # Restore old stock
                for si in sal.line_items:
                    old_item = db.session.get(Item, si.item_id)
                    if old_item:
                        old_item.stock += si.quantity
                # Check new stock
                stock_errors = []
                for iid, qty, price, *_ in rows:
                    item_obj = db.session.get(Item, int(iid))
                    if item_obj and item_obj.stock < int(qty):
                        stock_errors.append(f"{item_obj.name}: only {item_obj.stock} available")
                if stock_errors:
                    # Undo stock restoration
                    for si in sal.line_items:
                        old_item = db.session.get(Item, si.item_id)
                        if old_item:
                            old_item.stock -= si.quantity
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
                    for iid, qty, price, d_type, d_val, tax in rows:
                        item_obj = db.session.get(Item, int(iid)) or abort(404)
                        qty_i = int(qty); price_f = float(price)
                        d_val_f = float(d_val or 0); tax_f = float(tax or 0)
                        gross = qty_i * price_f
                        disc_amt, tax_amt, net = calc_discount_tax(gross, d_type or "percent", d_val_f, tax_f)
                        si = SaleItem(
                            sale_id=sal.id, item_id=int(iid),
                            quantity=qty_i, sale_price=price_f,
                            cost_price=float(item_obj.purchase_price or 0),
                            discount_type=d_type or "percent", discount_value=d_val_f,
                            discount_amount=disc_amt, tax_percent=tax_f,
                            tax_amount=tax_amt, amount=net,
                        )
                        db.session.add(si)
                        item_obj.stock -= qty_i
                    db.session.flush()
                    db.session.refresh(sal)
                    if old_customer_id != int(customer_id):
                        remove_customer_ledger_entry("sale", sal.id)
                        recalculate_customer_ledger(old_customer_id)
                    sync_customer_sale(sal)
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
            item_obj.stock += si.quantity
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

        if not sup_id or not date_str:
            flash("Supplier and payment date are required!", "danger")
        elif method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
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
                            reference_no=reference_no or None,
                            notes=notes or None,
                        )
                        db.session.add(pmt)
                        db.session.flush()
                        sync_supplier_payment(pmt)
                        count += 1
                        total_paid_sum += amt
                    if gen_amt > 0:
                        pmt = SupplierPayment(
                            supplier_id=int(sup_id),
                            purchase_id=None,
                            amount=gen_amt,
                            payment_date=pay_date,
                            payment_method=method,
                            reference_no=reference_no or None,
                            notes=notes or None,
                        )
                        db.session.add(pmt)
                        db.session.flush()
                        sync_supplier_payment(pmt)
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
        today=datetime.now().strftime("%Y-%m-%d"),
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

        if not cust_id or not date_str:
            flash("Customer and receipt date are required!", "danger")
        elif method not in PAYMENT_METHODS:
            flash("Invalid payment method!", "danger")
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
                            reference_no=reference_no or None,
                            notes=notes or None,
                        )
                        db.session.add(rcpt)
                        db.session.flush()
                        sync_customer_receipt(rcpt)
                        count += 1
                        total_recv_sum += amt
                    if gen_amt > 0:
                        rcpt = CustomerPayment(
                            customer_id=int(cust_id),
                            sale_id=None,
                            amount=gen_amt,
                            payment_date=pay_date,
                            payment_method=method,
                            reference_no=reference_no or None,
                            notes=notes or None,
                        )
                        db.session.add(rcpt)
                        db.session.flush()
                        sync_customer_receipt(rcpt)
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
        today=datetime.now().strftime("%Y-%m-%d"),
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

@app.route("/profile", methods=["GET", "POST"])
@verified_required
def profile():
    if request.method == "POST":
        current_password = request.form.get("current_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        if not current_password or not new_password or not confirm_password:
            flash("All fields are required!", "danger")
        elif not pwd_context.verify(current_password, current_user.password):
            flash("Current password is incorrect!", "danger")
        elif len(new_password) < 6:
            flash("New password must be at least 6 characters!", "danger")
        elif new_password != confirm_password:
            flash("New passwords do not match!", "danger")
        else:
            current_user.password = pwd_context.hash(new_password)
            db.session.commit()
            flash("Password changed successfully!", "success")
            return redirect(url_for("profile"))
    return render_template("profile.html")

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
    purchase_report = sale_report = reorder_report = date_profit_report = item_profit = customer_profit = category_profit = []
    supplier_balances = customer_balances = supplier_payment_history = customer_receipt_history = []
    purchase_return_report = sale_return_report = supplier_purchase_report = stock_report = []
    total_sale_amt = total_profit_amt = total_purchase_cost = 0
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
                reorder_report     = Item.query.filter(Item.stock <= Item.reorder_level).all()
                purchase_return_report = PurchaseReturn.query.filter(PurchaseReturn.date.between(start_date, end_date)).order_by(PurchaseReturn.date.desc()).all()
                sale_return_report = SaleReturn.query.filter(SaleReturn.date.between(start_date, end_date)).order_by(SaleReturn.date.desc()).all()
                stock_report       = Item.query.outerjoin(Category, Item.category_id == Category.id).order_by(Category.name, Item.name).all()
                # sale_amt  = gross - discount + tax  (what customer pays = net total) = SaleItem.amount
                # profit    = gross - discount - cogs (tax excluded from profit)
                _sale_net  = SaleItem.amount
                _sale_prof = SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.cost_price
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
                        db.func.sum(PurchaseItem.quantity).label("total_qty"),
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
                        db.func.sum(SaleItem.quantity * SaleItem.cost_price).label("total_purchase_cost"),
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
                purchase_qty_total  = sum(pi.quantity for p in purchase_report for pi in p.line_items)
                purchase_amt_total  = sum(purchase_total(p) for p in purchase_report)
                sale_qty_total      = sum(si.quantity for s in sale_report for si in s.line_items)
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
        reorder_report=reorder_report,
        purchase_return_report=purchase_return_report,
        sale_return_report=sale_return_report,
        supplier_purchase_report=supplier_purchase_report,
        stock_report=stock_report,
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
        purchases = (
            Purchase.query.join(Supplier)
            .join(Item)
            .filter(Purchase.date.between(start_date, end_date))
            .all()
        )
        col_headers = ["ID", "Supplier", "Item", "Category", "Quantity", "Purchase Price", "Total", "Date"]
        rows = [
            [p.id, p.id_supplier.name, p.id_item.name,
             p.id_item.id_category.name if p.id_item.id_category else "N/A",
             p.quantity, round(p.purchase_price, 2),
             round(p.quantity * p.purchase_price, 2), p.date.strftime("%Y-%m-%d")]
            for p in purchases
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
        sales = (
            Sale.query.join(Customer)
            .join(Item)
            .filter(Sale.date.between(start_date, end_date))
            .all()
        )
        col_headers = ["ID", "Customer", "Item", "Category", "Quantity", "Sale Price", "Total", "Date"]
        rows = [
            [s.id, s.id_customer.name, s.id_item.name,
             s.id_item.id_category.name if s.id_item.id_category else "N/A",
             s.quantity, round(s.sale_price, 2),
             round(s.quantity * s.sale_price, 2), s.date.strftime("%Y-%m-%d")]
            for s in sales
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
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.cost_price).label("profit_amt"),
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
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.cost_price).label("profit_amt"),
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
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.cost_price).label("profit_amt"),
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
                db.func.sum(SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.cost_price).label("profit_amt"),
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
                db.func.sum(PurchaseItem.quantity).label("total_qty"),
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
    col_headers = ["Item", "Category", "Stock", "Reorder Level", "Purchase Price", "Sale Price", "Stock Value", "Status"]
    rows = []
    for item in items:
        rows.append([
            item.name, item.id_category.name if item.id_category else "N/A",
            item.stock, item.reorder_level,
            round(item.purchase_price or 0, 2), round(item.sale_price or 0, 2),
            round((item.stock or 0) * (item.purchase_price or 0), 2),
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
    all_pis = PurchaseItem.query.join(Purchase).order_by(Purchase.date.desc(), PurchaseItem.id).all()
    items_available = [
        {"pi": pi, "remaining": pi.quantity - get_purchase_item_returned_qty(pi.purchase_id, pi.item_id)}
        for pi in all_pis
        if pi.quantity - get_purchase_item_returned_qty(pi.purchase_id, pi.item_id) > 0
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
                    remaining = pi.quantity - get_purchase_item_returned_qty(pi.purchase_id, pi.item_id)
                    if int(qty_s) > remaining:
                        errors.append(f"Row {idx} ({pi.item.name}): cannot return {qty_s}, only {remaining} remaining.")
                        continue
                    if pi.item and pi.item.stock < int(qty_s):
                        errors.append(f"Row {idx} ({pi.item.name}): only {pi.item.stock} in current stock, cannot return {qty_s}.")
                        continue
                    rows.append((pi, int(qty_s), price_f, reason_s.strip() or None))
                if errors:
                    for e in errors:
                        flash(e, "danger")
                else:
                    for pi, qty, price, reason_val in rows:
                        purchase = pi.purchase_header
                        item = db.session.get(Item, pi.item_id)
                        pr = PurchaseReturn(
                            purchase_id=purchase.id,
                            supplier_id=purchase.supplier_id,
                            item_id=pi.item_id,
                            quantity=qty,
                            return_price=price,
                            date=ret_date,
                            reason=reason_val,
                        )
                        if item:
                            item.stock -= qty
                        db.session.add(pr)
                        db.session.flush()
                        sync_supplier_purchase_return(pr)
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
        today=datetime.now().strftime("%Y-%m-%d"),
    )

@app.route("/purchase_return/delete/<int:id>", methods=["POST"])
@admin_required
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
    all_sis = SaleItem.query.join(Sale).order_by(Sale.date.desc(), SaleItem.id).all()
    items_available = [
        {"si": si, "remaining": si.quantity - get_sale_item_returned_qty(si.sale_id, si.item_id)}
        for si in all_sis
        if si.quantity - get_sale_item_returned_qty(si.sale_id, si.item_id) > 0
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
                    remaining = si.quantity - get_sale_item_returned_qty(si.sale_id, si.item_id)
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
                        item = db.session.get(Item, si.item_id)
                        sr = SaleReturn(
                            sale_id=sale.id,
                            customer_id=sale.customer_id,
                            item_id=si.item_id,
                            quantity=qty,
                            return_price=price,
                            date=ret_date,
                            reason=reason_val,
                        )
                        if item:
                            item.stock += qty
                        db.session.add(sr)
                        db.session.flush()
                        sync_customer_sale_return(sr)
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
        today=datetime.now().strftime("%Y-%m-%d"),
    )

@app.route("/sale_return/delete/<int:id>", methods=["POST"])
@admin_required
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
        elif not db.session.get(Item, int(item_id)):
            flash("Item not found.", "danger")
        else:
            item_obj = db.session.get(Item, int(item_id))
            qty = int(qty_str)
            direction = "out" if adj_type in ("Stock Out", "Damage Write-off", "Sample / Free Issue") else "in"
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
                if direction == "out":
                    item_obj.stock -= qty
                else:
                    item_obj.stock += qty
                db.session.commit()
                flash(f"Stock {'reduced' if direction=='out' else 'increased'} by {qty} for {item_obj.name}.", "success")
                return redirect(url_for("stock_adjustment"))
    return render_template("stock_adjustment.html",
        adjustments=adjustments, items=items, pagination=pagination,
        search=search, adj_types=ADJUSTMENT_TYPES,
        today=datetime.now().strftime("%Y-%m-%d"))

@app.route("/stock_adjustment/delete/<int:id>", methods=["POST"])
@admin_required
def delete_stock_adjustment(id):
    adj = db.session.get(StockAdjustment, id) or abort(404)
    item_obj = db.session.get(Item, adj.item_id)
    if item_obj:
        if adj.direction == "out":
            item_obj.stock += adj.quantity
        else:
            item_obj.stock -= adj.quantity
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
            if not cat_name:
                flash("Category name is required.", "danger")
            elif ExpenseCategory.query.filter_by(name=cat_name).first():
                flash("Category already exists.", "warning")
            else:
                db.session.add(ExpenseCategory(name=cat_name))
                db.session.commit()
                flash(f"Category '{cat_name}' added.", "success")
            return redirect(url_for("expenses"))
        # add expense
        desc       = request.form.get("description", "").strip()
        amount_str = request.form.get("amount", "").strip()
        date_str   = request.form.get("date", "").strip()
        method     = request.form.get("payment_method", "Cash").strip()
        cat_id     = request.form.get("category_id", "").strip() or None
        ref        = request.form.get("reference_no", "").strip() or None
        notes      = request.form.get("notes", "").strip() or None
        if not desc or not amount_str or not date_str:
            flash("Description, amount and date are required.", "danger")
        else:
            try:
                amount = float(amount_str)
                if amount <= 0:
                    flash("Amount must be positive.", "danger")
                else:
                    db.session.add(Expense(
                        category_id=int(cat_id) if cat_id else None,
                        description=desc, amount=amount,
                        date=datetime.strptime(date_str, "%Y-%m-%d"),
                        payment_method=method, reference_no=ref, notes=notes,
                    ))
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
        total_expenses=total_expenses,
        today=datetime.now().strftime("%Y-%m-%d"))

@app.route("/expenses/delete/<int:id>", methods=["POST"])
@admin_required
def delete_expense(id):
    exp = db.session.get(Expense, id) or abort(404)
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
        rows = [(iid.strip(), qty.strip(), pr.strip())
                for iid, qty, pr in zip(item_ids, quantities, prices)
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
            for iid, qty, price in rows:
                db.session.add(PurchaseOrderItem(
                    po_id=po.id, item_id=int(iid),
                    quantity=int(qty), purchase_price=float(price),
                ))
            db.session.commit()
            flash(f"Purchase Order #{po.id} created.", "success")
            return redirect(url_for("purchase_orders"))
    return render_template("purchase_orders.html",
        orders=orders, suppliers=suppliers, items=items,
        pagination=pagination, search=search,
        po_statuses=PO_STATUSES,
        today=datetime.now().strftime("%Y-%m-%d"))

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
        pur_date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    except ValueError:
        pur_date = datetime.now()
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
        ))
        item_obj = db.session.get(Item, poi.item_id)
        if item_obj:
            item_obj.stock += poi.quantity
    db.session.flush()
    db.session.refresh(pur)
    sync_supplier_purchase(pur)
    po.status = "Received"
    po.converted_purchase_id = pur.id
    db.session.commit()
    flash(f"PO #{po.id} converted to Purchase #{pur.id} successfully.", "success")
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
        rows = []
        for i, (iid, qty, price) in enumerate(zip(item_ids, quantities, prices)):
            if iid.strip() and qty.strip() and price.strip():
                rows.append((iid.strip(), qty.strip(), price.strip(),
                    disc_types[i] if i < len(disc_types) else "percent",
                    disc_values[i] if i < len(disc_values) else "0",
                    tax_pcts[i] if i < len(tax_pcts) else "0"))
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
            for iid, qty, price, d_type, d_val, tax in rows:
                db.session.add(QuotationItem(
                    quotation_id=q.id, item_id=int(iid),
                    quantity=int(qty), sale_price=float(price),
                    discount_type=d_type or "percent",
                    discount_value=float(d_val or 0),
                    tax_percent=float(tax or 0),
                ))
            db.session.commit()
            flash(f"Quotation #{q.id} created.", "success")
            return redirect(url_for("quotations"))
    return render_template("quotations.html",
        quotes=quotes, customers=customers, items=items,
        pagination=pagination, search=search,
        quote_statuses=QUOTATION_STATUSES,
        today=datetime.now().strftime("%Y-%m-%d"))

@app.route("/quotations/<int:id>")
@manager_required
def quotation_detail(id):
    q = db.session.get(Quotation, id) or abort(404)
    def q_item_net(qi):
        gross = qi.quantity * qi.sale_price
        _, _, net = calc_discount_tax(gross, qi.discount_type, qi.discount_value, qi.tax_percent)
        return net
    total = sum(q_item_net(qi) for qi in q.line_items)
    return render_template("quotation_detail.html", q=q, total=total,
                           q_item_net=q_item_net, quote_statuses=QUOTATION_STATUSES)

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
        sal_date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    except ValueError:
        sal_date = datetime.now()
    # stock check
    stock_errors = []
    for qi in q.line_items:
        item_obj = db.session.get(Item, qi.item_id)
        if item_obj and item_obj.stock < qi.quantity:
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
        db.session.add(SaleItem(
            sale_id=sal.id, item_id=qi.item_id,
            quantity=qi.quantity, sale_price=qi.sale_price,
            cost_price=float(item_obj.purchase_price or 0) if item_obj else 0,
            discount_type=qi.discount_type, discount_value=qi.discount_value,
            discount_amount=disc_amt, tax_percent=qi.tax_percent,
            tax_amount=tax_amt, amount=net,
        ))
        if item_obj:
            item_obj.stock -= qi.quantity
    db.session.flush()
    db.session.refresh(sal)
    sync_customer_sale(sal)
    q.status = "Converted"
    q.converted_sale_id = sal.id
    db.session.commit()
    flash(f"Quotation #{q.id} converted to Sale #{sal.id}.", "success")
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
        today=datetime.now().strftime("%Y-%m-%d"))

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
    today = datetime.now().date()

    def age_bucket(date_val):
        days = (today - date_val.date()).days
        if days <= 30:   return "0-30"
        elif days <= 60: return "31-60"
        elif days <= 90: return "61-90"
        else:            return "90+"

    # AP Aging (Suppliers)
    ap_rows = []
    for sup in Supplier.query.order_by(Supplier.name).all():
        buckets = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        for pur in Purchase.query.filter_by(supplier_id=sup.id).all():
            due = purchase_total(pur) - get_purchase_paid(pur.id)
            if due > 0.01:
                buckets[age_bucket(pur.date)] += due
        total_due = sum(buckets.values())
        if total_due > 0.01:
            ap_rows.append({"name": sup.name, "buckets": buckets, "total": total_due})

    # AR Aging (Customers)
    ar_rows = []
    for cust in Customer.query.order_by(Customer.name).all():
        buckets = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        for sal in Sale.query.filter_by(customer_id=cust.id).all():
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
    start_str = request.args.get("start", "")
    end_str   = request.args.get("end", "")
    today     = datetime.now()
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d") if start_str else datetime(today.year, 1, 1)
        end   = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_str else today
    except ValueError:
        start = datetime(today.year, 1, 1)
        end   = today

    # Sales revenue & COGS from SaleItem
    sales_q = Sale.query.filter(Sale.date >= start, Sale.date <= end).all()
    revenue  = sum(sale_total(s) for s in sales_q)
    cogs     = sum(
        float(si.cost_price * si.quantity)
        for s in sales_q for si in s.line_items
    )
    gross_profit = revenue - cogs

    # Purchase returns reduce COGS
    pr_total = float(db.session.query(func.sum(PurchaseReturn.quantity * PurchaseReturn.return_price))
        .filter(PurchaseReturn.date >= start, PurchaseReturn.date <= end).scalar() or 0)
    # Sale returns reduce revenue
    sr_total = float(db.session.query(func.sum(SaleReturn.quantity * SaleReturn.return_price))
        .filter(SaleReturn.date >= start, SaleReturn.date <= end).scalar() or 0)

    net_revenue     = revenue - sr_total
    adj_gross       = net_revenue - (cogs - pr_total)

    # Expenses
    expense_rows = (
        db.session.query(
            ExpenseCategory.name,
            func.sum(Expense.amount).label("total")
        )
        .outerjoin(ExpenseCategory, Expense.category_id == ExpenseCategory.id)
        .filter(Expense.date >= start, Expense.date <= end)
        .group_by(ExpenseCategory.name)
        .all()
    )
    total_expenses = float(db.session.query(func.sum(Expense.amount))
        .filter(Expense.date >= start, Expense.date <= end).scalar() or 0)
    net_profit = adj_gross - total_expenses

    return render_template("report_pl.html",
        start=start, end=end,
        revenue=revenue, sr_total=sr_total, net_revenue=net_revenue,
        cogs=cogs, pr_total=pr_total, adj_gross=adj_gross,
        expense_rows=expense_rows, total_expenses=total_expenses,
        net_profit=net_profit)

@app.route("/reports/cash_book")
@manager_required
def report_cash_book():
    start_str = request.args.get("start", "")
    end_str   = request.args.get("end", "")
    method_filter = request.args.get("method", "")
    today = datetime.now()
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d") if start_str else datetime(today.year, today.month, 1)
        end   = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_str else today
    except ValueError:
        start = datetime(today.year, today.month, 1)
        end   = today

    entries = []

    # Supplier payments (cash out)
    sp_q = SupplierPayment.query.filter(
        SupplierPayment.payment_date >= start,
        SupplierPayment.payment_date <= end,
    )
    if method_filter:
        sp_q = sp_q.filter(SupplierPayment.payment_method == method_filter)
    for p in sp_q.all():
        entries.append({
            "date": p.payment_date, "type": "Supplier Payment",
            "description": f"{p.supplier.name}" + (f" — Bill #{p.purchase_id}" if p.purchase_id else ""),
            "method": p.payment_method, "out": p.amount, "in": 0,
        })

    # Customer receipts (cash in)
    cr_q = CustomerPayment.query.filter(
        CustomerPayment.payment_date >= start,
        CustomerPayment.payment_date <= end,
    )
    if method_filter:
        cr_q = cr_q.filter(CustomerPayment.payment_method == method_filter)
    for r in cr_q.all():
        entries.append({
            "date": r.payment_date, "type": "Customer Receipt",
            "description": f"{r.customer.name}" + (f" — Sale #{r.sale_id}" if r.sale_id else ""),
            "method": r.payment_method, "in": r.amount, "out": 0,
        })

    # Expenses (cash out)
    exp_q = Expense.query.filter(Expense.date >= start, Expense.date <= end)
    if method_filter:
        exp_q = exp_q.filter(Expense.payment_method == method_filter)
    for e in exp_q.all():
        entries.append({
            "date": e.date, "type": "Expense",
            "description": e.description,
            "method": e.payment_method, "out": e.amount, "in": 0,
        })

    entries.sort(key=lambda x: x["date"])
    total_in  = sum(e["in"]  for e in entries)
    total_out = sum(e["out"] for e in entries)
    net       = total_in - total_out

    return render_template("report_cash_book.html",
        entries=entries, start=start, end=end,
        total_in=total_in, total_out=total_out, net=net,
        method_filter=method_filter, payment_methods=PAYMENT_METHODS)

# ─── Cash & Bank Accounts ───────────────────────────────────────────────────────
@app.route("/accounts")
@manager_required
def accounts():
    accts = FinancialAccount.query.order_by(FinancialAccount.id).all()
    rows = [{"acct": a, "balance": get_account_balance(a)} for a in accts]
    total = sum(r["balance"] for r in rows)
    return render_template("accounts.html", rows=rows, total=total)

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
        elif ob_str and not ob_str.replace("-", "", 1).replace(".", "", 1).isdigit():
            flash("Opening balance must be a valid number!", "danger")
        else:
            acct.name = name
            acct.opening_balance = float(ob_str or 0)
            acct.account_type = acc_type if acc_type in ("Cash", "Bank") else "Cash"
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

@app.route("/reports/gst")
@manager_required
def report_gst():
    start_str = request.args.get("start", "")
    end_str   = request.args.get("end", "")
    today = datetime.now()
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
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("latin1")
    raise TypeError(f"Not JSON serializable: {type(v)}")

def _coerce_value(value, column):
    """Convert a JSON-decoded value back to the Python type the column expects."""
    if value is None:
        return None
    from sqlalchemy import DateTime, Numeric, Float, Boolean, Integer
    coltype = column.type
    if isinstance(coltype, DateTime):
        return datetime.fromisoformat(value) if isinstance(value, str) else value
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
        backup_name=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )

@app.route("/admin/backup")
@admin_required
def admin_backup():
    try:
        payload = json.dumps(export_database_dict(), default=_json_default, indent=1)
    except Exception as e:
        app.logger.exception("Backup failed")
        flash(f"Backup failed: {e}", "danger")
        return redirect(url_for("admin_system"))
    filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
        path = os.path.join("backups", f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=_json_default, indent=1)
    rows = sum(len(v) for v in data["tables"].values())
    click.echo(f"Backup written to {path} ({rows} rows, {os.path.getsize(path)//1024} KB).")

@app.cli.command("seed-data")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def seed_data_cmd(yes):
    """Wipe all non-user data and populate with demo master data + all transaction types."""
    if not yes:
        click.echo("WARNING: This will DELETE ALL existing data (except User accounts) and insert demo data.")
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    # ── 1. Clear data in FK-safe order ───────────────────────────────────────
    click.echo("Step 1/3: Clearing all data...")
    db.session.query(SupplierLedgerEntry).delete()
    db.session.query(CustomerLedgerEntry).delete()
    db.session.query(SaleReturn).delete()
    db.session.query(PurchaseReturn).delete()
    db.session.query(CustomerPayment).delete()
    db.session.query(SupplierPayment).delete()
    db.session.query(SaleItem).delete()
    db.session.query(PurchaseItem).delete()
    db.session.query(Sale).delete()
    db.session.query(Purchase).delete()
    db.session.query(Item).delete()
    db.session.query(Category).delete()
    db.session.query(Customer).delete()
    db.session.query(Supplier).delete()
    db.session.commit()
    click.echo("  Done.")

    # ── 2. Master Data ────────────────────────────────────────────────────────
    click.echo("Step 2/3: Creating master data...")

    cat_elec   = Category(name="Electronics")
    cat_fabric = Category(name="Fabric & Textile")
    db.session.add_all([cat_elec, cat_fabric])
    db.session.flush()

    item_mobile = Item(name="Samsung Mobile",  category_id=cat_elec.id,   unit="Pcs",
                       opening_stock=10,  stock=10,  reorder_level=5,
                       purchase_price=25000.0, sale_price=30000.0)
    item_laptop = Item(name="Laptop HP",        category_id=cat_elec.id,   unit="Pcs",
                       opening_stock=5,   stock=5,   reorder_level=2,
                       purchase_price=55000.0, sale_price=65000.0)
    item_cable  = Item(name="USB Cable",        category_id=cat_elec.id,   unit="Pcs",
                       opening_stock=100, stock=100, reorder_level=20,
                       purchase_price=300.0,   sale_price=500.0)
    item_cotton = Item(name="Cotton Fabric",    category_id=cat_fabric.id, unit="Meter",
                       opening_stock=500, stock=500, reorder_level=100,
                       purchase_price=200.0,   sale_price=280.0)
    item_poly   = Item(name="Polyester Fabric", category_id=cat_fabric.id, unit="Meter",
                       opening_stock=300, stock=300, reorder_level=50,
                       purchase_price=150.0,   sale_price=200.0)
    db.session.add_all([item_mobile, item_laptop, item_cable, item_cotton, item_poly])
    db.session.flush()

    sup1 = Supplier(name="Shaheen Electronics", contact="0321-1234567",
                    address="Hall Road, Lahore",       opening_balance=15000.0)
    sup2 = Supplier(name="Karimi Cloth House",  contact="0333-7654321",
                    address="Bolton Market, Karachi",  opening_balance=8000.0)
    sup3 = Supplier(name="National Traders",    contact="0345-9876543",
                    address="Blue Area, Islamabad",    opening_balance=0.0)
    db.session.add_all([sup1, sup2, sup3])
    db.session.flush()

    cust1 = Customer(name="Ahmed Brothers",     contact="0312-1111111",
                     address="Karkhana Bazaar, Faisalabad", opening_balance=5000.0)
    cust2 = Customer(name="Zafar Retail Store", contact="0322-2222222",
                     address="Hussain Agahi, Multan",       opening_balance=0.0)
    cust3 = Customer(name="City Electronics",   contact="0300-3333333",
                     address="Saddar, Rawalpindi",           opening_balance=12000.0)
    db.session.add_all([cust1, cust2, cust3])
    db.session.flush()

    for s in [sup1, sup2, sup3]:
        sync_supplier_opening(s)
    for c in [cust1, cust2, cust3]:
        sync_customer_opening(c)
    db.session.commit()
    click.echo("  2 categories, 5 items, 3 suppliers, 3 customers created.")

    # ── 3. Transactions ───────────────────────────────────────────────────────
    click.echo("Step 3/3: Creating all transaction types...")

    def dt(s):
        return datetime.strptime(s, "%Y-%m-%d")

    # Shorthand tuple: no discount, no tax
    _N = ("percent", 0.0, 0.0)

    def make_purchase(supplier, date, rows, notes=None):
        first = rows[0]
        pur = Purchase(
            supplier_id=supplier.id,
            item_id=first[0].id, quantity=first[1], purchase_price=first[2],
            discount_type="percent", discount_value=0, discount_amount=0,
            tax_percent=0, tax_amount=0,
            date=date, notes=notes,
        )
        db.session.add(pur)
        db.session.flush()
        for item, qty, price, d_type, d_val, tax in rows:
            gross = qty * price
            disc_amt, tax_amt, net = calc_discount_tax(gross, d_type, d_val, tax)
            db.session.add(PurchaseItem(
                purchase_id=pur.id, item_id=item.id,
                quantity=qty, purchase_price=price,
                discount_type=d_type, discount_value=d_val,
                discount_amount=disc_amt, tax_percent=tax,
                tax_amount=tax_amt, amount=net,
            ))
            item.stock += qty
        db.session.flush()
        db.session.refresh(pur)
        sync_supplier_purchase(pur)
        db.session.commit()
        return pur

    def make_sale(customer, date, rows, notes=None):
        first = rows[0]
        sal = Sale(
            customer_id=customer.id,
            item_id=first[0].id, quantity=first[1], sale_price=first[2],
            cost_price=0.0,
            discount_type="percent", discount_value=0, discount_amount=0,
            tax_percent=0, tax_amount=0,
            date=date, notes=notes,
        )
        db.session.add(sal)
        db.session.flush()
        for item, qty, price, d_type, d_val, tax in rows:
            gross = qty * price
            disc_amt, tax_amt, net = calc_discount_tax(gross, d_type, d_val, tax)
            db.session.add(SaleItem(
                sale_id=sal.id, item_id=item.id,
                quantity=qty, sale_price=price,
                cost_price=float(item.purchase_price or 0),
                discount_type=d_type, discount_value=d_val,
                discount_amount=disc_amt, tax_percent=tax,
                tax_amount=tax_amt, amount=net,
            ))
            item.stock -= qty
        db.session.flush()
        db.session.refresh(sal)
        sync_customer_sale(sal)
        db.session.commit()
        return sal

    # Purchases  ──────────────────────────────────────────────────────────────
    pur1 = make_purchase(sup1, dt("2026-05-01"), [
        (item_mobile,  5, 25000.0) + _N,   # 125,000
        (item_cable,  20,   300.0) + _N,   #   6,000  → total 131,000
    ], notes="Stock replenishment")
    pur2 = make_purchase(sup2, dt("2026-05-05"), [
        (item_cotton, 100, 200.0) + _N,    #  20,000
        (item_poly,    50, 150.0) + _N,    #   7,500  → total 27,500
    ])
    pur3 = make_purchase(sup3, dt("2026-05-10"), [
        (item_laptop, 2, 55000.0) + _N,    # 110,000
    ])
    click.echo("  Purchases:  3 invoices (2 multi-item, 1 single)")

    # Sales  ──────────────────────────────────────────────────────────────────
    sal1 = make_sale(cust1, dt("2026-05-15"), [
        (item_mobile, 2, 30000.0) + _N,    #  60,000
        (item_cable,  10,   500.0) + _N,   #   5,000  → total 65,000
    ])
    sal2 = make_sale(cust2, dt("2026-05-20"), [
        (item_cotton, 50, 280.0) + _N,     #  14,000
        (item_poly,   30, 200.0) + _N,     #   6,000  → total 20,000
    ])
    sal3 = make_sale(cust3, dt("2026-05-25"), [
        (item_laptop, 1, 65000.0) + _N,    #  65,000
    ])
    click.echo("  Sales:      3 invoices (2 multi-item, 1 single)")

    # Supplier Payments  ───────────────────────────────────────────────────────
    def sup_pay(supplier, purchase, amount, date, method, ref=None, notes=None):
        p = SupplierPayment(
            supplier_id=supplier.id,
            purchase_id=purchase.id if purchase else None,
            amount=amount, payment_date=date,
            payment_method=method, reference_no=ref, notes=notes,
        )
        db.session.add(p)
        db.session.flush()
        sync_supplier_payment(p)
        db.session.commit()

    sup_pay(sup1, pur1, 80000.0, dt("2026-05-16"), "Bank",   "TXN-001", "Partial against Bill #1")
    sup_pay(sup1, None, 20000.0, dt("2026-05-22"), "Cash",   notes="On account")
    sup_pay(sup2, pur2, 27500.0, dt("2026-05-25"), "Cheque", "CHQ-4521", "Full payment - fabric")
    click.echo("  Supplier Payments: 3 recorded")

    # Customer Receipts  ───────────────────────────────────────────────────────
    def cust_recv(customer, sale, amount, date, method, ref=None, notes=None):
        r = CustomerPayment(
            customer_id=customer.id,
            sale_id=sale.id if sale else None,
            amount=amount, payment_date=date,
            payment_method=method, reference_no=ref, notes=notes,
        )
        db.session.add(r)
        db.session.flush()
        sync_customer_receipt(r)
        db.session.commit()

    cust_recv(cust1, sal1, 40000.0, dt("2026-05-17"), "Cash",   notes="Partial against Sale #1")
    cust_recv(cust1, None, 15000.0, dt("2026-05-23"), "Bank",   "TXN-502", "On account")
    cust_recv(cust3, sal3, 65000.0, dt("2026-05-28"), "Online", "ONL-789",  "Full payment - laptop")
    click.echo("  Customer Receipts: 3 recorded")

    # Purchase Returns  ────────────────────────────────────────────────────────
    def pur_ret(purchase, supplier, item, qty, price, date, reason=None):
        item.stock -= qty
        pr = PurchaseReturn(
            purchase_id=purchase.id, supplier_id=supplier.id, item_id=item.id,
            quantity=qty, return_price=price, date=date, reason=reason,
        )
        db.session.add(pr)
        db.session.flush()
        sync_supplier_purchase_return(pr)
        db.session.commit()

    pur_ret(pur2, sup2, item_cotton,  10, 200.0, dt("2026-06-01"), "Defective material")
    pur_ret(pur1, sup1, item_cable,    5, 300.0, dt("2026-06-03"), "Wrong specification")
    click.echo("  Purchase Returns: 2 recorded")

    # Sale Returns  ────────────────────────────────────────────────────────────
    def sal_ret(sale, customer, item, qty, price, date, reason=None):
        item.stock += qty
        sr = SaleReturn(
            sale_id=sale.id, customer_id=customer.id, item_id=item.id,
            quantity=qty, return_price=price, date=date, reason=reason,
        )
        db.session.add(sr)
        db.session.flush()
        sync_customer_sale_return(sr)
        db.session.commit()

    sal_ret(sal1, cust1, item_cable, 2, 500.0, dt("2026-06-05"), "Customer not satisfied")
    sal_ret(sal2, cust2, item_poly,  5, 200.0, dt("2026-06-07"), "Wrong color")
    click.echo("  Sale Returns:     2 recorded")

    # ── Verification Report ───────────────────────────────────────────────────
    db.session.expire_all()
    click.echo("")
    W = 66
    click.echo("=" * W)
    click.echo("  VERIFICATION REPORT")
    click.echo("=" * W)
    all_ok = True

    def chk(label, expected, actual):
        nonlocal all_ok
        ok = abs(float(actual) - float(expected)) < 0.01
        if not ok:
            all_ok = False
        tick = "OK" if ok else "FAIL"
        click.echo(
            f"  {label:<28} {float(expected):>12,.2f} {float(actual):>12,.2f}"
            f"  {'OK' if ok else '!! FAIL'}"
        )

    click.echo("")
    click.echo("ITEM STOCKS  (expected = opening + purchases - sales - pur.returns + sal.returns):")
    click.echo(f"  {'Item':<28} {'Expected':>12} {'Actual':>12}  Verdict")
    click.echo(f"  {'-'*28} {'-'*12} {'-'*12}  {'-'*7}")
    exp_stock = {
        item_mobile.id: 10 + 5  - 2,             # 13
        item_cable.id:  100 + 20 - 10 - 5 + 2,   # 107
        item_cotton.id: 500 + 100 - 50 - 10,      # 540
        item_poly.id:   300 + 50 - 30 + 5,        # 325
        item_laptop.id: 5 + 2   - 1,              # 6
    }
    for item in Item.query.order_by(Item.name).all():
        chk(item.name, exp_stock.get(item.id, 0), item.stock)

    click.echo("")
    click.echo("SUPPLIER BALANCES  (opening + purchases - payments - pur.returns):")
    click.echo(f"  {'Supplier':<28} {'Expected':>12} {'Actual':>12}  Verdict")
    click.echo(f"  {'-'*28} {'-'*12} {'-'*12}  {'-'*7}")
    exp_sup = {
        # 15,000 opening + 131,000 pur1 - 80,000 pay1 - 20,000 pay2 - 1,500 pr2 = 44,500
        sup1.id: 15000 + (5*25000 + 20*300) - 80000 - 20000 - (5*300),
        # 8,000 opening + 27,500 pur2 - 27,500 pay3 - 2,000 pr1 = 6,000
        sup2.id: 8000  + (100*200 + 50*150) - 27500 - (10*200),
        # 0 opening + 110,000 pur3 = 110,000
        sup3.id: 0     + (2*55000),
    }
    for sup in Supplier.query.order_by(Supplier.name).all():
        chk(sup.name, exp_sup.get(sup.id, 0), get_supplier_balance(sup.id))

    click.echo("")
    click.echo("CUSTOMER BALANCES  (opening + sales - receipts - sal.returns):")
    click.echo(f"  {'Customer':<28} {'Expected':>12} {'Actual':>12}  Verdict")
    click.echo(f"  {'-'*28} {'-'*12} {'-'*12}  {'-'*7}")
    exp_cust = {
        # 5,000 opening + 65,000 sal1 - 40,000 rec1 - 15,000 rec2 - 1,000 sr1 = 14,000
        cust1.id: 5000  + (2*30000 + 10*500) - 40000 - 15000 - (2*500),
        # 0 opening + 20,000 sal2 - 1,000 sr2 = 19,000
        cust2.id: 0     + (50*280 + 30*200)  - 0     - 0     - (5*200),
        # 12,000 opening + 65,000 sal3 - 65,000 rec3 = 12,000
        cust3.id: 12000 + (1*65000)          - 65000,
    }
    for cust in Customer.query.order_by(Customer.name).all():
        chk(cust.name, exp_cust.get(cust.id, 0), get_customer_balance(cust.id))

    click.echo("")
    click.echo("PURCHASE INVOICE STATUS:")
    click.echo(f"  {'#':<4} {'Supplier':<22} {'Total':>10} {'Paid':>9} {'Due':>9}  Status")
    click.echo(f"  {'-'*4} {'-'*22} {'-'*10} {'-'*9} {'-'*9}  {'-'*8}")
    for pur in Purchase.query.order_by(Purchase.id).all():
        total = purchase_total(pur)
        paid  = get_purchase_paid(pur.id)
        due   = total - paid
        click.echo(
            f"  #{pur.id:<3} {pur.id_supplier.name:<22} {total:>10,.2f}"
            f" {paid:>9,.2f} {due:>9,.2f}  {get_payment_status(total, paid)}"
        )

    click.echo("")
    click.echo("SALE INVOICE STATUS:")
    click.echo(f"  {'#':<4} {'Customer':<22} {'Total':>10} {'Rcvd':>9} {'Due':>9}  Status")
    click.echo(f"  {'-'*4} {'-'*22} {'-'*10} {'-'*9} {'-'*9}  {'-'*8}")
    for sal in Sale.query.order_by(Sale.id).all():
        total    = sale_total(sal)
        received = get_sale_received(sal.id)
        due      = total - received
        click.echo(
            f"  #{sal.id:<3} {sal.id_customer.name:<22} {total:>10,.2f}"
            f" {received:>9,.2f} {due:>9,.2f}  {get_payment_status(total, received)}"
        )

    click.echo("")
    click.echo("=" * W)
    if all_ok:
        click.echo("  RESULT: ALL CHECKS PASSED  OK")
    else:
        click.echo("  RESULT: SOME CHECKS FAILED — see !! FAIL lines above")
    click.echo("=" * W)
    click.echo("")
    click.echo("Seed complete. Log in and explore all features.")


if __name__ == "__main__":
    # Debugger stays off in production. FLASK_DEBUG overrides explicitly;
    # otherwise default to on only for local SQLite dev (no DATABASE_URL) so a
    # stray `python app.py` against a real database never exposes the debugger.
    _debug_env = os.getenv("FLASK_DEBUG")
    if _debug_env is not None:
        debug_mode = _debug_env.lower() in ("1", "true", "yes", "on")
    else:
        debug_mode = not DATABASE_URL
    app.run(debug=debug_mode, host="127.0.0.1", port=5172)