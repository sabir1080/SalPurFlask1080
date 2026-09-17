"""Client-side fix: reveal a hidden category tab (e.g. "Batch & Expiry")
BEFORE the browser paints its native "Please fill in this field" tooltip
for a required field inside it.

Root cause: this is browser-native HTML5 constraint validation, which runs
entirely client-side, BEFORE the form POST ever reaches Flask -- the
server-side error_tab_name fix (tests/test_item_error_active_tab.py) only
helps once a request has already round-tripped, so it cannot address this
case at all. A `required` field sitting inside a Bootstrap tab-pane that
is not the active one is still hidden via that tab-pane's own `display:
none` (Bootstrap's fade/tab-pane CSS), and a browser cannot anchor its
native validation tooltip to an element it cannot see -- so with General
tab open and Batch No. required-but-blank inside the hidden Batch &
Expiry tab, clicking "Add Item" silently does nothing.

Fix (templates/item.html): a single `invalid` event listener on the form
(#itemCreateForm), registered for the CAPTURE phase (the `true` third
argument -- `invalid` does not bubble, unlike `submit`, so a capture-phase
ancestor listener is the only way to observe it without one listener per
field). On each invalid field it walks up to the enclosing `.tab-pane`;
if that pane isn't the active one, it finds the matching tab button (by
id convention `<pane.id>-tab`, the same one loadCategoryFields() already
generates) and calls `bootstrap.Tab.getOrCreateInstance(tabButton).show()`
on it. It never calls preventDefault(), never touches `required`, and
never runs a custom validity check -- the browser's own native validation
proceeds completely unmodified immediately afterward, now against a
visible field, so its own tooltip can anchor and appear normally.

LIMITATION: this project's test suite (pytest + Flask's test client) has
no real browser and cannot execute JavaScript or observe DOM/CSS
visibility, Bootstrap's tab-show behavior, or the browser's native
`invalid` event/tooltip. These tests instead verify the exact HTML/JS
CONTRACT the fix depends on -- the generated markup, ids, and script
content -- by inspecting the real server-rendered page byte-for-byte, the
same evidence-first approach already used for the two earlier item-create
fixes. A true end-to-end confirmation (this exact scenario) was performed
manually against a live local dev server in a prior turn of this session,
via real HTTP requests reproducing the same request/response the browser
receives; genuine mouse-click/tooltip verification would require an
actual browser automation tool, which is not available in this
environment (see that turn's report for the tooling gap and the request
that was verified instead).
"""
import re

from app import app as flask_app, db, User, pwd_context
from salpurflask.models.business_config import BusinessCategory, ProductField


def _admin(email="admin@nativeval.com"):
    """Creates the user AND returns an authenticated client for it (mirrors
    the meta-csrf-token dance the real login form's own JS performs, since
    the test client never executes JavaScript)."""
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    r = c.get("/signin")
    m = re.search(r'name="csrf-token" content="([^"]+)"', r.data.decode("utf-8"))
    csrf = m.group(1) if m else None
    c.post("/signin", data={"email": email, "password": "secret123", "csrf_token": csrf})
    return c


def _category_with_required_batch_expiry(name="Native Validation Test Category"):
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


def _get_item_page(c):
    resp = c.get("/item")
    assert resp.status_code == 200, "expected an authenticated 200 from /item"
    return resp.data.decode("utf-8", errors="replace")


def test_form_has_a_stable_id_for_the_invalid_listener_to_target(appctx):
    """The capture-phase listener is attached via
    document.getElementById('itemCreateForm') -- the create form must
    actually carry that id, or the whole mechanism silently attaches to
    nothing."""
    c = _admin()
    body = _get_item_page(c)
    assert '<form method="POST" id="itemCreateForm">' in body


def test_invalid_listener_registered_on_capture_phase(appctx):
    """`invalid` does not bubble -- the listener MUST be registered with
    the capture flag (the literal `true` third argument), or it will never
    fire for any field's invalid event at all."""
    c = _admin()
    body = _get_item_page(c)
    assert "getElementById('itemCreateForm').addEventListener('invalid'" in body
    # The registration must end in `}, true);` (capture phase) for this
    # specific listener -- check the exact statement's own closing, not
    # just that the substring "true" appears somewhere else on the page.
    start = body.index("getElementById('itemCreateForm').addEventListener('invalid'")
    end = body.index("}, true);", start)
    statement = body[start:end]
    assert "bootstrap.Tab.getOrCreateInstance" in statement  # confirms this IS the handler body


def test_invalid_handler_never_calls_preventDefault(appctx):
    """The fix must only reveal the tab, never block or replace native
    validation -- preventDefault() on the invalid event (or on the form's
    submit) would suppress the browser's own tooltip entirely, which is
    the opposite of what this fix exists to restore."""
    c = _admin()
    body = _get_item_page(c)
    start = body.index("addEventListener('invalid'")
    end = body.index("}, true);", start)
    handler_body = body[start:end]
    assert "preventDefault" not in handler_body


def test_invalid_handler_uses_bootstrap_tab_show_api(appctx):
    """The reveal mechanism must be Bootstrap's own documented Tab API
    (getOrCreateInstance(...).show()) -- not a raw class/style toggle that
    could drift out of sync with how loadCategoryFields() itself manages
    the active tab's classes."""
    c = _admin()
    body = _get_item_page(c)
    assert "bootstrap.Tab.getOrCreateInstance" in body
    assert ".show()" in body


def test_general_fields_keep_their_required_attribute_unchanged(appctx):
    """The always-visible General fields (never inside a tab-pane) must
    keep native `required` exactly as before -- this fix must not touch
    their validation behavior at all."""
    c = _admin()
    body = _get_item_page(c)
    assert 'id="name" name="name" value="" required>' in body
    assert 'id="business_category_id" name="business_category_id" required>' in body


def test_category_specific_required_field_still_carries_native_required(appctx):
    """The Batch & Expiry field's `required` attribute is what makes this
    whole scenario reachable in the first place -- renderField() must
    still emit a real HTML5 `required` via its own template placeholder,
    never removed or replaced by a custom validity mechanism."""
    _category_with_required_batch_expiry()
    c = _admin()
    body = _get_item_page(c)
    # renderField()'s text-input branch (see templates/item.html): the
    # ${required} placeholder is still literally present in the JS source
    # sent to the browser -- it is filled in client-side per field, so a
    # static server-rendered page never contains "batch_no" directly, but
    # the mechanism that WOULD emit it must be intact and unchanged.
    assert 'placeholder="${placeholder}" value="${value}" ${required}>' in body


def test_tab_pane_id_and_button_id_convention_matches_reveal_lookup(appctx):
    """The invalid handler looks up the tab button as `pane.id + '-tab'` --
    this must match exactly what loadCategoryFields() itself generates for
    both the button id and the pane id, or the lookup silently finds
    nothing and getOrCreateInstance/.show() never runs."""
    c = _admin()
    body = _get_item_page(c)
    # Button id template: `tab-${tab...}-tab` ; pane id template: `tab-${tab...}`
    assert "id=\"tab-${tab.replace(/\\s+/g, '-')}-tab\"" in body
    assert "id=\"tab-${tab.replace(/\\s+/g, '-')}\">" in body
    # The reveal handler's own lookup expression.
    assert "pane.id + '-tab'" in body


def test_general_tab_error_path_unaffected_form_data_still_preserved(appctx):
    """Regression: the server-side error_tab_name / form_data preservation
    fixes from the earlier turns must still work exactly as before -- this
    client-side addition changes nothing about that response shape."""
    cat = _category_with_required_batch_expiry("Regression Category")
    c = _admin()
    resp = c.post("/item", data={
        "name": "Regression Widget", "unit": "Pcs", "item_type": "STOCK",
        "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20",
        "business_category_id": str(cat.id),
        # batch_no omitted -> category_field_errors -> error_tab_name path
    }, follow_redirects=True)
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert 'const ERROR_TAB_NAME = "Batch \\u0026 Expiry"' in body
    assert 'value="Regression Widget"' in body
