"""Preconfigured business categories + Item.sku + universal lookup/search —
see salpurflask/services/category_catalog.py and lookup_service.py.

Covers: built-in category seeding (idempotent), SKU as a real column distinct
from barcode/category fields, reserved-name protection, and the ranked,
paginated, category-filterable item/supplier/customer search.
"""
from decimal import Decimal

import pytest

from app import app as flask_app, db, User, pwd_context, Customer, Supplier
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory, ProductField, ProductCategoryData
from salpurflask.services.category_catalog import ensure_builtin_categories, CATEGORIES, PROTECTED_FIELD_NAMES
from salpurflask.services.config_service import ConfigurationService
from salpurflask.services.lookup_service import search_items, search_suppliers, search_customers, get_item_filter_fields
from salpurflask.utils import sku_taken, barcode_taken


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


def _item(name, cat, **kw):
    it = Item(name=name, business_category_id=cat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             stock=kw.pop("stock", 10), opening_stock=0, reorder_level=5, **kw)
    db.session.add(it)
    db.session.commit()
    return it


# ── Category seeding ────────────────────────────────────────────────────────

def test_ensure_builtin_categories_creates_all_catalog_entries(appctx):
    created, fields = ensure_builtin_categories()
    assert created == len(CATEGORIES)
    assert fields > 0
    assert BusinessCategory.query.filter_by(slug="garments-apparel").first() is not None
    assert BusinessCategory.query.filter_by(slug="medical-pharmacy").first() is not None


def test_ensure_builtin_categories_is_idempotent(appctx):
    ensure_builtin_categories()
    before = BusinessCategory.query.count()
    created, fields = ensure_builtin_categories()
    assert created == 0 and fields == 0
    assert BusinessCategory.query.count() == before


def test_ensure_builtin_categories_skips_existing_same_name_different_slug(appctx):
    """A pre-existing category with the same NAME but a different slug (legacy
    data) must not cause a UNIQUE-constraint crash — it's left alone."""
    db.session.add(BusinessCategory(name="Garments / Apparel", slug="garments-old", is_enabled=True))
    db.session.commit()
    created, _ = ensure_builtin_categories()
    assert BusinessCategory.query.filter_by(slug="garments-apparel").first() is None
    assert BusinessCategory.query.filter_by(slug="garments-old").first() is not None


def test_garments_category_has_size_color_brand_fields(appctx):
    ensure_builtin_categories()
    cat = BusinessCategory.query.filter_by(slug="garments-apparel").first()
    names = {f.field_name for f in cat.fields}
    assert {"size", "color", "brand", "fabric"}.issubset(names)


def test_medical_category_has_expiry_and_batch_fields(appctx):
    ensure_builtin_categories()
    cat = BusinessCategory.query.filter_by(slug="medical-pharmacy").first()
    names = {f.field_name for f in cat.fields}
    assert {"expiry_date", "batch_no", "generic_name"}.issubset(names)


def test_catalog_never_declares_a_protected_field_name(appctx):
    for spec in CATEGORIES:
        for f in spec["fields"]:
            assert f["field_name"] not in PROTECTED_FIELD_NAMES


def test_custom_category_still_creatable_after_seeding(appctx):
    """Admin customization capability (Phase 6) must survive seeding."""
    ensure_builtin_categories()
    custom = BusinessCategory(name="My Custom Trade", slug="my-custom-trade", is_enabled=True)
    db.session.add(custom)
    db.session.commit()
    field = ConfigurationService.add_product_field(
        custom.id, {"field_name": "widget_type", "field_label": "Widget Type", "field_type": "text"})
    assert field.id is not None
    assert ProductField.query.filter_by(category_id=custom.id, field_name="widget_type").count() == 1


def test_add_product_field_rejects_reserved_name(appctx):
    cat = _category()
    with pytest.raises(ValueError):
        ConfigurationService.add_product_field(cat.id, {"field_name": "sku", "field_label": "SKU"})
    with pytest.raises(ValueError):
        ConfigurationService.add_product_field(cat.id, {"field_name": "barcode", "field_label": "Barcode"})


# ── SKU identifier ───────────────────────────────────────────────────────────

def test_sku_is_a_real_item_column_distinct_from_barcode(appctx):
    cat = _category()
    it = _item("Blue Shirt", cat, sku="SKU-001", barcode="1234567890")
    assert it.sku == "SKU-001"
    assert it.barcode == "1234567890"


def test_sku_taken_blocks_duplicate_case_insensitive(appctx):
    cat = _category()
    _item("Item A", cat, sku="ABC-1")
    assert sku_taken("abc-1") is True
    assert sku_taken("ABC-1") is True
    assert sku_taken("XYZ-9") is False


def test_sku_taken_ignores_blank(appctx):
    cat = _category()
    _item("Item A", cat, sku=None)
    _item("Item B", cat, sku=None)
    assert sku_taken(None) is False
    assert sku_taken("") is False


def test_sku_taken_excludes_self_on_edit(appctx):
    cat = _category()
    it = _item("Item A", cat, sku="ABC-1")
    assert sku_taken("ABC-1", exclude_id=it.id) is False
    assert sku_taken("ABC-1") is True


def test_item_add_route_rejects_duplicate_sku(appctx):
    cat = _category()
    _item("Existing Item", cat, sku="DUP-1")
    client = _login("manager")
    resp = client.post("/item", data={
        "name": "New Item", "business_category_id": str(cat.id), "item_type": "STOCK",
        "unit": "Pcs", "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20", "sku": "dup-1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert Item.query.filter_by(name="New Item").first() is None
    assert b"already used by another item" in resp.data


def test_item_add_route_saves_sku(appctx):
    cat = _category()
    client = _login("manager")
    client.post("/item", data={
        "name": "New SKU Item", "business_category_id": str(cat.id), "item_type": "STOCK",
        "unit": "Pcs", "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20", "sku": "NEW-1",
    })
    it = Item.query.filter_by(name="New SKU Item").first()
    assert it is not None
    assert it.sku == "NEW-1"


def test_item_edit_route_updates_sku(appctx):
    cat = _category()
    it = _item("Editable Item", cat, sku="OLD-1")
    client = _login("manager")
    client.post(f"/item/edit/{it.id}", data={
        "name": it.name, "business_category_id": str(cat.id), "item_type": "STOCK",
        "unit": "Pcs", "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20", "sku": "NEW-2",
    })
    db.session.refresh(it)
    assert it.sku == "NEW-2"


def test_medical_required_field_validation_blocks_missing_generic_name(appctx):
    ensure_builtin_categories()
    cat = BusinessCategory.query.filter_by(slug="medical-pharmacy").first()
    client = _login("manager")
    resp = client.post("/item", data={
        "name": "Some Medicine", "business_category_id": str(cat.id), "item_type": "STOCK",
        "unit": "Pcs", "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20",
        # generic_name deliberately omitted — it is required for this category
    }, follow_redirects=True)
    assert Item.query.filter_by(name="Some Medicine").first() is None


# ── Universal search / lookup ───────────────────────────────────────────────

def test_search_items_exact_barcode_ranks_first(appctx):
    cat = _category()
    _item("Common Word Shirt", cat, barcode=None)
    exact = _item("Another Shirt", cat, barcode="999")
    rows, total, page, per_page = search_items(q="999")
    assert rows[0].id == exact.id


def test_search_items_case_insensitive_partial_name(appctx):
    cat = _category()
    _item("Blue Denim Jacket", cat)
    rows, total, page, per_page = search_items(q="denim")
    assert total == 1
    assert rows[0].name == "Blue Denim Jacket"


def test_search_items_by_sku(appctx):
    cat = _category()
    it = _item("Item With SKU", cat, sku="FIND-ME-123")
    rows, total, page, per_page = search_items(q="FIND-ME-123")
    assert total == 1
    assert rows[0].id == it.id


def test_search_items_no_results(appctx):
    cat = _category()
    _item("Something", cat)
    rows, total, page, per_page = search_items(q="nonexistent-xyz")
    assert total == 0
    assert rows == []


def test_search_items_paginates_server_side(appctx):
    cat = _category()
    for i in range(25):
        _item(f"Bulk Item {i:02d}", cat)
    rows, total, page, per_page = search_items(q="Bulk", page=1, per_page=10)
    assert total == 25
    assert len(rows) == 10
    rows2, total2, page2, per_page2 = search_items(q="Bulk", page=2, per_page=10)
    assert len(rows2) == 10
    assert {r.id for r in rows} & {r.id for r in rows2} == set()


def test_search_items_filters_by_category(appctx):
    garments = _category("garments-apparel")
    medical = _category("medical-pharmacy")
    _item("Shirt", garments)
    _item("Tablet", medical)
    rows, total, page, per_page = search_items(category_id=garments.id)
    assert total == 1
    assert rows[0].name == "Shirt"


def test_search_items_category_specific_filter_matches(appctx):
    cat = _category("garments-apparel")
    field = ProductField(category_id=cat.id, field_name="size", field_label="Size",
                         field_type="text", is_filterable=True)
    db.session.add(field)
    db.session.commit()

    large = _item("Large Shirt", cat)
    small = _item("Small Shirt", cat)
    ConfigurationService.save_product_category_data(large.id, cat.id, {"size": "Large"})
    ConfigurationService.save_product_category_data(small.id, cat.id, {"size": "Small"})
    db.session.commit()

    rows, total, page, per_page = search_items(category_id=cat.id, filters={"size": "Large"})
    assert total == 1
    assert rows[0].id == large.id


def test_search_items_combined_text_and_category_filter(appctx):
    cat = _category("garments-apparel")
    field = ProductField(category_id=cat.id, field_name="color", field_label="Color",
                         field_type="text", is_filterable=True)
    db.session.add(field)
    db.session.commit()

    match = _item("Blue Shirt", cat)
    other_color = _item("Blue Pants", cat)
    ConfigurationService.save_product_category_data(match.id, cat.id, {"color": "Blue"})
    ConfigurationService.save_product_category_data(other_color.id, cat.id, {"color": "Red"})
    db.session.commit()

    rows, total, page, per_page = search_items(q="Shirt", category_id=cat.id, filters={"color": "Blue"})
    assert total == 1
    assert rows[0].id == match.id


def test_get_item_filter_fields_only_returns_filterable(appctx):
    cat = _category("garments-apparel")
    db.session.add(ProductField(category_id=cat.id, field_name="size", field_label="Size",
                                field_type="text", is_filterable=True))
    db.session.add(ProductField(category_id=cat.id, field_name="internal_note", field_label="Note",
                                field_type="text", is_filterable=False))
    db.session.commit()
    fields = get_item_filter_fields(cat.id)
    names = {f.field_name for f in fields}
    assert "size" in names
    assert "internal_note" not in names


def test_api_item_lookup_endpoint_returns_paginated_json(appctx):
    cat = _category()
    _item("Lookup Target", cat, sku="LOOK-1")
    client = _login("manager")
    resp = client.get("/api/items/lookup?q=Lookup")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["results"][0]["sku"] == "LOOK-1"


def test_api_item_lookup_requires_login(appctx):
    client = flask_app.test_client()
    resp = client.get("/api/items/lookup?q=x")
    assert resp.status_code == 302  # redirected to sign-in, not a bare 200 with data


def test_search_suppliers_by_name_and_phone(appctx):
    db.session.add(Supplier(name="Acme Trading", contact="03001234567", address="X", opening_balance=0))
    db.session.add(Supplier(name="Beta Co", contact="03009999999", address="Y", opening_balance=0))
    db.session.commit()

    rows, total, page, per_page = search_suppliers(q="Acme")
    assert total == 1
    rows2, total2, page2, per_page2 = search_suppliers(q="03009999999")
    assert total2 == 1
    assert rows2[0].name == "Beta Co"


def test_search_customers_paginated(appctx):
    for i in range(15):
        db.session.add(Customer(name=f"Customer {i:02d}", contact="0300", address="A", opening_balance=0))
    db.session.commit()
    rows, total, page, per_page = search_customers(page=1, per_page=10)
    assert total == 15
    assert len(rows) == 10


def test_api_supplier_lookup_endpoint(appctx):
    db.session.add(Supplier(name="Lookup Supplier", contact="0300", address="A", opening_balance=0))
    db.session.commit()
    client = _login("manager")
    resp = client.get("/api/suppliers/lookup?q=Lookup")
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 1


def test_api_customer_lookup_endpoint(appctx):
    db.session.add(Customer(name="Lookup Customer", contact="0300", address="A", opening_balance=0))
    db.session.commit()
    client = _login("manager")
    resp = client.get("/api/customers/lookup?q=Lookup")
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 1
