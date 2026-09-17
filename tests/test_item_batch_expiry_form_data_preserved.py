"""When creating an Item fails validation, the Batch & Expiry tab's typed
values (Batch No., Expiry Date -- category-specific ProductFields, e.g. for
the Medical Store category) must survive the re-render too, not just the
General tab's static fields.

Root cause traced: the Batch & Expiry tab is entirely JavaScript-rendered
(templates/item.html's loadCategoryFields()/renderField()) -- it fetches
field DEFINITIONS from /admin/config/api/category/<id>/fields (label, type,
options) but that endpoint has no concept of a previously-typed VALUE.
renderField() never accepted or rendered a `value` attribute at all, so on
every re-render (validation error OR a plain page load) the tab always
started blank, regardless of any server-side form_data fix -- a purely
JSON-value fix (form_data=request.form) has no effect on markup that
JavaScript, not Jinja, generates. localStorage's own save/restore
(saveFormData()/restoreFormData()) also doesn't cover these fields --
they're not in its hardcoded field-id list and don't exist in the DOM
until a category is picked.

Fix: the route's existing `form_data` (already added for the General tab)
is now also serialized into a JS constant (EXISTING_CATEGORY_FIELD_DATA),
and renderField()/loadCategoryFields() (mirroring edit_item.html's already-
correct pattern for the EDIT page) accept and render a `value`/`selected`/
`checked` attribute from it, escaped through the same escapeHtml() helper
edit_item.html already uses.
"""
from app import app as flask_app, db, User, pwd_context
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory, ProductField


def _admin(email="admin@batchexpiry.com"):
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _category_with_batch_expiry_fields(name="Medical Store Test"):
    bc = BusinessCategory(name=name, slug=name.lower().replace(" ", "-"), is_enabled=True)
    db.session.add(bc)
    db.session.flush()
    db.session.add(ProductField(
        category_id=bc.id, field_name="batch_no", field_label="Batch No.",
        field_type="text", tab_name="Batch & Expiry", position=1,
    ))
    db.session.add(ProductField(
        category_id=bc.id, field_name="expiry_date", field_label="Expiry Date",
        field_type="date", tab_name="Batch & Expiry", position=2,
    ))
    db.session.commit()
    return bc


def _item_form(**overrides):
    form = {
        "name": "Batch Expiry Widget", "unit": "Pcs", "item_type": "STOCK",
        "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20",
    }
    form.update(overrides)
    return form


def test_batch_and_expiry_values_present_in_page_after_validation_error(appctx):
    """A duplicate-barcode failure (the same class of error the General-tab
    fix already covers) must also carry the typed Batch No./Expiry Date
    values back in the response -- serialized for renderField() to pick up,
    since the tab itself is built entirely by JavaScript."""
    cat = _category_with_batch_expiry_fields()
    existing = Item(name="Existing Batch Item", business_category_id=cat.id, unit="Pcs",
                    item_type="STOCK", opening_stock=0, stock=0, reorder_level=5,
                    purchase_price=10, sale_price=20, barcode="DUPBATCH1")
    db.session.add(existing)
    db.session.commit()

    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(cat.id), barcode="DUPBATCH1",
        batch_no="B0099", expiry_date="2027-12-31",
    ), follow_redirects=True)

    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert Item.query.filter_by(name="Batch Expiry Widget").first() is None

    # The values must be present in the serialized JS data the tab reads
    # from on re-render -- not just floating somewhere in a flash message.
    assert "EXISTING_CATEGORY_FIELD_DATA" in body
    assert '"batch_no": "B0099"' in body or '"batch_no":"B0099"' in body
    assert '"expiry_date": "2027-12-31"' in body or '"expiry_date":"2027-12-31"' in body


def test_batch_and_expiry_values_survive_reorder_level_error(appctx):
    """A different validation failure (missing Reorder Level) -- same
    guarantee must hold regardless of which check rejected the submission."""
    cat = _category_with_batch_expiry_fields("Medical Store Test 2")
    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(cat.id), reorder_level="",
        batch_no="LOT-42", expiry_date="2028-01-15",
    ), follow_redirects=True)

    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert Item.query.filter_by(name="Batch Expiry Widget").first() is None
    assert '"batch_no": "LOT-42"' in body or '"batch_no":"LOT-42"' in body
    assert '"expiry_date": "2028-01-15"' in body or '"expiry_date":"2028-01-15"' in body


def test_get_request_has_empty_category_field_data(appctx):
    """A plain GET must not crash (the earlier bug risk: form_data={} has no
    .to_dict(), only a real MultiDict does) and must serialize as an empty
    object, not leak stale data."""
    _category_with_batch_expiry_fields("GET Batch Test")
    c = _admin()
    resp = c.get("/item")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert "EXISTING_CATEGORY_FIELD_DATA = {}" in body


def test_category_field_value_is_html_escaped(appctx):
    """A value containing quote/angle-bracket characters must not break out
    of its value="..." attribute or inject markup -- the same XSS class the
    edit_item.html hotfix (commit 6dabc72) already closed for the edit page;
    the create page's own renderField() must hold the identical line."""
    cat = _category_with_batch_expiry_fields("XSS Batch Test")
    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(cat.id), reorder_level="",  # forces the re-render path
        batch_no='B"><script>alert(1)</script>',
    ), follow_redirects=True)

    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert "<script>alert(1)</script>" not in body


def test_successful_creation_with_batch_and_expiry_still_works(appctx):
    """Regression: the fix must not break the normal, valid-submission path
    where Batch No./Expiry Date are correctly saved as category field data."""
    from salpurflask.models.business_config import ProductCategoryData

    cat = _category_with_batch_expiry_fields("Success Batch Test")
    c = _admin()
    resp = c.post("/item", data=_item_form(
        business_category_id=str(cat.id), batch_no="B0001", expiry_date="2027-06-30",
    ), follow_redirects=True)

    assert resp.status_code == 200
    item = Item.query.filter_by(name="Batch Expiry Widget").first()
    assert item is not None
    saved = {d.field_name: d.field_value for d in
            ProductCategoryData.query.filter_by(product_id=item.id, category_id=cat.id).all()}
    assert saved.get("batch_no") == "B0001"
    assert saved.get("expiry_date") == "2027-06-30"
