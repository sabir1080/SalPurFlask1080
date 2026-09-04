"""Shared plumbing for the test-data tools: PostgreSQL safety check, app
bootstrap, deterministic RNG, and the tables/sentinel the generator owns.

Every tool in tools/ (generate_test_data.py, reset_test_data.py,
verify_test_data.py, test_data_cli.py) imports this module FIRST, before
importing `app`, so the PostgreSQL check runs before a single query touches
any database.
"""

import os
import random
import sys
from urllib.parse import urlsplit

from dotenv import load_dotenv

# Load .env the same way app.py does, BEFORE reading DATABASE_URL below — this
# module is imported before `import app`, specifically so the PostgreSQL check
# can run before app.py's own load_dotenv() + engine construction, so this
# tool has to do its own .env load to see the same DATABASE_URL app.py will.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

# ─── PostgreSQL-only safety gate ───────────────────────────────────────────
# This must run before `import app` (which builds the SQLAlchemy engine from
# DATABASE_URL at import time) — by the time app.py has an opinion, it is too
# late to refuse.


def require_postgres():
    """Abort immediately if DATABASE_URL is missing or points at SQLite.

    This is the one gate every tool in this package shares — a script that
    forgets to call it would silently generate or delete rows in whatever
    database app.py happens to fall back to, which for this app is the local
    SQLite dev database. That file must never be touched by these tools.
    """
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("ERROR: DATABASE_URL is not configured.")
        print("This test-data generator requires PostgreSQL.")
        sys.exit(1)
    if database_url.startswith("sqlite"):
        print("ERROR: SQLite is not supported by this generator.")
        print(f"DATABASE_URL is set to a SQLite path: {database_url}")
        sys.exit(1)
    if not database_url.startswith("postgresql"):
        print("ERROR: SQLite is not supported by this generator.")
        print(f"DATABASE_URL does not look like a PostgreSQL URL: {database_url}")
        sys.exit(1)
    return database_url


def describe_database_url(database_url):
    """host/dbname only — never the password — for status output."""
    parsed = urlsplit(database_url)
    return {"host": parsed.hostname or "unknown", "port": parsed.port,
            "database": (parsed.path or "").lstrip("/") or "unknown"}


# ─── Deterministic RNG ──────────────────────────────────────────────────────

DEFAULT_SEED = 1080


def make_rng(seed=DEFAULT_SEED):
    return random.Random(seed)


# ─── Dataset sentinel ───────────────────────────────────────────────────────
# One AppConfiguration row records that the generator has run, plus which
# seed/version produced the data currently in the database. status/generate/
# reset all read and write this same key.

SENTINEL_KEY = "TEST_DATA_GENERATED"
DATASET_VERSION = 1


def read_sentinel():
    """{'generated': bool, 'seed': int|None, 'version': int|None,
    'generated_at': str|None} — never raises, even before any run."""
    from salpurflask.models import AppConfiguration
    raw = AppConfiguration.get_value(SENTINEL_KEY, None)
    if not raw:
        return {"generated": False, "seed": None, "version": None, "generated_at": None}
    # Stored as "version|seed|iso-timestamp"
    parts = raw.split("|")
    version = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else None
    seed = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else None
    generated_at = parts[2] if len(parts) > 2 else None
    return {"generated": True, "seed": seed, "version": version, "generated_at": generated_at}


def write_sentinel(seed):
    from datetime import datetime, timezone
    from salpurflask.models import AppConfiguration
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    AppConfiguration.set_value(
        SENTINEL_KEY, f"{DATASET_VERSION}|{seed}|{stamp}",
        updated_by="test_data_cli", description="Generated ERP test dataset marker",
        data_type="string", category="test_data",
    )


def clear_sentinel():
    from salpurflask.extensions import db
    from salpurflask.models import AppConfiguration
    row = AppConfiguration.query.filter_by(key=SENTINEL_KEY).first()
    if row is not None:
        db.session.delete(row)
        db.session.commit()


# ─── Generator-owned tables ─────────────────────────────────────────────────
# Every table the generator writes to. Used by reset_test_data.py to build a
# targeted TRUNCATE list, and by verify_test_data.py to know what it owns.
# Deliberately excludes the 9 baseline tables from Phase 2 (account,
# app_configuration, financial_account, tax_code, tax_component, fiscal_year,
# accounting_period, branch, location) — those are idempotently regenerated
# by migrate_database() on next boot and are NEVER truncated by this tool,
# except `location`, which the generator DOES extend (adds 2 more warehouses
# beyond the seeded default) — see NOTE below.
#
# NOTE on `location`/`branch`: the generator adds Locations 2 and 3 on top of
# the seeded default (id 1). Truncating `location` would also remove the
# mandatory default warehouse, which migrate_database() would then recreate
# with a NEW id, breaking any FK a reset-but-not-regenerated row might still
# reference. So `location`/`branch` are NOT in this list — reset leaves all
# locations in place (including the 2 extra warehouses; they are harmless,
# idempotent-equivalent master data, and the next `generate` run detects and
# reuses them by name rather than duplicating).
GENERATOR_OWNED_TABLES = [
    # HR / payroll (children before parents for FK safety, though the CLI
    # uses TRUNCATE ... CASCADE so order is a courtesy, not a requirement)
    "hr_payroll_payment",
    "hr_payroll_item",
    "hr_payroll_entry",
    "hr_payroll_period",
    "hr_employee_advance",
    "hr_leave_request",
    "hr_leave_allocation",
    "hr_salary_structure_line",
    "hr_salary_structure",
    "hr_attendance",
    "hr_employee",
    "hr_designation",
    "hr_department",
    # Inventory movement
    "inventory_reconciliation_line",
    "inventory_reconciliation",
    "inventory_transfer_item",
    "inventory_transfer",
    "stock_movement",
    "item_stock",
    "stock_adjustment",
    # Sales
    "delivery_challan",
    "sale_return",
    "customer_payment",
    "sale_item",
    "sale",
    "quotation_item",
    "quotation",
    "pos_hold",
    "customer_ledger_entry",
    # Purchasing
    "purchase_return",
    "supplier_payment",
    "purchase_item",
    "purchase",
    "purchase_order_item",
    "purchase_order",
    "supplier_ledger_entry",
    # Accounting (manual journal entries + everything post_* wrote)
    "journal_line",
    "journal_entry",
    # Master data
    "item_unit",
    "item",
    "supplier",
    "customer",
    "user",
    # Document numbering (purchase/sale/transfer counters the generator advanced)
    "document_sequence",
]

# Tables intentionally left alone by reset (see docstring above).
BASELINE_TABLES_NEVER_TRUNCATED = [
    "account", "app_configuration", "financial_account", "tax_code",
    "tax_component", "fiscal_year", "accounting_period", "branch", "location",
]

# `category` (the legacy Category table) is no longer written by the
# generator — BusinessCategory is now the only category system it uses (see
# generate_test_data.py). Left out of GENERATOR_OWNED_TABLES on purpose: an
# earlier generator run (before this fix) may have left legacy Category rows
# behind, and this tool must not silently delete data it no longer creates
# without being told to — see the one-time repair step in generate_test_data.py
# instead, which removes exactly those 10 rows by name.

# `business_category` is likewise NOT in GENERATOR_OWNED_TABLES and the
# generator no longer creates any BusinessCategory rows at all — the 25
# default categories (salpurflask/services/category_catalog.py's
# DEFAULT_BUSINESS_CATEGORIES) are SYSTEM DEFAULT MASTER DATA, seeded by
# app.py's migrate_database() the same way the chart of accounts is, and
# must survive `reset` exactly like the 9 BASELINE_TABLES_NEVER_TRUNCATED
# above. The generator only looks up and reuses these existing rows by name
# (see generate_test_data.py's stage2_master_data()) — it never owns them,
# so reset_test_data.py has nothing of its own to delete here.


# ─── App bootstrap ──────────────────────────────────────────────────────────

def load_app():
    """Import app.py (runs migrate_database() as a side effect) and return
    (app, db). Call require_postgres() before this, always."""
    import app as appmod
    return appmod.app, appmod.db
