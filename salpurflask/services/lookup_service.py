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
from sqlalchemy.orm import joinedload

from salpurflask.extensions import db
from salpurflask.models import (
    Item, Supplier, Customer, Sale, SaleItem, Purchase, PurchaseItem, STATUS_POSTED,
)
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


# ── Sale Return / Purchase Return line pickers ──────────────────────────────
#
# "Remaining to return" is not a column — it's computed per line by
# app.get_sale_item_returned_qty / get_purchase_item_returned_qty, which have
# a same-item-twice-on-one-document tie-breaker that must stay in exactly one
# place (see those functions' docstrings). So this module does NOT recompute
# remaining qty in SQL. It only does the part a database is good at: text
# search, party/date filtering, excluding reversed documents and (quantity==0
# already-fully-tagged) obviously-empty lines, ordered newest first, and
# paginated to a candidate page — a caller-supplied `over_fetch` multiplier
# widens that candidate page since a handful of rows on it may still turn out
# to be fully returned once the real remaining-qty check runs and get
# filtered out client-side of this function, same as the pre-existing
# behaviour when the whole table was loaded and filtered by remaining > 0.


def search_returnable_sale_items(q="", customer_id=None, date_from=None, date_to=None,
                                 page=1, per_page=20, over_fetch=3):
    """Candidate SaleItem rows for the Sale Return picker: not on a reversed
    sale, not on a Draft sale (it has no stock/ledger/GL effect yet -- see
    the Draft -> Posted workflow -- so there is nothing on it a return could
    correctly unwind), not already fully returned by quantity alone (the
    exact remaining qty, which needs the tie-breaker logic, is checked by
    the caller). Matches on invoice/sale number, customer name, or the
    sale's date."""
    query = (SaleItem.query
             .join(Sale, SaleItem.sale_id == Sale.id)
             .join(Customer, Sale.customer_id == Customer.id)
             .options(joinedload(SaleItem.item), joinedload(SaleItem.sale_header))
             .filter(Sale.is_reversed.is_(False))
             .filter(Sale.status == STATUS_POSTED)
             .filter(SaleItem.quantity > 0))
    if customer_id:
        query = query.filter(Sale.customer_id == customer_id)

    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        query = query.join(Item, SaleItem.item_id == Item.id).filter(or_(
            Sale.invoice_no.ilike(like),
            Customer.name.ilike(like),
            Item.name.ilike(like),
        ))
    if date_from:
        query = query.filter(Sale.date >= date_from)
    if date_to:
        query = query.filter(Sale.date <= date_to)

    query = query.order_by(Sale.date.desc(), SaleItem.id.desc())
    candidate_per_page = max(1, min(per_page, MAX_RESULTS)) * max(1, over_fetch)
    rows, total, page, _ = _paginate(query, page, candidate_per_page)
    return rows, total, page, per_page


def search_returnable_purchase_items(q="", supplier_id=None, date_from=None, date_to=None,
                                     page=1, per_page=20, over_fetch=3):
    """Candidate PurchaseItem rows for the Purchase Return picker — see
    search_returnable_sale_items, the purchase-side mirror (including the
    Draft exclusion: a Draft purchase has no stock/ledger/GL effect yet for
    a return to unwind)."""
    query = (PurchaseItem.query
             .join(Purchase, PurchaseItem.purchase_id == Purchase.id)
             .join(Supplier, Purchase.supplier_id == Supplier.id)
             .options(joinedload(PurchaseItem.item), joinedload(PurchaseItem.purchase_header))
             .filter(Purchase.is_reversed.is_(False))
             .filter(Purchase.status == STATUS_POSTED)
             .filter(PurchaseItem.quantity > 0))
    if supplier_id:
        query = query.filter(Purchase.supplier_id == supplier_id)

    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        query = query.join(Item, PurchaseItem.item_id == Item.id).filter(or_(
            Purchase.invoice_no.ilike(like),
            Supplier.name.ilike(like),
            Item.name.ilike(like),
        ))
    if date_from:
        query = query.filter(Purchase.date >= date_from)
    if date_to:
        query = query.filter(Purchase.date <= date_to)

    query = query.order_by(Purchase.date.desc(), PurchaseItem.id.desc())
    candidate_per_page = max(1, min(per_page, MAX_RESULTS)) * max(1, over_fetch)
    rows, total, page, _ = _paginate(query, page, candidate_per_page)
    return rows, total, page, per_page
