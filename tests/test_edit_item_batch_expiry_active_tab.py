"""Edit Item: the tab containing a Batch & Expiry (or any category-
specific) validation error must open automatically, on both the
server-side re-render path and the client-side native-HTML5-validation
path -- mirroring the two item-create fixes (tests/
test_item_error_active_tab.py and tests/
test_item_native_validation_tab_reveal.py), reusing the same
_first_error_tab_name() helper rather than duplicating it.

Root cause traced (Edit Item is NOT identical to Create Item):
1. edit_item()'s `elif category_field_errors:` branch (salpurflask/
   inventory/routes.py) flash()ed the combined error message but had NO
   `return` at all -- it fell through the elif chain to the function's
   final `return render_template("edit_item.html", item=item,
   categories=categories, business_categories=business_categories)`,
   which passes no error_tab_name, no form_data-equivalent (Edit Item
   never had one to begin with -- it reloads item.* directly from the DB,
   which is why saved Batch/Expiry values were already correct: point 5
   of the investigation).
2. templates/edit_item.html's loadCategoryFields() had the identical
   hardcoded `Object.keys(fieldsByTab)[0] === tab` "always the first tab"
   logic Create Item had before its own fix -- no ERROR_TAB_NAME concept
   existed here at all.
3. The exact same native-HTML5-validation gap Create Item had also
   applies here unchanged: a required category field inside a non-active
   Bootstrap tab-pane is hidden via display:none, so the browser's native
   "Please fill in this field" tooltip cannot anchor to it and is
   silently swallowed -- edit_item.html's form had no `invalid` capture-
   phase listener at all.

Fix:
- salpurflask/inventory/routes.py: added the missing `return` on the
  category_field_errors branch, passing error_tab_name=_first_error_tab_name(
  resolved_category.slug, category_field_errors) -- reusing the exact
  same helper item()'s own fix already introduced, not a new one.
- templates/edit_item.html: added the ERROR_TAB_NAME JS constant, the
  activeTab computation in loadCategoryFields() (identical to item.html's),
  and a capture-phase `invalid` listener on the newly-id'd
  #itemEditForm -- identical mechanism to item.html's own fix.

LIMITATION: as with the two prior item-create-page fixes, this project's
test suite cannot execute JavaScript or observe a real browser's native
`invalid` event/tooltip/Bootstrap tab-show behavior. These tests verify
the exact HTML/JS CONTRACT (generated markup, ids, script content) via
the real server-rendered page, the same evidence-first approach used
throughout this fix series.
"""
import re
from decimal import Decimal

from app import app as flask_app, db, User, pwd_context
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory, ProductField, ProductCategoryData


def _admin(email="admin@editactivetab.com"):
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    r = c.get("/signin")
    m = re.search(r'name="csrf-token" content="([^"]+)"', r.data.decode("utf-8"))
    csrf = m.group(1) if m else None
    c.post("/signin", data={"email": email, "password": "secret123", "csrf_token": csrf})
    return c


def _category_with_required_batch_expiry(name="Edit Active Tab Test Category"):
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


def _existing_item(cat, name="Existing Edit Item", batch_no="B0001"):
    item = Item(name=name, business_category_id=cat.id, unit="Pcs",
               item_type="STOCK", opening_stock=0, stock=0, reorder_level=5,
               purchase_price=Decimal("10"), sale_price=Decimal("20"))
    db.session.add(item)
    db.session.commit()
    db.session.add(ProductCategoryData(
        product_id=item.id, category_id=cat.id, field_name="batch_no", field_value=batch_no,
    ))
    db.session.commit()
    return item


def _edit_form(item, **overrides):
    form = {
        "name": item.name, "unit": "Pcs", "item_type": "STOCK",
        "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20",
        "business_category_id": str(item.business_category_id),
    }
    form.update(overrides)
    return form


def test_batch_expiry_validation_error_on_edit_opens_that_tab(appctx):
    """Submitting an edit with the required Batch No. cleared must open
    the "Batch & Expiry" tab automatically on re-render."""
    cat = _category_with_required_batch_expiry()
    item = _existing_item(cat)
    c = _admin()

    resp = c.post(f"/item/edit/{item.id}", data=_edit_form(item, batch_no=""),
                  follow_redirects=True)
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert "Batch No. is required" in body
    assert 'const ERROR_TAB_NAME = "Batch \\u0026 Expiry"' in body

    db.session.refresh(item)
    assert item.name == "Existing Edit Item"  # unchanged -- the edit was rejected


def test_general_field_error_on_edit_does_not_set_an_error_tab(appctx):
    """A General-tab failure (blank required Reorder Level) must leave
    ERROR_TAB_NAME null -- the category_field_errors branch is never
    reached for this kind of failure."""
    cat = _category_with_required_batch_expiry("Edit General Error Category")
    item = _existing_item(cat)
    c = _admin()

    resp = c.post(f"/item/edit/{item.id}", data=_edit_form(item, reorder_level="", batch_no="B0002"),
                  follow_redirects=True)
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert "const ERROR_TAB_NAME = null" in body


def test_get_request_on_edit_has_no_error_tab(appctx):
    """A plain GET to the edit page (nothing submitted) must not set an
    error tab."""
    cat = _category_with_required_batch_expiry("Edit GET Category")
    item = _existing_item(cat)
    c = _admin()

    resp = c.get(f"/item/edit/{item.id}")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert "const ERROR_TAB_NAME = null" in body


def test_successful_edit_still_works_after_active_tab_fix(appctx):
    """Regression: a normal, fully valid edit must still succeed."""
    cat = _category_with_required_batch_expiry("Edit Success Category")
    item = _existing_item(cat)
    c = _admin()

    resp = c.post(f"/item/edit/{item.id}", data=_edit_form(item, name="Renamed Item", batch_no="B0003"),
                  follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(item)
    assert item.name == "Renamed Item"


def test_saved_batch_expiry_values_still_load_correctly_on_plain_edit_view(appctx):
    """Regression (investigation point 5): the existing
    /api/product-category-data/<item_id>/<category_id> fetch that
    pre-fills saved values must be completely untouched by this fix."""
    cat = _category_with_required_batch_expiry("Edit Saved Values Category")
    item = _existing_item(cat, batch_no="B0099")
    c = _admin()

    resp = c.get(f"/api/product-category-data/{item.id}/{cat.id}")
    assert resp.status_code == 200
    assert resp.get_json().get("batch_no") == "B0099"


# ─── client-side native-validation mechanism (HTML/JS contract) ───────────


def test_edit_form_has_a_stable_id_for_the_invalid_listener_to_target(appctx):
    cat = _category_with_required_batch_expiry("Edit Form Id Category")
    item = _existing_item(cat)
    c = _admin()
    body = c.get(f"/item/edit/{item.id}").data.decode("utf-8", errors="replace")
    assert 'id="itemEditForm"' in body


def test_edit_invalid_listener_registered_on_capture_phase(appctx):
    cat = _category_with_required_batch_expiry("Edit Capture Category")
    item = _existing_item(cat)
    c = _admin()
    body = c.get(f"/item/edit/{item.id}").data.decode("utf-8", errors="replace")
    assert "getElementById('itemEditForm').addEventListener('invalid'" in body
    start = body.index("getElementById('itemEditForm').addEventListener('invalid'")
    end = body.index("}, true);", start)
    statement = body[start:end]
    assert "bootstrap.Tab.getOrCreateInstance" in statement


def test_edit_invalid_handler_never_calls_preventDefault(appctx):
    cat = _category_with_required_batch_expiry("Edit PreventDefault Category")
    item = _existing_item(cat)
    c = _admin()
    body = c.get(f"/item/edit/{item.id}").data.decode("utf-8", errors="replace")
    start = body.index("getElementById('itemEditForm').addEventListener('invalid'")
    end = body.index("}, true);", start)
    handler_body = body[start:end]
    assert "preventDefault" not in handler_body


def test_edit_required_attributes_unchanged_for_general_and_category_fields(appctx):
    cat = _category_with_required_batch_expiry("Edit Required Attrs Category")
    item = _existing_item(cat)
    c = _admin()
    body = c.get(f"/item/edit/{item.id}").data.decode("utf-8", errors="replace")
    assert 'id="name" name="name" value="Existing Edit Item" required>' in body
    assert 'id="business_category_id" name="business_category_id" required>' in body
    # renderField()'s own template placeholder for `required` must still
    # be intact (filled in client-side per field from the live API).
    assert 'placeholder="${placeholder}" value="${value}" ${required}>' in body


def test_edit_tab_pane_id_and_button_id_convention_matches_reveal_lookup(appctx):
    cat = _category_with_required_batch_expiry("Edit Id Convention Category")
    item = _existing_item(cat)
    c = _admin()
    body = c.get(f"/item/edit/{item.id}").data.decode("utf-8", errors="replace")
    assert "id=\"tab-${tab.replace(/\\s+/g, '-')}-tab\"" in body
    assert "id=\"tab-${tab.replace(/\\s+/g, '-')}\">" in body
    assert "pane.id + '-tab'" in body
