"""Warehouse-wise stock for the item pickers.

Item.stock is the company-wide total (the sum of every ItemStock row). A picker
that shows that number on a form which is about to move stock at ONE warehouse
tells the user "63 available" when the warehouse they are about to use holds 0 --
and the save then fails the per-location check (stock_at_location, unchanged).

Everything a picker shows comes from here, so the item lookup modal, the POS
search and the stock-hint under a picked item all agree:

    stock_by_location()   -> every active warehouse the user may see, with its qty
    picker_stock_fields() -> the API fields for one item: the selected warehouse's
                             stock (when a warehouse is selected) plus the breakdown

This is display only. Nothing here changes how stock is validated or moved.
"""
from salpurflask.extensions import db
from salpurflask.models.inventory_location import ItemStock, Location


def stock_by_location(items, accessible_ids=None):
    """{item_id: [{"id", "name", "qty"}, ...]} -- one entry per active location the
    user may see (all of them when accessible_ids is None). Two queries for any
    number of items.

    The quantity at a location is exactly what stock_at_location() -- the number the
    save validates against -- would return: the ItemStock row if there is one, 0 for
    a location an already-migrated item has no row at, and, for an item with no
    ItemStock row anywhere yet (legacy), Item.stock at the default location."""
    from salpurflask.models.inventory_location import get_or_create_default_location

    items = list(items)
    if not items:
        return {}
    locations = Location.query.filter_by(active=True)
    if accessible_ids is not None:
        locations = locations.filter(Location.id.in_(accessible_ids))
    locations = locations.order_by(Location.name).all()

    qty, has_rows = {}, set()
    rows = (db.session.query(ItemStock.item_id, ItemStock.location_id, ItemStock.quantity)
            .filter(ItemStock.item_id.in_([it.id for it in items])).all())
    for item_id, location_id, quantity in rows:
        qty[(item_id, location_id)] = quantity or 0
        has_rows.add(item_id)
    default_id = get_or_create_default_location().id

    def at(item, location):
        if (item.id, location.id) in qty:
            return qty[(item.id, location.id)]
        if item.id not in has_rows and location.id == default_id:
            return item.stock or 0
        return 0

    return {it.id: [{"id": loc.id, "name": loc.name, "qty": at(it, loc)} for loc in locations]
            for it in items}


def picker_stock_fields(item, location_id, breakdown):
    """The stock fields a picker row needs. `stock` is the SELECTED warehouse's
    quantity when one is selected (and visible to the user); otherwise it stays the
    company total, exactly as before. `stock_total` is always the company total."""
    selected = next((b for b in breakdown if b["id"] == location_id), None) \
        if location_id is not None else None
    return {
        "stock": selected["qty"] if selected else item.stock,
        "stock_total": item.stock,
        "location_id": selected["id"] if selected else None,
        "location_name": selected["name"] if selected else None,
        "stock_by_location": breakdown,
    }
