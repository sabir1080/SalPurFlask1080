"""
Populate SalPurFlask with test data from Local database
Copies all data from Local SQLite to SalPurFlask (Neon PostgreSQL)
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect
import sys

# Load environments
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
LOCAL_DB_URL = "sqlite:///instance/database.db"

# Load SalPurFlask
load_dotenv(os.path.join(BASE_DIR, ".env.render-salpurflask"), override=True)
SALPURFLASK_DB_URL = os.getenv("DATABASE_URL")

print("="*60)
print("[SYNC] Data Population Tool - Local to SalPurFlask")
print("="*60)

if not SALPURFLASK_DB_URL:
    print("ERROR: SalPurFlask DATABASE_URL not configured!")
    sys.exit(1)

print(f"\nSource: Local (SQLite)")
print(f"Target: SalPurFlask (Neon)")
print(f"\nTarget URL: {SALPURFLASK_DB_URL[:60]}...")

# Connect to both databases
try:
    local_engine = create_engine(LOCAL_DB_URL)
    with local_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("\n[OK] Local database connected")
except Exception as e:
    print(f"\n[ERROR] Local database: {e}")
    sys.exit(1)

try:
    salpurflask_engine = create_engine(SALPURFLASK_DB_URL)
    with salpurflask_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("[OK] SalPurFlask database connected")
except Exception as e:
    print(f"[ERROR] SalPurFlask database: {e}")
    sys.exit(1)

# Get tables from Local
inspector = inspect(local_engine)
local_tables = sorted(inspector.get_table_names())

print(f"\n[INFO] Found {len(local_tables)} tables in Local database")

# Tables to skip (IDs, sequences, etc)
SKIP_TABLES = {
    "alembic_version",
    "sqlite_sequence",
}

tables_to_copy = [t for t in local_tables if t not in SKIP_TABLES]

print(f"[INFO] Will copy {len(tables_to_copy)} tables\n")

# Confirm before proceeding
response = input("WARNING: This will clear and repopulate SalPurFlask data!\nProceed? (yes/no): ").strip().lower()
if response != "yes":
    print("Cancelled!")
    sys.exit(0)

print("\n" + "="*60)
print("Starting data sync...")
print("="*60)

total_rows = 0
success_count = 0
failed_tables = []

# Copy data table by table
for table_name in tables_to_copy:
    try:
        # Count rows in source
        with local_engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            row_count = result.fetchone()[0]

        if row_count == 0:
            print(f"\n[SKIP] {table_name}: 0 rows")
            continue

        # Get data from Local
        with local_engine.connect() as conn:
            result = conn.execute(text(f"SELECT * FROM {table_name}"))
            columns = list(result.keys())
            rows = result.fetchall()

        # Clear target table
        with salpurflask_engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))

        # Insert into SalPurFlask
        if rows:
            with salpurflask_engine.begin() as conn:
                col_names = ", ".join(columns)
                placeholders = ", ".join([f":{col}" for col in columns])

                for row in rows:
                    row_dict = {col: val for col, val in zip(columns, row)}
                    insert_sql = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"
                    conn.execute(text(insert_sql), row_dict)

        print(f"[OK] {table_name}: {row_count} rows copied")
        total_rows += row_count
        success_count += 1

    except Exception as e:
        print(f"[ERROR] {table_name}: {str(e)}")
        failed_tables.append(table_name)

# Summary
print("\n" + "="*60)
print("Sync Complete!")
print("="*60)
print(f"\nSuccessful tables: {success_count}")
print(f"Total rows copied: {total_rows}")

if failed_tables:
    print(f"\nFailed tables ({len(failed_tables)}):")
    for table in failed_tables:
        print(f"  - {table}")
else:
    print(f"\n[SUCCESS] All data synced to SalPurFlask! 🎉")

print("\nVerifying...")
with salpurflask_engine.connect() as conn:
    result = conn.execute(text("SELECT COUNT(*) FROM item"))
    item_count = result.fetchone()[0]
    result = conn.execute(text("SELECT COUNT(*) FROM sale"))
    sale_count = result.fetchone()[0]
    result = conn.execute(text("SELECT COUNT(*) FROM purchase"))
    purchase_count = result.fetchone()[0]

print(f"\nSalPurFlask verification:")
print(f"  Items: {item_count}")
print(f"  Sales: {sale_count}")
print(f"  Purchases: {purchase_count}")
