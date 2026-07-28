#app.py
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, abort, Response
from flask_paginate import Pagination, get_page_args
from flask_login import UserMixin, login_user, logout_user, login_required, current_user
from salpurflask.auth import verified_required, role_required, admin_required, manager_required
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

# now_local() moved to salpurflask/utils/helpers.py

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
from salpurflask.models.business_config import BusinessCategory
from salpurflask.utils import (
    now_local, get_paginated_results, csv_response, excel_response,
    is_demo_mode, barcode_taken, write_csv_header,
    line_base_qty
)

# Register blueprints
from salpurflask.routes import auth_bp, dashboard_bp
from salpurflask.routes.admin_config import config_bp
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(config_bp)

# Register inventory routes directly (not via blueprint, to preserve endpoint names)
from salpurflask.inventory.routes import (
    item_ledger, get_item, report_stock, item, edit_item, delete_item,
    category, edit_category, delete_category,
    export_item_ledger, export_item_ledger_excel,
    bulk_import, process_import,
    stock_adjustment, delete_stock_adjustment,
    labels, labels_assign, send_low_stock_alert
)

# Register purchase routes directly (not via blueprint, to preserve endpoint names)
from salpurflask.purchase.routes import (
    purchase, edit_purchase, delete_purchase,
    purchase_return, delete_purchase_return, purchase_invoice,
    purchase_orders, purchase_order_detail, update_po_status, convert_po_to_purchase, delete_purchase_order,
    export_purchase_report, export_purchase_return_report, export_supplier_purchase_report
)
from salpurflask.sales.routes import (
    sale, edit_sale, delete_sale,
    sale_return, delete_sale_return, sale_invoice,
    pos, pos_lookup, pos_checkout, pos_receipt,
    pos_hold, list_pos_holds, get_pos_hold, delete_pos_hold,
    delivery_challans, create_delivery_challan, update_delivery_challan,
    export_sale_report, export_date_sale_report, export_item_sale_report,
    export_customer_sale_report, export_category_sale_report, export_sale_return_report
)
from salpurflask.supplier.routes import (
    supplier, edit_supplier, delete_supplier, export_suppliers, export_suppliers_excel,
    supplier_payment, edit_supplier_payment, delete_supplier_payment, supplier_bulk_payment,
    supplier_ledger, delete_supplier_ledger_adjustment, export_supplier_ledger, export_supplier_ledger_excel,
    api_supplier_balance
)
from salpurflask.customer.routes import (
    customer, edit_customer, delete_customer, export_customers, export_customers_excel,
    customer_receipt, edit_customer_receipt, delete_customer_receipt, customer_bulk_receipt,
    customer_ledger, delete_customer_ledger_adjustment, export_customer_ledger, export_customer_ledger_excel,
    api_customer_balance
)
from salpurflask.accounting.routes import (
    accounts, new_account, edit_account, account_ledger,
    report_balance_sheet, report_trial_balance,
    journal, journal_new, journal_view, journal_reverse,
    reverse_document_route, fixed_assets, fixed_asset_new, fixed_asset_view,
    fixed_assets_depreciation, fixed_asset_dispose,
    periods, toggle_period, close_year, new_fiscal_year,
    report_reconciliation, chart_of_accounts, new_gl_account, edit_gl_account,
    delete_gl_account, tax_codes, report_gst
)
app.add_url_rule("/item/<int:id>/ledger", "item_ledger", item_ledger)
app.add_url_rule("/api/item/<int:id>", "get_item", get_item)
app.add_url_rule("/reports/stock", "report_stock", report_stock)
app.add_url_rule("/item", "item", item, methods=["GET", "POST"])
app.add_url_rule("/item/edit/<int:id>", "edit_item", edit_item, methods=["GET", "POST"])
app.add_url_rule("/item/delete/<int:id>", "delete_item", delete_item, methods=["POST"])
app.add_url_rule("/category", "category", category, methods=["GET", "POST"])
app.add_url_rule("/category/edit/<int:id>", "edit_category", edit_category, methods=["GET", "POST"])
app.add_url_rule("/category/delete/<int:id>", "delete_category", delete_category, methods=["POST"])
app.add_url_rule("/item/<int:id>/ledger/export", "export_item_ledger", export_item_ledger)
app.add_url_rule("/item/<int:id>/ledger/export/excel", "export_item_ledger_excel", export_item_ledger_excel)
app.add_url_rule("/import", "bulk_import", bulk_import, methods=["GET"])
app.add_url_rule("/import/process", "process_import", process_import, methods=["POST"])
app.add_url_rule("/stock_adjustment", "stock_adjustment", stock_adjustment, methods=["GET", "POST"])
app.add_url_rule("/stock_adjustment/delete/<int:id>", "delete_stock_adjustment", delete_stock_adjustment, methods=["POST"])
app.add_url_rule("/labels", "labels", labels, methods=["GET"])
app.add_url_rule("/labels/assign", "labels_assign", labels_assign, methods=["POST"])
app.add_url_rule("/low_stock_alert", "send_low_stock_alert", send_low_stock_alert, methods=["POST"])
app.add_url_rule("/purchase", "purchase", purchase, methods=["GET", "POST"])
app.add_url_rule("/purchase/edit/<int:id>", "edit_purchase", edit_purchase, methods=["GET", "POST"])
app.add_url_rule("/purchase/delete/<int:id>", "delete_purchase", delete_purchase, methods=["POST"])
app.add_url_rule("/purchase_return", "purchase_return", purchase_return, methods=["GET", "POST"])
app.add_url_rule("/purchase_return/delete/<int:id>", "delete_purchase_return", delete_purchase_return, methods=["POST"])
app.add_url_rule("/purchase/<int:id>/invoice", "purchase_invoice", purchase_invoice, methods=["GET"])
app.add_url_rule("/purchase_orders", "purchase_orders", purchase_orders, methods=["GET", "POST"])
app.add_url_rule("/purchase_orders/<int:id>", "purchase_order_detail", purchase_order_detail, methods=["GET"])
app.add_url_rule("/purchase_orders/<int:id>/status", "update_po_status", update_po_status, methods=["POST"])
app.add_url_rule("/purchase_orders/<int:id>/convert", "convert_po_to_purchase", convert_po_to_purchase, methods=["POST"])
app.add_url_rule("/purchase_orders/<int:id>/delete", "delete_purchase_order", delete_purchase_order, methods=["POST"])
app.add_url_rule("/export_purchase_report", "export_purchase_report", export_purchase_report, methods=["POST"])
app.add_url_rule("/export_purchase_return_report", "export_purchase_return_report", export_purchase_return_report, methods=["POST"])
app.add_url_rule("/export_supplier_purchase_report", "export_supplier_purchase_report", export_supplier_purchase_report, methods=["POST"])
app.add_url_rule("/sale", "sale", sale, methods=["GET", "POST"])
app.add_url_rule("/sale/edit/<int:id>", "edit_sale", edit_sale, methods=["GET", "POST"])
app.add_url_rule("/sale/delete/<int:id>", "delete_sale", delete_sale, methods=["POST"])
app.add_url_rule("/sale_return", "sale_return", sale_return, methods=["GET", "POST"])
app.add_url_rule("/sale_return/delete/<int:id>", "delete_sale_return", delete_sale_return, methods=["POST"])
app.add_url_rule("/sale/<int:id>/invoice", "sale_invoice", sale_invoice, methods=["GET"])
app.add_url_rule("/pos", "pos", pos, methods=["GET"])
app.add_url_rule("/pos/lookup", "pos_lookup", pos_lookup, methods=["GET"])
app.add_url_rule("/pos/checkout", "pos_checkout", pos_checkout, methods=["POST"])
app.add_url_rule("/pos/receipt/<int:id>", "pos_receipt", pos_receipt, methods=["GET"])
app.add_url_rule("/pos/hold", "pos_hold", pos_hold, methods=["POST"])
app.add_url_rule("/pos/held-bills", "list_pos_holds", list_pos_holds, methods=["GET"])
app.add_url_rule("/pos/held-bills/<int:id>", "get_pos_hold", get_pos_hold, methods=["GET"])
app.add_url_rule("/pos/held-bills/<int:id>/delete", "delete_pos_hold", delete_pos_hold, methods=["POST"])
app.add_url_rule("/delivery_challans", "delivery_challans", delivery_challans, methods=["GET"])
app.add_url_rule("/delivery_challans/create", "create_delivery_challan", create_delivery_challan, methods=["POST"])
app.add_url_rule("/delivery_challans/<int:id>/update", "update_delivery_challan", update_delivery_challan, methods=["POST"])
app.add_url_rule("/export_sale_report", "export_sale_report", export_sale_report, methods=["POST"])
app.add_url_rule("/export_date_sale_report", "export_date_sale_report", export_date_sale_report, methods=["POST"])
app.add_url_rule("/export_item_sale_report", "export_item_sale_report", export_item_sale_report, methods=["POST"])
app.add_url_rule("/export_customer_sale_report", "export_customer_sale_report", export_customer_sale_report, methods=["POST"])
app.add_url_rule("/export_category_sale_report", "export_category_sale_report", export_category_sale_report, methods=["POST"])
app.add_url_rule("/export_sale_return_report", "export_sale_return_report", export_sale_return_report, methods=["POST"])
app.add_url_rule("/supplier", "supplier", supplier, methods=["GET", "POST"])
app.add_url_rule("/supplier/edit/<int:id>", "edit_supplier", edit_supplier, methods=["GET", "POST"])
app.add_url_rule("/supplier/delete/<int:id>", "delete_supplier", delete_supplier, methods=["POST"])
app.add_url_rule("/export_suppliers", "export_suppliers", export_suppliers, methods=["GET"])
app.add_url_rule("/export_suppliers_excel", "export_suppliers_excel", export_suppliers_excel, methods=["GET"])
app.add_url_rule("/customer", "customer", customer, methods=["GET", "POST"])
app.add_url_rule("/customer/edit/<int:id>", "edit_customer", edit_customer, methods=["GET", "POST"])
app.add_url_rule("/customer/delete/<int:id>", "delete_customer", delete_customer, methods=["POST"])
app.add_url_rule("/export_customers", "export_customers", export_customers, methods=["GET"])
app.add_url_rule("/export_customers_excel", "export_customers_excel", export_customers_excel, methods=["GET"])
app.add_url_rule("/supplier_payment", "supplier_payment", supplier_payment, methods=["GET", "POST"])
app.add_url_rule("/supplier_payment/edit/<int:id>", "edit_supplier_payment", edit_supplier_payment, methods=["GET", "POST"])
app.add_url_rule("/supplier_payment/delete/<int:id>", "delete_supplier_payment", delete_supplier_payment, methods=["POST"])
app.add_url_rule("/supplier_bulk_payment", "supplier_bulk_payment", supplier_bulk_payment, methods=["GET", "POST"])
app.add_url_rule("/customer_receipt", "customer_receipt", customer_receipt, methods=["GET", "POST"])
app.add_url_rule("/customer_receipt/edit/<int:id>", "edit_customer_receipt", edit_customer_receipt, methods=["GET", "POST"])
app.add_url_rule("/customer_receipt/delete/<int:id>", "delete_customer_receipt", delete_customer_receipt, methods=["POST"])
app.add_url_rule("/customer_bulk_receipt", "customer_bulk_receipt", customer_bulk_receipt, methods=["GET", "POST"])
app.add_url_rule("/supplier/<int:id>/ledger", "supplier_ledger", supplier_ledger, methods=["GET", "POST"])
app.add_url_rule("/supplier/<int:id>/ledger/adjustment/delete/<int:entry_id>", "delete_supplier_ledger_adjustment", delete_supplier_ledger_adjustment, methods=["POST"])
app.add_url_rule("/supplier/<int:id>/ledger/export", "export_supplier_ledger", export_supplier_ledger, methods=["GET"])
app.add_url_rule("/supplier/<int:id>/ledger/export/excel", "export_supplier_ledger_excel", export_supplier_ledger_excel, methods=["GET"])
app.add_url_rule("/api/supplier/<int:id>/balance", "api_supplier_balance", api_supplier_balance, methods=["GET"])
app.add_url_rule("/customer/<int:id>/ledger", "customer_ledger", customer_ledger, methods=["GET", "POST"])
app.add_url_rule("/customer/<int:id>/ledger/adjustment/delete/<int:entry_id>", "delete_customer_ledger_adjustment", delete_customer_ledger_adjustment, methods=["POST"])
app.add_url_rule("/customer/<int:id>/ledger/export", "export_customer_ledger", export_customer_ledger, methods=["GET"])
app.add_url_rule("/customer/<int:id>/ledger/export/excel", "export_customer_ledger_excel", export_customer_ledger_excel, methods=["GET"])
app.add_url_rule("/api/customer/<int:id>/balance", "api_customer_balance", api_customer_balance, methods=["GET"])
app.add_url_rule("/accounts", "accounts", accounts, methods=["GET"])
app.add_url_rule("/accounts/new", "new_account", new_account, methods=["GET", "POST"])
app.add_url_rule("/accounts/<int:id>/edit", "edit_account", edit_account, methods=["GET", "POST"])
app.add_url_rule("/accounts/<int:id>/ledger", "account_ledger", account_ledger, methods=["GET"])
app.add_url_rule("/reports/balance_sheet", "report_balance_sheet", report_balance_sheet, methods=["GET"])
app.add_url_rule("/reports/trial_balance", "report_trial_balance", report_trial_balance, methods=["GET"])
app.add_url_rule("/journal", "journal", journal, methods=["GET"])
app.add_url_rule("/journal/new", "journal_new", journal_new, methods=["GET", "POST"])
app.add_url_rule("/journal/<int:id>", "journal_view", journal_view, methods=["GET"])
app.add_url_rule("/journal/<int:id>/reverse", "journal_reverse", journal_reverse, methods=["POST"])
app.add_url_rule("/document/<kind>/<int:id>/reverse", "reverse_document_route", reverse_document_route, methods=["POST"])
app.add_url_rule("/fixed_assets", "fixed_assets", fixed_assets, methods=["GET"])
app.add_url_rule("/fixed_assets/new", "fixed_asset_new", fixed_asset_new, methods=["GET", "POST"])
app.add_url_rule("/fixed_assets/<int:id>", "fixed_asset_view", fixed_asset_view, methods=["GET"])
app.add_url_rule("/fixed_assets/depreciation", "fixed_assets_depreciation", fixed_assets_depreciation, methods=["POST"])
app.add_url_rule("/fixed_assets/<int:id>/dispose", "fixed_asset_dispose", fixed_asset_dispose, methods=["POST"])
app.add_url_rule("/periods", "periods", periods, methods=["GET"])
app.add_url_rule("/periods/<int:id>/toggle", "toggle_period", toggle_period, methods=["POST"])
app.add_url_rule("/fiscal_years/<int:id>/close", "close_year", close_year, methods=["POST"])
app.add_url_rule("/fiscal_years/new", "new_fiscal_year", new_fiscal_year, methods=["POST"])
app.add_url_rule("/reports/reconciliation", "report_reconciliation", report_reconciliation, methods=["GET"])
app.add_url_rule("/chart_of_accounts", "chart_of_accounts", chart_of_accounts, methods=["GET"])
app.add_url_rule("/chart_of_accounts/new", "new_gl_account", new_gl_account, methods=["GET", "POST"])
app.add_url_rule("/chart_of_accounts/<int:id>/edit", "edit_gl_account", edit_gl_account, methods=["GET", "POST"])
app.add_url_rule("/chart_of_accounts/<int:id>/delete", "delete_gl_account", delete_gl_account, methods=["POST"])
app.add_url_rule("/tax_codes", "tax_codes", tax_codes, methods=["GET", "POST"])
app.add_url_rule("/reports/gst", "report_gst", report_gst, methods=["GET"])

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

def sql_date_fmt(col, fmt="%Y-%m"):
    if db.engine.dialect.name == "postgresql":
        return db.func.to_char(col, fmt.replace("%Y", "YYYY").replace("%m", "MM"))
    return db.func.strftime(fmt, col)

def is_signup_allowed():
    return os.getenv("ALLOW_SIGNUP", "false").lower() in ("1", "true", "yes")

# is_demo_mode() moved to salpurflask/utils/config_utils.py

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

    # Optimistic locking for hold bills to prevent race conditions
    if "pos_hold" in inspector.get_table_names():
        cols = {col["name"] for col in inspector.get_columns("pos_hold")}
        if "version" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE pos_hold ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))

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
    return url_for("dashboard.index")

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

# Routes /import and /import/process moved to salpurflask.inventory.routes

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

# write_csv_header, csv_response, excel_response moved to salpurflask/utils/export_utils.py

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

# get_paginated_results moved to salpurflask/utils/pagination.py

# Auth routes have been moved to salpurflask/routes/auth.py blueprint


# Dashboard routes have been moved to salpurflask/routes/dashboard.py blueprint

# Category CRUD routes moved to salpurflask.inventory.routes
# - /category (GET/POST) → category()
# - /category/edit/<id> (GET/POST) → edit_category()
# - /category/delete/<id> (POST) → delete_category()

# barcode_taken moved to salpurflask/utils/inventory_utils.py

# Route /item moved to salpurflask.inventory.routes.item

# Route /item/edit/<id> moved to salpurflask.inventory.routes.edit_item

# Route /item/delete/<id> moved to salpurflask.inventory.routes.delete_item

# Route /item/<id>/ledger moved to salpurflask.inventory.routes.item_ledger

# Export routes moved to salpurflask.inventory.routes
# - /item/<id>/ledger/export → export_item_ledger()
# - /item/<id>/ledger/export/excel → export_item_ledger_excel()

# ─── BULK IMPORT ROUTES ───────────────────────────────────────────────────────

# Routes /import and /import/process moved to salpurflask.inventory.routes
# (bulk_import() and process_import() functions)

# Route /api/item/<id> moved to salpurflask.inventory.routes.get_item

# Routes /purchase, /purchase/edit, /purchase/delete moved to salpurflask.purchase.routes
# (purchase, edit_purchase, delete_purchase functions)

# Routes /purchase/edit and /purchase/delete moved to salpurflask.purchase.routes
# (edit_purchase and delete_purchase functions)


# ── Point of Sale (POS) ───────────────────────────────────────────────────────
# A fast counter screen: scan or search an item, build a cart, take payment, print a
# receipt. It creates an ordinary Sale — and, when money changes hands, an ordinary
# customer receipt — through the *same* posting layer every other document uses. There
# is no second path into the ledger: a POS sale moves stock, posts COGS, updates the
# customer's ledger and hits the general ledger exactly like a sale typed on the sales
# page. That is the whole point; a till that kept its own books would defeat the system.


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

# Routes /labels and /labels/assign moved to salpurflask.inventory.routes
# (labels() and labels_assign() functions)


# Routes moved to salpurflask.supplier.routes

# Routes moved to salpurflask.supplier.routes and salpurflask.customer.routes

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
                        BusinessCategory.name.label("name"),
                        db.func.sum(_sale_net).label("sale_amt"),
                        db.func.sum(_sale_prof).label("profit_amt"),
                    )
                    .select_from(SaleItem)
                    .join(Sale, SaleItem.sale_id == Sale.id)
                    .join(Item, SaleItem.item_id == Item.id)
                    .join(BusinessCategory, Item.business_category_id == BusinessCategory.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(BusinessCategory.name)
                    .order_by(BusinessCategory.name)
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

# Routes /purchase_return and /purchase_return/delete moved to salpurflask.purchase.routes
# (purchase_return and delete_purchase_return functions)



# Route /purchase/<id>/invoice moved to salpurflask.purchase.routes
# (purchase_invoice function)


# ─── Stock Adjustment ──────────────────────────────────────────────────────────

# Routes /stock_adjustment and /stock_adjustment/delete moved to salpurflask.inventory.routes
# (stock_adjustment() and delete_stock_adjustment() functions)

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

def account_name_taken(name, exclude_id=None):
    """Two accounts with the same name are indistinguishable in every dropdown,
    so names must be unique. Case- and space-insensitive."""
    q = FinancialAccount.query.filter(
        func.lower(func.trim(FinancialAccount.name)) == name.strip().lower())
    if exclude_id is not None:
        q = q.filter(FinancialAccount.id != exclude_id)
    return db.session.query(q.exists()).scalar()

















# ─── Low Stock Alert ───────────────────────────────────────────────────────────

# Route /low_stock_alert moved to salpurflask.inventory.routes
# (send_low_stock_alert() function)

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