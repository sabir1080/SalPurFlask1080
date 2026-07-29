#!/usr/bin/env python
"""Seed test data for demo"""

from app import app, db
from salpurflask.models import Supplier, Customer, Item
from salpurflask.models.business_config import ProductCategoryData
from salpurflask.services.config_service import ConfigurationService
from decimal import Decimal

if __name__ == '__main__':
    with app.app_context():
        print("Seeding test data...\n")

        # Create Suppliers
        print("Creating Suppliers...")
        suppliers = [
            {"name": "Pharma Plus Inc", "contact": "03001234567", "address": "Karachi, Pakistan", "opening_balance": 50000},
            {"name": "Fresh Foods Ltd", "contact": "03109876543", "address": "Lahore, Pakistan", "opening_balance": 30000},
            {"name": "Fashion World", "contact": "03215551234", "address": "Islamabad, Pakistan", "opening_balance": 25000},
        ]

        supplier_objects = []
        for s in suppliers:
            supp = Supplier(
                name=s["name"],
                contact=s["contact"],
                address=s["address"],
                opening_balance=Decimal(str(s["opening_balance"]))
            )
            db.session.add(supp)
            db.session.flush()
            supplier_objects.append(supp)
            print(f"  + {s['name']}")

        db.session.commit()

        # Create Customers
        print("\nCreating Customers...")
        customers = [
            {"name": "Ali Khan", "contact": "03001111111", "address": "Karachi", "opening_balance": 15000},
            {"name": "Fatima Ahmed", "contact": "03102222222", "address": "Lahore", "opening_balance": 20000},
            {"name": "Hassan Ali", "contact": "03213333333", "address": "Islamabad", "opening_balance": 10000},
        ]

        customer_objects = []
        for c in customers:
            cust = Customer(
                name=c["name"],
                contact=c["contact"],
                address=c["address"],
                opening_balance=Decimal(str(c["opening_balance"]))
            )
            db.session.add(cust)
            db.session.flush()
            customer_objects.append(cust)
            print(f"  + {c['name']}")

        db.session.commit()

        # Create Items for each enabled category
        print("\nCreating Items...")

        # Medical Store Items
        med_items = [
            {"name": "Paracetamol 500mg", "category_id": 1, "purchase_price": 5, "sale_price": 8, "stock": 500, "reorder": 100,
             "fields": {"batch_number": "MED001", "expiry_date": "2026-12-31", "generic_name": "Paracetamol", "manufacturer": "Pharma Plus"}},
            {"name": "Amoxicillin 250mg", "category_id": 1, "purchase_price": 15, "sale_price": 25, "stock": 200, "reorder": 50,
             "fields": {"batch_number": "MED002", "expiry_date": "2026-11-30", "generic_name": "Amoxicillin", "manufacturer": "Pharma Plus"}},
            {"name": "Vitamin C 1000mg", "category_id": 1, "purchase_price": 20, "sale_price": 35, "stock": 300, "reorder": 75,
             "fields": {"batch_number": "MED003", "expiry_date": "2027-06-30", "generic_name": "Ascorbic Acid", "manufacturer": "Health Labs"}},
        ]

        # Grocery Items
        grocery_items = [
            {"name": "Basmati Rice 1kg", "category_id": 2, "purchase_price": 80, "sale_price": 120, "stock": 100, "reorder": 20,
             "fields": {"weight": "1", "unit_type": "kg", "origin": "Pakistan"}},
            {"name": "Cooking Oil 2L", "category_id": 2, "purchase_price": 300, "sale_price": 450, "stock": 50, "reorder": 10,
             "fields": {"weight": "2", "unit_type": "liter", "pack_size": "2L", "expiry_date": "2027-12-31", "origin": "Pakistan"}},
            {"name": "Wheat Flour 2kg", "category_id": 2, "purchase_price": 120, "sale_price": 180, "stock": 75, "reorder": 15,
             "fields": {"weight": "2", "unit_type": "kg", "origin": "Pakistan"}},
        ]

        # Garments Items
        garment_items = [
            {"name": "Men's Cotton T-Shirt", "category_id": 3, "purchase_price": 200, "sale_price": 400, "stock": 50, "reorder": 10,
             "fields": {"size": "M", "colour": "Blue", "fabric": "Cotton", "gender": "Male"}},
            {"name": "Women's Casual Jeans", "category_id": 3, "purchase_price": 800, "sale_price": 1500, "stock": 30, "reorder": 5,
             "fields": {"size": "M", "colour": "Black", "fabric": "Polyester", "gender": "Female"}},
            {"name": "Kids Polo Shirt", "category_id": 3, "purchase_price": 150, "sale_price": 300, "stock": 40, "reorder": 8,
             "fields": {"size": "S", "colour": "White", "fabric": "Cotton", "gender": "Unisex"}},
        ]

        all_items = med_items + grocery_items + garment_items

        for item_data in all_items:
            item = Item(
                name=item_data["name"],
                category_id=None,  # Old system disabled
                business_category_id=item_data["category_id"],
                unit="Pcs",
                opening_stock=item_data["stock"],
                stock=item_data["stock"],
                reorder_level=item_data["reorder"],
                purchase_price=Decimal(str(item_data["purchase_price"])),
                sale_price=Decimal(str(item_data["sale_price"])),
                barcode=f"BAR{item_data['category_id']}{item_data['name'][:3].upper()}",
            )
            item.inventory_value = (Decimal(str(item_data["stock"])) * Decimal(str(item_data["purchase_price"]))).quantize(Decimal('0.01'))

            db.session.add(item)
            db.session.flush()

            # Save category-specific fields
            if "fields" in item_data:
                ConfigurationService.save_product_category_data(
                    item.id,
                    item_data["category_id"],
                    item_data["fields"]
                )

            print(f"  + {item_data['name']}")

        db.session.commit()
        print("\nTest data seeding complete!")

        # Summary
        total_suppliers = Supplier.query.count()
        total_customers = Customer.query.count()
        total_items = Item.query.filter(Item.business_category_id != None).count()

        print(f"\nSummary:")
        print(f"  Suppliers: {total_suppliers}")
        print(f"  Customers: {total_customers}")
        print(f"  Items: {total_items}")
