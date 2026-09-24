"""Reusable CLI to manage the TradeFlow ERP test dataset — PostgreSQL by
default, or the local SQLite dev database with an explicit --allow-sqlite.

    python tools/test_data_cli.py generate [--seed N] [--force] [--allow-sqlite]
    python tools/test_data_cli.py reset [--yes] [--allow-sqlite]
    python tools/test_data_cli.py verify [--verbose] [--allow-sqlite]
    python tools/test_data_cli.py status [--allow-sqlite]

Every subcommand refuses to run against anything but PostgreSQL UNLESS
--allow-sqlite is passed, in which case it explicitly targets the local
SQLite dev database instead — for validating the generator/reset/verify
cycle locally before running the same commands against Postgres. Without
that flag, nothing about these tools' behavior has changed: SQLite is
refused exactly as it always was.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Parsed and applied before any tool-module import below — every one of
# those modules re-derives ALLOW_SQLITE from this same env var at ITS OWN
# import time (see generate_test_data.py's own top-of-file comment), since
# the gate must run before `import app` wherever it happens, and this CLI's
# own subcommand functions import those modules lazily, inside cmd_*(), well
# after argparse has already run.
if "--allow-sqlite" in sys.argv:
    os.environ["TEST_DATA_ALLOW_SQLITE"] = "1"

from tools._data_common import (
    require_database, describe_database_url, read_sentinel, DEFAULT_SEED,
)

ALLOW_SQLITE = os.environ.get("TEST_DATA_ALLOW_SQLITE") == "1"
DATABASE_URL = require_database(allow_sqlite=ALLOW_SQLITE)


def cmd_status(args):
    from app import app, db

    with app.app_context():
        dialect = db.engine.dialect.name
        info = describe_database_url(DATABASE_URL) if dialect == "postgresql" else {
            "host": "local", "database": "SQLite (instance/database.db)"}
        sentinel = read_sentinel()

        def count(model):
            return model.query.count()

        from salpurflask.models import (
            Item, Supplier, Customer, Purchase, Sale, CustomerPayment,
        )
        from salpurflask.models.hr import Employee
        from salpurflask.models import PayrollPeriod

        n_items = Item.query.filter(Item.sku.like("ITEM-%")).count()
        n_medicines = Item.query.filter(Item.sku.like("MED-%")).count()
        n_suppliers = Supplier.query.count()
        n_customers = Customer.query.count()
        n_employees = Employee.query.count()
        n_purchases = Purchase.query.count()
        n_sales = Sale.query.count()
        n_pos_receipts = CustomerPayment.query.filter(
            CustomerPayment.reference_no.like("POS-%")).count()
        n_periods = PayrollPeriod.query.count()

        print("TradeFlow Test Data")
        print("-------------------")
        print(f"Database: {'PostgreSQL' if dialect == 'postgresql' else 'SQLite'}")
        print(f"Database: {info['database']}")
        print(f"Host: {info['host']}")
        print(f"Generated Dataset: {'YES' if sentinel['generated'] else 'NO'}")
        if sentinel["generated"]:
            print(f"Dataset Version: {sentinel['version']}")
            print(f"Dataset Seed: {sentinel['seed']}")
            print(f"Generated At: {sentinel['generated_at']}")
        print(f"Items: {n_items}")
        print(f"Medicines (batch-tracked): {n_medicines}")
        print(f"Suppliers: {n_suppliers}")
        print(f"Customers: {n_customers}")
        print(f"Employees: {n_employees}")
        print(f"Purchases: {n_purchases}")
        print(f"Sales: {n_sales}")
        print(f"POS Sales: {n_pos_receipts}")
        print(f"Payroll Periods: {n_periods}")


def cmd_generate(args):
    from tools.generate_test_data import run
    run(seed=args.seed, force=args.force)


def cmd_reset(args):
    from tools.reset_test_data import run
    run(confirmed=args.yes)


def cmd_verify(args):
    from tools.verify_test_data import run
    result = run(verbose=args.verbose)
    if not result.overall:
        sys.exit(1)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog="test_data_cli.py",
        description="Manage the TradeFlow ERP test dataset (PostgreSQL by "
                    "default; local SQLite with --allow-sqlite)")
    sub = parser.add_subparsers(dest="command", required=True)

    # --allow-sqlite is declared on every subparser for --help/error-message
    # purposes, but the actual gate already ran above (has to, before `import
    # app`) from a raw sys.argv scan — this flag is otherwise unused here.
    sqlite_help = "Explicitly allow targeting the local SQLite database."

    p_status = sub.add_parser("status", help="Show dataset status")
    p_status.add_argument("--allow-sqlite", action="store_true", help=sqlite_help)
    p_status.set_defaults(func=cmd_status)

    p_generate = sub.add_parser("generate", help="Generate the full test dataset")
    p_generate.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_generate.add_argument("--force", action="store_true",
                            help="Regenerate even if a dataset already exists")
    p_generate.add_argument("--allow-sqlite", action="store_true", help=sqlite_help)
    p_generate.set_defaults(func=cmd_generate)

    p_reset = sub.add_parser("reset", help="Truncate generator-owned tables only")
    p_reset.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p_reset.add_argument("--allow-sqlite", action="store_true", help=sqlite_help)
    p_reset.set_defaults(func=cmd_reset)

    p_verify = sub.add_parser("verify", help="Run the full verification suite")
    p_verify.add_argument("--verbose", action="store_true")
    p_verify.add_argument("--allow-sqlite", action="store_true", help=sqlite_help)
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
