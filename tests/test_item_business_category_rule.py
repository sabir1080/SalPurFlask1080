"""Hard business rule: an Item may never be created or saved without a valid,
ENABLED BusinessCategory. See salpurflask/services/config_service.py's
resolve_enabled_category() and its use in salpurflask/inventory/routes.py's
item()/edit_item()/import_items().

The legacy Category table/category_id must never be a fallback — a route
that only rejects a bad business_category_id via HTML `required` or a
disabled submit button is not enough; these tests hit the backend directly
with crafted POSTs a browser would never send, exactly like a curl/API call.
"""
from decimal import Decimal

import pytest

from app import app as flask_app, db, User, pwd_context, Category
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory
from salpurflask.services.config_service import ConfigurationService


def _admin(email="admin@catrule.com"):
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _enabled_category(name="Rule Test Category"):
    bc = BusinessCategory(name=name, slug=name.lower().replace(" ", "-"), is_enabled=True)
    db.session.add(bc)
    db.session.commit()
    return bc


def _disabled_category(name="Disabled Category"):
    bc = BusinessCategory(name=name, slug=name.lower().replace(" ", "-"), is_enabled=False)
    db.session.add(bc)
    db.session.commit()
    return bc


def _item_form(**overrides):
    form = {
        "name": "Rule Test Item", "unit": "Pcs", "item_type": "STOCK",
        "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20",
    }
    form.update(overrides)
    return form


# ── ConfigurationService.resolve_enabled_category() — the backend primitive ──

def test_resolve_enabled_category_accepts_a_valid_enabled_category(appctx):
    cat = _enabled_category()
    resolved = ConfigurationService.resolve_enabled_category(str(cat.id))
    assert resolved is not None
    assert resolved.id == cat.id


def test_resolve_enabled_category_rejects_none(appctx):
    assert ConfigurationService.resolve_enabled_category(None) is None


def test_resolve_enabled_category_rejects_blank_string(appctx):
    assert ConfigurationService.resolve_enabled_category("") is None


def test_resolve_enabled_category_rejects_nonexistent_id(appctx):
    assert ConfigurationService.resolve_enabled_category("999999") is None


def test_resolve_enabled_category_rejects_non_numeric_id(appctx):
    assert ConfigurationService.resolve_enabled_category("not-a-number") is None


def test_resolve_enabled_category_rejects_disabled_category(appctx):
    cat = _disabled_category()
    assert ConfigurationService.resolve_enabled_category(str(cat.id)) is None


def test_resolve_enabled_category_accepts_int_input(appctx):
    cat = _enabled_category()
    resolved = ConfigurationService.resolve_enabled_category(cat.id)
    assert resolved is not None and resolved.id == cat.id


# ── Item creation via the real /item route — PASS case ──────────────────────

def test_item_created_with_valid_enabled_category_succeeds(appctx):
    cat = _enabled_category()
    c = _admin()
    resp = c.post("/item", data=_item_form(business_category_id=str(cat.id)),
                  follow_redirects=True)
    assert resp.status_code == 200
    item = Item.query.filter_by(name="Rule Test Item").first()
    assert item is not None
    assert item.business_category_id == cat.id
    assert item.category_id is None


# ── Item creation via the real /item route — FAIL cases (Cases A-F) ─────────

def test_item_creation_rejected_without_any_category_field(appctx):
    """Case E: both category fields missing entirely."""
    _enabled_category()  # at least one enabled category exists, so the form isn't blocked earlier
    c = _admin()
    c.post("/item", data=_item_form(), follow_redirects=True)
    assert Item.query.filter_by(name="Rule Test Item").first() is None


def test_item_creation_rejected_with_null_business_category_id(appctx):
    """Case A: business_category_id explicitly blank."""
    _enabled_category()
    c = _admin()
    c.post("/item", data=_item_form(business_category_id=""), follow_redirects=True)
    assert Item.query.filter_by(name="Rule Test Item").first() is None


def test_item_creation_rejected_with_nonexistent_business_category_id(appctx):
    """Case B: business_category_id does not exist."""
    _enabled_category()
    c = _admin()
    c.post("/item", data=_item_form(business_category_id="999999"), follow_redirects=True)
    assert Item.query.filter_by(name="Rule Test Item").first() is None


def test_item_creation_rejected_with_disabled_business_category(appctx):
    """Case C: business_category_id points to a disabled BusinessCategory —
    this is the exact gap that existed before the fix (the old check only
    verified the row existed, never that it was enabled)."""
    disabled = _disabled_category()
    _enabled_category("Some Other Enabled Category")  # so business_categories isn't globally empty
    c = _admin()
    c.post("/item", data=_item_form(business_category_id=str(disabled.id)),
          follow_redirects=True)
    assert Item.query.filter_by(name="Rule Test Item").first() is None


def test_item_creation_rejected_with_only_legacy_category_id(appctx):
    """Case D: only legacy category_id supplied (crafted POST — the live form
    has no field for this at all, so this simulates a direct/API-style call).
    There must be no silent fallback to the legacy field."""
    _enabled_category()
    legacy_cat = Category(name="Legacy Only")
    db.session.add(legacy_cat)
    db.session.commit()
    c = _admin()
    c.post("/item", data=_item_form(category_id=str(legacy_cat.id)), follow_redirects=True)
    assert Item.query.filter_by(name="Rule Test Item").first() is None


def test_item_creation_rejected_with_invalid_category_via_crafted_post(appctx):
    """Case F: an invalid BusinessCategory submitted through a raw/API-style
    POST — non-digit garbage in the field, not just an empty value."""
    _enabled_category()
    c = _admin()
    c.post("/item", data=_item_form(business_category_id="<script>alert(1)</script>"),
          follow_redirects=True)
    assert Item.query.filter_by(name="Rule Test Item").first() is None


def test_no_business_categories_enabled_blocks_item_creation_entirely(appctx):
    """With zero enabled categories anywhere, item creation must be refused
    even before considering the submitted id."""
    disabled = _disabled_category()
    c = _admin()
    c.post("/item", data=_item_form(business_category_id=str(disabled.id)),
          follow_redirects=True)
    assert Item.query.filter_by(name="Rule Test Item").first() is None


# ── Item update via the real /item/edit/<id> route ───────────────────────────

def _existing_item(cat):
    item = Item(name="Existing Item", business_category_id=cat.id, unit="Pcs",
               item_type="STOCK", opening_stock=0, stock=0, reorder_level=5,
               purchase_price=Decimal("10"), sale_price=Decimal("20"))
    db.session.add(item)
    db.session.commit()
    return item


def test_item_update_rejected_with_null_business_category(appctx):
    cat = _enabled_category()
    item = _existing_item(cat)
    c = _admin()
    c.post(f"/item/edit/{item.id}", data=_item_form(
        name="Existing Item", business_category_id=""), follow_redirects=True)
    db.session.refresh(item)
    assert item.business_category_id == cat.id  # unchanged, never nulled


def test_item_update_rejected_with_disabled_business_category(appctx):
    cat = _enabled_category()
    disabled = _disabled_category()
    item = _existing_item(cat)
    c = _admin()
    c.post(f"/item/edit/{item.id}", data=_item_form(
        name="Existing Item", business_category_id=str(disabled.id)), follow_redirects=True)
    db.session.refresh(item)
    assert item.business_category_id == cat.id  # unchanged, not silently switched to disabled


def test_item_update_succeeds_to_another_valid_enabled_category(appctx):
    cat = _enabled_category("Original Category")
    other = _enabled_category("Other Enabled Category")
    item = _existing_item(cat)
    c = _admin()
    c.post(f"/item/edit/{item.id}", data=_item_form(
        name="Existing Item", business_category_id=str(other.id)), follow_redirects=True)
    db.session.refresh(item)
    assert item.business_category_id == other.id
    assert item.category_id is None


# ── CSV / bulk import path ───────────────────────────────────────────────────

def test_import_item_with_valid_enabled_category_succeeds(appctx):
    from salpurflask.inventory.routes import import_items

    _enabled_category("Import Category")
    success, failed, errors = import_items([{
        "name": "Imported Widget", "category": "Import Category",
        "unit": "Pcs", "purchase_price": "10", "sale_price": "20", "stock": "0",
    }])
    assert failed == 0
    item = Item.query.filter_by(name="Imported Widget").first()
    assert item is not None
    assert item.business_category_id is not None
    assert item.category_id is None


def test_import_item_with_missing_category_is_rejected(appctx):
    from salpurflask.inventory.routes import import_items

    success, failed, errors = import_items([{
        "name": "No Category Widget", "category": "",
        "unit": "Pcs", "purchase_price": "10", "sale_price": "20", "stock": "0",
    }])
    assert failed == 1
    assert Item.query.filter_by(name="No Category Widget").first() is None


def test_import_item_with_disabled_category_is_rejected(appctx):
    from salpurflask.inventory.routes import import_items

    _disabled_category("Disabled Import Category")
    success, failed, errors = import_items([{
        "name": "Disabled Category Widget", "category": "Disabled Import Category",
        "unit": "Pcs", "purchase_price": "10", "sale_price": "20", "stock": "0",
    }])
    assert failed == 1
    assert Item.query.filter_by(name="Disabled Category Widget").first() is None


def test_import_item_with_nonexistent_category_is_rejected(appctx):
    from salpurflask.inventory.routes import import_items

    success, failed, errors = import_items([{
        "name": "Ghost Category Widget", "category": "Does Not Exist At All",
        "unit": "Pcs", "purchase_price": "10", "sale_price": "20", "stock": "0",
    }])
    assert failed == 1
    assert Item.query.filter_by(name="Ghost Category Widget").first() is None
