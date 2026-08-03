"""
Database Manager - Interactive tool to manage data across all 3 databases
Supports Local (SQLite), SalPurFlask (Neon), and TradeFlow (Neon)
"""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

# Load environment
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Database configurations
DATABASES = {
    "1": {
        "name": "Local (SQLite)",
        "url": "sqlite:///instance/database.db"
    },
    "2": {
        "name": "SalPurFlask (Neon)",
        "url": os.getenv("DATABASE_URL")
    },
    "3": {
        "name": "TradeFlow (Neon)",
        "url": None
    }
}

# Load TradeFlow URL
load_dotenv(os.path.join(BASE_DIR, ".env.render-tradeflow"), override=True)
DATABASES["3"]["url"] = os.getenv("DATABASE_URL")

def get_database_connection(db_key):
    """Create engine for selected database"""
    db_config = DATABASES.get(db_key)
    if not db_config or not db_config["url"]:
        print(f"Error: Database URL not configured for {db_config['name']}")
        return None

    try:
        engine = create_engine(db_config["url"])
        # Test connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as e:
        print(f"Connection error: {str(e)}")
        return None

def get_table_count(engine, table_name):
    """Get row count for table"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) as cnt FROM {table_name}"))
            return result.fetchone()[0]
    except:
        return 0

def list_tables_with_counts(engine):
    """List all tables with row counts"""
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())

    table_data = []
    for table in tables:
        count = get_table_count(engine, table)
        table_data.append({
            "Table": table,
            "Rows": count
        })

    return table_data

def show_database_summary(engine, db_name):
    """Show summary of database"""
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()

        print(f"\n{'='*60}")
        print(f"[STAT] Database Summary: {db_name}")
        print(f"{'='*60}")
        print(f"\nTotal Tables: {len(tables)}")

        total_rows = 0
        for table in tables:
            count = get_table_count(engine, table)
            total_rows += count

        print(f"Total Rows (all tables): {total_rows:,}")

        # Show top 10 tables by row count
        print(f"\nTop Tables by Row Count:")
        print("-"*60)

        table_stats = []
        for table in tables:
            count = get_table_count(engine, table)
            table_stats.append((table, count))

        table_stats.sort(key=lambda x: x[1], reverse=True)

        for i, (table, count) in enumerate(table_stats[:10], 1):
            print(f"  {i:2d}. {table:30s} {count:10,} rows")

        return True

    except Exception as e:
        print(f"Error: {str(e)}")
        return False

def main():
    print("\n" + "="*60)
    print("[DB] Database Manager - Multi-Database Tool")
    print("="*60)

    # Select database
    print("\nSelect Database:")
    for key, db in DATABASES.items():
        status = "[OK]" if db["url"] else "[X]"
        print(f"  {key}. {status} {db['name']}")
    print("  0. Exit")

    db_choice = input("\nEnter choice (0-3): ").strip()

    if db_choice == "0":
        print("Goodbye!")
        return

    if db_choice not in DATABASES:
        print("Invalid choice!")
        return

    db_config = DATABASES[db_choice]
    print(f"\nConnecting to {db_config['name']}...")

    engine = get_database_connection(db_choice)
    if not engine:
        print("Failed to connect!")
        return

    print("[OK] Connected!")

    while True:
        print(f"\n" + "-"*60)
        print(f"Database: {db_config['name']}")
        print("-"*60)
        print("\n  1. Database Summary")
        print("  2. List All Tables")
        print("  3. View Table Details")
        print("  4. Switch Database")
        print("  0. Exit")

        choice = input("\nEnter choice: ").strip()

        if choice == "0":
            print("Goodbye!")
            return

        if choice == "4":
            main()
            return

        if choice == "1":
            show_database_summary(engine, db_config["name"])

        elif choice == "2":
            inspector = inspect(engine)
            tables = sorted(inspector.get_table_names())
            print(f"\n{'='*60}")
            print(f"Tables in {db_config['name']}: {len(tables)}")
            print(f"{'='*60}\n")

            table_data = list_tables_with_counts(engine)
            for row in table_data:
                print(f"  * {row['Table']:30s} ({row['Rows']:6,} rows)")

        elif choice == "3":
            inspector = inspect(engine)
            tables = sorted(inspector.get_table_names())

            print(f"\nSelect Table:")
            for i, table in enumerate(tables, 1):
                print(f"  {i:2d}. {table}")

            try:
                table_idx = int(input("\nEnter table number: ").strip()) - 1
                if 0 <= table_idx < len(tables):
                    table_name = tables[table_idx]

                    columns = inspector.get_columns(table_name)
                    count = get_table_count(engine, table_name)

                    print(f"\n{'='*60}")
                    print(f"Table: {table_name}")
                    print(f"{'='*60}")
                    print(f"Total Rows: {count:,}")
                    print(f"\nColumns ({len(columns)}):")

                    for col in columns:
                        nullable = "NULL" if col["nullable"] else "NOT NULL"
                        print(f"  * {col['name']:30s} {str(col['type']):20s} {nullable}")

                else:
                    print("Invalid table number!")
            except ValueError:
                print("Invalid input!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted!")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
