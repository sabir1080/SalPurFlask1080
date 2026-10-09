"""Category-wise Batch Tracking and Stock Expiry settings.

Two independent switches per Business Category, stored in BusinessCategory.config_data (no schema change).
An unconfigured category falls back to what it already is: it tracks batches if it has a Batch No. field or
already has batch-tracked items, and tracks expiry if it has an Expiry Date field or batches with expiry.
Warranty Expiry Date is a different field and never counts. The Add Item page applies the settings at once and
the server enforces them whatever the form sent.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context, Item, Batch,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year, seed_financial_account_links,
    FinancialAccount,
)
from salpurflask.models.business_config import BusinessCategory, ProductField, ProductCategoryData


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()


def _clear_cached_user():
    from flask import g
    try:
        del g._login_user
    except AttributeError:
        pass


class _RoleClient:
    """Flask-Login caches the resolved user on flask.g, and the appctx fixture keeps one app context for
    the whole test, so a second login would otherwise be answered as the first user (the sign-in view even
    redirects an 'already authenticated' request). Same wrapper as tests/test_draft_purchase.py."""
    def __init__(self, client):
        self._client = client

    def get(self, *a, **kw):
        _clear_cached_user()
        return self._client.get(*a, **kw)

    def post(self, *a, **kw):
        _clear_cached_user()
        return self._client.post(*a, **kw)


def _login(role="admin", email=None):
    email = email or f"{role}@cattrack.com"
    db.session.add(User(name=role, email=email, password=pwd_context.hash("secret123"), verified=True, role=role))
    db.session.commit()
    c = _RoleClient(flask_app.test_client())
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _cat(name, fields=(), config=None):
    bc = BusinessCategory(name=name, slug=name.lower().replace(" ", "-"), is_enabled=True,
                          config_data=config if config is not None else {})
    db.session.add(bc)
    db.session.flush()
    for pos, (fname, ftype, required) in enumerate(fields):
        db.session.add(ProductField(category_id=bc.id, field_name=fname, field_label=fname, field_type=ftype,
                                    is_required=required, tab_name="Batch & Expiry", position=pos))
    db.session.commit()
    return bc


MEDICAL = (("batch_no", "text", True), ("expiry_date", "date", True))
ELECTRONICS = (("warranty_months", "number", False), ("warranty_expiry_date", "date", True))


def _form(cat, **extra):
    data = {"name": "Thing", "business_category_id": str(cat.id), "unit": "Pcs", "item_type": "STOCK",
            "opening_stock": "0", "reorder_level": "5", "purchase_price": "10", "sale_price": "20"}
    data.update(extra)
    return data


def _batch_rows(rows):
    return {"batch_tracked": "1",
            "opening_batch_no[]": [r[0] for r in rows], "opening_batch_expiry[]": [r[1] for r in rows],
            "opening_batch_qty[]": [r[2] for r in rows], "opening_batch_rate[]": [r[3] for r in rows]}


def _stored(item):
    return {d.field_name: d.field_value for d in ProductCategoryData.query.filter_by(product_id=item.id)}


# ── defaults ────────────────────────────────────────────────────────────────


def test_defaults_follow_the_category_fields(appctx):
    medical = _cat("Medical Store", MEDICAL)
    garments = _cat("Garments")
    electronics = _cat("Electronics", ELECTRONICS)
    footwear = _cat("Footwear")
    assert (medical.batch_tracking_enabled, medical.stock_expiry_enabled) == (True, True)
    assert (garments.batch_tracking_enabled, garments.stock_expiry_enabled) == (False, False)
    # warranty_expiry_date is NOT a stock expiry
    assert (electronics.batch_tracking_enabled, electronics.stock_expiry_enabled) == (False, False)
    assert (footwear.batch_tracking_enabled, footwear.stock_expiry_enabled) == (False, False)
    assert garments.disabled_stock_field_names == ("batch_no", "expiry_date")


def test_expiry_only_category_tracks_expiry_but_not_batches(appctx):
    dairy = _cat("Dairy", (("expiry_date", "date", False),))
    assert (dairy.batch_tracking_enabled, dairy.stock_expiry_enabled) == (False, True)


def test_a_category_already_using_batches_keeps_them(appctx):
    _books()
    cat = _cat("Departmental Store")                      # no batch fields, nothing configured
    assert cat.batch_tracking_enabled is False
    db.session.add(Item(name="Syrup", business_category_id=cat.id, unit="Pcs", item_type="STOCK", stock=0,
                        opening_stock=0, batch_tracked=True, purchase_price=1, sale_price=2))
    db.session.commit()
    db.session.refresh(cat)
    assert cat.batch_tracking_enabled is True
    assert cat.stock_expiry_enabled is True            # expiry lives on the batch: still available


def test_explicit_settings_win_and_are_independent(appctx):
    medical = _cat("Medical Store", MEDICAL, {"batch_tracking": False})
    assert (medical.batch_tracking_enabled, medical.stock_expiry_enabled) == (False, True)
    garments = _cat("Garments", config={"batch_tracking": True, "stock_expiry": False})
    assert (garments.batch_tracking_enabled, garments.stock_expiry_enabled) == (True, False)


def test_inactive_field_does_not_count(appctx):
    cat = _cat("Odd", MEDICAL)
    for f in cat.fields:
        f.is_active = False
    db.session.commit()
    db.session.refresh(cat)
    assert (cat.batch_tracking_enabled, cat.stock_expiry_enabled) == (False, False)


# ── the Add Item page ───────────────────────────────────────────────────────


def test_add_item_options_carry_each_categorys_settings(appctx):
    medical, garments = _cat("Medical Store", MEDICAL), _cat("Garments")
    html = _login().get("/item").get_data(as_text=True)
    assert f'value="{medical.id}" data-category-name="Medical Store" data-batch-tracking="1" data-stock-expiry="1"' in html
    assert f'value="{garments.id}" data-category-name="Garments" data-batch-tracking="0" data-stock-expiry="0"' in html
    assert "function categoryTracking" in html


# ── server-side enforcement on create ───────────────────────────────────────


def test_batch_tracked_item_is_refused_in_a_category_without_batches(appctx):
    _books()
    garments = _cat("Garments")
    resp = _login().post("/item", data={**_form(garments), **_batch_rows([("B1", "", "5", "10")])},
                         follow_redirects=True)
    assert b"Batch tracking is not enabled for the" in resp.data
    assert Item.query.filter_by(name="Thing").count() == 0 and Batch.query.count() == 0


def test_plain_item_in_a_category_without_batches_is_created(appctx):
    _books()
    garments = _cat("Garments")
    _login().post("/item", data=_form(garments, opening_stock="4"), follow_redirects=True)
    item = Item.query.filter_by(name="Thing").one()
    assert item.batch_tracked is False and item.stock == 4


def test_disabled_fields_are_neither_required_nor_saved(appctx):
    _books()
    cat = _cat("Medical Store", MEDICAL, {"batch_tracking": False, "stock_expiry": False})
    _login().post("/item", data=_form(cat, batch_no="STRAY", expiry_date="2030-01-01"), follow_redirects=True)
    item = Item.query.filter_by(name="Thing").one()        # created although both fields are 'required'
    stored = _stored(item)
    assert "batch_no" not in stored and "expiry_date" not in stored


def test_stock_expiry_off_refuses_an_expiry_on_a_batch_row(appctx):
    _books()
    cat = _cat("Batchy", config={"batch_tracking": True, "stock_expiry": False})
    admin = _login()
    resp = admin.post("/item", data={**_form(cat), **_batch_rows([("B1", "2030-01-01", "5", "10")])},
                      follow_redirects=True)
    assert b"stock expiry is not enabled" in resp.data
    assert Item.query.filter_by(name="Thing").count() == 0

    admin.post("/item", data={**_form(cat), **_batch_rows([("B1", "", "5", "10")])}, follow_redirects=True)
    item = Item.query.filter_by(name="Thing").one()
    assert item.batch_tracked is True and Batch.query.filter_by(item_id=item.id, batch_no="B1").one().expiry_date is None


def test_medical_store_defaults_still_work_with_batches_and_expiry(appctx):
    _books()
    cat = _cat("Medical Store", MEDICAL)
    _login().post("/item", data={**_form(cat), **_batch_rows([("B1", "2030-01-01", "5", "10")])},
                  follow_redirects=True)
    item = Item.query.filter_by(name="Thing").one()
    assert item.batch_tracked is True and item.stock == 5
    assert str(Batch.query.filter_by(item_id=item.id, batch_no="B1").one().expiry_date) == "2030-01-01"


def test_warranty_fields_are_unaffected(appctx):
    _books()
    cat = _cat("Electronics", ELECTRONICS)
    _login().post("/item", data=_form(cat, warranty_months="12", warranty_expiry_date="2030-06-30"),
                  follow_redirects=True)
    stored = _stored(Item.query.filter_by(name="Thing").one())
    assert stored.get("warranty_expiry_date") == "2030-06-30" and stored.get("warranty_months") == "12"


# ── enabling batch tracking on an existing item ─────────────────────────────


def test_enabling_batch_tracking_on_an_existing_item_respects_the_category(appctx):
    _books()
    garments, medical = _cat("Garments"), _cat("Medical Store", MEDICAL)
    shirt = Item(name="Shirt", business_category_id=garments.id, unit="Pcs", item_type="STOCK", stock=0,
                 opening_stock=0, purchase_price=1, sale_price=2)
    syrup = Item(name="Syrup", business_category_id=medical.id, unit="Pcs", item_type="STOCK", stock=0,
                 opening_stock=0, purchase_price=1, sale_price=2)
    db.session.add_all([shirt, syrup])
    db.session.commit()
    admin = _login()

    resp = admin.post(f"/item/{shirt.id}/enable-batch-tracking", follow_redirects=True)
    assert b"Batch tracking is not enabled for the" in resp.data
    admin.post(f"/item/{syrup.id}/enable-batch-tracking", follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Item, shirt.id).batch_tracked is False
    assert db.session.get(Item, syrup.id).batch_tracked is True


# ── the admin setting ───────────────────────────────────────────────────────


def test_admin_can_set_each_switch_independently_and_nothing_else_changes(appctx):
    cat = _cat("Garments", config={"is_system_default": True})
    item = Item(name="Shirt", business_category_id=cat.id, unit="Pcs", item_type="STOCK", stock=7,
                opening_stock=7, purchase_price=1, sale_price=2)
    db.session.add(item)
    db.session.commit()
    admin = _login()

    r = admin.post(f"/admin/config/category/{cat.id}/stock-tracking", json={"batch_tracking": True})
    assert r.status_code == 200 and r.get_json()["batch_tracking"] is True and r.get_json()["stock_expiry"] is False
    r = admin.post(f"/admin/config/category/{cat.id}/stock-tracking", json={"stock_expiry": True})
    assert r.get_json() == {"success": True, "batch_tracking": True, "stock_expiry": True}

    db.session.expire_all()
    cfg = db.session.get(BusinessCategory, cat.id).config_data
    assert cfg == {"is_system_default": True, "batch_tracking": True, "stock_expiry": True}
    assert db.session.get(Item, item.id).stock == 7 and db.session.get(Item, item.id).batch_tracked is False


def test_only_an_admin_may_change_the_setting_and_values_must_be_booleans(appctx):
    cat = _cat("Garments")
    manager = _login("manager")
    assert manager.post(f"/admin/config/category/{cat.id}/stock-tracking", json={"batch_tracking": True}).status_code == 403
    admin = _login("admin")
    assert admin.post(f"/admin/config/category/{cat.id}/stock-tracking", json={"batch_tracking": "yes"}).status_code == 400
    assert admin.post(f"/admin/config/category/{cat.id}/stock-tracking", json={}).status_code == 400
    assert admin.post("/admin/config/category/999999/stock-tracking", json={"batch_tracking": True}).status_code == 404
    db.session.expire_all()
    assert db.session.get(BusinessCategory, cat.id).config_data == {}


def test_admin_page_shows_the_two_switches(appctx):
    cat = _cat("Medical Store", MEDICAL)
    html = _login().get(f"/admin/config/category/{cat.id}").get_data(as_text=True)
    assert 'id="trackBatch"' in html and 'id="trackExpiry"' in html
    assert "Warranty and Warranty Expiry Date" in html


# ── purchase forms: stock expiry per category, server-enforced ──────────────


def _batch_item(cat, name="Syrup"):
    item = Item(name=name, business_category_id=cat.id, unit="Pcs", item_type="STOCK", stock=0, opening_stock=0,
                purchase_price=10, sale_price=20, batch_tracked=True)
    db.session.add(item)
    db.session.commit()
    return item


def _purchase_form(client, supplier, item, expiry):
    return client.post("/purchase", data={
        "supplier_id": str(supplier.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": "4", "purchase_price[]": "10",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
        "batch_no[]": "B-1", "expiry_date[]": expiry}, follow_redirects=True)


def test_units_api_reports_whether_the_items_category_tracks_stock_expiry(appctx):
    on = _batch_item(_cat("Medical Store", MEDICAL), "Syrup")
    off = _batch_item(_cat("Batchy", config={"batch_tracking": True, "stock_expiry": False}), "Tin")
    admin = _login()
    assert admin.get(f"/api/items/{on.id}/units").get_json()["stock_expiry"] is True
    body = admin.get(f"/api/items/{off.id}/units").get_json()
    assert body["stock_expiry"] is False and body["batch_tracked"] is True


def test_purchase_refuses_an_expiry_where_the_category_has_stock_expiry_off(appctx):
    from app import Supplier, Purchase, PurchaseItem
    _books()
    cat = _cat("Batchy", config={"batch_tracking": True, "stock_expiry": False})
    item = _batch_item(cat)
    sup = Supplier(name="S", contact="0", address="X", opening_balance=0)
    db.session.add(sup)
    db.session.commit()
    admin = _login()

    resp = _purchase_form(admin, sup, item, "2030-01-01")
    assert b"stock expiry is not enabled for the category" in resp.data
    assert Purchase.query.count() == 0

    _purchase_form(admin, sup, item, "")                     # no expiry: fine
    pi = PurchaseItem.query.one()
    assert pi.pending_batch_no == "B-1" and pi.pending_expiry_date is None


def test_purchase_keeps_accepting_expiry_where_the_category_tracks_it(appctx):
    from app import Supplier, PurchaseItem
    _books()
    item = _batch_item(_cat("Medical Store", MEDICAL))
    sup = Supplier(name="S", contact="0", address="X", opening_balance=0)
    db.session.add(sup)
    db.session.commit()

    _purchase_form(_login(), sup, item, "2030-01-01")
    assert str(PurchaseItem.query.one().pending_expiry_date) == "2030-01-01"
