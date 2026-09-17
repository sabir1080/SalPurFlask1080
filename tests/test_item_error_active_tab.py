"""When creating an Item fails validation, the tab CONTAINING the failing
field must open automatically -- not always whichever tab happens to be
first.

Root cause traced: "General" is not a Bootstrap tab at all (Name, Category,
Price, etc. are always-visible plain fields, never hidden) -- only
category-specific fields (e.g. a "Batch & Expiry" tab) are grouped into
Bootstrap nav-tabs/tab-panes by templates/item.html's loadCategoryFields(),
which always marked Object.keys(fieldsByTab)[0] (the first tab, by
whatever order the fields came back in) as active, with zero awareness of
which field actually failed validation. validate_product_data()'s error
dict (salpurflask/services/config_service.py) is keyed by field_name with
no tab_name attached, so that identity was lost by the time the route
flash()ed a single combined message.

Fix: _first_error_tab_name() (salpurflask/inventory/routes.py) looks the
first failing field_name back up against the category's real ProductField
rows to recover its tab_name, passed to the template as error_tab_name ->
serialized as the JS constant ERROR_TAB_NAME -> loadCategoryFields() opens
that tab if it exists among the category's own tabs, else keeps the
existing default-to-first-tab behavior unchanged.
"""
from app import app as flask_app, db, User, pwd_context
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory, ProductField


def _admin(email="admin@activetab.com"):
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _category_with_required_batch_expiry(name="Active Tab Test Category"):
    """A category whose Batch & Expiry tab has a REQUIRED field, so leaving
    it blank actually triggers validate_product_data()'s error path (the
    shipped default fields are all optional)."""
    bc = BusinessCategory(name=name, slug=name.lower().replace(" ", "-"), is_enabled=True)
    db.session.add(bc)
    db.session.flush()
    db.session.add(ProductField(
        category_id=bc.id, field_name="batch_no", field_label="Batch No.",
        field_type="text", tab_name="Batch & Expiry", position=1, is_required=True,
    ))
    db.session.add(ProductField(
        category_id=bc.id, field_name="expiry_date", field_label="Expiry Date",
        field_type="date", tab_name="Batch & Expiry", position=2,
    ))
    db.session.commit()
    return bc


def _item_form(**overrides):
    form = {
        "name": "Active Tab Widget", "unit": "Pcs", "item_type": "STOCK",
        "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20",
    }
    form.update(overrides)
    return form


def test_batch_expiry_validation_error_opens_that_tab(appctx):
    """Leaving the required Batch No. blank must open the "Batch & Expiry"
    tab automatically on re-render, not silently default to some other
    tab."""
    cat = _category_with_required_batch_expiry()
    c = _admin()
    resp = c.post("/item", data=_item_form(business_category_id=str(cat.id)),
                  follow_redirects=True)

    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert Item.query.filter_by(name="Active Tab Widget").first() is None
    assert "ERROR_TAB_NAME" in body
    # Jinja's |tojson escapes "&" as & for HTML-embedding safety --
    # this is the exact valid-JS form the browser actually receives.
    assert 'const ERROR_TAB_NAME = "Batch \\u0026 Expiry"' in body


def test_general_field_error_does_not_set_an_error_tab(appctx):
    """A General-tab failure (duplicate barcode) never even reaches the
    category_field_errors branch -- ERROR_TAB_NAME must stay null so
    loadCategoryFields() keeps its normal default-to-first-tab behavior,
    and the flash message (already always visible, outside any tab) is
    what actually surfaces the problem."""
    cat = _category_with_required_batch_expiry("General Error Category")
    existing = Item(name="Existing Item", business_category_id=cat.id, unit="Pcs",
                    item_type="STOCK", opening_stock=0, stock=0, reorder_level=5,
                    purchase_price=10, sale_price=20, barcode="DUPTAB1")
    db.session.add(existing)
    db.session.commit()

    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(cat.id), barcode="DUPTAB1", batch_no="B1",
    ), follow_redirects=True)

    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert Item.query.filter_by(name="Active Tab Widget").first() is None
    assert "const ERROR_TAB_NAME = null" in body
    # The typed Batch No. must still be preserved (the earlier fix), even
    # though this specific failure was a General-tab one.
    assert '"batch_no": "B1"' in body or '"batch_no":"B1"' in body


def test_get_request_has_no_error_tab(appctx):
    """A plain GET (nothing submitted, nothing failed) must not set an
    error tab either."""
    _category_with_required_batch_expiry("GET Active Tab Category")
    c = _admin()
    resp = c.get("/item")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert "const ERROR_TAB_NAME = null" in body


def test_successful_creation_unaffected_by_active_tab_fix(appctx):
    """Regression: a normal, fully valid submission must still succeed."""
    cat = _category_with_required_batch_expiry("Success Active Tab Category")
    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(cat.id), batch_no="B0001", expiry_date="2027-06-30",
    ), follow_redirects=True)

    assert resp.status_code == 200
    item = Item.query.filter_by(name="Active Tab Widget").first()
    assert item is not None


def test_category_with_no_tabs_at_all_falls_back_safely(appctx):
    """A category with zero ProductFields (no Batch & Expiry, no tabs at
    all) must not crash _first_error_tab_name() or the template -- an
    ordinary General-only failure still just flashes and re-renders."""
    bc = BusinessCategory(name="No Fields Category", slug="no-fields-category", is_enabled=True)
    db.session.add(bc)
    db.session.commit()

    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(bc.id), reorder_level="",  # forces a General-tab error
    ), follow_redirects=True)

    assert resp.status_code == 200
    assert Item.query.filter_by(name="Active Tab Widget").first() is None
