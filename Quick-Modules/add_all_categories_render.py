"""Add all 21 business categories to Render database"""
import os
from pathlib import Path
from dotenv import load_dotenv

env_file = Path('.env.render')
if not env_file.exists():
    print("ERROR: .env.render not found")
    exit(1)

load_dotenv(env_file)
RENDER_DB_URL = os.getenv("DATABASE_URL")

if not RENDER_DB_URL or "USER:PASSWORD" in RENDER_DB_URL:
    print("ERROR: DATABASE_URL not valid in .env.render")
    exit(1)

os.environ["DATABASE_URL"] = RENDER_DB_URL

from app import app, db
from salpurflask.models import BusinessCategory
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("SELECT 1"))
        masked = RENDER_DB_URL.split('@')[1] if '@' in RENDER_DB_URL else RENDER_DB_URL
        print(f"Connected to: {masked}\n")
    except Exception as e:
        print(f"Connection failed: {e}")
        exit(1)

    # All 21 business categories
    categories = [
        ('Medical Store', 'medical-store', 'Pharmacy and medical supplies', 'first-aid-kit', True, 1, '#FF6B6B'),
        ('Grocery', 'grocery', 'Daily grocery and food items', 'shopping-cart', True, 2, '#4ECDC4'),
        ('Garments', 'garments', 'Clothing and apparel', 'shirt', True, 3, '#FFE66D'),
        ('Footwear', 'footwear', 'Shoes and footwear products', 'shoe-prints', True, 4, '#95E1D3'),
        ('Electronics', 'electronics', 'Electronic devices and gadgets', 'monitor', True, 5, '#F38181'),
        ('Cosmetics', 'cosmetics', 'Beauty and cosmetic products', 'sparkles', True, 6, '#AA96DA'),
        ('Hardware', 'hardware', 'Construction and hardware supplies', 'hammer', True, 7, '#FCBAD3'),
        ('Kitchen & Home', 'kitchen-home', 'Kitchen and household items', 'home', True, 8, '#A8D8EA'),
        ('Stationery', 'stationery', 'Office and school supplies', 'pen-tool', True, 9, '#FFD7A8'),
        ('Bakery', 'bakery', 'Baked goods and confectionery', 'cake', True, 10, '#CAFFBF'),
        ('Dairy', 'dairy', 'Milk and dairy products', 'milk', True, 11, '#FFB7B7'),
        ('Frozen Foods', 'frozen-foods', 'Frozen and chilled items', 'ice-cream', True, 12, '#B4D7FF'),
        ('Fruits & Vegetables', 'fruits-vegetables', 'Fresh produce', 'apple', True, 13, '#FFFB96'),
        ('Meat & Poultry', 'meat-poultry', 'Meat and poultry products', 'drumstick', True, 14, '#E0AAFF'),
        ('Toys', 'toys', 'Toys and games', 'puzzle', True, 15, '#FDD262'),
        ('Gift Shop', 'gift-shop', 'Gifts and novelty items', 'gift', True, 16, '#FF9770'),
        ('Mobile Shop', 'mobile-shop', 'Mobile phones and accessories', 'smartphone', True, 17, '#BDB2FF'),
        ('Departmental Store', 'departmental-store', 'Multi-category retail store', 'store', True, 18, '#FFDAB9'),
        ('Premium Products', 'premium', 'High-end luxury items', 'star', True, 19, '#FF6B6B'),
        ('Budget Range', 'budget', 'Affordable and economical products', 'dollar-sign', True, 20, '#4ECDC4'),
        ('Seasonal Items', 'seasonal', 'Limited time seasonal offerings', 'calendar', True, 21, '#FFE66D'),
    ]

    print("Adding Business Categories...\n")

    added = 0
    skipped = 0

    for name, slug, desc, icon, enabled, priority, color in categories:
        if not BusinessCategory.query.filter_by(slug=slug).first():
            db.session.add(BusinessCategory(
                name=name, slug=slug, description=desc,
                icon=icon, is_enabled=enabled, priority=priority, color=color
            ))
            print(f"  [OK] {name}")
            added += 1
        else:
            print(f"  [SKIP] {name} (exists)")
            skipped += 1

    db.session.commit()

    print("\n" + "=" * 60)
    print(f"Added: {added} | Skipped: {skipped}")
    print(f"Total: {BusinessCategory.query.count()}")
    print("=" * 60 + "\n")
