"""
Data Display Manager - Interactive tool to view data across all 3 databases
Supports Local (SQLite), SalPurFlask (Neon), and TradeFlow (Neon)
"""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from tabulate import tabulate

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
        "url": os.getenv("DATABASE_URL")  # Will be overridden by .env.render-salpurflask
    },
    "3": {
        "name": "TradeFlow (Neon)",
        "url": None  # Will be loaded from .env.render-tradeflow
    }
}

# Load TradeFlow URL
load_dotenv(os.path.join(BASE_DIR, ".env.render-tradeflow"), override=True)
DATABASES["3"]["url"] = os.getenv("DATABASE_URL")

def get_database_connection(db_key):
    """Create engine for selected database"""
    db_config = DATABASES.get(db_key)
    if not db_config or not db_config["url"]:
        print(f"Error: Database URL not configured")
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

def list_tables(engine):
    """List all tables in database"""
    inspector = inspect(engine)
    return sorted(inspector.get_table_names())

def get_table_data(engine, table_name, limit=100):
    """Get data from table"""
    try:
        with engine.connect() as conn:
            # Get row count
            count_result = conn.execute(text(f"SELECT COUNT(*) as cnt FROM {table_name}"))
            row_count = count_result.fetchone()[0]

            # Get data
            query = f"SELECT * FROM {table_name} LIMIT {limit}"
            result = conn.execute(text(query))

            columns = result.keys()
            rows = result.fetchall()

            return {
                "columns": columns,
                "rows": rows,
                "total_rows": row_count,
                "displayed": len(rows)
            }
    except Exception as e:
        print(f"Error fetching data: {str(e)}")
        return None

def get_table_info(engine, table_name):
    """Get table structure"""
    inspector = inspect(engine)
    columns = inspector.get_columns(table_name)

    col_info = []
    for col in columns:
        col_info.append({
            "Name": col["name"],
            "Type": str(col["type"]),
            "Nullable": "Yes" if col["nullable"] else "No"
        })
    return col_info

def main():
    print("\n" + "="*60)
    print("[STAT] Data Display Manager - Multi-Database Viewer")
    print("="*60)

    # Select database
    print("\nSelect Database:")
    for key, db in DATABASES.items():
        print(f"  {key}. {db['name']}")
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
        # List tables
        print("\n" + "-"*60)
        tables = list_tables(engine)
        print(f"\nTotal Tables: {len(tables)}")
        print("\nAvailable Tables:")

        for i, table in enumerate(tables, 1):
            print(f"  {i:2d}. {table}")

        print(f"\n  0. Switch Database")
        print(f"  99. Exit")

        choice = input("\nEnter table number (or option): ").strip()

        if choice == "0":
            main()
            return

        if choice == "99":
            print("Goodbye!")
            return

        try:
            table_idx = int(choice) - 1
            if table_idx < 0 or table_idx >= len(tables):
                print("Invalid table number!")
                continue

            table_name = tables[table_idx]

        except ValueError:
            print("Invalid input!")
            continue

        # Show table menu
        while True:
            print(f"\n" + "="*60)
            print(f"Table: {table_name}")
            print("="*60)
            print("\n  1. View Data (first 100 rows)")
            print("  2. View Table Structure")
            print("  3. Get Row Count")
            print("  4. Back to Table List")
            print("  0. Exit")

            action = input("\nEnter choice: ").strip()

            if action == "0":
                print("Goodbye!")
                return

            if action == "4":
                break

            if action == "1":
                print(f"\nFetching data from {table_name}...")
                data = get_table_data(engine, table_name)

                if data:
                    print(f"\nShowing {data['displayed']} of {data['total_rows']} rows")
                    print("-"*60)

                    if data['rows']:
                        # Format for tabulate
                        table_data = [list(row) for row in data['rows']]
                        print(tabulate(table_data, headers=data['columns'], tablefmt="grid", maxcolwidths=20))
                    else:
                        print("(No data)")

            elif action == "2":
                print(f"\nTable Structure: {table_name}")
                print("-"*60)

                col_info = get_table_info(engine, table_name)
                print(tabulate(col_info, headers="keys", tablefmt="grid"))

            elif action == "3":
                with engine.connect() as conn:
                    result = conn.execute(text(f"SELECT COUNT(*) as cnt FROM {table_name}"))
                    count = result.fetchone()[0]
                    print(f"\nTotal rows in {table_name}: {count:,}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted!")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {str(e)}")
        sys.exit(1)
