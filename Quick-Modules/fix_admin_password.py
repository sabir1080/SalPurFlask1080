"""Fix admin password with correct hashing"""
import os
from pathlib import Path
from dotenv import load_dotenv

# For Render database
env_file = Path('.env.render')
if env_file.exists():
    load_dotenv(env_file)
    RENDER_DB_URL = os.getenv("DATABASE_URL")
    if RENDER_DB_URL and "USER:PASSWORD" not in RENDER_DB_URL:
        os.environ["DATABASE_URL"] = RENDER_DB_URL

from app import app, db
from salpurflask.models import User
from salpurflask.extensions import pwd_context
from sqlalchemy import text

with app.app_context():
    print("Fixing admin password...\n")

    try:
        db.session.execute(text("SELECT 1"))
        db_type = "Render"
    except:
        db_type = "Local"

    print(f"Database: {db_type}\n")

    admin = User.query.filter_by(email='admin@example.com').first()

    if admin:
        print(f"Found: {admin.email}")

        # Use pwd_context.hash (argon2)
        correct_hash = pwd_context.hash('admin123')
        admin.password = correct_hash
        admin.verified = True

        db.session.commit()
        print("[OK] Password reset with correct hashing")
        print("[OK] Verified set to True")

        # Verify it works
        is_correct = pwd_context.verify('admin123', admin.password)
        print(f"[OK] Verification test: {is_correct}")

        print("\n" + "=" * 60)
        print("LOGIN CREDENTIALS:")
        print("=" * 60)
        print("Email:    admin@example.com")
        print("Password: admin123")
        print("=" * 60 + "\n")
    else:
        print("Admin not found!")

