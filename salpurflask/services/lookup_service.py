"""Universal server-side lookup/search — Item, Supplier, Customer.

One shared shape so Sale/Purchase/POS/Quotation pickers can all be backed by
the same kind of endpoint instead of each screen inventing its own search:
never load the whole table into the page, always rank exact matches first,
always paginate. Category-specific filters for Item are read straight off
ProductField.is_filterable, so a field an admin adds through the existing
Business Configuration UI is filterable here with no new code.

Ranking is a single indexed query with a CASE-based tier column, not a
separate exact-match query unioned with a fuzzy one — cheap to compute,
correct enough for typeahead-sized result sets (see MAX_RESULTS).
"""

from sqlalchemy import case, or_, and_, cast, Text

from salpurflask.extensions import db
from salpurflask.models import Item, Supplier, Customer
from salpurflask.models.business_config import BusinessCategory, ProductField, ProductCategoryData

MAX_RESULTS = 50


def _paginate(query, page, per_page):
    page = max(1, page)
    per_page = max(1, min(per_page or 20, MAX_RESULTS))
    total = query.count()
    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    return rows, total, page, per_page


def search_items(q="", category_id=None, filters=None, page=1, per_page=20,
                 only_categorized=False):
    """Rank: exact barcode > exact SKU > exact name > name starts-with > contains.

    `filters` is {field_name: value} for the selected category's is_filterable
    ProductFields, applied via an EXISTS against ProductCategoryData — this is
    what makes "Category=Garments, Size=Large" a real, combinable condition
    without a dedicated column per attribute.
    """
    query = Item.query
    if only_categorized:
        query = query.filter(Item.business_category_id.isnot(None))
    if category_id:
        query = query.filter(Item.business_category_id == category_id)

    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Item.name.ilike(like), Item.sku.ilike(like), Item.barcode.ilike(like)))
        rank = case(
            (Item.barcode == q, 0),
            (db.func.lower(Item.sku) == q.lower(), 1),
            (db.func.lower(Item.name) == q.lower(), 2),
            (Item.name.ilike(f"{q}%"), 3),
            else_=4,
        )
        query = query.order_by(rank, Item.name)
    else:
        query = query.order_by(Item.name)

    if category_id and filters:
        for field_name, value in filters.items():
            if value in (None, ""):
                continue
            field = ProductField.query.filter_by(
                category_id=category_id, field_name=field_name, is_filterable=True).first()
            if not field:
                continue
            # field_value is a generic JSON column holding a plain string (see
            # ConfigurationService.save_product_category_data). Comparing a
            # JSON column to a Python string with `==` is not portable — on
            # SQLite the column is stored JSON-encoded ('"Large"') while the
            # bound value is the raw string ('Large'), so it silently matches
            # nothing; casting both sides to text sidesteps the JSON
            # comparator entirely and compares the same way on every backend.
            query = query.filter(Item.id.in_(
                db.session.query(ProductCategoryData.product_id).filter(
                    ProductCategoryData.category_id == category_id,
                    ProductCategoryData.field_name == field_name,
                    cast(ProductCategoryData.field_value, Text) == f'"{value}"',
                )))

    return _paginate(query, page, per_page)


def get_item_filter_fields(category_id):
    """The active, is_filterable ProductFields for one category — what the
    lookup UI should render as extra filter controls when that category is
    selected. A disabled field (is_active=False) is excluded, matching
    ConfigurationService.get_category_fields()'s same rule for the item form."""
    if not category_id:
        return []
    return (ProductField.query
            .filter_by(category_id=category_id, is_filterable=True, is_active=True)
            .order_by(ProductField.position).all())


def _party_search(model, q, page, per_page):
    query = model.query
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(or_(model.name.ilike(like), model.contact.ilike(like)))
        rank = case(
            (db.func.lower(model.name) == q.lower(), 0),
            (model.contact == q, 0),
            (model.name.ilike(f"{q}%"), 1),
            else_=2,
        )
        query = query.order_by(rank, model.name)
    else:
        query = query.order_by(model.name)
    return _paginate(query, page, per_page)


def search_suppliers(q="", page=1, per_page=20):
    return _party_search(Supplier, q, page, per_page)


def search_customers(q="", page=1, per_page=20):
    return _party_search(Customer, q, page, per_page)
