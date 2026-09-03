"""Universal Item Lookup UI migration — Sale/Purchase/Quotation (+ their edit
forms) stopped embedding the full item catalogue and now pick items through
/api/items/lookup. See static/js/item_lookup.js and
salpurflask/services/lookup_service.py.

The critical regression this guards: opening /sale, /purchase, /quotations,
or an edit form must never again send every Item row to the browser.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context, Customer, Supplier,
    seed_chart_of_accounts, seed_fiscal_year, post_item_opening,
)
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory
from salpurflask.models.inventory_location import get_or_create_default_location


def _world():
    """Chart of accounts and an open fiscal year — required for a sale/purchase
    to actually post (see app.py's post_document()); unrelated to the lookup
    migration itself, just the fixture every transaction-creating test needs."""
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    db.session.commit()


def _login(role="manager", email=None):
    email = email or f"{role}@t.com"
    u = User(name=role.capitalize(), email=email, password=pwd_context.hash("secret123"),
            verified=True, role=role)
    db.session.add(u)
    db.session.commit()
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True
    return c


def _category(slug="garments-apparel"):
    cat = BusinessCategory.query.filter_by(slug=slug).first()
    if cat is None:
        cat = BusinessCategory(name=slug, slug=slug, is_enabled=True)
        db.session.add(cat)
        db.session.commit()
    return cat


def _bulk_items(n, cat):
    """A pile of items, larger than one lookup page — big enough that if the
    page embedded them all, the response would visibly balloon."""
    made = []
    for i in range(n):
        it = Item(name=f"Bulk Catalogue Item {i:04d}", business_category_id=cat.id, unit="Pcs",
                 purchase_price=Decimal("10"), sale_price=Decimal("20"),
                 stock=10, opening_stock=0, reorder_level=5, sku=f"BULK-{i:04d}")
        db.session.add(it)
        made.append(it)
    db.session.commit()
    return made


# ── No-full-catalogue regression (the critical requirement) ─────────────────

def test_sale_page_does_not_embed_full_item_catalogue(appctx):
    cat = _category()
    items = _bulk_items(120, cat)
    client = _login("manager")
    resp = client.get("/sale")
    html = resp.get_data(as_text=True)
    assert "ITEMS_DATA" not in html
    assert "ITEM_UNITS" not in html
    # None of the 120 item names were serialized into the page.
    assert items[0].name not in html
    assert items[-1].name not in html


def test_purchase_page_does_not_embed_full_item_catalogue(appctx):
    cat = _category()
    items = _bulk_items(120, cat)
    client = _login("manager")
    resp = client.get("/purchase")
    html = resp.get_data(as_text=True)
    assert "ITEMS_DATA" not in html
    assert "ITEM_UNITS" not in html
    assert items[0].name not in html


def test_quotations_page_does_not_embed_full_item_catalogue(appctx):
    cat = _category()
    items = _bulk_items(120, cat)
    client = _login("manager")
    resp = client.get("/quotations")
    html = resp.get_data(as_text=True)
    assert "ITEMS_DATA" not in html
    assert items[0].name not in html


def test_sale_page_response_size_does_not_scale_with_item_count(appctx):
    cat = _category()
    client = _login("manager")

    resp_small = client.get("/sale")
    size_small = len(resp_small.get_data())

    _bulk_items(500, cat)
    resp_large = client.get("/sale")
    size_large = len(resp_large.get_data())

    # A page that embedded 500 extra items would grow by many KB; a page that
    # doesn't should grow by (near) nothing regardless of catalogue size.
    assert size_large - size_small < 2000


def test_item_lookup_js_is_loaded_on_transaction_pages(appctx):
    client = _login("manager")
    for path in ("/sale", "/purchase", "/quotations"):
        html = client.get(path).get_data(as_text=True)
        assert "item_lookup.js" in html
        assert "item-picker-btn" in html


# ── Lazy per-item units endpoint (replaces item_units_for_js for every item) ─

def test_api_item_units_returns_base_unit(appctx):
    cat = _category()
    it = Item(name="Simple Item", business_category_id=cat.id, unit="Box",
             purchase_price=Decimal("5"), sale_price=Decimal("8"),
             stock=10, opening_stock=0, reorder_level=5)
    db.session.add(it)
    db.session.commit()

    client = _login("manager")
    resp = client.get(f"/api/items/{it.id}/units")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["units"]) == 1
    assert data["units"][0]["name"] == "Box"
    assert data["units"][0]["sale_price"] == 8.0


def test_api_item_units_includes_alternate_units(appctx):
    from salpurflask.models.models import ItemUnit
    cat = _category()
    it = Item(name="Multi Unit Item", business_category_id=cat.id, unit="Pcs",
             purchase_price=Decimal("5"), sale_price=Decimal("8"),
             stock=100, opening_stock=0, reorder_level=5)
    db.session.add(it)
    db.session.flush()
    db.session.add(ItemUnit(item_id=it.id, name="Box", factor=12,
                            purchase_price=Decimal("55"), sale_price=Decimal("90")))
    db.session.commit()

    client = _login("manager")
    resp = client.get(f"/api/items/{it.id}/units")
    data = resp.get_json()
    assert len(data["units"]) == 2
    names = {u["name"] for u in data["units"]}
    assert names == {"Pcs", "Box"}


def test_api_item_units_requires_login(appctx):
    cat = _category()
    it = Item(name="Locked Item", business_category_id=cat.id, unit="Pcs",
             purchase_price=Decimal("5"), sale_price=Decimal("8"),
             stock=10, opening_stock=0, reorder_level=5)
    db.session.add(it)
    db.session.commit()
    client = flask_app.test_client()
    resp = client.get(f"/api/items/{it.id}/units")
    assert resp.status_code == 302


def test_api_item_units_404_for_missing_item(appctx):
    client = _login("manager")
    resp = client.get("/api/items/999999/units")
    assert resp.status_code == 404


# ── End-to-end: a sale/purchase/quotation can still be created after migration
#    (the picker's job is just finding the item — the form still submits
#    item_id[] the same way it always did) ──────────────────────────────────

def test_sale_can_still_be_created_after_migration(appctx):
    _world()
    cat = _category()
    it = Item(name="Sellable Item", business_category_id=cat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             stock=50, opening_stock=50, reorder_level=5, inventory_value=Decimal("500"))
    db.session.add(it)
    db.session.flush()
    post_item_opening(it)
    db.session.commit()

    cust = Customer(name="Test Customer", contact="0300", address="X", opening_balance=0)
    db.session.add(cust)
    db.session.commit()
    loc = get_or_create_default_location()

    client = _login("manager")
    import re
    get_resp = client.get("/sale")
    m = re.search(r'name="csrf-token" content="([^"]+)"', get_resp.get_data(as_text=True))
    csrf = m.group(1)

    resp = client.post("/sale", data={
        "csrf_token": csrf, "customer_id": str(cust.id), "date": "2026-01-15",
        "location_id": str(loc.id),
        "item_id[]": [str(it.id)], "quantity[]": ["2"], "sale_price[]": ["20"],
        "discount_type[]": ["percent"], "discount_value[]": ["0"],
        "tax_percent[]": ["0"], "unit_id[]": [""],
    })
    assert resp.status_code == 302
    from salpurflask.models.models import Sale
    sale = Sale.query.filter_by(customer_id=cust.id).first()
    assert sale is not None
    assert sale.line_items[0].item_id == it.id
    assert sale.line_items[0].quantity == 2


def test_purchase_can_still_be_created_after_migration(appctx):
    _world()
    cat = _category()
    it = Item(name="Purchasable Item", business_category_id=cat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             stock=0, opening_stock=0, reorder_level=5)
    db.session.add(it)
    db.session.commit()

    supp = Supplier(name="Test Supplier", contact="0300", address="X", opening_balance=0)
    db.session.add(supp)
    db.session.commit()
    loc = get_or_create_default_location()

    client = _login("manager")
    import re
    get_resp = client.get("/purchase")
    m = re.search(r'name="csrf-token" content="([^"]+)"', get_resp.get_data(as_text=True))
    csrf = m.group(1)

    resp = client.post("/purchase", data={
        "csrf_token": csrf, "supplier_id": str(supp.id), "date": "2026-01-15",
        "location_id": str(loc.id),
        "item_id[]": [str(it.id)], "quantity[]": ["5"], "purchase_price[]": ["10"],
        "discount_type[]": ["percent"], "discount_value[]": ["0"],
        "tax_percent[]": ["0"], "unit_id[]": [""],
    })
    assert resp.status_code == 302
    from salpurflask.models.models import Purchase
    purchase = Purchase.query.filter_by(supplier_id=supp.id).first()
    assert purchase is not None
    assert purchase.line_items[0].item_id == it.id


def test_quotation_can_still_be_created_after_migration(appctx):
    cat = _category()
    it = Item(name="Quotable Item", business_category_id=cat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             stock=10, opening_stock=10, reorder_level=5)
    db.session.add(it)
    db.session.commit()

    cust = Customer(name="Quote Customer", contact="0300", address="X", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    client = _login("manager")
    import re
    get_resp = client.get("/quotations")
    m = re.search(r'name="csrf-token" content="([^"]+)"', get_resp.get_data(as_text=True))
    csrf = m.group(1)

    resp = client.post("/quotations", data={
        "csrf_token": csrf, "customer_id": str(cust.id), "quote_date": "2026-01-15",
        "item_id[]": [str(it.id)], "quantity[]": ["3"], "sale_price[]": ["20"],
        "discount_type[]": ["percent"], "discount_value[]": ["0"],
        "tax_percent[]": ["0"], "unit_id[]": [""],
    })
    assert resp.status_code == 302
    from salpurflask.models.models import Quotation
    q = Quotation.query.filter_by(customer_id=cust.id).first()
    assert q is not None
    assert q.line_items[0].item_id == it.id
    assert q.line_items[0].quantity == 3
