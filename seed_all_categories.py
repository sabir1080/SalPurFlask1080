#!/usr/bin/env python
"""Seed all 17 business categories"""

from app import app, db
from salpurflask.models.business_config import BusinessCategory, ProductField

CATEGORIES = [
    {
        "name": "Medical Store",
        "slug": "medical-store",
        "description": "Pharmaceuticals and medical products",
        "icon": "bi-pill",
        "enabled": True,
        "fields": [
            {"label": "Batch Number", "name": "batch_number", "type": "text", "required": True, "tab": "General"},
            {"label": "Expiry Date", "name": "expiry_date", "type": "date", "required": True, "tab": "General"},
            {"label": "Generic Name", "name": "generic_name", "type": "text", "required": False, "tab": "Details"},
            {"label": "Manufacturer", "name": "manufacturer", "type": "text", "required": False, "tab": "Details"},
            {"label": "HSN Code", "name": "hsn_code", "type": "text", "required": False, "tab": "Details"},
            {"label": "Prescription Required", "name": "prescription_required", "type": "boolean", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Grocery",
        "slug": "grocery",
        "description": "Food and grocery items",
        "icon": "bi-bag-check",
        "enabled": True,
        "fields": [
            {"label": "Weight (kg)", "name": "weight", "type": "number", "required": False, "tab": "General"},
            {"label": "Unit", "name": "unit_type", "type": "select", "required": False, "tab": "General", "options": ["kg", "liter", "piece"]},
            {"label": "Pack Size", "name": "pack_size", "type": "text", "required": False, "tab": "General"},
            {"label": "Expiry Date", "name": "expiry_date", "type": "date", "required": False, "tab": "General"},
            {"label": "Origin", "name": "origin", "type": "text", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Garments",
        "slug": "garments",
        "description": "Clothing and apparel",
        "icon": "bi-bag",
        "enabled": True,
        "fields": [
            {"label": "Size", "name": "size", "type": "select", "required": False, "tab": "General", "options": ["XS", "S", "M", "L", "XL", "XXL"]},
            {"label": "Colour", "name": "colour", "type": "text", "required": False, "tab": "General"},
            {"label": "Fabric", "name": "fabric", "type": "select", "required": False, "tab": "Details", "options": ["Cotton", "Polyester", "Silk", "Wool"]},
            {"label": "Gender", "name": "gender", "type": "select", "required": False, "tab": "Details", "options": ["Male", "Female", "Unisex"]},
            {"label": "Season", "name": "season", "type": "select", "required": False, "tab": "Details", "options": ["Summer", "Winter", "All-Season"]},
        ]
    },
    {
        "name": "Footwear",
        "slug": "footwear",
        "description": "Shoes and footwear",
        "icon": "bi-shoe",
        "enabled": False,
        "fields": [
            {"label": "Size", "name": "size", "type": "text", "required": False, "tab": "General"},
            {"label": "Colour", "name": "colour", "type": "text", "required": False, "tab": "General"},
            {"label": "Material", "name": "material", "type": "text", "required": False, "tab": "Details"},
            {"label": "Gender", "name": "gender", "type": "select", "required": False, "tab": "Details", "options": ["Male", "Female", "Unisex"]},
        ]
    },
    {
        "name": "Electronics",
        "slug": "electronics",
        "description": "Electronic devices and gadgets",
        "icon": "bi-lightning-charge",
        "enabled": False,
        "fields": [
            {"label": "Model", "name": "model", "type": "text", "required": False, "tab": "General"},
            {"label": "Serial Number", "name": "serial_number", "type": "text", "required": False, "tab": "General"},
            {"label": "Warranty (months)", "name": "warranty_months", "type": "number", "required": False, "tab": "Details"},
            {"label": "Voltage", "name": "voltage", "type": "text", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Cosmetics",
        "slug": "cosmetics",
        "description": "Beauty and cosmetic products",
        "icon": "bi-palette",
        "enabled": False,
        "fields": [
            {"label": "Fragrance", "name": "fragrance", "type": "text", "required": False, "tab": "General"},
            {"label": "Type", "name": "type", "type": "select", "required": False, "tab": "General", "options": ["Skincare", "Makeup", "Haircare"]},
            {"label": "Ingredients", "name": "ingredients", "type": "textarea", "required": False, "tab": "Details"},
            {"label": "Expiry Date", "name": "expiry_date", "type": "date", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Hardware",
        "slug": "hardware",
        "description": "Hardware and tools",
        "icon": "bi-tools",
        "enabled": False,
        "fields": [
            {"label": "Unit Type", "name": "unit_type", "type": "text", "required": False, "tab": "General"},
            {"label": "Size", "name": "size", "type": "text", "required": False, "tab": "General"},
            {"label": "Material", "name": "material", "type": "text", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Kitchen & Home",
        "slug": "kitchen-home",
        "description": "Kitchen and home items",
        "icon": "bi-cup-hot",
        "enabled": False,
        "fields": [
            {"label": "Material", "name": "material", "type": "text", "required": False, "tab": "General"},
            {"label": "Colour", "name": "colour", "type": "text", "required": False, "tab": "General"},
            {"label": "Capacity", "name": "capacity", "type": "text", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Stationery",
        "slug": "stationery",
        "description": "Office and school stationery",
        "icon": "bi-file-text",
        "enabled": False,
        "fields": [
            {"label": "Type", "name": "type", "type": "text", "required": False, "tab": "General"},
            {"label": "Colour", "name": "colour", "type": "text", "required": False, "tab": "General"},
            {"label": "Quantity", "name": "quantity", "type": "number", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Bakery",
        "slug": "bakery",
        "description": "Bakery products",
        "icon": "bi-cake2",
        "enabled": False,
        "fields": [
            {"label": "Expiry Date", "name": "expiry_date", "type": "date", "required": False, "tab": "General"},
            {"label": "Type", "name": "type", "type": "select", "required": False, "tab": "General", "options": ["Bread", "Cake", "Pastry"]},
            {"label": "Weight", "name": "weight", "type": "text", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Dairy",
        "slug": "dairy",
        "description": "Dairy products",
        "icon": "bi-cup",
        "enabled": False,
        "fields": [
            {"label": "Expiry Date", "name": "expiry_date", "type": "date", "required": False, "tab": "General"},
            {"label": "Type", "name": "type", "type": "select", "required": False, "tab": "General", "options": ["Milk", "Cheese", "Yogurt"]},
            {"label": "Volume", "name": "volume", "type": "text", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Frozen Foods",
        "slug": "frozen-foods",
        "description": "Frozen food items",
        "icon": "bi-snow",
        "enabled": False,
        "fields": [
            {"label": "Expiry Date", "name": "expiry_date", "type": "date", "required": False, "tab": "General"},
            {"label": "Type", "name": "type", "type": "text", "required": False, "tab": "General"},
            {"label": "Weight", "name": "weight", "type": "text", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Fruits & Vegetables",
        "slug": "fruits-vegetables",
        "description": "Fresh fruits and vegetables",
        "icon": "bi-apple",
        "enabled": False,
        "fields": [
            {"label": "Weight", "name": "weight", "type": "text", "required": False, "tab": "General"},
            {"label": "Origin", "name": "origin", "type": "text", "required": False, "tab": "General"},
            {"label": "Expiry Date", "name": "expiry_date", "type": "date", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Meat & Poultry",
        "slug": "meat-poultry",
        "description": "Meat and poultry products",
        "icon": "bi-egg",
        "enabled": False,
        "fields": [
            {"label": "Type", "name": "type", "type": "select", "required": False, "tab": "General", "options": ["Chicken", "Mutton", "Fish"]},
            {"label": "Weight", "name": "weight", "type": "text", "required": False, "tab": "General"},
            {"label": "Expiry Date", "name": "expiry_date", "type": "date", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Toys",
        "slug": "toys",
        "description": "Toys and games",
        "icon": "bi-puzzle",
        "enabled": False,
        "fields": [
            {"label": "Age Group", "name": "age_group", "type": "text", "required": False, "tab": "General"},
            {"label": "Material", "name": "material", "type": "text", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Gift Shop",
        "slug": "gift-shop",
        "description": "Gift items and hampers",
        "icon": "bi-gift",
        "enabled": False,
        "fields": [
            {"label": "Occasion", "name": "occasion", "type": "select", "required": False, "tab": "General", "options": ["Birthday", "Wedding", "Anniversary"]},
            {"label": "Material", "name": "material", "type": "text", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Mobile Shop",
        "slug": "mobile-shop",
        "description": "Mobile phones and accessories",
        "icon": "bi-phone",
        "enabled": False,
        "fields": [
            {"label": "Model", "name": "model", "type": "text", "required": False, "tab": "General"},
            {"label": "Colour", "name": "colour", "type": "text", "required": False, "tab": "General"},
            {"label": "Warranty (months)", "name": "warranty_months", "type": "number", "required": False, "tab": "Details"},
        ]
    },
    {
        "name": "Departmental Store",
        "slug": "departmental-store",
        "description": "General departmental goods",
        "icon": "bi-shop",
        "enabled": False,
        "fields": [
            {"label": "Department", "name": "department", "type": "text", "required": False, "tab": "General"},
            {"label": "Size", "name": "size", "type": "text", "required": False, "tab": "Details"},
        ]
    },
]

if __name__ == '__main__':
    with app.app_context():
        print("Seeding 17 business categories...")

        for idx, cat_data in enumerate(CATEGORIES):
            existing = BusinessCategory.query.filter_by(slug=cat_data["slug"]).first()
            if existing:
                print(f"  {idx+1}. {cat_data['name']} (already exists)")
                continue

            cat = BusinessCategory(
                name=cat_data["name"],
                slug=cat_data["slug"],
                description=cat_data["description"],
                icon=cat_data["icon"],
                is_enabled=cat_data["enabled"],
                priority=idx
            )
            db.session.add(cat)
            db.session.flush()

            # Add fields
            for field_idx, field_data in enumerate(cat_data["fields"]):
                field = ProductField(
                    category_id=cat.id,
                    field_name=field_data["name"],
                    field_label=field_data["label"],
                    field_type=field_data["type"],
                    is_required=field_data.get("required", False),
                    tab_name=field_data.get("tab", "General"),
                    position=field_idx,
                    options=field_data.get("options")
                )
                db.session.add(field)

            status = "ENABLED" if cat_data["enabled"] else "disabled"
            print(f"  {idx+1}. {cat_data['name']} ({status}) - {len(cat_data['fields'])} fields")

        db.session.commit()
        print("\nSeeding complete!")

        # Verify
        total_cats = BusinessCategory.query.count()
        enabled_cats = BusinessCategory.query.filter_by(is_enabled=True).count()
        total_fields = ProductField.query.count()
        print(f"\nTotal categories: {total_cats}")
        print(f"Enabled categories: {enabled_cats}")
        print(f"Total fields: {total_fields}")
