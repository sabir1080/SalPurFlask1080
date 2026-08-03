"""
Database Schema Synchronization Tool
Synchronizes schema (tables, columns, constraints) across all 3 databases
Data remains separate in each database
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, MetaData, Table
from sqlalchemy.schema import CreateTable, DropTable, AddConstraint
import difflib

class DatabaseSyncManager:
    def __init__(self):
        self.connections = {}
        self.inspectors = {}
        self.load_env_files()

    def load_env_files(self):
        """Load environment variables from all .env files"""
        env_files = {
            "local": Path(".env"),
            "salpurflask": Path(".env.render-salpurflask"),
            "tradeflow": Path(".env.render-tradeflow"),
        }

        for name, env_file in env_files.items():
            if env_file.exists():
                load_dotenv(env_file)

    def connect(self, db_name):
        """Connect to database"""
        db_url = os.getenv("DATABASE_URL")

        if not db_url:
            if db_name == "local":
                db_url = f"sqlite:///{Path('instance/database.db').resolve()}"
            else:
                print(f"[!] No DATABASE_URL for {db_name}")
                return False

        try:
            engine = create_engine(db_url, echo=False)
            with engine.connect() as conn:
                from sqlalchemy import text
                conn.execute(text("SELECT 1"))

            self.connections[db_name] = engine
            self.inspectors[db_name] = inspect(engine)
            print(f"[OK] Connected to {db_name}")
            return True
        except Exception as e:
            print(f"[!] Connection failed for {db_name}: {e}")
            return False

    def get_tables(self, db_name):
        """Get all table names"""
        if db_name not in self.inspectors:
            return []
        return sorted(self.inspectors[db_name].get_table_names())

    def get_schema_info(self, db_name, table_name):
        """Get detailed schema info for a table"""
        inspector = self.inspectors[db_name]

        schema = {
            "columns": [],
            "primary_keys": [],
            "indexes": [],
            "foreign_keys": [],
        }

        # Columns
        for col in inspector.get_columns(table_name):
            schema["columns"].append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col["nullable"],
                "default": col["default"],
            })

        # Primary keys
        pk = inspector.get_pk_constraint(table_name)
        if pk and pk.get("constrained_columns"):
            schema["primary_keys"] = list(pk["constrained_columns"])

        # Foreign keys
        for fk in inspector.get_foreign_keys(table_name):
            schema["foreign_keys"].append({
                "name": fk.get("name"),
                "constrained_columns": fk["constrained_columns"],
                "referred_table": fk["referred_table"],
                "referred_columns": fk["referred_columns"],
            })

        # Indexes
        for idx in inspector.get_indexes(table_name):
            schema["indexes"].append({
                "name": idx["name"],
                "columns": idx["column_names"],
                "unique": idx.get("unique", False),
            })

        return schema

    def compare_schemas(self, source_db, target_db):
        """Compare schema between two databases"""
        source_tables = set(self.get_tables(source_db))
        target_tables = set(self.get_tables(target_db))

        differences = {
            "missing_tables": sorted(source_tables - target_tables),
            "extra_tables": sorted(target_tables - source_tables),
            "column_differences": {},
        }

        # Check column differences in common tables
        common_tables = source_tables & target_tables
        for table in common_tables:
            source_schema = self.get_schema_info(source_db, table)
            target_schema = self.get_schema_info(target_db, table)

            source_cols = {c["name"]: c for c in source_schema["columns"]}
            target_cols = {c["name"]: c for c in target_schema["columns"]}

            missing_cols = set(source_cols.keys()) - set(target_cols.keys())
            extra_cols = set(target_cols.keys()) - set(source_cols.keys())

            if missing_cols or extra_cols:
                differences["column_differences"][table] = {
                    "missing": list(missing_cols),
                    "extra": list(extra_cols),
                }

        return differences

    def display_comparison(self, source_db, target_db):
        """Display schema comparison"""
        diffs = self.compare_schemas(source_db, target_db)

        print(f"\n{'='*70}")
        print(f"SCHEMA COMPARISON: {source_db} → {target_db}")
        print(f"{'='*70}\n")

        if diffs["missing_tables"]:
            print(f"[!] Missing tables in {target_db}:")
            for table in diffs["missing_tables"]:
                print(f"    - {table}")

        if diffs["extra_tables"]:
            print(f"[!] Extra tables in {target_db}:")
            for table in diffs["extra_tables"]:
                print(f"    - {table}")

        if diffs["column_differences"]:
            print(f"\n[!] Column differences:")
            for table, cols in diffs["column_differences"].items():
                if cols["missing"]:
                    print(f"    {table} missing columns: {', '.join(cols['missing'])}")
                if cols["extra"]:
                    print(f"    {table} extra columns: {', '.join(cols['extra'])}")

        if not diffs["missing_tables"] and not diffs["extra_tables"] and not diffs["column_differences"]:
            print(f"[OK] Schemas are identical!")

        return diffs

    def get_all_tables_comparison(self):
        """Compare all 3 databases"""
        dbs = ["local", "salpurflask", "tradeflow"]

        print(f"\n{'='*70}")
        print("ALL DATABASE SCHEMA COMPARISON")
        print(f"{'='*70}\n")

        for db in dbs:
            tables = self.get_tables(db)
            print(f"{db.upper():20} - Tables: {len(tables):3} - {', '.join(tables[:5])}{'...' if len(tables) > 5 else ''}")

    def run(self):
        """Main execution"""
        dbs = ["local", "salpurflask", "tradeflow"]

        print(f"\n{'='*70}")
        print("DATABASE SCHEMA SYNCHRONIZATION TOOL")
        print(f"{'='*70}\n")

        # Connect to all databases
        print("[*] Connecting to databases...\n")
        for db in dbs:
            if not self.connect(db):
                print(f"[!] Failed to connect to {db}")
                return

        # Show menu
        while True:
            print(f"\n{'='*70}")
            print("MENU")
            print(f"{'='*70}")
            print("1. Compare Local <-> SalPurFlask")
            print("2. Compare Local <-> TradeFlow")
            print("3. Compare SalPurFlask <-> TradeFlow")
            print("4. View all databases schema summary")
            print("5. Generate sync report")
            print("0. Exit\n")

            choice = input("Select option (0-5): ").strip()

            if choice == "1":
                self.display_comparison("local", "salpurflask")
                input("\nPress Enter to continue...")
            elif choice == "2":
                self.display_comparison("local", "tradeflow")
                input("\nPress Enter to continue...")
            elif choice == "3":
                self.display_comparison("salpurflask", "tradeflow")
                input("\nPress Enter to continue...")
            elif choice == "4":
                self.get_all_tables_comparison()
                input("\nPress Enter to continue...")
            elif choice == "5":
                self.generate_sync_report()
                input("\nPress Enter to continue...")
            elif choice == "0":
                print("\n[*] Goodbye!\n")
                break
            else:
                print("[!] Invalid option")

    def generate_sync_report(self):
        """Generate comprehensive sync report"""
        dbs = ["local", "salpurflask", "tradeflow"]

        print(f"\n{'='*70}")
        print("SYNC REPORT - What needs to be synced")
        print(f"{'='*70}\n")

        # Use local as source
        source_db = "local"
        source_tables = self.get_tables(source_db)

        print(f"SOURCE DATABASE: {source_db.upper()}")
        print(f"Total Tables: {len(source_tables)}\n")

        for target_db in dbs:
            if target_db == source_db:
                continue

            diffs = self.compare_schemas(source_db, target_db)

            print(f"\nTARGET: {target_db.upper()}")
            print("-" * 70)

            if diffs["missing_tables"]:
                print(f"  Tables to CREATE: {len(diffs['missing_tables'])}")
                for table in diffs["missing_tables"]:
                    print(f"    - CREATE TABLE {table}")

            if diffs["column_differences"]:
                print(f"  Columns to ADD/MODIFY: {len(diffs['column_differences'])}")
                for table, cols in diffs["column_differences"].items():
                    for col in cols["missing"]:
                        print(f"    - ALTER TABLE {table} ADD COLUMN {col}")

            if not diffs["missing_tables"] and not diffs["column_differences"]:
                print("  [OK] All schemas synchronized!")


if __name__ == "__main__":
    manager = DatabaseSyncManager()
    manager.run()
