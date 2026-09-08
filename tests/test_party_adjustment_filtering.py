"""Adjust Purchase / Adjust Sale must only ever offer bills belonging to the
party actually selected. The bug: /supplier_payment and /customer_receipt
shipped every party's open bills to the browser and relied on a client-side
filter that (for supplier_payment/customer_receipt) was silently broken —
the JS looked for a CSS class the server never added, so nothing was ever
hidden. These tests pin the fix: the outstanding-bills API is scoped
server-side by the real FK, the page never renders another party's bills
into the DOM at all, and payment/receipt submission rejects a bill that
does not belong to the party on the form even if the request is hand-built.
"""
from app import (
    app as flask_app, db, User, pwd_context,
    Supplier, Customer, Category, Item, Purchase, PurchaseItem, Sale, SaleItem,
    SupplierPayment, CustomerPayment, FinancialAccount,
    calc_discount_tax, purchase_total, sale_total, get_purchase_paid, get_sale_received,
    sync_supplier_opening, sync_supplier_purchase,
    sync_customer_opening, sync_customer_sale,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links,
)


def _manager(email="m@t.com"):
    db.session.add(User(name="M", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="manager"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _books():
    """Chart of accounts + a funded Cash account, so a payment/receipt can
    actually post (posting requires an account_id — unrelated to this fix,
    but required for these routes to succeed at all)."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()
    return FinancialAccount.query.filter_by(name="Cash").first().id


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


def make_item():
    cat = Category(name="Cat"); db.session.add(cat); db.session.flush()
    it = Item(name="Widget", category_id=cat.id, stock=1000, purchase_price=10, sale_price=20)
    db.session.add(it); db.session.flush()
    return it


def add_purchase(sup, item, qty=1, price=100):
    pur = Purchase(supplier_id=sup.id, item_id=item.id, quantity=qty, purchase_price=price)
    db.session.add(pur); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    pi = PurchaseItem(purchase_id=pur.id, item_id=item.id, quantity=qty, purchase_price=price,
                      discount_type="percent", discount_value=0, discount_amount=d,
                      tax_percent=0, tax_amount=t, amount=net)
    db.session.add(pi)
    db.session.flush(); db.session.refresh(pur)
    sync_supplier_purchase(pur); db.session.commit()
    return pur


def add_sale(cust, item, qty=1, price=100):
    sale = Sale(customer_id=cust.id, item_id=item.id, quantity=qty, sale_price=price, cost_price=item.purchase_price or 0)
    db.session.add(sale); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    si = SaleItem(sale_id=sale.id, item_id=item.id, quantity=qty, sale_price=price,
                  cost_price=item.purchase_price or 0, discount_type="percent", discount_value=0,
                  discount_amount=d, tax_percent=0, tax_amount=t, amount=net)
    db.session.add(si)
    db.session.flush(); db.session.refresh(sale)
    sync_customer_sale(sale); db.session.commit()
    return sale


# ── Supplier: /supplier_payment ("Against Purchase" dropdown data) ─────────


def test_outstanding_purchases_api_returns_only_that_suppliers_bills(appctx):
    item = make_item()
    sup_a = make_supplier("Supplier A")
    sup_b = make_supplier("Supplier B")
    pa = add_purchase(sup_a, item, price=100)
    pb = add_purchase(sup_b, item, price=200)
    c = _manager()

    r = c.get(f"/api/supplier/{sup_a.id}/outstanding-purchases")
    assert r.status_code == 200
    ids = [row["id"] for row in r.get_json()["purchases"]]
    assert pa.id in ids
    assert pb.id not in ids

    r = c.get(f"/api/supplier/{sup_b.id}/outstanding-purchases")
    ids = [row["id"] for row in r.get_json()["purchases"]]
    assert pb.id in ids
    assert pa.id not in ids


def test_supplier_payment_page_never_embeds_another_suppliers_purchase_id(appctx):
    item = make_item()
    sup_a = make_supplier("Supplier A")
    sup_b = make_supplier("Supplier B")
    pa = add_purchase(sup_a, item, price=100)
    pb = add_purchase(sup_b, item, price=200)
    c = _manager()

    body = c.get("/supplier_payment").get_data(as_text=True)
    # Neither purchase is server-rendered into the page at all any more —
    # the dropdown is populated client-side, scoped by whichever supplier
    # is picked. This is the actual fix: no leak is possible via view-source
    # regardless of what CSS/JS does afterwards.
    assert f'value="{pa.id}"' not in body
    assert f'value="{pb.id}"' not in body


def test_supplier_with_no_outstanding_purchases_gets_empty_list(appctx):
    sup = make_supplier("Supplier A")
    c = _manager()
    r = c.get(f"/api/supplier/{sup.id}/outstanding-purchases")
    assert r.get_json()["purchases"] == []


def test_supplier_payment_rejects_a_purchase_belonging_to_another_supplier(appctx):
    account_id = _books()
    item = make_item()
    sup_a = make_supplier("Supplier A")
    sup_b = make_supplier("Supplier B")
    pb = add_purchase(sup_b, item, price=200)   # belongs to B
    c = _manager()

    # Manipulated request: payment says Supplier A, but adjusts B's bill.
    r = c.post("/supplier_payment", data={
        "supplier_id": str(sup_a.id),
        "purchase_id": str(pb.id),
        "amount": "50",
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    assert SupplierPayment.query.count() == 0     # nothing was created
    assert get_purchase_paid(pb.id) == 0.0         # B's bill untouched


def test_supplier_payment_accepts_a_purchase_belonging_to_the_selected_supplier(appctx):
    account_id = _books()
    item = make_item()
    sup_a = make_supplier("Supplier A")
    pa = add_purchase(sup_a, item, price=100)
    c = _manager()

    r = c.post("/supplier_payment", data={
        "supplier_id": str(sup_a.id),
        "purchase_id": str(pa.id),
        "amount": "40",
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    assert SupplierPayment.query.count() == 1
    assert get_purchase_paid(pa.id) == 40.0


# ── Supplier: /supplier_bulk_payment ────────────────────────────────────────


def test_bulk_supplier_payment_page_only_lists_selected_suppliers_bills(appctx):
    item = make_item()
    sup_a = make_supplier("Supplier A")
    sup_b = make_supplier("Supplier B")
    pa = add_purchase(sup_a, item, price=100)
    pb = add_purchase(sup_b, item, price=200)
    c = _manager()

    body = c.get(f"/supplier_bulk_payment?supplier_id={sup_a.id}").get_data(as_text=True)
    assert f'name="purchase_id[]" value="{pa.id}"' in body
    assert f'name="purchase_id[]" value="{pb.id}"' not in body

    # switching supplier (a fresh GET, as the "Load" button does) drops A's
    # bill and shows only B's — no bleed-through from the previous selection
    body = c.get(f"/supplier_bulk_payment?supplier_id={sup_b.id}").get_data(as_text=True)
    assert f'name="purchase_id[]" value="{pb.id}"' in body
    assert f'name="purchase_id[]" value="{pa.id}"' not in body


def test_bulk_supplier_payment_rejects_manipulated_cross_supplier_row(appctx):
    item = make_item()
    sup_a = make_supplier("Supplier A")
    sup_b = make_supplier("Supplier B")
    pb = add_purchase(sup_b, item, price=200)
    c = _manager()

    r = c.post("/supplier_bulk_payment", data={
        "supplier_id": str(sup_a.id),
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "purchase_id[]": [str(pb.id)],
        "amount[]": ["50"],
    }, follow_redirects=True)
    assert r.status_code == 200
    assert SupplierPayment.query.count() == 0
    assert get_purchase_paid(pb.id) == 0.0


# ── Customer: /customer_receipt ("Against Sale" dropdown data) ─────────────


def test_outstanding_sales_api_returns_only_that_customers_invoices(appctx):
    item = make_item()
    cust_a = make_customer("Customer A")
    cust_b = make_customer("Customer B")
    sa = add_sale(cust_a, item, price=100)
    sb = add_sale(cust_b, item, price=200)
    c = _manager()

    r = c.get(f"/api/customer/{cust_a.id}/outstanding-sales")
    ids = [row["id"] for row in r.get_json()["sales"]]
    assert sa.id in ids
    assert sb.id not in ids

    r = c.get(f"/api/customer/{cust_b.id}/outstanding-sales")
    ids = [row["id"] for row in r.get_json()["sales"]]
    assert sb.id in ids
    assert sa.id not in ids


def test_customer_receipt_page_never_embeds_another_customers_sale_id(appctx):
    item = make_item()
    cust_a = make_customer("Customer A")
    cust_b = make_customer("Customer B")
    sa = add_sale(cust_a, item, price=100)
    sb = add_sale(cust_b, item, price=200)
    c = _manager()

    body = c.get("/customer_receipt").get_data(as_text=True)
    assert f'value="{sa.id}"' not in body
    assert f'value="{sb.id}"' not in body


def test_customer_with_no_outstanding_sales_gets_empty_list(appctx):
    cust = make_customer("Customer A")
    c = _manager()
    r = c.get(f"/api/customer/{cust.id}/outstanding-sales")
    assert r.get_json()["sales"] == []


def test_customer_receipt_rejects_a_sale_belonging_to_another_customer(appctx):
    item = make_item()
    cust_a = make_customer("Customer A")
    cust_b = make_customer("Customer B")
    sb = add_sale(cust_b, item, price=200)   # belongs to B
    c = _manager()

    r = c.post("/customer_receipt", data={
        "customer_id": str(cust_a.id),
        "sale_id": str(sb.id),
        "amount": "50",
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert CustomerPayment.query.count() == 0
    assert get_sale_received(sb.id) == 0.0


def test_customer_receipt_accepts_a_sale_belonging_to_the_selected_customer(appctx):
    account_id = _books()
    item = make_item()
    cust_a = make_customer("Customer A")
    sa = add_sale(cust_a, item, price=100)
    c = _manager()

    r = c.post("/customer_receipt", data={
        "customer_id": str(cust_a.id),
        "sale_id": str(sa.id),
        "amount": "40",
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    assert CustomerPayment.query.count() == 1
    assert get_sale_received(sa.id) == 40.0


# ── Customer: /customer_bulk_receipt ────────────────────────────────────────


def test_bulk_customer_receipt_page_only_lists_selected_customers_invoices(appctx):
    item = make_item()
    cust_a = make_customer("Customer A")
    cust_b = make_customer("Customer B")
    sa = add_sale(cust_a, item, price=100)
    sb = add_sale(cust_b, item, price=200)
    c = _manager()

    body = c.get(f"/customer_bulk_receipt?customer_id={cust_a.id}").get_data(as_text=True)
    assert f'name="sale_id[]" value="{sa.id}"' in body
    assert f'name="sale_id[]" value="{sb.id}"' not in body

    body = c.get(f"/customer_bulk_receipt?customer_id={cust_b.id}").get_data(as_text=True)
    assert f'name="sale_id[]" value="{sb.id}"' in body
    assert f'name="sale_id[]" value="{sa.id}"' not in body


def test_bulk_customer_receipt_rejects_manipulated_cross_customer_row(appctx):
    item = make_item()
    cust_a = make_customer("Customer A")
    cust_b = make_customer("Customer B")
    sb = add_sale(cust_b, item, price=200)
    c = _manager()

    r = c.post("/customer_bulk_receipt", data={
        "customer_id": str(cust_a.id),
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "sale_id[]": [str(sb.id)],
        "amount[]": ["50"],
    }, follow_redirects=True)
    assert r.status_code == 200
    assert CustomerPayment.query.count() == 0
    assert get_sale_received(sb.id) == 0.0


# ── No party selected / existing valid behavior ─────────────────────────────


def test_no_supplier_selected_outstanding_list_is_empty(appctx):
    item = make_item()
    sup = make_supplier("Supplier A")
    add_purchase(sup, item, price=100)
    c = _manager()
    body = c.get("/supplier_bulk_payment").get_data(as_text=True)
    assert "outstanding" not in body.lower() or "Choose Supplier" in body


def test_no_customer_selected_outstanding_list_is_empty(appctx):
    item = make_item()
    cust = make_customer("Customer A")
    add_sale(cust, item, price=100)
    c = _manager()
    body = c.get("/customer_bulk_receipt").get_data(as_text=True)
    assert "Choose Customer" in body or "outstanding" not in body.lower()


def test_general_on_account_supplier_payment_still_works(appctx):
    """Existing valid behavior: paying a supplier with no purchase_id at all
    (General / On Account) must remain unaffected by the filtering fix."""
    account_id = _books()
    sup = make_supplier("Supplier A")
    c = _manager()
    r = c.post("/supplier_payment", data={
        "supplier_id": str(sup.id),
        "purchase_id": "",
        "amount": "100",
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    assert SupplierPayment.query.count() == 1
    assert SupplierPayment.query.first().purchase_id is None


def test_general_on_account_customer_receipt_still_works(appctx):
    account_id = _books()
    cust = make_customer("Customer A")
    c = _manager()
    r = c.post("/customer_receipt", data={
        "customer_id": str(cust.id),
        "sale_id": "",
        "amount": "100",
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    assert CustomerPayment.query.count() == 1
    assert CustomerPayment.query.first().sale_id is None
