"""Reset the generator-owned test dataset in PostgreSQL, leaving the 67
mandatory baseline/system rows (chart of accounts, tax codes, fiscal years,
financial accounts, the default branch/warehouse) untouched.

Run via the CLI, not directly:
    python tools/test_data_cli.py reset [--yes]

Safety:
  - Refuses to run against anything but PostgreSQL.
  - TRUNCATEs only the tables in _data_common.GENERATOR_OWNED_TABLES.
  - Never touches business_category — the 26 default categories are SYSTEM
    DEFAULT MASTER DATA (seeded by app.py's migrate_database(), the same
    tier as the chart of accounts), and the generator no longer creates any
    BusinessCategory rows of its own to clean up. Any custom categories a
    user created are equally untouched.
  - Never touches the 9 baseline tables (_data_common.BASELINE_TABLES_NEVER_TRUNCATED).
  - Never drops the database or a table; only ever truncates rows.
  - Never touches SQLite.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._data_common import (
    require_postgres, GENERATOR_OWNED_TABLES, BASELINE_TABLES_NEVER_TRUNCATED,
    clear_sentinel,
)

DATABASE_URL = require_postgres()


def run(confirmed=False):
    from app import app, db

    with app.app_context():
        overlap = set(GENERATOR_OWNED_TABLES) & set(BASELINE_TABLES_NEVER_TRUNCATED)
        assert not overlap, f"BUG: baseline tables would be truncated: {overlap}"

        if not confirmed:
            print("This will TRUNCATE the following generator-owned tables in PostgreSQL:")
            for t in GENERATOR_OWNED_TABLES:
                print(f"  - {t}")
            print()
            print("The 26 default Business Categories are system master data and will")
            print("NOT be touched — nor will any custom categories you created.")
            print()
            print("The 67 baseline/system rows (chart of accounts, tax codes, fiscal")
            print("years, financial accounts, default branch/warehouse) will NOT be touched.")
            print("SQLite is never touched by this tool.")
            print()
            answer = input("Type 'yes' to proceed: ").strip().lower()
            if answer != "yes":
                print("Aborted — no changes made.")
                sys.exit(1)

        table_list = ", ".join(f'"{t}"' for t in GENERATOR_OWNED_TABLES)
        db.session.execute(db.text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))

        db.session.commit()
        clear_sentinel()
        print(f"Reset complete — {len(GENERATOR_OWNED_TABLES)} generator-owned tables truncated.")
        print("Baseline/system rows and the 26 default Business Categories preserved. "
              "Run `generate` to build a fresh dataset.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Reset TradeFlow ERP test data (PostgreSQL only)")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()
    run(confirmed=args.yes)
