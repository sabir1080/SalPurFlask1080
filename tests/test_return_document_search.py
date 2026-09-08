"""Sale Return / Purchase Return document search.

Root cause this pins: /sale-return and /purchase_return embedded EVERY
returnable sale-item / purchase-item in the whole system into the page's
<select> (and, for the JS-rebuilt "Add Item" rows, into a giant inline JSON
blob) — unfiltered by customer/supplier, unpaginated, with no search. These
tests cover the replacement: GET /api/sale-returns/lookup and
GET /api/purchase-returns/lookup, server-side searched and paginated, used
by the new DocumentReturnLookup modal instead.

The actual return-eligibility rule (line not on a reversed document, still
has quantity left to return, including the same-item-twice-on-one-document
tie-breaker) is NOT reimplemented here — the search endpoints call this
module's own get_sale_item_returned_qty / get_purchase_item_returned_qty,
the exact same functions sale_return()/purchase_return() themselves call.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Supplier, Customer, Category, Item, Purchase, PurchaseItem, Sale, SaleItem,
    SaleReturn, PurchaseReturn,
    calc_discount_tax,
    sync_supplier_opening, sync_supplier_purchase, sync_supplier_purchase_return,
    sync_customer_opening, sync_customer_sale, sync_customer_sale_return,
    item_add_stock, item_remove_stock, post_item_opening,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
)


def _manager(email="m@t.com"):
    db.session.add(User(name="M", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="manager"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _books():
    """Chart of accounts + open fiscal year, so a return can actually post
    (post_document requires an open period — unrelated to this fix, but
    required for /sale_return and /purchase_return to succeed at all)."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    db.session.commit()


def make_supplier(name):
    s = Supplier(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


def make_customer(name):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


def make_item(name="Widget"):
    cat = Category(name="Cat-" + name); db.session.add(cat); db.session.flush()
    it = Item(name=name, category_id=cat.id, stock=0, purchase_price=10, sale_price=20)
    db.session.add(it); db.session.flush()
    return it


def make_item_with_value(name="Widget"):
    """Opening stock carries real inventory value so item_remove_stock (the
    purchase-return workflow test) has cost to draw down when a return
    reverses part of it — unrelated to this fix, but required for that
    route to succeed at all. Callers of this variant must call _books()
    first (post_item_opening needs an open fiscal year)."""
    cat = Category(name="Cat-" + name); db.session.add(cat); db.session.flush()
    it = Item(name=name, category_id=cat.id, unit="Pcs", purchase_price=Decimal("10"),
             sale_price=Decimal("20"), opening_stock=1000, stock=1000,
             inventory_value=Decimal("10000"))
    db.session.add(it); db.session.flush()
    post_item_opening(it)
    db.session.commit()
    return it


def add_purchase(sup, item, qty=10, price=100, invoice_no=None):
    pur = Purchase(supplier_id=sup.id, item_id=item.id, quantity=qty, purchase_price=price,
                   invoice_no=invoice_no)
    db.session.add(pur); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    pi = PurchaseItem(purchase_id=pur.id, item_id=item.id, quantity=qty, purchase_price=price,
                      discount_type="percent", discount_value=0, discount_amount=d,
                      tax_percent=0, tax_amount=t, amount=net)
    db.session.add(pi)
    item.stock += qty
    db.session.flush(); db.session.refresh(pur); db.session.refresh(pi)
    sync_supplier_purchase(pur); db.session.commit()
    return pur, pi


def add_sale(cust, item, qty=10, price=100, invoice_no=None):
    sale = Sale(customer_id=cust.id, item_id=item.id, quantity=qty, sale_price=price,
               cost_price=item.purchase_price or 0, invoice_no=invoice_no)
    db.session.add(sale); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    si = SaleItem(sale_id=sale.id, item_id=item.id, quantity=qty, sale_price=price,
                  cost_price=item.purchase_price or 0, discount_type="percent", discount_value=0,
                  discount_amount=d, tax_percent=0, tax_amount=t, amount=net)
    db.session.add(si)
    item.stock -= qty
    db.session.flush(); db.session.refresh(sale); db.session.refresh(si)
    sync_customer_sale(sale); db.session.commit()
    return sale, si


# ── Sale Return: search ─────────────────────────────────────────────────────


def test_sale_return_search_by_invoice_number(appctx):
    item = make_item()
    cust = make_customer("Customer A")
    sale, si = add_sale(cust, item, invoice_no="INV-0001")
    c = _manager()

    r = c.get("/api/sale-returns/lookup?q=INV-0001")
    assert r.status_code == 200
    ids = [row["id"] for row in r.get_json()["results"]]
    assert si.id in ids


def test_sale_return_search_by_customer_name(appctx):
    item = make_item()
    cust_a = make_customer("Alpha Traders")
    cust_b = make_customer("Beta Traders")
    _, si_a = add_sale(cust_a, item)
    _, si_b = add_sale(cust_b, item)
    c = _manager()

    r = c.get("/api/sale-returns/lookup?q=Alpha")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert si_a.id in ids
    assert si_b.id not in ids


def test_sale_return_search_by_date(appctx):
    from datetime import datetime
    item = make_item()
    cust = make_customer("Customer A")
    sale, si = add_sale(cust, item)
    sale.date = datetime(2026, 3, 15)
    db.session.commit()
    c = _manager()

    r = c.get("/api/sale-returns/lookup?date_from=2026-03-01&date_to=2026-03-31")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert si.id in ids

    r = c.get("/api/sale-returns/lookup?date_from=2026-04-01&date_to=2026-04-30")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert si.id not in ids


def test_sale_return_search_matching_sale_is_returned(appctx):
    item = make_item("Cheese")
    cust = make_customer("Customer A")
    _, si = add_sale(cust, item)
    c = _manager()

    r = c.get("/api/sale-returns/lookup?q=Cheese")
    data = r.get_json()
    row = next(row for row in data["results"] if row["id"] == si.id)
    assert row["item"] == "Cheese"
    assert row["remaining"] == 10
    assert row["customer"] == "Customer A"


def test_sale_return_search_non_matching_sale_is_not_returned(appctx):
    item = make_item()
    cust = make_customer("Customer A")
    add_sale(cust, item)
    c = _manager()

    r = c.get("/api/sale-returns/lookup?q=NoSuchInvoiceOrCustomer")
    assert r.get_json()["results"] == []


def test_sale_return_search_scoped_to_selected_customer(appctx):
    item = make_item()
    cust_a = make_customer("Customer A")
    cust_b = make_customer("Customer B")
    _, si_a = add_sale(cust_a, item)
    _, si_b = add_sale(cust_b, item)
    c = _manager()

    r = c.get(f"/api/sale-returns/lookup?customer_id={cust_a.id}")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert si_a.id in ids
    assert si_b.id not in ids


def test_sale_return_search_excludes_fully_returned_lines(appctx):
    item = make_item()
    cust = make_customer("Customer A")
    sale, si = add_sale(cust, item, qty=5)
    sr = SaleReturn(sale_id=sale.id, customer_id=cust.id, item_id=item.id, quantity=5,
                    return_price=100, sale_item_id=si.id)
    db.session.add(sr); db.session.flush()
    item_add_stock(item, 5, Decimal("50"), movement_type="sale_return",
                   source_type="sale_return", source_id=sr.id)
    sync_customer_sale_return(sr)
    db.session.commit()
    c = _manager()

    r = c.get("/api/sale-returns/lookup")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert si.id not in ids


def test_sale_return_search_excludes_reversed_sale(appctx):
    item = make_item()
    cust = make_customer("Customer A")
    sale, si = add_sale(cust, item)
    sale.is_reversed = True
    db.session.commit()
    c = _manager()

    r = c.get("/api/sale-returns/lookup")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert si.id not in ids


def test_sale_return_search_no_results_is_graceful(appctx):
    c = _manager()
    r = c.get("/api/sale-returns/lookup?q=nothing-here")
    assert r.status_code == 200
    assert r.get_json() == {"results": [], "total": 0, "page": 1, "per_page": 20}


# ── Sale Return: submission security ────────────────────────────────────────


def test_sale_return_workflow_still_works(appctx):
    _books()
    item = make_item_with_value()
    cust = make_customer("Customer A")
    sale, si = add_sale(cust, item, qty=5, price=50)
    c = _manager()

    r = c.post("/sale_return", data={
        "date": "2026-01-01",
        "sale_item_id[]": [str(si.id)],
        "quantity[]": ["2"],
        "return_price[]": ["50"],
        "reason[]": ["Damaged"],
    }, follow_redirects=True)
    assert r.status_code == 200
    assert SaleReturn.query.count() == 1
    ret = SaleReturn.query.first()
    assert ret.quantity == 2
    assert ret.customer_id == cust.id
    assert ret.sale_item_id == si.id


def test_sale_return_different_customers_sale_item_id_still_maps_correctly(appctx):
    """A crafted sale_item_id[] naming a real SaleItem always carries its own
    correct sale/customer via the DB relationship — sale_return() derives
    customer_id from si.sale_header.customer_id, so there is no separate
    'selected customer' field on this form for an attacker to mismatch
    against. This test locks that derivation in place."""
    _books()
    item = make_item_with_value()
    cust_a = make_customer("Customer A")
    cust_b = make_customer("Customer B")
    _, si_a = add_sale(cust_a, item, qty=5, price=50)
    _, si_b = add_sale(cust_b, item, qty=5, price=50)
    c = _manager()

    r = c.post("/sale_return", data={
        "date": "2026-01-01",
        "sale_item_id[]": [str(si_b.id)],
        "quantity[]": ["1"],
        "return_price[]": ["50"],
        "reason[]": [""],
    }, follow_redirects=True)
    assert r.status_code == 200
    ret = SaleReturn.query.one()
    assert ret.customer_id == cust_b.id     # never cust_a, regardless of who's "selected" in the UI
    assert ret.sale_id == si_b.sale_id


def test_sale_return_rejects_quantity_beyond_remaining(appctx):
    item = make_item()
    cust = make_customer("Customer A")
    sale, si = add_sale(cust, item, qty=3, price=50)
    c = _manager()

    r = c.post("/sale_return", data={
        "date": "2026-01-01",
        "sale_item_id[]": [str(si.id)],
        "quantity[]": ["999"],
        "return_price[]": ["50"],
        "reason[]": [""],
    }, follow_redirects=True)
    assert r.status_code == 200
    assert SaleReturn.query.count() == 0


# ── Purchase Return: search ─────────────────────────────────────────────────


def test_purchase_return_search_by_purchase_number(appctx):
    item = make_item()
    sup = make_supplier("Supplier A")
    pur, pi = add_purchase(sup, item, invoice_no="PINV-0001")
    c = _manager()

    r = c.get("/api/purchase-returns/lookup?q=PINV-0001")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert pi.id in ids


def test_purchase_return_search_by_supplier_name(appctx):
    item = make_item()
    sup_a = make_supplier("Alpha Supply Co")
    sup_b = make_supplier("Beta Supply Co")
    _, pi_a = add_purchase(sup_a, item)
    _, pi_b = add_purchase(sup_b, item)
    c = _manager()

    r = c.get("/api/purchase-returns/lookup?q=Alpha")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert pi_a.id in ids
    assert pi_b.id not in ids


def test_purchase_return_search_by_date(appctx):
    from datetime import datetime
    item = make_item()
    sup = make_supplier("Supplier A")
    pur, pi = add_purchase(sup, item)
    pur.date = datetime(2026, 5, 10)
    db.session.commit()
    c = _manager()

    r = c.get("/api/purchase-returns/lookup?date_from=2026-05-01&date_to=2026-05-31")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert pi.id in ids

    r = c.get("/api/purchase-returns/lookup?date_from=2026-06-01&date_to=2026-06-30")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert pi.id not in ids


def test_purchase_return_search_matching_purchase_is_returned(appctx):
    item = make_item("Rice Bag")
    sup = make_supplier("Supplier A")
    _, pi = add_purchase(sup, item)
    c = _manager()

    r = c.get("/api/purchase-returns/lookup?q=Rice")
    data = r.get_json()
    row = next(row for row in data["results"] if row["id"] == pi.id)
    assert row["item"] == "Rice Bag"
    assert row["remaining"] == 10
    assert row["supplier"] == "Supplier A"


def test_purchase_return_search_non_matching_purchase_is_not_returned(appctx):
    item = make_item()
    sup = make_supplier("Supplier A")
    add_purchase(sup, item)
    c = _manager()

    r = c.get("/api/purchase-returns/lookup?q=NoSuchInvoiceOrSupplier")
    assert r.get_json()["results"] == []


def test_purchase_return_search_scoped_to_selected_supplier(appctx):
    item = make_item()
    sup_a = make_supplier("Supplier A")
    sup_b = make_supplier("Supplier B")
    _, pi_a = add_purchase(sup_a, item)
    _, pi_b = add_purchase(sup_b, item)
    c = _manager()

    r = c.get(f"/api/purchase-returns/lookup?supplier_id={sup_a.id}")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert pi_a.id in ids
    assert pi_b.id not in ids


def test_purchase_return_search_excludes_fully_returned_lines(appctx):
    item = make_item()
    sup = make_supplier("Supplier A")
    pur, pi = add_purchase(sup, item, qty=5)
    pr = PurchaseReturn(purchase_id=pur.id, supplier_id=sup.id, item_id=item.id, quantity=5,
                        return_price=100, purchase_item_id=pi.id)
    db.session.add(pr); db.session.flush()
    item_remove_stock(item, 5, movement_type="purchase_return",
                      source_type="purchase_return", source_id=pr.id)
    sync_supplier_purchase_return(pr)
    db.session.commit()
    c = _manager()

    r = c.get("/api/purchase-returns/lookup")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert pi.id not in ids


def test_purchase_return_search_excludes_reversed_purchase(appctx):
    item = make_item()
    sup = make_supplier("Supplier A")
    pur, pi = add_purchase(sup, item)
    pur.is_reversed = True
    db.session.commit()
    c = _manager()

    r = c.get("/api/purchase-returns/lookup")
    ids = [row["id"] for row in r.get_json()["results"]]
    assert pi.id not in ids


def test_purchase_return_search_no_results_is_graceful(appctx):
    c = _manager()
    r = c.get("/api/purchase-returns/lookup?q=nothing-here")
    assert r.status_code == 200
    assert r.get_json() == {"results": [], "total": 0, "page": 1, "per_page": 20}


# ── Purchase Return: submission security ─────────────────────────────────────


def test_purchase_return_workflow_still_works(appctx):
    _books()
    item = make_item_with_value()
    sup = make_supplier("Supplier A")
    pur, pi = add_purchase(sup, item, qty=5, price=50)
    c = _manager()

    r = c.post("/purchase_return", data={
        "date": "2026-01-01",
        "purchase_item_id[]": [str(pi.id)],
        "quantity[]": ["2"],
        "return_price[]": ["50"],
        "reason[]": ["Damaged"],
    }, follow_redirects=True)
    assert r.status_code == 200
    assert PurchaseReturn.query.count() == 1
    ret = PurchaseReturn.query.first()
    assert ret.quantity == 2
    assert ret.supplier_id == sup.id
    assert ret.purchase_item_id == pi.id


def test_purchase_return_different_suppliers_purchase_item_id_still_maps_correctly(appctx):
    _books()
    item = make_item_with_value()
    sup_a = make_supplier("Supplier A")
    sup_b = make_supplier("Supplier B")
    _, pi_a = add_purchase(sup_a, item, qty=5, price=50)
    _, pi_b = add_purchase(sup_b, item, qty=5, price=50)
    c = _manager()

    r = c.post("/purchase_return", data={
        "date": "2026-01-01",
        "purchase_item_id[]": [str(pi_b.id)],
        "quantity[]": ["1"],
        "return_price[]": ["50"],
        "reason[]": [""],
    }, follow_redirects=True)
    assert r.status_code == 200
    ret = PurchaseReturn.query.one()
    assert ret.supplier_id == sup_b.id
    assert ret.purchase_id == pi_b.purchase_id


def test_purchase_return_rejects_quantity_beyond_remaining(appctx):
    item = make_item()
    sup = make_supplier("Supplier A")
    pur, pi = add_purchase(sup, item, qty=3, price=50)
    c = _manager()

    r = c.post("/purchase_return", data={
        "date": "2026-01-01",
        "purchase_item_id[]": [str(pi.id)],
        "quantity[]": ["999"],
        "return_price[]": ["50"],
        "reason[]": [""],
    }, follow_redirects=True)
    assert r.status_code == 200
    assert PurchaseReturn.query.count() == 0
