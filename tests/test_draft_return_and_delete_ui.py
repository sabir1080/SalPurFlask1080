"""Phase 5 of the Draft -> Posted workflow: two focused safety/UI additions
on top of the already-safe Draft edit/delete backend the audit confirmed.

1. A Draft Sale/Purchase must never be eligible for a Sale/Purchase Return
   -- it has no stock/ledger/GL effect yet for a return to correctly
   unwind. Mirrors the existing reversed-document exclusion in
   tests/test_return_document_search.py exactly (see
   test_sale_return_search_excludes_reversed_sale there), just flagging
   status="draft" instead of is_reversed=True.

2. A Delete action now appears in the Sale/Purchase list UI for Draft rows
   only (there was previously no Delete UI at all for either) -- these
   tests check the button's presence/absence in the rendered page rather
   than duplicating the already-covered route-level safety
   (test_draft_sale.py / test_draft_purchase.py's delete tests already
   prove the route itself is safe).
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Supplier, Customer, Category, Item, Purchase, PurchaseItem, Sale, SaleItem,
    calc_discount_tax,
    sync_supplier_opening, sync_supplier_purchase,
    sync_customer_opening, sync_customer_sale,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
)
from salpurflask.models.models import STATUS_DRAFT, STATUS_POSTED


def _manager(email="m@t.com"):
    db.session.add(User(name="M", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="manager"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _admin(email="a@t.com"):
    db.session.add(User(name="A", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    db.session.commit()


def make_supplier(name="Supplier A"):
    s = Supplier(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


def make_customer(name="Customer A"):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


def make_item(name="Widget"):
    cat = Category(name="Cat-" + name); db.session.add(cat); db.session.flush()
    it = Item(name=name, category_id=cat.id, stock=0, purchase_price=10, sale_price=20)
    db.session.add(it); db.session.flush()
    return it


def add_purchase(sup, item, qty=10, price=100, invoice_no=None, status=STATUS_POSTED):
    pur = Purchase(supplier_id=sup.id, item_id=item.id, quantity=qty, purchase_price=price,
                   invoice_no=invoice_no, status=status)
    db.session.add(pur); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    pi = PurchaseItem(purchase_id=pur.id, item_id=item.id, quantity=qty, purchase_price=price,
                      discount_type="percent", discount_value=0, discount_amount=d,
                      tax_percent=0, tax_amount=t, amount=net)
    db.session.add(pi)
    item.stock += qty
    db.session.flush(); db.session.refresh(pur); db.session.refresh(pi)
    if status == STATUS_POSTED:
        sync_supplier_purchase(pur)
    db.session.commit()
    return pur, pi


def add_sale(cust, item, qty=10, price=100, invoice_no=None, status=STATUS_POSTED):
    sale = Sale(customer_id=cust.id, item_id=item.id, quantity=qty, sale_price=price,
               cost_price=item.purchase_price or 0, invoice_no=invoice_no, status=status)
    db.session.add(sale); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    si = SaleItem(sale_id=sale.id, item_id=item.id, quantity=qty, sale_price=price,
                  cost_price=item.purchase_price or 0, discount_type="percent", discount_value=0,
                  discount_amount=d, tax_percent=0, tax_amount=t, amount=net)
    db.session.add(si)
    item.stock -= qty
    db.session.flush(); db.session.refresh(sale); db.session.refresh(si)
    if status == STATUS_POSTED:
        sync_customer_sale(sale)
    db.session.commit()
    return sale, si


# ══════════════════════════════════════════════════════════════════════════
# Return protection: Draft Sale
# ══════════════════════════════════════════════════════════════════════════


def test_draft_sale_item_absent_from_sale_return_inline_picker(appctx):
    """items_available (the inline all_sis query) gates whether the "Record
    Sale Return" form renders at all -- actual line selection goes through
    the JS search modal (covered by the search-API test below). So the
    externally observable effect of the Draft exclusion here is: with only
    a Draft sale in the system, there is nothing returnable, and the form
    must not render."""
    item = make_item()
    cust = make_customer()
    sale, si = add_sale(cust, item, status=STATUS_DRAFT)
    c = _manager()

    r = c.get("/sale_return")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'name="date"' not in body


def test_posted_sale_item_makes_sale_return_form_appear(appctx):
    """Regression: a genuinely Posted sale must still make the form appear."""
    item = make_item()
    cust = make_customer()
    sale, si = add_sale(cust, item, status=STATUS_POSTED)
    c = _manager()

    r = c.get("/sale_return")
    body = r.get_data(as_text=True)
    assert 'name="date"' in body


def test_draft_sale_item_absent_from_sale_return_search_api(appctx):
    item = make_item()
    cust = make_customer()
    sale, si = add_sale(cust, item, status=STATUS_DRAFT)
    c = _manager()

    r = c.get("/api/sale-returns/lookup")
    assert r.status_code == 200
    ids = [row["id"] for row in r.get_json()["results"]]
    assert si.id not in ids


def test_posted_sale_item_still_present_in_sale_return_search_api(appctx):
    """Regression: the Draft exclusion must not also exclude Posted sales."""
    item = make_item()
    cust = make_customer()
    sale, si = add_sale(cust, item, status=STATUS_POSTED)
    c = _manager()

    r = c.get("/api/sale-returns/lookup")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert si.id in ids


# ══════════════════════════════════════════════════════════════════════════
# Return protection: Draft Purchase
# ══════════════════════════════════════════════════════════════════════════


def test_draft_purchase_item_absent_from_purchase_return_inline_picker(appctx):
    """See test_draft_sale_item_absent_from_sale_return_inline_picker -- same
    reasoning, purchase side: with only a Draft purchase in the system,
    nothing is returnable, so the "Record Purchase Return" form must not
    render."""
    item = make_item()
    sup = make_supplier()
    pur, pi = add_purchase(sup, item, status=STATUS_DRAFT)
    c = _manager()

    r = c.get("/purchase_return")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'name="date"' not in body


def test_posted_purchase_item_makes_purchase_return_form_appear(appctx):
    """Regression: a genuinely Posted purchase must still make the form appear."""
    item = make_item()
    sup = make_supplier()
    pur, pi = add_purchase(sup, item, status=STATUS_POSTED)
    c = _manager()

    r = c.get("/purchase_return")
    body = r.get_data(as_text=True)
    assert 'name="date"' in body


def test_draft_purchase_item_absent_from_purchase_return_search_api(appctx):
    item = make_item()
    sup = make_supplier()
    pur, pi = add_purchase(sup, item, status=STATUS_DRAFT)
    c = _manager()

    r = c.get("/api/purchase-returns/lookup")
    assert r.status_code == 200
    ids = [row["id"] for row in r.get_json()["results"]]
    assert pi.id not in ids


def test_posted_purchase_item_still_present_in_purchase_return_search_api(appctx):
    """Regression: the Draft exclusion must not also exclude Posted purchases."""
    item = make_item()
    sup = make_supplier()
    pur, pi = add_purchase(sup, item, status=STATUS_POSTED)
    c = _manager()

    r = c.get("/api/purchase-returns/lookup")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert pi.id in ids


# ══════════════════════════════════════════════════════════════════════════
# Delete UI: shown only for Draft, admin-gated, existing actions intact
# ══════════════════════════════════════════════════════════════════════════


def test_delete_button_shown_for_draft_sale_to_admin(appctx):
    """delete_sale's URL is built by url_for('delete_sale', ...), which
    resolves to the unprefixed app.add_url_rule alias
    ("/sale/delete/<id>"), not the sales_bp blueprint's own
    "/sale/<id>/delete" route for the same view function -- see
    post_sale_route's identical dual-registration in app.py."""
    item = make_item()
    cust = make_customer()
    sale, si = add_sale(cust, item, status=STATUS_DRAFT)
    c = _admin()

    r = c.get("/sale")
    body = r.get_data(as_text=True)
    assert f"/sale/delete/{sale.id}" in body


def test_delete_button_absent_for_posted_sale(appctx):
    item = make_item()
    cust = make_customer()
    sale, si = add_sale(cust, item, status=STATUS_POSTED)
    c = _admin()

    r = c.get("/sale")
    body = r.get_data(as_text=True)
    assert f"/sale/delete/{sale.id}" not in body


def test_delete_button_absent_for_draft_sale_to_non_admin_manager(appctx):
    item = make_item()
    cust = make_customer()
    sale, si = add_sale(cust, item, status=STATUS_DRAFT)
    c = _manager()

    r = c.get("/sale")
    body = r.get_data(as_text=True)
    assert f"/sale/delete/{sale.id}" not in body


def test_edit_and_post_buttons_still_present_alongside_delete_for_draft_sale(appctx):
    item = make_item()
    cust = make_customer()
    sale, si = add_sale(cust, item, status=STATUS_DRAFT)
    c = _admin()

    r = c.get("/sale")
    body = r.get_data(as_text=True)
    assert f"/sale/edit/{sale.id}" in body
    assert f"/sale/{sale.id}/post" in body
    assert f"/sale/delete/{sale.id}" in body


def test_delete_button_shown_for_draft_purchase_to_admin(appctx):
    item = make_item()
    sup = make_supplier()
    pur, pi = add_purchase(sup, item, status=STATUS_DRAFT)
    c = _admin()

    r = c.get("/purchase")
    body = r.get_data(as_text=True)
    assert f"/purchase/delete/{pur.id}" in body


def test_delete_button_absent_for_posted_purchase(appctx):
    item = make_item()
    sup = make_supplier()
    pur, pi = add_purchase(sup, item, status=STATUS_POSTED)
    c = _admin()

    r = c.get("/purchase")
    body = r.get_data(as_text=True)
    assert f"/purchase/delete/{pur.id}" not in body


def test_delete_button_absent_for_draft_purchase_to_non_admin_manager(appctx):
    item = make_item()
    sup = make_supplier()
    pur, pi = add_purchase(sup, item, status=STATUS_DRAFT)
    c = _manager()

    r = c.get("/purchase")
    body = r.get_data(as_text=True)
    assert f"/purchase/delete/{pur.id}" not in body


def test_edit_and_post_buttons_still_present_alongside_delete_for_draft_purchase(appctx):
    item = make_item()
    sup = make_supplier()
    pur, pi = add_purchase(sup, item, status=STATUS_DRAFT)
    c = _admin()

    r = c.get("/purchase")
    body = r.get_data(as_text=True)
    assert f"/purchase/edit/{pur.id}" in body
    assert f"/purchase/{pur.id}/post" in body
    assert f"/purchase/delete/{pur.id}" in body
