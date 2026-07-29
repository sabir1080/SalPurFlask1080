"""Flask extensions initialization."""

from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_login import LoginManager
from passlib.context import CryptContext

# Database extension
db = SQLAlchemy()

# CSRF protection
csrf = CSRFProtect()

# Password hashing
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Login manager
login_manager = LoginManager()
login_manager.login_view = "auth.signin"

__all__ = [
    'db',
    'csrf',
    'pwd_context',
    'login_manager',
]
