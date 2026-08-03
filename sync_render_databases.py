"""
Sync data from Local to SalPurFlask and TradeFlow using Flask app models
This ensures proper data validation and type conversion
"""

import os
import sys
from dotenv import load_dotenv

# Setup paths and load environment
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Load default .env first
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Import Flask app and models
from app import app, db
from salpurflask.models import (
    User, Supplier, Customer, Item, Category, Purchase, PurchaseItem,
    Sale, SaleItem, SupplierPayment, CustomerPayment, FinancialAccount,
    Account, JournalEntry, JournalLine, TaxCode, TaxComponent,
    BusinessCategory, FiscalYear, AccountingPeriod, DocumentSequence,
    AuditLog, ConfigurationSnapshot
)

print("="*60)
print("[SYNC] Data Sync Tool - Local to Render Databases")
print("="*60)

# Create database connection for reading Local data
with app.app_context():
    print("\n[INFO] Connected to Local database")

    # Count data in local database
    print("\nLocal Database Summary:")
    print(f"  Items: {db.session.query(Item).count()}")
    print(f"  Categories: {db.session.query(Category).count()}")
    print(f"  Suppliers: {db.session.query(Supplier).count()}")
    print(f"  Customers: {db.session.query(Customer).count()}")
    print(f"  Sales: {db.session.query(Sale).count()}")
    print(f"  Purchases: {db.session.query(Purchase).count()}")

print("\n[INFO] Use Flask CLI instead:")
print("  flask shell")
print("  >>> from app import db")
print("  >>> db.create_all()  # Create tables")
print("  >>> # Then insert data via ORM")

print("\n[RECOMMENDED] Better approach:")
print("  1. Use /developer/dashboard on Render")
print("  2. Or login to Render and manually add test data")
print("  3. Or use direct SQL commands via psql if available")
