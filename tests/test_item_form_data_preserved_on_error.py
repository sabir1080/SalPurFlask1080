"""When creating an Item fails validation (duplicate barcode, missing
category, etc.), the form must re-render with whatever the user already
typed still filled in — never a blank form forcing them to retype
everything. templates/item.html already reads every field through
form_data.get(...), but the item() route (salpurflask/inventory/routes.py)
never actually passed a form_data variable to the template, so every
field silently rendered empty on any validation failure.
"""
from app import app as flask_app, db, User, pwd_context
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory


def _admin(email="admin@formdata.com"):
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _enabled_category(name="Form Data Test Category"):
    bc = BusinessCategory(name=name, slug=name.lower().replace(" ", "-"), is_enabled=True)
    db.session.add(bc)
    db.session.commit()
    return bc


def _item_form(**overrides):
    form = {
        "name": "Form Data Widget", "unit": "Pcs", "item_type": "STOCK",
        "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20",
    }
    form.update(overrides)
    return form


def test_duplicate_barcode_failure_preserves_typed_fields(appctx):
    """A duplicate barcode is a pure `flash()`-then-fall-through failure
    (routes.py's elif chain, no early return) -- the exact class of bug
    reported: page reloads, but the name/price/etc. the user typed must
    still be there."""
    cat = _enabled_category()
    existing = Item(name="Existing Item", business_category_id=cat.id, unit="Pcs",
                    item_type="STOCK", opening_stock=0, stock=0, reorder_level=5,
                    purchase_price=10, sale_price=20, barcode="DUPLICATE123")
    db.session.add(existing)
    db.session.commit()

    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(cat.id), barcode="DUPLICATE123",
        purchase_price="55.50", sale_price="99.99",
    ), follow_redirects=True)

    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    # The item was correctly rejected...
    assert Item.query.filter_by(name="Form Data Widget").first() is None
    # ...but the typed values must still be sitting in the form, not blank.
    assert 'value="Form Data Widget"' in body
    assert 'value="55.5"' in body or 'value="55.50"' in body
    assert 'value="99.99"' in body
    assert 'value="DUPLICATE123"' in body


def test_missing_reorder_level_failure_preserves_typed_fields(appctx):
    """Reorder Level required for a STOCK item -- another flash()-and-
    fall-through case in the same elif chain."""
    cat = _enabled_category("Reorder Test Category")
    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(cat.id), reorder_level="", name="Reorder Widget",
        sku="MY-SKU-1",
    ), follow_redirects=True)

    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert Item.query.filter_by(name="Reorder Widget").first() is None
    assert 'value="Reorder Widget"' in body
    assert 'value="MY-SKU-1"' in body


def test_negative_purchase_price_failure_preserves_typed_fields(appctx):
    cat = _enabled_category("Negative Price Category")
    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(cat.id), name="Negative Price Widget",
        purchase_price="-5",
    ), follow_redirects=True)

    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert Item.query.filter_by(name="Negative Price Widget").first() is None
    assert 'value="Negative Price Widget"' in body
    assert 'value="-5"' in body


def test_get_request_does_not_prefill_form_with_stale_data(appctx):
    """A plain GET to /item (no submission at all) must show a genuinely
    blank form -- form_data must default to empty, not leak a previous
    request's data or crash on an undefined variable."""
    _enabled_category("GET Test Category")
    c = _admin()
    resp = c.get("/item")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    # The Name field's value attribute should be empty for a fresh GET.
    assert 'id="name" name="name" value=""' in body


def test_successful_creation_still_works_after_the_fix(appctx):
    """Regression: the fix must not break the normal, valid-input path."""
    cat = _enabled_category("Success Category")
    c = _admin()
    resp = c.post("/item", data=_item_form(business_category_id=str(cat.id)),
                  follow_redirects=True)
    assert resp.status_code == 200
    item = Item.query.filter_by(name="Form Data Widget").first()
    assert item is not None
    assert item.business_category_id == cat.id
