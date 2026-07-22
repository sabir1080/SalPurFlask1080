"""Flask extensions package."""

from salpurflask.extensions.extensions import db, csrf, pwd_context, login_manager

__all__ = [
    'db',
    'csrf',
    'pwd_context',
    'login_manager',
]
