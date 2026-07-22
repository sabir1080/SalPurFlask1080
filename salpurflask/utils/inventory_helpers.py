"""Inventory-specific calculation helpers.

These functions are used across inventory modules for consistent
calculation of quantities and valuations.

Note: purchase_item_total() and sale_item_total() with discount/tax handling
are defined in app.py and are NOT exported from here. They are specific to
purchase order and sales order calculations. The simple unit-based calculations
for ledger display use internal functions in inventory/routes.py.
"""


def line_base_qty(line):
    """Calculate quantity in base unit.

    Converts a line item's quantity to the item's base unit using
    the unit_factor. Used in stock movement ledgers and export routes.

    Args:
        line: Line item with quantity and unit_factor attributes

    Returns:
        Quantity in base unit
    """
    return line.quantity * (line.unit_factor or 1)


__all__ = ['line_base_qty']
