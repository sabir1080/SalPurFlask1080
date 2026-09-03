"""Inventory-specific utility functions."""

from salpurflask.models import Item
from salpurflask.extensions import db


def barcode_taken(barcode, exclude_id=None):
    """Check if barcode is already used by another item.

    A barcode must point at one item and one only — the whole point of scanning is that
    the code names the product with no ambiguity. Two items sharing a code (a mistyped
    company barcode, the same number pasted twice) would make the POS pick whichever it
    found first, silently ringing up the wrong thing. Blank is never 'taken': any number of
    items may have no code.

    Args:
        barcode: Barcode string to check (or None)
        exclude_id: Optional item ID to exclude from check (for edit operations)

    Returns:
        True if barcode is already in use by another item, False otherwise
    """
    if not barcode:
        return False
    q = Item.query.filter(Item.barcode == barcode)
    if exclude_id is not None:
        q = q.filter(Item.id != exclude_id)
    return db.session.query(q.exists()).scalar()


def sku_taken(sku, exclude_id=None):
    """Check if a SKU is already used by another item. Same optional-uniqueness
    rule as barcode_taken(): blank is never 'taken', a non-blank SKU must point
    at one item only. Case-insensitive — 'ABC-1' and 'abc-1' are the same SKU
    to a human re-typing it from a supplier invoice."""
    if not sku:
        return False
    q = Item.query.filter(db.func.lower(Item.sku) == sku.strip().lower())
    if exclude_id is not None:
        q = q.filter(Item.id != exclude_id)
    return db.session.query(q.exists()).scalar()


__all__ = ['barcode_taken', 'sku_taken']
