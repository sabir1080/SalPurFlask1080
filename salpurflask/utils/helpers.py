"""Utility helper functions."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from salpurflask.config import APP_TIMEZONE

# Phone number validation
_PHONE_PUNCTUATION = set("+-() ./")

def valid_phone(raw):
    """Validate phone number format."""
    if not raw or len(raw) < 7:
        return False
    digits = ''.join(c for c in raw if c.isdigit())
    if len(digits) < 7 or len(digits) > 15:
        return False
    if not all(c.isdigit() or c in _PHONE_PUNCTUATION for c in raw):
        return False
    return True

def barcode_taken(barcode):
    """Check if barcode is already used (imported from models)."""
    # This will be implemented when models are moved
    pass

def account_name_taken(name):
    """Check if account name is taken (imported from models)."""
    # This will be implemented when models are moved
    pass

def to_local(dt):
    """Convert UTC datetime to local timezone."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(APP_TIMEZONE)
    return dt.astimezone(tz)

def now_local():
    """Get current datetime in local timezone."""
    return to_local(datetime.now(timezone.utc))

def localdt_filter(dt, fmt="%Y-%m-%d %H:%M"):
    """Jinja filter to format datetime in local timezone."""
    return to_local(dt).strftime(fmt) if dt else ""

def bizdate_filter(dt, fmt="%Y-%m-%d"):
    """Jinja filter for business date formatting."""
    return to_local(dt).strftime(fmt) if dt else ""

__all__ = [
    'valid_phone',
    'barcode_taken',
    'account_name_taken',
    'to_local',
    'now_local',
    'localdt_filter',
    'bizdate_filter',
]
