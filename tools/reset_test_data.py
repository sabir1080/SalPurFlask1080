"""Reset the generator-owned test dataset (PostgreSQL, or local SQLite with
--allow-sqlite), leaving the 67 mandatory baseline/system rows (chart of
accounts, tax codes, fiscal years, financial accounts, the default
branch/warehouse) untouched.

Run via the CLI, not directly:
    python tools/test_data_cli.py reset [--yes] [--allow-sqlite]

Safety:
  - Refuses to run against anything but PostgreSQL, unless --allow-sqlite.
  - Deletes rows only from the tables in _data_common.GENERATOR_OWNED_TABLES —
    PostgreSQL via TRUNCATE ... CASCADE (as before); SQLite via per-table
    DELETE FROM in the same children-before-parents order the list is
    already written in (SQLite's DELETE has no CASCADE, so order matters
    there in a way it never did for the Postgres path), plus clearing
    sqlite_sequence for each so AUTOINCREMENT ids restart the same way
    RESTART IDENTITY does on Postgres.
  - Never touches business_category — the 26 default categories are SYSTEM
    DEFAULT MASTER DATA (seeded by app.py's migrate_database(), the same
    tier as the chart of accounts), and the generator no longer creates any
    BusinessCategory rows of its own to clean up. Any custom categories a
    user created are equally untouched.
  - Never touches the 9 baseline tables (_data_common.BASELINE_TABLES_NEVER_TRUNCATED).
  - Never drops the database or a table; only ever deletes rows.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "--allow-sqlite" in sys.argv:
    os.environ["TEST_DATA_ALLOW_SQLITE"] = "1"

from tools._data_common import (
    require_database, GENERATOR_OWNED_TABLES, BASELINE_TABLES_NEVER_TRUNCATED,
    clear_sentinel,
)

ALLOW_SQLITE = os.environ.get("TEST_DATA_ALLOW_SQLITE") == "1"
DATABASE_URL = require_database(allow_sqlite=ALLOW_SQLITE)


def run(confirmed=False):
    from app import app, db

    with app.app_context():
        overlap = set(GENERATOR_OWNED_TABLES) & set(BASELINE_TABLES_NEVER_TRUNCATED)
        assert not overlap, f"BUG: baseline tables would be truncated: {overlap}"

        dialect = db.engine.dialect.name
        engine_label = "PostgreSQL" if dialect == "postgresql" else "SQLite (local dev database)"

        if not confirmed:
            print(f"This will delete rows from the following generator-owned tables in {engine_label}:")
            for t in GENERATOR_OWNED_TABLES:
                print(f"  - {t}")
            print()
            print("The 26 default Business Categories are system master data and will")
            print("NOT be touched — nor will any custom categories you created.")
            print()
            print("The 67 baseline/system rows (chart of accounts, tax codes, fiscal")
            print("years, financial accounts, default branch/warehouse) will NOT be touched.")
            print()
            answer = input("Type 'yes' to proceed: ").strip().lower()
            if answer != "yes":
                print("Aborted — no changes made.")
                sys.exit(1)

        if dialect == "postgresql":
            table_list = ", ".join(f'"{t}"' for t in GENERATOR_OWNED_TABLES)
            db.session.execute(db.text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))
        else:
            # SQLite: no CASCADE, so delete in the same children-first order
            # GENERATOR_OWNED_TABLES is already written in (see its own
            # comment — the list was ordered for exactly this reason, even
            # though the Postgres path never needed the ordering itself).
            for t in GENERATOR_OWNED_TABLES:
                db.session.execute(db.text(f'DELETE FROM "{t}"'))
            # AUTOINCREMENT counters live in sqlite_sequence, one row per
            # table that has ever used AUTOINCREMENT — deleting from it is
            # SQLite's equivalent of RESTART IDENTITY. A table with no
            # AUTOINCREMENT column simply has no row here; safe to no-op.
            for t in GENERATOR_OWNED_TABLES:
                db.session.execute(
                    db.text("DELETE FROM sqlite_sequence WHERE name = :t"), {"t": t})

        db.session.commit()
        clear_sentinel()
        print(f"Reset complete — {len(GENERATOR_OWNED_TABLES)} generator-owned tables cleared.")
        print("Baseline/system rows and the 26 default Business Categories preserved. "
              "Run `generate` to build a fresh dataset.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Reset TradeFlow ERP test data (PostgreSQL by default; local SQLite with --allow-sqlite)")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument("--allow-sqlite", action="store_true",
                        help="Explicitly allow targeting the local SQLite database.")
    args = parser.parse_args()
    run(confirmed=args.yes)
