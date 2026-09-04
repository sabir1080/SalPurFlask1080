"""Default ProductFields for the 26 default Business Categories — system
master data, the same tier as the categories themselves and the chart of
accounts. See salpurflask/services/category_catalog.py's
DEFAULT_PRODUCT_FIELDS / ensure_default_product_fields().

Covers: fresh-init creates every expected field with the right
type/required/options, idempotency, user-edit preservation, is_active
behavior, custom-field distinguishability, and compatibility with existing
Items (additive only — no retroactive population).
"""
from decimal import Decimal

import pytest

from app import app as flask_app, db, User, pwd_context
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory, ProductField, ProductCategoryData
from salpurflask.services.category_catalog import (
    DEFAULT_BUSINESS_CATEGORIES, DEFAULT_PRODUCT_FIELDS,
    ensure_default_business_categories, ensure_default_product_fields,
    PROTECTED_FIELD_NAMES,
)


def _admin(email="admin@defaultfields.com"):
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


# ── Catalog data integrity (no DB needed) ────────────────────────────────────

def test_every_default_category_has_a_field_spec_list():
    cat_slugs = {slug for _n, slug, _i, _c, _p in DEFAULT_BUSINESS_CATEGORIES}
    field_slugs = set(DEFAULT_PRODUCT_FIELDS.keys())
    assert cat_slugs == field_slugs


def test_no_default_field_uses_a_protected_name():
    for slug, specs in DEFAULT_PRODUCT_FIELDS.items():
        for spec in specs:
            assert spec["field_name"] not in PROTECTED_FIELD_NAMES, (
                f"{slug}.{spec['field_name']} is a protected name")


def test_no_duplicate_field_names_within_a_category():
    for slug, specs in DEFAULT_PRODUCT_FIELDS.items():
        names = [s["field_name"] for s in specs]
        assert len(names) == len(set(names)), f"duplicate field name within {slug}"


def test_model_year_is_a_number_field_not_date():
    automotive = DEFAULT_PRODUCT_FIELDS["automotive"]
    model_year = next(s for s in automotive if s["field_name"] == "model_year")
    assert model_year["field_type"] == "number"


def test_expiry_date_fields_are_date_type():
    for slug in ("grocery", "dairy", "bakery", "medical-store", "personal-care"):
        specs = DEFAULT_PRODUCT_FIELDS[slug]
        expiry = next(s for s in specs if s["field_name"] == "expiry_date")
        assert expiry["field_type"] == "date"


def test_numeric_fields_use_number_type():
    checks = [
        ("medical-store", "mrp"), ("fabrics", "gsm"), ("fabrics", "width"),
        ("electrical", "power_watt"), ("automotive", "model_year"),
    ]
    for slug, field_name in checks:
        spec = next(s for s in DEFAULT_PRODUCT_FIELDS[slug] if s["field_name"] == field_name)
        assert spec["field_type"] == "number", f"{slug}.{field_name} should be number"


def test_is_organic_is_boolean_type():
    spec = next(s for s in DEFAULT_PRODUCT_FIELDS["fruits-vegetables"]
               if s["field_name"] == "is_organic")
    assert spec["field_type"] == "boolean"


def test_select_fields_declare_options():
    select_fields = [(slug, s) for slug, specs in DEFAULT_PRODUCT_FIELDS.items()
                     for s in specs if s["field_type"] == "select"]
    assert select_fields, "expected at least one select field in the catalog"
    for slug, spec in select_fields:
        assert spec.get("options"), f"{slug}.{spec['field_name']} is select but has no options"


# ── Required field spec, per your explicit call-outs ─────────────────────────

def test_medical_store_required_fields():
    specs = {s["field_name"]: s for s in DEFAULT_PRODUCT_FIELDS["medical-store"]}
    for name in ("generic_name", "batch_no", "expiry_date", "mrp", "manufacturer"):
        assert specs[name].get("is_required") is True, f"{name} should be required"
    assert specs["brand"].get("is_required", False) is False
    assert specs["dosage_form"].get("is_required", False) is False


def test_garments_required_fields():
    specs = {s["field_name"]: s for s in DEFAULT_PRODUCT_FIELDS["garments"]}
    assert specs["size"]["is_required"] is True
    assert specs["color"]["is_required"] is True
    for name in ("fabric", "brand", "season", "gender"):
        assert specs[name].get("is_required", False) is False


def test_shoes_required_fields():
    specs = {s["field_name"]: s for s in DEFAULT_PRODUCT_FIELDS["shoes"]}
    assert specs["size"]["is_required"] is True
    assert specs["color"]["is_required"] is True
    for name in ("material", "brand", "gender"):
        assert specs[name].get("is_required", False) is False


def test_fabrics_required_fields():
    specs = {s["field_name"]: s for s in DEFAULT_PRODUCT_FIELDS["fabrics"]}
    assert specs["fabric_type"]["is_required"] is True
    assert specs["color"]["is_required"] is True
    for name in ("width", "gsm", "pattern"):
        assert specs[name].get("is_required", False) is False


# ── Fresh initialization ──────────────────────────────────────────────────────

def test_fresh_init_creates_all_expected_default_fields(appctx):
    ensure_default_business_categories()
    total_expected = sum(len(specs) for specs in DEFAULT_PRODUCT_FIELDS.values())
    assert ProductField.query.count() == total_expected


def test_fresh_init_fields_are_active_and_system_default(appctx):
    ensure_default_business_categories()
    for f in ProductField.query.all():
        assert f.is_active is True
        assert f.is_system_default is True


def test_fresh_init_field_types_match_catalog(appctx):
    ensure_default_business_categories()
    cat = BusinessCategory.query.filter_by(slug="fabrics").first()
    fields = {f.field_name: f for f in ProductField.query.filter_by(category_id=cat.id).all()}
    assert fields["fabric_type"].field_type == "select"
    assert fields["fabric_type"].options
    assert fields["width"].field_type == "number"
    assert fields["gsm"].field_type == "number"
    assert fields["color"].field_type == "text"
    assert fields["pattern"].field_type == "text"


def test_fabrics_category_and_fields_exist(appctx):
    ensure_default_business_categories()
    cat = BusinessCategory.query.filter_by(slug="fabrics").first()
    assert cat is not None
    assert cat.name == "Fabrics"
    assert cat.is_enabled is True
    names = {f.field_name for f in ProductField.query.filter_by(category_id=cat.id).all()}
    assert names == {"fabric_type", "color", "width", "gsm", "pattern"}


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_running_init_twice_creates_no_duplicate_fields(appctx):
    ensure_default_business_categories()
    total_expected = sum(len(specs) for specs in DEFAULT_PRODUCT_FIELDS.values())
    first_count = ProductField.query.count()
    assert first_count == total_expected

    ensure_default_business_categories()
    assert ProductField.query.count() == total_expected

    dup_rows = (db.session.query(ProductField.category_id, ProductField.field_name)
               .group_by(ProductField.category_id, ProductField.field_name)
               .having(db.func.count(ProductField.id) > 1).all())
    assert dup_rows == []


def test_running_init_many_times_stays_stable(appctx):
    for _ in range(4):
        ensure_default_business_categories()
    total_expected = sum(len(specs) for specs in DEFAULT_PRODUCT_FIELDS.values())
    assert ProductField.query.count() == total_expected


# ── User-edit preservation ────────────────────────────────────────────────────

def test_init_preserves_user_customized_field_properties(appctx):
    ensure_default_business_categories()
    cat = BusinessCategory.query.filter_by(slug="garments").first()
    size_field = ProductField.query.filter_by(category_id=cat.id, field_name="size").first()

    size_field.field_label = "My Custom Size Label"
    size_field.is_required = False
    db.session.commit()

    ensure_default_business_categories()  # re-run, as a second boot would

    db.session.refresh(size_field)
    assert size_field.field_label == "My Custom Size Label"
    assert size_field.is_required is False
    # no duplicate "size" field was created for Garments
    assert ProductField.query.filter_by(category_id=cat.id, field_name="size").count() == 1


def test_disabling_a_default_field_survives_reinitialization(appctx):
    """is_active is the disable mechanism — a re-run must never flip it back
    on, or an admin's choice to hide a field would be undone every boot."""
    ensure_default_business_categories()
    cat = BusinessCategory.query.filter_by(slug="electronics").first()
    warranty = ProductField.query.filter_by(category_id=cat.id, field_name="warranty").first()

    warranty.is_active = False
    db.session.commit()

    ensure_default_business_categories()

    db.session.refresh(warranty)
    assert warranty.is_active is False


# ── Custom fields remain distinguishable ─────────────────────────────────────

def test_custom_field_on_a_default_category_is_not_marked_system_default(appctx):
    ensure_default_business_categories()
    cat = BusinessCategory.query.filter_by(slug="hardware").first()
    custom = ProductField(category_id=cat.id, field_name="warranty_months",
                          field_label="Warranty (months)", field_type="number",
                          is_active=True, is_system_default=False)
    db.session.add(custom)
    db.session.commit()

    assert custom.is_system_default is False
    system_fields = ProductField.query.filter_by(category_id=cat.id, is_system_default=True).count()
    custom_fields = ProductField.query.filter_by(category_id=cat.id, is_system_default=False).count()
    assert system_fields == len(DEFAULT_PRODUCT_FIELDS["hardware"])
    assert custom_fields == 1


def test_reinit_does_not_touch_or_duplicate_custom_fields(appctx):
    ensure_default_business_categories()
    cat = BusinessCategory.query.filter_by(slug="miscellaneous").first()
    custom = ProductField(category_id=cat.id, field_name="my_custom_attr",
                          field_label="My Attribute", field_type="text",
                          is_active=True, is_system_default=False)
    db.session.add(custom)
    db.session.commit()

    ensure_default_business_categories()

    matches = ProductField.query.filter_by(category_id=cat.id, field_name="my_custom_attr").all()
    assert len(matches) == 1
    assert matches[0].is_system_default is False


# ── is_active behavior in the live item-editing flow ─────────────────────────

def test_disabled_default_field_does_not_appear_in_category_fields_api(appctx):
    """The category-fields API feeds the Item form's dropdown-driven field
    renderer — a disabled field (is_active=False) must be excluded from it,
    so disabling a field actually hides it from new/edited Items, not just
    flips a stored flag with no effect."""
    ensure_default_business_categories()
    cat = BusinessCategory.query.filter_by(slug="electronics").first()
    warranty = ProductField.query.filter_by(category_id=cat.id, field_name="warranty").first()
    warranty.is_active = False
    db.session.commit()

    c = _admin()
    resp = c.get(f"/admin/config/api/category/{cat.id}/fields")
    assert resp.status_code == 200
    field_names = {f["field_name"] for f in resp.get_json()}
    assert "warranty" not in field_names
    # every other Electronics field is still offered
    assert "brand" in field_names and "model" in field_names


# ── Compatibility with existing Items ─────────────────────────────────────────

def test_seeding_default_fields_does_not_create_any_product_category_data(appctx):
    """Additive only — adding field DEFINITIONS must never retroactively
    populate VALUES for existing items."""
    ensure_default_business_categories()
    cat = BusinessCategory.query.filter_by(slug="grocery").first()
    item = Item(name="Existing Rice", business_category_id=cat.id, unit="Kg",
               item_type="STOCK", opening_stock=0, stock=0, reorder_level=5,
               purchase_price=Decimal("10"), sale_price=Decimal("20"))
    db.session.add(item)
    db.session.commit()

    assert ProductCategoryData.query.filter_by(product_id=item.id).count() == 0

    # Re-running initialization (e.g. a later boot) must not populate it either.
    ensure_default_business_categories()
    assert ProductCategoryData.query.filter_by(product_id=item.id).count() == 0


def test_existing_item_still_saves_correctly_after_default_fields_are_added(appctx):
    """An Item created before its category had any default fields must
    remain fully editable afterward — adding field definitions is additive
    and must never block or alter unrelated Item saves."""
    cat = BusinessCategory(name="Pre-Fields Category", slug="pre-fields-category", is_enabled=True)
    db.session.add(cat)
    db.session.commit()

    item = Item(name="Old Item", business_category_id=cat.id, unit="Pcs",
               item_type="STOCK", opening_stock=0, stock=0, reorder_level=5,
               purchase_price=Decimal("10"), sale_price=Decimal("20"))
    db.session.add(item)
    db.session.commit()

    ensure_default_business_categories()  # does nothing for this non-default category

    c = _admin()
    resp = c.post(f"/item/edit/{item.id}", data={
        "name": "Old Item Renamed", "business_category_id": str(cat.id),
        "unit": "Pcs", "item_type": "STOCK", "opening_stock": "0",
        "reorder_level": "5", "purchase_price": "10", "sale_price": "20",
    }, follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(item)
    assert item.name == "Old Item Renamed"
