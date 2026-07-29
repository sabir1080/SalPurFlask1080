"""Seed Business Categories - Run once to populate categories"""

from datetime import datetime
from salpurflask.extensions import db
from salpurflask.models.business_config import BusinessCategory, ProductField


RETAIL_CATEGORIES_SEED = {
    'medical_store': {
        'name': 'Medical Store',
        'icon': 'bi-capsule',
        'color': 'danger',
        'description': 'Medicines, supplements, healthcare products',
        'fields': [
            ('batch_number', 'Batch Number', 'text', True, 'General', 0),
            ('expiry_date', 'Expiry Date', 'date', True, 'General', 1),
            ('generic_name', 'Generic Name', 'text', False, 'General', 2),
            ('manufacturer', 'Manufacturer', 'text', False, 'General', 3),
            ('hsn_code', 'HSN Code', 'text', False, 'Compliance', 0),
            ('prescription_required', 'Prescription Required', 'boolean', False, 'Compliance', 1),
        ]
    },
    'grocery': {
        'name': 'Grocery',
        'icon': 'bi-basket',
        'color': 'success',
        'description': 'Food items, vegetables, dairy products',
        'fields': [
            ('weight', 'Weight', 'number', False, 'General', 0),
            ('unit', 'Unit', 'select', False, 'General', 1),
            ('pack_size', 'Pack Size', 'text', False, 'General', 2),
            ('expiry_date', 'Expiry Date', 'date', False, 'General', 3),
            ('origin', 'Origin/Source', 'text', False, 'General', 4),
        ]
    },
    'garments': {
        'name': 'Garments',
        'icon': 'bi-person-check',
        'color': 'primary',
        'description': 'Clothing, apparel, textiles',
        'fields': [
            ('size', 'Size', 'select', False, 'General', 0),
            ('colour', 'Colour', 'text', False, 'General', 1),
            ('fabric', 'Fabric Type', 'text', False, 'General', 2),
            ('gender', 'Gender', 'select', False, 'General', 3),
            ('season', 'Season', 'text', False, 'General', 4),
        ]
    },
    'footwear': {
        'name': 'Footwear',
        'icon': 'bi-shoe',
        'color': 'warning',
        'description': 'Shoes, sandals, boots',
        'fields': [
            ('size', 'Size', 'select', True, 'General', 0),
            ('colour', 'Colour', 'text', False, 'General', 1),
            ('material', 'Material', 'text', False, 'General', 2),
            ('gender', 'Gender', 'select', False, 'General', 3),
        ]
    },
    'electronics': {
        'name': 'Electronics',
        'icon': 'bi-phone',
        'color': 'dark',
        'description': 'Electronic devices and gadgets',
        'fields': [
            ('model', 'Model', 'text', False, 'General', 0),
            ('serial_number', 'Serial Number', 'text', False, 'General', 1),
            ('warranty_months', 'Warranty (Months)', 'number', False, 'General', 2),
            ('voltage', 'Voltage', 'text', False, 'Technical', 0),
        ]
    },
    'cosmetics': {
        'name': 'Cosmetics',
        'icon': 'bi-heart-pulse',
        'color': 'info',
        'description': 'Makeup, skincare, beauty products',
        'fields': [
            ('fragrance', 'Fragrance/Scent', 'text', False, 'General', 0),
            ('type', 'Product Type', 'select', False, 'General', 1),
            ('ingredients', 'Key Ingredients', 'text', False, 'General', 2),
            ('expiry_date', 'Expiry Date', 'date', False, 'General', 3),
        ]
    },
    'hardware': {
        'name': 'Hardware',
        'icon': 'bi-tools',
        'color': 'secondary',
        'description': 'Tools, nails, screws, building materials',
        'fields': [
            ('unit_type', 'Unit Type', 'select', False, 'General', 0),
            ('size', 'Size', 'text', False, 'General', 1),
            ('material', 'Material', 'text', False, 'General', 2),
        ]
    },
    'kitchen_home': {
        'name': 'Kitchen & Home',
        'icon': 'bi-cup-hot',
        'color': 'warning',
        'description': 'Cookware, utensils, home appliances',
        'fields': [
            ('material', 'Material', 'text', False, 'General', 0),
            ('colour', 'Colour', 'text', False, 'General', 1),
            ('capacity', 'Capacity', 'text', False, 'General', 2),
        ]
    },
    'stationery': {
        'name': 'Stationery',
        'icon': 'bi-pencil-square',
        'color': 'success',
        'description': 'Paper, pens, office supplies',
        'fields': [
            ('type', 'Product Type', 'select', False, 'General', 0),
            ('colour', 'Colour', 'text', False, 'General', 1),
            ('quantity', 'Quantity per Pack', 'number', False, 'General', 2),
        ]
    },
    'bakery': {
        'name': 'Bakery',
        'icon': 'bi-cookie',
        'color': 'danger',
        'description': 'Bread, cakes, pastries',
        'fields': [
            ('expiry_date', 'Expiry Date', 'date', True, 'General', 0),
            ('type', 'Bakery Type', 'select', False, 'General', 1),
            ('weight', 'Weight', 'number', False, 'General', 2),
        ]
    },
    'dairy': {
        'name': 'Dairy',
        'icon': 'bi-cup',
        'color': 'info',
        'description': 'Milk, cheese, yogurt, butter',
        'fields': [
            ('expiry_date', 'Expiry Date', 'date', True, 'General', 0),
            ('type', 'Dairy Type', 'select', False, 'General', 1),
            ('volume', 'Volume/Weight', 'text', False, 'General', 2),
        ]
    },
    'frozen_foods': {
        'name': 'Frozen Foods',
        'icon': 'bi-snow2',
        'color': 'info',
        'description': 'Frozen vegetables, ice cream, frozen meals',
        'fields': [
            ('expiry_date', 'Expiry Date', 'date', True, 'General', 0),
            ('type', 'Food Type', 'select', False, 'General', 1),
            ('weight', 'Weight', 'number', False, 'General', 2),
        ]
    },
    'fruits_vegetables': {
        'name': 'Fruits & Vegetables',
        'icon': 'bi-leaf',
        'color': 'success',
        'description': 'Fresh produce',
        'fields': [
            ('weight', 'Weight/Quantity', 'number', False, 'General', 0),
            ('origin', 'Origin', 'text', False, 'General', 1),
            ('expiry_date', 'Best Before', 'date', False, 'General', 2),
        ]
    },
    'meat_poultry': {
        'name': 'Meat & Poultry',
        'icon': 'bi-egg',
        'color': 'danger',
        'description': 'Meat, chicken, seafood',
        'fields': [
            ('type', 'Meat Type', 'select', False, 'General', 0),
            ('weight', 'Weight', 'number', False, 'General', 1),
            ('expiry_date', 'Expiry Date', 'date', True, 'General', 2),
        ]
    },
    'toys': {
        'name': 'Toys',
        'icon': 'bi-puzzle',
        'color': 'warning',
        'description': 'Toys, games, puzzles',
        'fields': [
            ('age_group', 'Age Group', 'select', False, 'General', 0),
            ('material', 'Material', 'text', False, 'General', 1),
        ]
    },
    'gift_shop': {
        'name': 'Gift Shop',
        'icon': 'bi-gift',
        'color': 'primary',
        'description': 'Gifts, decorations, souvenirs',
        'fields': [
            ('occasion', 'Occasion', 'select', False, 'General', 0),
            ('material', 'Material', 'text', False, 'General', 1),
        ]
    },
    'mobile_shop': {
        'name': 'Mobile Shop',
        'icon': 'bi-phone',
        'color': 'dark',
        'description': 'Mobile phones, accessories',
        'fields': [
            ('model', 'Model', 'text', True, 'General', 0),
            ('colour', 'Colour', 'text', False, 'General', 1),
            ('warranty_months', 'Warranty (Months)', 'number', False, 'General', 2),
        ]
    },
    'departmental_store': {
        'name': 'Departmental Store',
        'icon': 'bi-shop',
        'color': 'success',
        'description': 'Mixed retail products',
        'fields': [
            ('department', 'Department', 'select', False, 'General', 0),
            ('size', 'Size', 'text', False, 'General', 1),
        ]
    },
}

FIELD_OPTIONS = {
    'unit': ['kg', 'g', 'ml', 'l', 'pieces', 'box', 'dozen', 'pack'],
    'size': ['XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL', '5', '6', '7', '8', '9', '10', '11', '12'],
    'gender': ['Male', 'Female', 'Unisex', 'Kids', 'Girls', 'Boys'],
    'type': ['Standard', 'Premium', 'Budget', 'Luxury'],
    'fragrance': ['Floral', 'Fruity', 'Fresh', 'Woody', 'Citrus'],
    'occasion': ['Birthday', 'Anniversary', 'Wedding', 'Graduation', 'Festival'],
    'age_group': ['0-3 years', '3-5 years', '5-8 years', '8-12 years', '12+ years'],
    'bakery_type': ['Bread', 'Cakes', 'Pastries', 'Cookies'],
    'dairy_type': ['Milk', 'Yogurt', 'Cheese', 'Butter', 'Cream'],
    'meat_type': ['Chicken', 'Mutton', 'Beef', 'Fish', 'Seafood'],
    'food_type': ['Vegetables', 'Fruits', 'Prepared Meals'],
    'department': ['Electronics', 'Clothing', 'Food', 'Home & Garden', 'Sports'],
}


def seed_categories():
    """Seed all business categories"""
    from flask import current_app

    try:
        for slug, data in RETAIL_CATEGORIES_SEED.items():
            # Check if already exists
            existing = BusinessCategory.query.filter_by(slug=slug).first()
            if existing:
                current_app.logger.info(f"Category {slug} already exists, skipping")
                continue

            # Create category
            cat = BusinessCategory(
                name=data['name'],
                slug=slug,
                description=data.get('description', ''),
                icon=data.get('icon', 'bi-box'),
                color=data.get('color', 'primary'),
                is_enabled=False,  # Don't auto-enable
            )
            db.session.add(cat)
            db.session.flush()  # Get category ID

            # Add fields
            for field_name, field_label, field_type, is_required, tab_name, position in data.get('fields', []):
                options = FIELD_OPTIONS.get(field_name)

                field = ProductField(
                    category_id=cat.id,
                    field_name=field_name,
                    field_label=field_label,
                    field_type=field_type,
                    is_required=is_required,
                    options=options,
                    tab_name=tab_name,
                    position=position,
                )
                db.session.add(field)

            db.session.commit()
            current_app.logger.info(f"Created category: {data['name']}")

        current_app.logger.info("✅ All categories seeded successfully")
        return True

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"❌ Error seeding categories: {str(e)}")
        return False


if __name__ == '__main__':
    from salpurflask.extensions import create_app

    app = create_app()
    with app.app_context():
        seed_categories()
