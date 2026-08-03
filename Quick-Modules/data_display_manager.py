"""
Professional Data Display Manager
Display data from 3 databases: Local, SalPurFlask Render, TradeFlow Render
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import sqlite3
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from tabulate import tabulate
from colorama import Fore, Back, Style, init

init(autoreset=True)

class DataDisplayManager:
    def __init__(self):
        self.engine = None
        self.session = None
        self.db_type = None
        self.db_name = None
        self.tables = []

    def load_env_files(self):
        """Load environment variables"""
        env_file = Path('.env')
        env_render = Path('.env.render')

        if env_file.exists():
            load_dotenv(env_file)
        if env_render.exists():
            load_dotenv(env_render)

    def select_database(self):
        """Display database selection menu"""
        self.clear_screen()
        print(f"\n{Fore.CYAN}{'='*70}")
        print(f"{Fore.CYAN}[*] DATA DISPLAY MANAGER - SELECT DATABASE")
        print(f"{Fore.CYAN}{'='*70}\n")

        # Define database locations with their config files
        self.db_configs = {
            "1": {
                "name": "[L] Local Database (SQLite)",
                "location": "instance/database.db",
                "env_file": ".env",
                "type": "local"
            },
            "2": {
                "name": "[R] SalPurFlask Render (PostgreSQL)",
                "location": "https://salpurflask.onrender.com",
                "env_file": ".env.render-salpurflask",
                "type": "render"
            },
            "3": {
                "name": "[R] TradeFlow Demo Render (PostgreSQL)",
                "location": "https://tradeflow-demo.onrender.com",
                "env_file": ".env.render-tradeflow",
                "type": "render"
            }
        }

        databases = [
            (key, config["name"], config["location"])
            for key, config in self.db_configs.items()
        ]

        for num, name, location in databases:
            print(f"  {Fore.GREEN}{num}{Style.RESET_ALL}. {name}")
            print(f"     {Fore.YELLOW}{location}{Style.RESET_ALL}\n")

        while True:
            choice = input(f"{Fore.CYAN}Select database (1-3): {Style.RESET_ALL}").strip()
            if choice in ['1', '2', '3']:
                return choice
            print(f"{Fore.RED}[!] Invalid choice. Please select 1, 2, or 3.{Style.RESET_ALL}")

    def connect_local(self):
        """Connect to Local SQLite database"""
        db_path = "instance/database.db"
        if not Path(db_path).exists():
            print(f"{Fore.RED}[!] Error: {db_path} not found{Style.RESET_ALL}")
            return False

        try:
            self.engine = create_engine(f"sqlite:///{db_path}")
            self.db_type = "sqlite"
            self.db_name = "Local Database"
            print(f"{Fore.GREEN}[OK] Connected to Local Database{Style.RESET_ALL}")
            return True
        except Exception as e:
            print(f"{Fore.RED}[!] Connection failed: {e}{Style.RESET_ALL}")
            return False

    def connect_render(self, db_choice, env_file_name):
        """Connect to Render PostgreSQL database"""
        env_file = Path(env_file_name)

        if not env_file.exists():
            print(f"{Fore.RED}[!] Error: {env_file.name} not found{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}   Create {env_file.name} with DATABASE_URL{Style.RESET_ALL}")
            return False

        # Clear previous env vars and load fresh
        if "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]

        load_dotenv(env_file, override=True)
        db_url = os.getenv("DATABASE_URL")

        if not db_url or "USER:PASSWORD" in db_url:
            print(f"{Fore.RED}[!] Error: DATABASE_URL not configured in {env_file.name}{Style.RESET_ALL}")
            return False

        try:
            self.engine = create_engine(db_url, echo=False)
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))

            self.db_type = "postgresql"
            self.db_name = self.db_configs[db_choice]["name"].replace("[R] ", "")

            masked_url = db_url.split('@')[1] if '@' in db_url else db_url[:30] + "..."
            print(f"{Fore.GREEN}[OK] Connected to {self.db_name}{Style.RESET_ALL}")
            print(f"   {Fore.YELLOW}{masked_url}{Style.RESET_ALL}")
            return True
        except Exception as e:
            print(f"{Fore.RED}[!] Connection failed: {e}{Style.RESET_ALL}")
            return False

    def get_tables(self):
        """Get list of all tables"""
        try:
            inspector = inspect(self.engine)
            self.tables = sorted(inspector.get_table_names())
            return self.tables
        except Exception as e:
            print(f"{Fore.RED}[!] Error fetching tables: {e}{Style.RESET_ALL}")
            return []

    def display_table_menu(self):
        """Display table selection menu"""
        self.clear_screen()
        print(f"\n{Fore.CYAN}{'='*70}")
        print(f"{Fore.CYAN}[*] SELECT TABLE - {self.db_name}")
        print(f"{Fore.CYAN}{'='*70}\n")

        tables = self.get_tables()
        if not tables:
            print(f"{Fore.RED}[!] No tables found{Style.RESET_ALL}")
            return None

        # Display tables in columns
        for idx, table in enumerate(tables, 1):
            print(f"  {idx:2d}. {table}")
            if idx % 3 == 0:
                print()

        print(f"\n   0. Back to Database Selection")
        print(f"   Q. Quit\n")

        while True:
            choice = input(f"{Fore.CYAN}Select table (1-{len(tables)}) or (0/Q): {Style.RESET_ALL}").strip().upper()

            if choice == 'Q':
                return 'QUIT'
            if choice == '0':
                return None

            try:
                table_idx = int(choice) - 1
                if 0 <= table_idx < len(tables):
                    return tables[table_idx]
            except ValueError:
                pass

            print(f"{Fore.RED}[!] Invalid choice{Style.RESET_ALL}")

    def display_table_data(self, table_name):
        """Display table data in beautiful format"""
        self.clear_screen()

        try:
            inspector = inspect(self.engine)
            columns = [col['name'] for col in inspector.get_columns(table_name)]

            with self.engine.connect() as conn:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                row_count = result.scalar()

            print(f"\n{Fore.CYAN}{'='*100}")
            print(f"{Fore.CYAN}[*] TABLE DATA - {table_name.upper()}")
            print(f"{Fore.CYAN}{'='*100}")
            print(f"{Fore.YELLOW}Database: {self.db_name} | Rows: {row_count} | Columns: {len(columns)}{Style.RESET_ALL}\n")

            with self.engine.connect() as conn:
                result = conn.execute(text(f"SELECT * FROM {table_name} LIMIT 100"))
                rows = result.fetchall()

            if not rows:
                print(f"{Fore.YELLOW}[*] Table is empty{Style.RESET_ALL}\n")
                input(f"{Fore.CYAN}Press Enter to continue...{Style.RESET_ALL}")
                return

            table_data = []
            for row in rows:
                formatted_row = []
                for val in row:
                    if val is None:
                        formatted_row.append("NULL")
                    elif isinstance(val, bool):
                        formatted_row.append("True" if val else "False")
                    elif isinstance(val, (int, float)):
                        try:
                            formatted_row.append(f"{val:,}")
                        except:
                            formatted_row.append(str(val))
                    elif isinstance(val, str):
                        if len(val) > 50:
                            formatted_row.append(f"{val[:47]}...")
                        else:
                            formatted_row.append(val)
                    else:
                        formatted_row.append(str(val)[:100])
                table_data.append(formatted_row)

            print(tabulate(
                table_data,
                headers=columns,
                tablefmt="grid"
            ))

            print(f"\n{Fore.GREEN}Showing first {len(rows)} rows of {row_count} total{Style.RESET_ALL}")

            print(f"\n{Fore.CYAN}Column Information:{Style.RESET_ALL}")
            col_info = []
            for col in inspector.get_columns(table_name):
                col_type = str(col['type'])
                nullable = "Yes" if col['nullable'] else "No"
                col_info.append([col['name'], col_type, nullable])

            print(tabulate(
                col_info,
                headers=["Column", "Type", "Nullable"],
                tablefmt="simple"
            ))

        except Exception as e:
            print(f"{Fore.RED}[!] Error displaying table: {e}{Style.RESET_ALL}")

        print()
        input(f"{Fore.CYAN}Press Enter to continue...{Style.RESET_ALL}")

    def clear_screen(self):
        """Clear terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')

    def main_loop(self):
        """Main application loop"""
        while True:
            db_choice = self.select_database()

            if db_choice == '1':
                if not self.connect_local():
                    input(f"{Fore.CYAN}Press Enter to continue...{Style.RESET_ALL}")
                    continue
            elif db_choice in ['2', '3']:
                config = self.db_configs[db_choice]
                if not self.connect_render(db_choice, config["env_file"]):
                    input(f"{Fore.CYAN}Press Enter to continue...{Style.RESET_ALL}")
                    continue

            while True:
                table = self.display_table_menu()

                if table == 'QUIT':
                    print(f"\n{Fore.YELLOW}[*] Goodbye!{Style.RESET_ALL}\n")
                    return

                if table is None:
                    break

                self.display_table_data(table)

    def run(self):
        """Start the application"""
        self.load_env_files()
        try:
            self.main_loop()
        except KeyboardInterrupt:
            print(f"\n\n{Fore.YELLOW}[*] Application terminated{Style.RESET_ALL}\n")
            sys.exit(0)
        except Exception as e:
            print(f"\n{Fore.RED}[!] Fatal error: {e}{Style.RESET_ALL}\n")
            sys.exit(1)


if __name__ == "__main__":
    manager = DataDisplayManager()
    manager.run()
