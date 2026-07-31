"""
Interactive Database Manager
Add, Edit, Delete data from Local or Render database with menu-driven interface
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# Load environment
env_file = Path('.env.render')
if env_file.exists():
    load_dotenv(env_file)

def select_database():
    """Select which database to work with"""
    print("\n" + "=" * 70)
    print("DATABASE SELECTION")
    print("=" * 70)
    print("1. Local Database (SQLite)")
    print("2. Render Database (PostgreSQL)")
    choice = input("\nSelect database (1-2): ").strip()

    if choice == "2":
        render_url = os.getenv("DATABASE_URL")
        if not render_url or "USER:PASSWORD" in render_url:
            print("\nERROR: DATABASE_URL not configured in .env.render")
            return None
        os.environ["DATABASE_URL"] = render_url
        print("[OK] Connected to Render")
    else:
        if "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]
        print("[OK] Using Local Database")

    return True

def get_app_context():
    """Get Flask app context"""
    from app import app, db
    return app, db

def main_menu():
    """Main menu"""
    print("\n" + "=" * 70)
    print("DATABASE MANAGER - MAIN MENU")
    print("=" * 70)
    print("1. Manage Users")
    print("2. Manage Suppliers")
    print("3. Manage Customers")
    print("4. Manage Items (Products)")
    print("5. Manage Categories")
    print("6. Manage Business Categories")
    print("7. Manage Financial Accounts")
    print("8. Manage Tax Codes")
    print("9. View All Data Summary")
    print("0. Exit")

    return input("\nSelect option (0-9): ").strip()

def manage_users(app, db):
    """Manage users"""
    from salpurflask.models import User
    from salpurflask.extensions import pwd_context
    from sqlalchemy import text

    with app.app_context():
        while True:
            print("\n" + "-" * 70)
            print("USER MANAGEMENT")
            print("-" * 70)
            print("1. Add New User")
            print("2. Edit User")
            print("3. Delete User")
            print("4. View All Users")
            print("0. Back")

            choice = input("\nSelect (0-4): ").strip()

            if choice == "1":
                name = input("User Name: ").strip()
                email = input("Email: ").strip().lower()
                password = input("Password (min 6 chars): ").strip()

                if len(password) < 6:
                    print("ERROR: Password must be at least 6 characters")
                    continue

                if User.query.filter_by(email=email).first():
                    print("ERROR: Email already exists")
                    continue

                print("\nRole:")
                print("1. admin")
                print("2. manager")
                print("3. staff")
                role = input("Select role (1-3): ").strip()
                roles = {"1": "admin", "2": "manager", "3": "staff"}

                user = User(
                    name=name,
                    email=email,
                    password=pwd_context.hash(password),
                    verified=True,
                    role=roles.get(role, "staff")
                )
                db.session.add(user)
                db.session.commit()
                print(f"[OK] User '{email}' created successfully")

            elif choice == "2":
                email = input("Enter email to edit: ").strip().lower()
                user = User.query.filter_by(email=email).first()

                if not user:
                    print("ERROR: User not found")
                    continue

                print(f"\nEditing: {user.email}")
                print("1. Change Password")
                print("2. Change Name")
                print("3. Change Role")
                print("0. Cancel")

                edit_choice = input("Select (0-3): ").strip()

                if edit_choice == "1":
                    new_pass = input("New Password: ").strip()
                    if len(new_pass) >= 6:
                        user.password = pwd_context.hash(new_pass)
                        db.session.commit()
                        print("[OK] Password updated")
                    else:
                        print("ERROR: Password too short")

                elif edit_choice == "2":
                    new_name = input("New Name: ").strip()
                    user.name = new_name
                    db.session.commit()
                    print("[OK] Name updated")

                elif edit_choice == "3":
                    print("1. admin | 2. manager | 3. staff")
                    role_choice = input("Select role (1-3): ").strip()
                    roles = {"1": "admin", "2": "manager", "3": "staff"}
                    user.role = roles.get(role_choice, user.role)
                    db.session.commit()
                    print("[OK] Role updated")

            elif choice == "3":
                email = input("Enter email to delete: ").strip().lower()
                user = User.query.filter_by(email=email).first()

                if not user:
                    print("ERROR: User not found")
                    continue

                confirm = input(f"Delete '{email}'? (yes/no): ").strip().lower()
                if confirm == "yes":
                    db.session.delete(user)
                    db.session.commit()
                    print("[OK] User deleted")

            elif choice == "4":
                users = User.query.all()
                print(f"\nTotal Users: {len(users)}\n")
                for u in users:
                    print(f"  {u.email:30} | Role: {u.role:10} | Name: {u.name}")

            elif choice == "0":
                break

def manage_suppliers(app, db):
    """Manage suppliers"""
    from salpurflask.models import Supplier

    with app.app_context():
        while True:
            print("\n" + "-" * 70)
            print("SUPPLIER MANAGEMENT")
            print("-" * 70)
            print("1. Add New Supplier")
            print("2. Edit Supplier")
            print("3. Delete Supplier")
            print("4. View All Suppliers")
            print("0. Back")

            choice = input("\nSelect (0-4): ").strip()

            if choice == "1":
                name = input("Supplier Name: ").strip()
                contact = input("Contact Number: ").strip()
                address = input("Address: ").strip()
                opening_bal = input("Opening Balance (0 if none): ").strip()

                try:
                    opening_bal = float(opening_bal) if opening_bal else 0.0
                except:
                    print("ERROR: Invalid amount")
                    continue

                if Supplier.query.filter_by(name=name).first():
                    print("ERROR: Supplier already exists")
                    continue

                supplier = Supplier(
                    name=name,
                    contact=contact,
                    address=address,
                    opening_balance=opening_bal
                )
                db.session.add(supplier)
                db.session.commit()
                print(f"[OK] Supplier '{name}' created")

            elif choice == "2":
                name = input("Enter supplier name to edit: ").strip()
                supplier = Supplier.query.filter_by(name=name).first()

                if not supplier:
                    print("ERROR: Supplier not found")
                    continue

                print(f"\nEditing: {supplier.name}")
                print("1. Change Contact")
                print("2. Change Address")
                print("3. Change Opening Balance")
                print("0. Cancel")

                edit_choice = input("Select (0-3): ").strip()

                if edit_choice == "1":
                    supplier.contact = input("New Contact: ").strip()
                    db.session.commit()
                    print("[OK] Contact updated")
                elif edit_choice == "2":
                    supplier.address = input("New Address: ").strip()
                    db.session.commit()
                    print("[OK] Address updated")
                elif edit_choice == "3":
                    try:
                        supplier.opening_balance = float(input("New Balance: ").strip())
                        db.session.commit()
                        print("[OK] Balance updated")
                    except:
                        print("ERROR: Invalid amount")

            elif choice == "3":
                name = input("Enter supplier name to delete: ").strip()
                supplier = Supplier.query.filter_by(name=name).first()

                if not supplier:
                    print("ERROR: Supplier not found")
                    continue

                confirm = input(f"Delete '{name}'? (yes/no): ").strip().lower()
                if confirm == "yes":
                    db.session.delete(supplier)
                    db.session.commit()
                    print("[OK] Supplier deleted")

            elif choice == "4":
                suppliers = Supplier.query.all()
                print(f"\nTotal Suppliers: {len(suppliers)}\n")
                for s in suppliers:
                    print(f"  {s.name:30} | Contact: {s.contact:15} | Balance: {s.opening_balance:12,.2f}")

            elif choice == "0":
                break

def manage_customers(app, db):
    """Manage customers"""
    from salpurflask.models import Customer

    with app.app_context():
        while True:
            print("\n" + "-" * 70)
            print("CUSTOMER MANAGEMENT")
            print("-" * 70)
            print("1. Add New Customer")
            print("2. Edit Customer")
            print("3. Delete Customer")
            print("4. View All Customers")
            print("0. Back")

            choice = input("\nSelect (0-4): ").strip()

            if choice == "1":
                name = input("Customer Name: ").strip()
                contact = input("Contact Number: ").strip()
                address = input("Address: ").strip()
                opening_bal = input("Opening Balance (0 if none): ").strip()

                try:
                    opening_bal = float(opening_bal) if opening_bal else 0.0
                except:
                    print("ERROR: Invalid amount")
                    continue

                if Customer.query.filter_by(name=name).first():
                    print("ERROR: Customer already exists")
                    continue

                customer = Customer(
                    name=name,
                    contact=contact,
                    address=address,
                    opening_balance=opening_bal
                )
                db.session.add(customer)
                db.session.commit()
                print(f"[OK] Customer '{name}' created")

            elif choice == "2":
                name = input("Enter customer name to edit: ").strip()
                customer = Customer.query.filter_by(name=name).first()

                if not customer:
                    print("ERROR: Customer not found")
                    continue

                print(f"\nEditing: {customer.name}")
                print("1. Change Contact")
                print("2. Change Address")
                print("3. Change Opening Balance")
                print("0. Cancel")

                edit_choice = input("Select (0-3): ").strip()

                if edit_choice == "1":
                    customer.contact = input("New Contact: ").strip()
                    db.session.commit()
                    print("[OK] Contact updated")
                elif edit_choice == "2":
                    customer.address = input("New Address: ").strip()
                    db.session.commit()
                    print("[OK] Address updated")
                elif edit_choice == "3":
                    try:
                        customer.opening_balance = float(input("New Balance: ").strip())
                        db.session.commit()
                        print("[OK] Balance updated")
                    except:
                        print("ERROR: Invalid amount")

            elif choice == "3":
                name = input("Enter customer name to delete: ").strip()
                customer = Customer.query.filter_by(name=name).first()

                if not customer:
                    print("ERROR: Customer not found")
                    continue

                confirm = input(f"Delete '{name}'? (yes/no): ").strip().lower()
                if confirm == "yes":
                    db.session.delete(customer)
                    db.session.commit()
                    print("[OK] Customer deleted")

            elif choice == "4":
                customers = Customer.query.all()
                print(f"\nTotal Customers: {len(customers)}\n")
                for c in customers:
                    print(f"  {c.name:30} | Contact: {c.contact:15} | Balance: {c.opening_balance:12,.2f}")

            elif choice == "0":
                break

def manage_items(app, db):
    """Manage items (products)"""
    from salpurflask.models import Item, Category

    with app.app_context():
        while True:
            print("\n" + "-" * 70)
            print("ITEM MANAGEMENT")
            print("-" * 70)
            print("1. Add New Item")
            print("2. Edit Item")
            print("3. Delete Item")
            print("4. View All Items")
            print("0. Back")

            choice = input("\nSelect (0-4): ").strip()

            if choice == "1":
                name = input("Item Name: ").strip()

                categories = Category.query.all()
                if categories:
                    print("\nCategories:")
                    for i, cat in enumerate(categories, 1):
                        print(f"  {i}. {cat.name}")
                    cat_choice = input("Select category (number): ").strip()
                    try:
                        cat_id = categories[int(cat_choice)-1].id
                    except:
                        cat_id = None
                else:
                    cat_id = None

                unit = input("Unit (pcs/kg/ltr/box): ").strip()
                barcode = input("Barcode (optional): ").strip()

                try:
                    purchase_price = float(input("Purchase Price: ").strip())
                    sale_price = float(input("Sale Price: ").strip())
                    tax_percent = float(input("Tax Percent (0 if none): ").strip() or "0")
                except:
                    print("ERROR: Invalid price")
                    continue

                is_taxable = input("Is Taxable? (yes/no): ").strip().lower() == "yes"

                if Item.query.filter_by(name=name).first():
                    print("ERROR: Item already exists")
                    continue

                item = Item(
                    name=name,
                    category_id=cat_id,
                    unit=unit,
                    barcode=barcode or None,
                    opening_stock=0,
                    stock=0,
                    reorder_level=0,
                    purchase_price=purchase_price,
                    sale_price=sale_price,
                    inventory_value=0.0,
                    default_tax_percent=tax_percent,
                    is_taxable=is_taxable
                )
                db.session.add(item)
                db.session.commit()
                print(f"[OK] Item '{name}' created")

            elif choice == "4":
                items = Item.query.all()
                print(f"\nTotal Items: {len(items)}\n")
                for item in items:
                    print(f"  {item.name:30} | Purchase: {item.purchase_price:10,.2f} | Sale: {item.sale_price:10,.2f}")

            elif choice == "0":
                break

def manage_categories(app, db):
    """Manage item categories"""
    from salpurflask.models import Category

    with app.app_context():
        while True:
            print("\n" + "-" * 70)
            print("CATEGORY MANAGEMENT")
            print("-" * 70)
            print("1. Add New Category")
            print("2. View All Categories")
            print("3. Delete Category")
            print("0. Back")

            choice = input("\nSelect (0-3): ").strip()

            if choice == "1":
                name = input("Category Name: ").strip()

                if Category.query.filter_by(name=name).first():
                    print("ERROR: Category already exists")
                    continue

                category = Category(name=name)
                db.session.add(category)
                db.session.commit()
                print(f"[OK] Category '{name}' created")

            elif choice == "2":
                categories = Category.query.all()
                print(f"\nTotal Categories: {len(categories)}\n")
                for c in categories:
                    print(f"  {c.name}")

            elif choice == "3":
                name = input("Category to delete: ").strip()
                cat = Category.query.filter_by(name=name).first()
                if cat:
                    confirm = input(f"Delete '{name}'? (yes/no): ").strip().lower()
                    if confirm == "yes":
                        db.session.delete(cat)
                        db.session.commit()
                        print("[OK] Deleted")

            elif choice == "0":
                break

def manage_business_categories(app, db):
    """Manage business categories"""
    from salpurflask.models import BusinessCategory

    with app.app_context():
        while True:
            print("\n" + "-" * 70)
            print("BUSINESS CATEGORY MANAGEMENT")
            print("-" * 70)
            print("1. Add New Business Category")
            print("2. View All Business Categories")
            print("3. Delete Business Category")
            print("0. Back")

            choice = input("\nSelect (0-3): ").strip()

            if choice == "1":
                name = input("Business Category Name: ").strip()
                slug = input("Slug (e.g., premium-products): ").strip()
                desc = input("Description: ").strip()
                icon = input("Icon (e.g., star): ").strip()

                if BusinessCategory.query.filter_by(slug=slug).first():
                    print("ERROR: Business Category already exists")
                    continue

                biz_cat = BusinessCategory(
                    name=name, slug=slug, description=desc, icon=icon,
                    is_enabled=True, priority=1, color="#FF6B6B"
                )
                db.session.add(biz_cat)
                db.session.commit()
                print(f"[OK] Business Category '{name}' created")

            elif choice == "2":
                cats = BusinessCategory.query.all()
                print(f"\nTotal: {len(cats)}\n")
                for c in cats:
                    print(f"  {c.name:30} | Slug: {c.slug}")

            elif choice == "3":
                name = input("Business Category to delete: ").strip()
                cat = BusinessCategory.query.filter_by(name=name).first()
                if cat:
                    confirm = input(f"Delete '{name}'? (yes/no): ").strip().lower()
                    if confirm == "yes":
                        db.session.delete(cat)
                        db.session.commit()
                        print("[OK] Deleted")

            elif choice == "0":
                break

def manage_financial_accounts(app, db):
    """Manage financial accounts"""
    from salpurflask.models import FinancialAccount

    with app.app_context():
        while True:
            print("\n" + "-" * 70)
            print("FINANCIAL ACCOUNT MANAGEMENT")
            print("-" * 70)
            print("1. Add New Account")
            print("2. View All Accounts")
            print("3. Delete Account")
            print("0. Back")

            choice = input("\nSelect (0-3): ").strip()

            if choice == "1":
                name = input("Account Name: ").strip()
                print("Account Type: 1. asset | 2. liability | 3. equity")
                acc_type = input("Select (1-3): ").strip()
                types = {"1": "asset", "2": "liability", "3": "equity"}

                try:
                    opening_bal = float(input("Opening Balance: ").strip() or "0")
                except:
                    print("ERROR: Invalid amount")
                    continue

                if FinancialAccount.query.filter_by(name=name).first():
                    print("ERROR: Account already exists")
                    continue

                account = FinancialAccount(
                    name=name,
                    method=name,
                    account_type=types.get(acc_type, "asset"),
                    opening_balance=opening_bal,
                    is_active=True,
                    is_control=False
                )
                db.session.add(account)
                db.session.commit()
                print(f"[OK] Account '{name}' created")

            elif choice == "2":
                accounts = FinancialAccount.query.all()
                print(f"\nTotal Accounts: {len(accounts)}\n")
                for a in accounts:
                    print(f"  {a.name:30} | Type: {a.account_type:10} | Balance: {a.opening_balance:12,.2f}")

            elif choice == "3":
                name = input("Account to delete: ").strip()
                acc = FinancialAccount.query.filter_by(name=name).first()
                if acc:
                    confirm = input(f"Delete '{name}'? (yes/no): ").strip().lower()
                    if confirm == "yes":
                        db.session.delete(acc)
                        db.session.commit()
                        print("[OK] Deleted")

            elif choice == "0":
                break

def manage_tax_codes(app, db):
    """Manage tax codes"""
    from salpurflask.models import TaxCode, TaxComponent

    with app.app_context():
        while True:
            print("\n" + "-" * 70)
            print("TAX CODE MANAGEMENT")
            print("-" * 70)
            print("1. Add New Tax Code")
            print("2. View All Tax Codes")
            print("3. Delete Tax Code")
            print("0. Back")

            choice = input("\nSelect (0-3): ").strip()

            if choice == "1":
                name = input("Tax Code Name: ").strip()

                if TaxCode.query.filter_by(name=name).first():
                    print("ERROR: Tax Code already exists")
                    continue

                tax = TaxCode(name=name, is_active=True)
                db.session.add(tax)
                db.session.flush()

                try:
                    rate = float(input("Tax Rate (%): ").strip())
                except:
                    print("ERROR: Invalid rate")
                    db.session.rollback()
                    continue

                component = TaxComponent(
                    tax_code_id=tax.id,
                    name=f"{name} Rate",
                    rate=rate,
                    input_account_id=1,
                    output_account_id=1
                )
                db.session.add(component)
                db.session.commit()
                print(f"[OK] Tax Code '{name}' ({rate}%) created")

            elif choice == "2":
                taxes = TaxCode.query.all()
                print(f"\nTotal Tax Codes: {len(taxes)}\n")
                for t in taxes:
                    print(f"  {t.name:30} | Active: {t.is_active}")

            elif choice == "3":
                name = input("Tax Code to delete: ").strip()
                tax = TaxCode.query.filter_by(name=name).first()
                if tax:
                    confirm = input(f"Delete '{name}'? (yes/no): ").strip().lower()
                    if confirm == "yes":
                        db.session.delete(tax)
                        db.session.commit()
                        print("[OK] Deleted")

            elif choice == "0":
                break

def view_summary(app, db):
    """View data summary"""
    from salpurflask.models import (
        User, Supplier, Customer, Item, Category,
        BusinessCategory, FinancialAccount, TaxCode
    )

    with app.app_context():
        print("\n" + "=" * 70)
        print("DATABASE SUMMARY")
        print("=" * 70)
        print(f"Users:                {User.query.count()}")
        print(f"Suppliers:            {Supplier.query.count()}")
        print(f"Customers:            {Customer.query.count()}")
        print(f"Items:                {Item.query.count()}")
        print(f"Categories:           {Category.query.count()}")
        print(f"Business Categories:  {BusinessCategory.query.count()}")
        print(f"Financial Accounts:   {FinancialAccount.query.count()}")
        print(f"Tax Codes:            {TaxCode.query.count()}")
        print("=" * 70 + "\n")

def main():
    """Main function"""
    print("\n" + "=" * 70)
    print("DATABASE MANAGER - Interactive CLI")
    print("=" * 70)

    if not select_database():
        print("\nExiting...")
        return

    app, db = get_app_context()

    try:
        with app.app_context():
            from sqlalchemy import text
            db.session.execute(text("SELECT 1"))
            print("[OK] Database connection successful\n")
    except Exception as e:
        print(f"[ERROR] Database connection failed: {e}")
        return

    while True:
        choice = main_menu()

        if choice == "1":
            manage_users(app, db)
        elif choice == "2":
            manage_suppliers(app, db)
        elif choice == "3":
            manage_customers(app, db)
        elif choice == "4":
            manage_items(app, db)
        elif choice == "5":
            manage_categories(app, db)
        elif choice == "6":
            manage_business_categories(app, db)
        elif choice == "7":
            manage_financial_accounts(app, db)
        elif choice == "8":
            manage_tax_codes(app, db)
        elif choice == "9":
            view_summary(app, db)
        elif choice == "0":
            print("\nGoodbye!\n")
            break
        else:
            print("ERROR: Invalid option")

if __name__ == '__main__':
    main()
