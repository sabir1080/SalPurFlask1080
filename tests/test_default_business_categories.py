"""System default Business Categories — the default-category master data
seeded by ensure_default_business_categories(), the same tier as the chart
of accounts. See salpurflask/services/category_catalog.py. The exact count
(26 as of the Fabrics addition) is read from DEFAULT_BUSINESS_CATEGORIES
throughout rather than hardcoded, so a future addition never requires
touching every assertion in this file again.

Covers: fresh-init creates every default, idempotency, user-edit
preservation, and that the Item hard rule (valid enabled BusinessCategory
required) still holds against this system-default catalog specifically.

Also covers, at the unit level (no live PostgreSQL needed — tools/*.py's own
require_postgres() gate means the actual CLI scripts can only be exercised
against a real Postgres database, which the pytest suite intentionally does
not use; see conftest.py): that the generator/reset tooling's table
ownership lists correctly exclude business_category, matching "the defaults
are system master data, not generator-owned test data."
"""
from decimal import Decimal

import pytest

from app import app as flask_app, db, User, pwd_context
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory
from salpurflask.services.category_catalog import (
    DEFAULT_BUSINESS_CATEGORIES, ensure_default_business_categories,
)


def _admin(email="admin@defaultcats.com"):
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


# ── Fresh initialization ─────────────────────────────────────────────────────

def test_fresh_init_creates_all_default_categories(appctx):
    created = ensure_default_business_categories()
    assert created == len(DEFAULT_BUSINESS_CATEGORIES)
    assert BusinessCategory.query.count() == len(DEFAULT_BUSINESS_CATEGORIES)
    for name, slug, icon, color, priority in DEFAULT_BUSINESS_CATEGORIES:
        cat = BusinessCategory.query.filter_by(slug=slug).first()
        assert cat is not None, f"missing default category: {slug}"
        assert cat.name == name
        assert cat.is_enabled is True


def test_all_default_slugs_are_present(appctx):
    ensure_default_business_categories()
    expected_slugs = {slug for _n, slug, _i, _c, _p in DEFAULT_BUSINESS_CATEGORIES}
    actual_slugs = {c.slug for c in BusinessCategory.query.filter(
        BusinessCategory.slug.in_(expected_slugs)).all()}
    assert actual_slugs == expected_slugs
    assert len(expected_slugs) == len(DEFAULT_BUSINESS_CATEGORIES)


def test_all_defaults_are_enabled_after_fresh_init(appctx):
    ensure_default_business_categories()
    disabled = BusinessCategory.query.filter_by(is_enabled=False).count()
    assert disabled == 0


def test_all_defaults_are_tagged_as_system_default(appctx):
    ensure_default_business_categories()
    for cat in BusinessCategory.query.all():
        assert (cat.config_data or {}).get("is_system_default") is True


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_running_init_twice_creates_no_duplicates(appctx):
    first = ensure_default_business_categories()
    assert first == len(DEFAULT_BUSINESS_CATEGORIES)
    second = ensure_default_business_categories()
    assert second == 0
    assert BusinessCategory.query.count() == len(DEFAULT_BUSINESS_CATEGORIES)


def test_running_init_many_times_stays_stable(appctx):
    for _ in range(5):
        ensure_default_business_categories()
    assert BusinessCategory.query.count() == len(DEFAULT_BUSINESS_CATEGORIES)
    dup_names = (db.session.query(BusinessCategory.name)
                .group_by(BusinessCategory.name)
                .having(db.func.count(BusinessCategory.id) > 1).all())
    assert dup_names == []


def test_init_preserves_user_customized_fields(appctx):
    """A user renames/recolors/disables a default category — a later boot's
    re-run of ensure_default_business_categories() must never overwrite
    that, only tag it as a system default if it wasn't already."""
    ensure_default_business_categories()
    grocery = BusinessCategory.query.filter_by(slug="grocery").first()
    grocery.name = "Grocery & Essentials"
    grocery.color = "dark"
    grocery.is_enabled = False
    db.session.commit()

    ensure_default_business_categories()  # re-run, as a second boot would

    db.session.refresh(grocery)
    assert grocery.name == "Grocery & Essentials"
    assert grocery.color == "dark"
    assert grocery.is_enabled is False  # not silently re-enabled
    assert BusinessCategory.query.count() == len(DEFAULT_BUSINESS_CATEGORIES)  # no duplicate "Grocery" created


def test_pre_existing_category_gets_tagged_without_duplication(appctx):
    """A category that already exists (e.g. from before this feature shipped)
    under the same name/slug must be tagged, not duplicated."""
    pre_existing = BusinessCategory(name="Grocery", slug="grocery", is_enabled=True)
    db.session.add(pre_existing)
    db.session.commit()

    created = ensure_default_business_categories()
    assert created == len(DEFAULT_BUSINESS_CATEGORIES) - 1  # Grocery already existed, the rest are new
    assert BusinessCategory.query.filter_by(slug="grocery").count() == 1
    db.session.refresh(pre_existing)
    assert (pre_existing.config_data or {}).get("is_system_default") is True


# ── Item hard rule against the default catalog ───────────────────────────────

def test_item_creation_succeeds_with_a_default_category(appctx):
    ensure_default_business_categories()
    # "Miscellaneous" has no required category-specific ProductFields, so this
    # test proves the BusinessCategory hard rule alone, uncoupled from any
    # particular category's own required-field validation (see
    # test_default_product_fields.py for that).
    cat = BusinessCategory.query.filter_by(slug="miscellaneous").first()
    c = _admin()
    resp = c.post("/item", data={
        "name": "Default Cat Item", "business_category_id": str(cat.id),
        "unit": "Pcs", "item_type": "STOCK", "opening_stock": "0",
        "reorder_level": "5", "purchase_price": "10", "sale_price": "20",
    }, follow_redirects=True)
    assert resp.status_code == 200
    item = Item.query.filter_by(name="Default Cat Item").first()
    assert item is not None
    assert item.business_category_id == cat.id
    assert item.category_id is None


def test_disabling_a_default_category_rejects_new_items_but_keeps_existing(appctx):
    ensure_default_business_categories()
    # "Hardware" has no required category-specific ProductFields — see the
    # note on test_item_creation_succeeds_with_a_default_category above.
    cat = BusinessCategory.query.filter_by(slug="hardware").first()
    c = _admin()

    # Create an item while the category is enabled.
    c.post("/item", data={
        "name": "Old Chair", "business_category_id": str(cat.id),
        "unit": "Pcs", "item_type": "STOCK", "opening_stock": "0",
        "reorder_level": "5", "purchase_price": "10", "sale_price": "20",
    }, follow_redirects=True)
    existing_item = Item.query.filter_by(name="Old Chair").first()
    assert existing_item is not None

    # Disable the category.
    cat.is_enabled = False
    db.session.commit()

    # New item creation against the now-disabled category is refused.
    c.post("/item", data={
        "name": "New Table", "business_category_id": str(cat.id),
        "unit": "Pcs", "item_type": "STOCK", "opening_stock": "0",
        "reorder_level": "5", "purchase_price": "10", "sale_price": "20",
    }, follow_redirects=True)
    assert Item.query.filter_by(name="New Table").first() is None

    # The existing item is untouched — not deleted, not reassigned.
    db.session.refresh(existing_item)
    assert existing_item.business_category_id == cat.id
    assert Item.query.filter_by(name="Old Chair").first() is not None


def test_custom_category_can_still_be_created_alongside_defaults(appctx):
    ensure_default_business_categories()
    custom = BusinessCategory(name="My Custom Category", slug="my-custom-category",
                              is_enabled=True)
    db.session.add(custom)
    db.session.commit()

    assert BusinessCategory.query.count() == len(DEFAULT_BUSINESS_CATEGORIES) + 1
    assert not (custom.config_data or {}).get("is_system_default")

    c = _admin()
    resp = c.post("/item", data={
        "name": "Custom Cat Item", "business_category_id": str(custom.id),
        "unit": "Pcs", "item_type": "STOCK", "opening_stock": "0",
        "reorder_level": "5", "purchase_price": "10", "sale_price": "20",
    }, follow_redirects=True)
    assert resp.status_code == 200
    item = Item.query.filter_by(name="Custom Cat Item").first()
    assert item is not None
    assert item.business_category_id == custom.id


# ── Generator/reset tooling: table-ownership contracts (no live Postgres) ──
# tools/_data_common.py itself never calls require_postgres() at import time
# (only generate_test_data.py/reset_test_data.py do, since they issue real
# writes) — so its plain data structures are safely importable and testable
# here without a live PostgreSQL connection, unlike the CLI scripts
# themselves which the pytest suite intentionally never invokes.

def test_reset_tooling_never_owns_business_category_table():
    """Phase 3 reset must never truncate business_category — the 25 defaults
    (and any custom categories) are system/user master data, not
    generator-owned test data."""
    import sys
    sys.path.insert(0, ".")
    from tools._data_common import GENERATOR_OWNED_TABLES

    assert "business_category" not in GENERATOR_OWNED_TABLES
    assert "category" not in GENERATOR_OWNED_TABLES  # legacy table: also never owned


def _read_generator_item_category_names():
    """ITEM_CATEGORY_NAMES, read from generate_test_data.py's source via AST
    rather than `import tools.generate_test_data` — that module calls
    require_postgres() at import time (tools/_data_common.py) and this
    process's DATABASE_URL is the test suite's own throwaway SQLite file
    (see conftest.py), so a real import would sys.exit(1) here. The
    constant is pure data with no Postgres dependency, so reading it
    statically is both safe and exactly what these tests need."""
    import ast

    with open("tools/generate_test_data.py", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "ITEM_CATEGORY_NAMES" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("ITEM_CATEGORY_NAMES not found in tools/generate_test_data.py")


def test_generator_item_category_names_are_a_subset_of_the_25_defaults():
    """The generator looks up categories by name from ITEM_CATEGORY_NAMES —
    every one of those names must actually be one of the 25 system defaults,
    or the generator would fail its own RuntimeError guard at generation
    time (see generate_test_data.py's stage2_master_data())."""
    item_category_names = _read_generator_item_category_names()
    default_names = {name for name, _slug, _icon, _color, _priority in DEFAULT_BUSINESS_CATEGORIES}
    missing = set(item_category_names) - default_names
    assert not missing, f"generator names not found in the 25 defaults: {missing}"


def test_generator_reuses_existing_default_categories_without_duplicating(appctx):
    """Simulates what the generator's stage2_master_data() does: after the
    25 defaults already exist (as they would on any initialized database),
    looking them up by name must find the existing rows, never create new
    ones — proving the generator cannot duplicate system-default categories
    even though this exact lookup pattern is exercised without going
    through the tools/ CLI machinery itself."""
    item_category_names = _read_generator_item_category_names()

    ensure_default_business_categories()
    before_count = BusinessCategory.query.count()

    found = {c.name: c for c in BusinessCategory.query.filter(
        BusinessCategory.name.in_(item_category_names)).all()}
    assert set(found.keys()) == set(item_category_names)

    after_count = BusinessCategory.query.count()
    assert after_count == before_count == len(DEFAULT_BUSINESS_CATEGORIES)
