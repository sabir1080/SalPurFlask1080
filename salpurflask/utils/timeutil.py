"""Time helpers."""
from datetime import datetime, timezone


def utcnow():
    """Naive UTC now. Drop-in for the deprecated datetime.utcnow() (same value, same naive type)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
