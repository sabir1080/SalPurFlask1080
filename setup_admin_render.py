"""Setup admin user on Render database"""
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
from salpurflask.models import User
from werkzeug.security import generate_password_hash
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("SELECT 1"))
        print("Connected to Render PostgreSQL!\n")
    except Exception as e:
        print(f"Connection failed: {e}")
        exit(1)

    print("Setting up admin user on Render...")

    admin = User.query.filter_by(email='admin@example.com').first()
    if admin:
        print("Admin exists, updating password...")
        admin.password = generate_password_hash('admin123')
        db.session.commit()
        print("[OK] admin@example.com password updated")
    else:
        print("Creating admin user...")
        admin = User(
            name='Admin User',
            email='admin@example.com',
            password=generate_password_hash('admin123'),
            verified=True,
            role='admin'
        )
        db.session.add(admin)
        db.session.commit()
        print("[OK] Created admin@example.com")

    print("\n" + "=" * 60)
    print("RENDER LOGIN CREDENTIALS:")
    print("=" * 60)
    print("Email:    admin@example.com")
    print("Password: admin123")
    print("=" * 60 + "\n")
