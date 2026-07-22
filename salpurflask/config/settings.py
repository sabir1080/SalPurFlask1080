"""Flask application configuration."""

import os
from datetime import timedelta
from dotenv import load_dotenv

# Load environment variables
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(BASE_DIR)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL")

SQLALCHEMY_DATABASE_URI = DATABASE_URL if DATABASE_URL else (
    f"sqlite:///{os.path.join(PROJECT_ROOT, 'instance', 'database.db').replace(chr(92), '/')}"
)

SQLALCHEMY_TRACK_MODIFICATIONS = False

# Application secret keys
SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key")
SECURITY_PASSWORD_SALT = os.getenv("SECURITY_PASSWORD_SALT", "your_salt")

# Application metadata
APP_NAME = os.getenv("APP_NAME", "SalPurFlask")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "UTC")

# Feature flags
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "false").lower() in ("1", "true", "yes")
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() in ("1", "true", "yes")

# Email configuration
MAIL_SERVER = "smtp.gmail.com"
MAIL_PORT = 587
MAIL_USE_TLS = True
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "").strip()
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "").replace(" ", "")

# Session configuration
_is_production = bool(DATABASE_URL)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = _is_production
REMEMBER_COOKIE_HTTPONLY = True
REMEMBER_COOKIE_SECURE = _is_production
PERMANENT_SESSION_LIFETIME = timedelta(hours=24)

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Analytics/Monitoring
SENTRY_DSN = os.getenv("SENTRY_DSN")

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
