"""
Add test data to Render free tier (using main app.py)
Instructions:
1. Get DATABASE_URL from Render dashboard (Environment tab)
2. Paste it in .env.render file
3. Run: python add_to_render_final.py
"""
import os
from pathlib import Path

# Load from .env.render (Render database URL)
env_file = Path('.env.render')
if not env_file.exists():
    print("\nERROR: .env.render file not found!")
    print("\nFix:")
    print("1. Go to https://dashboard.render.com")
    print("2. Select your service (tradeflow-demo)")
    print("3. Click 'Environment' tab")
    print("4. Copy the DATABASE_URL value")
    print("5. Edit .env.render and paste it")
    print("6. Run this script again\n")
    exit(1)

from dotenv import load_dotenv
load_dotenv(env_file)

RENDER_DB_URL = os.getenv("DATABASE_URL")

if not RENDER_DB_URL or "USER:PASSWORD" in RENDER_DB_URL:
    print("\nERROR: DATABASE_URL not set or still has placeholder!")
    print("\nEdit .env.render and replace:")
    print("  #DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require")
    print("\nWith actual value from Render dashboard\n")
    exit(1)

# Override app.py's DATABASE_URL
os.environ["DATABASE_URL"] = RENDER_DB_URL

# Now import app - it will use RENDER_DB_URL
from app import app, db
from salpurflask.models import (
    User, Supplier, Customer, Item, Category, BusinessCategory,
    FinancialAccount, TaxCode, TaxComponent
)
from werkzeug.security import generate_password_hash
from sqlalchemy import text

def add_test_data():
    """Add test data to Render PostgreSQL"""
    print("\n" + "=" * 80)
    print("ADDING TEST DATA TO RENDER FREE TIER")
    print("=" * 80)

    # Show masked URL
    masked_url = RENDER_DB_URL.split('@')[1] if '@' in RENDER_DB_URL else RENDER_DB_URL
    print(f"\nDatabase: {masked_url}\n")

    with app.app_context():
        try:
            # Test connection
            db.session.execute(text("SELECT 1"))
            print("[OK] Successfully connected to Render PostgreSQL!\n")
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}")
            print("\nFix: Check your DATABASE_URL in .env.render\n")
            return

        # 1. Users
        print("1. Adding Users...")
        try:
            if not User.query.filter_by(email='test@example.com').first():
                user = User(
                    name='Test User',
                    email='test@example.com',
                    password=generate_password_hash('test123'),
                    verified=True,
                    role='manager'
                )
                db.session.add(user)
                db.session.commit()
                print("   [OK] test@example.com")
            else:
                print("   [SKIP] test@example.com exists")
        except Exception as e:
            db.session.rollback()
            print(f"   [ERROR] {str(e)[:60]}")

        # 2. Suppliers
        print("2. Adding Suppliers...")
        try:
            suppliers = [
                ('TechCorp Industries', '03001234567', 'Karachi', 0.0),
                ('Global Imports Ltd', '03005678901', 'Lahore', 0.0),
                ('Premium Distributors', '03009876543', 'Islamabad', 0.0),
            ]
            for name, contact, address, opening_bal in suppliers:
                if not Supplier.query.filter_by(name=name).first():
                    db.session.add(Supplier(
                        name=name, contact=contact, address=address,
                        opening_balance=opening_bal
                    ))
                    print(f"   [OK] {name}")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"   [ERROR] {str(e)[:60]}")

        # 3. Customers
        print("3. Adding Customers...")
        try:
            customers = [
                ('Alpha Trading Co', '03114567890', 'Faisalabad', 0.0),
                ('Beta Retail Stores', '03115678901', 'Multan', 0.0),
                ('Gamma Enterprises', '03116789012', 'Peshawar', 0.0),
            ]
            for name, contact, address, opening_bal in customers:
                if not Customer.query.filter_by(name=name).first():
                    db.session.add(Customer(
                        name=name, contact=contact, address=address,
                        opening_balance=opening_bal
                    ))
                    print(f"   [OK] {name}")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"   [ERROR] {str(e)[:60]}")

        # 4. Categories
        print("4. Adding Categories...")
        try:
            for name in ['Electronics', 'Textiles', 'Food & Beverage']:
                if not Category.query.filter_by(name=name).first():
                    db.session.add(Category(name=name))
                    print(f"   [OK] {name}")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"   [ERROR] {str(e)[:60]}")

        # 5. Business Categories
        print("5. Adding Business Categories...")
        try:
            biz_cats = [
                ('Premium Products', 'premium', 'High-end', 'star', True, 1, '#FF6B6B'),
                ('Budget Range', 'budget', 'Affordable', 'dollar', True, 2, '#4ECDC4'),
                ('Seasonal Items', 'seasonal', 'Limited', 'calendar', True, 3, '#FFE66D'),
            ]
            for name, slug, desc, icon, enabled, priority, color in biz_cats:
                if not BusinessCategory.query.filter_by(slug=slug).first():
                    db.session.add(BusinessCategory(
                        name=name, slug=slug, description=desc,
                        icon=icon, is_enabled=enabled, priority=priority, color=color
                    ))
                    print(f"   [OK] {name}")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"   [ERROR] {str(e)[:60]}")

        # 6. Items
        print("6. Adding Items...")
        try:
            category = Category.query.filter_by(name='Electronics').first()
            cat_id = category.id if category else None

            items = [
                ('Laptop Pro 15', cat_id, 'pcs', '12345', 85000.00, 95000.00, 17.0, True),
                ('USB-C Cable 2m', cat_id, 'pcs', '12346', 500.00, 750.00, 0.0, False),
                ('Wireless Mouse', cat_id, 'pcs', '12347', 2000.00, 2500.00, 17.0, True),
                ('Keyboard Mechanical', cat_id, 'pcs', '12348', 5000.00, 6500.00, 17.0, True),
                ('Monitor 27 inch', cat_id, 'pcs', '12349', 25000.00, 30000.00, 17.0, True),
            ]
            for name, c_id, unit, barcode, p_price, s_price, tax, is_tax in items:
                if not Item.query.filter_by(name=name).first():
                    db.session.add(Item(
                        name=name, category_id=c_id, unit=unit, barcode=barcode,
                        opening_stock=0, stock=0, reorder_level=0,
                        purchase_price=p_price, sale_price=s_price,
                        inventory_value=0.0, default_tax_percent=tax, is_taxable=is_tax
                    ))
                    print(f"   [OK] {name}")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"   [ERROR] {str(e)[:60]}")

        # 7. Financial Accounts
        print("7. Adding Financial Accounts...")
        try:
            accounts = [
                ('Main Cash Account', 'Cash', 'asset', 50000.00),
                ('Business Bank Account', 'Bank', 'asset', 100000.00),
            ]
            for name, method, acc_type, opening_bal in accounts:
                if not FinancialAccount.query.filter_by(name=name).first():
                    db.session.add(FinancialAccount(
                        name=name, method=method, account_type=acc_type,
                        opening_balance=opening_bal, is_active=True, is_control=False
                    ))
                    print(f"   [OK] {name}")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"   [ERROR] {str(e)[:60]}")

        # 8. Tax Codes
        print("8. Adding Tax Codes...")
        try:
            if not TaxCode.query.filter_by(name='Standard').first():
                tax = TaxCode(name='Standard', is_active=True)
                db.session.add(tax)
                db.session.flush()
                component = TaxComponent(
                    tax_code_id=tax.id, name='Sales Tax (Pakistan)',
                    rate=17.0, input_account_id=1, output_account_id=1
                )
                db.session.add(component)
                db.session.commit()
                print("   [OK] Standard Tax")
        except Exception as e:
            db.session.rollback()
            print(f"   [ERROR] {str(e)[:60]}")

        # Summary
        print("\n" + "=" * 80)
        print("DATA ADDED SUCCESSFULLY!")
        print("=" * 80)
        print(f"Users:               {User.query.count()}")
        print(f"Suppliers:           {Supplier.query.count()}")
        print(f"Customers:           {Customer.query.count()}")
        print(f"Items:               {Item.query.count()}")
        print(f"Categories:          {Category.query.count()}")
        print(f"Business Categories: {BusinessCategory.query.count()}")
        print(f"Financial Accounts:  {FinancialAccount.query.count()}")
        print(f"Tax Codes:           {TaxCode.query.count()}")
        print("=" * 80 + "\n")

        print("Next: Check https://tradeflow-demo.onrender.com/\n")

if __name__ == '__main__':
    add_test_data()
