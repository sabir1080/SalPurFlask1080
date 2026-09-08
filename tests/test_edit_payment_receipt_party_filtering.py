"""Audit of the edit pages (edit_supplier_payment / edit_customer_receipt) for the
same party-isolation rule already enforced on the create pages.

Findings pinned by these tests:
  - The display side WAS already safe in practice: the edit templates correctly
    applied the "purchase-option"/"sale-option" CSS class and a working
    opt.hidden filter (unlike the create-page bug, where that class was never
    applied at all). But the route still shipped every party's bills into the
    page's HTML on every load -- a data-exposure gap even though nothing wrong
    was selectable. That's now fixed to reuse the same scoped API the create
    pages use (api_supplier_outstanding_purchases / api_customer_outstanding_sales),
    extended with exclude_payment_id/exclude_receipt_id so the bill already
    saved on the document being edited is never dropped from the list even if
    editing this document's own amount out of the balance would otherwise
    leave it fully paid/received.
  - The submission side was ALREADY correct: validate_supplier_payment and
    validate_customer_receipt (called with exclude_payment_id / exclude_receipt_id
    from both edit routes) already reject a purchase/sale that doesn't belong
    to the submitted party. No change was needed there.
"""
from app import (
    app as flask_app, db, User, pwd_context,
    Supplier, Customer, Category, Item, Purchase, PurchaseItem, Sale, SaleItem,
    SupplierPayment, CustomerPayment, FinancialAccount,
    calc_discount_tax, get_purchase_paid, get_sale_received,
    sync_supplier_opening, sync_supplier_purchase, sync_supplier_payment,
    sync_customer_opening, sync_customer_sale, sync_customer_receipt,
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


def add_supplier_payment(sup, purchase, amount, account_id):
    """Unposted on purpose: assert_not_posted blocks edits to a posted
    payment (by design — a posted document is history), and the edit route
    under test here must remain reachable to exercise it."""
    pay = SupplierPayment(supplier_id=sup.id, purchase_id=purchase.id if purchase else None,
                          amount=amount, payment_method="Cash", account_id=account_id)
    db.session.add(pay); db.session.flush()
    sync_supplier_payment(pay)
    db.session.commit()
    return pay


def add_customer_receipt(cust, sale, amount, account_id):
    rcpt = CustomerPayment(customer_id=cust.id, sale_id=sale.id if sale else None,
                           amount=amount, payment_method="Cash", account_id=account_id)
    db.session.add(rcpt); db.session.flush()
    sync_customer_receipt(rcpt)
    db.session.commit()
    return rcpt


# ── 1/2/3: edit_supplier_payment display scoping ───────────────────────────


def test_edit_supplier_payment_same_suppliers_purchase_is_available(appctx):
    account_id = _books()
    item = make_item()
    sup_a = make_supplier("Supplier A")
    pa1 = add_purchase(sup_a, item, price=100)
    pa2 = add_purchase(sup_a, item, price=150)
    pay = add_supplier_payment(sup_a, pa1, 30, account_id)
    c = _manager()

    r = c.get(f"/api/supplier/{sup_a.id}/outstanding-purchases?exclude_payment_id={pay.id}")
    ids = [row["id"] for row in r.get_json()["purchases"]]
    assert pa2.id in ids   # another of A's own unpaid purchases is available


def test_edit_supplier_payment_another_suppliers_purchase_is_not_available(appctx):
    account_id = _books()
    item = make_item()
    sup_a = make_supplier("Supplier A")
    sup_b = make_supplier("Supplier B")
    pa = add_purchase(sup_a, item, price=100)
    pb = add_purchase(sup_b, item, price=200)
    pay = add_supplier_payment(sup_a, pa, 30, account_id)
    c = _manager()

    r = c.get(f"/api/supplier/{sup_a.id}/outstanding-purchases?exclude_payment_id={pay.id}")
    ids = [row["id"] for row in r.get_json()["purchases"]]
    assert pb.id not in ids

    # And the edit page itself no longer server-renders any purchase options
    # at all -- the only purchase id it can embed is the currentPurchaseId JS
    # constant for the payment's own (A's) purchase, never B's. (Can't assert
    # '<option value="{pb.id}">' is absent from the whole page: other
    # unrelated dropdowns, e.g. the account selector, legitimately have
    # options whose ids collide with small purchase ids in a fresh test DB.)
    body = c.get(f"/supplier_payment/edit/{pay.id}").get_data(as_text=True)
    assert f"const currentPurchaseId = {pa.id};" in body
    assert f"const currentPurchaseId = {pb.id};" not in body


def test_edit_supplier_payment_existing_adjustment_remains_available_even_if_now_fully_paid(appctx):
    """The purchase already saved on this payment must stay selectable even
    when, after backing this payment's own amount out, it's fully paid --
    otherwise re-opening the edit form would silently drop the adjustment."""
    account_id = _books()
    item = make_item()
    sup_a = make_supplier("Supplier A")
    pa = add_purchase(sup_a, item, price=100)   # total due 100
    pay = add_supplier_payment(sup_a, pa, 100, account_id)   # fully pays it off
    c = _manager()

    r = c.get(f"/api/supplier/{sup_a.id}/outstanding-purchases?exclude_payment_id={pay.id}")
    rows = {row["id"]: row["due"] for row in r.get_json()["purchases"]}
    assert pa.id in rows
    assert rows[pa.id] == 100.0   # shown as if this payment didn't exist yet

    body = c.get(f"/supplier_payment/edit/{pay.id}").get_data(as_text=True)
    assert f"const currentPurchaseId = {pa.id};" in body


# ── 4: edit_supplier_payment submission-time rejection ─────────────────────


def test_edit_supplier_payment_rejects_manipulated_cross_supplier_update(appctx):
    account_id = _books()
    item = make_item()
    sup_a = make_supplier("Supplier A")
    sup_b = make_supplier("Supplier B")
    pb = add_purchase(sup_b, item, price=200)
    pay = add_supplier_payment(sup_a, None, 30, account_id)   # A's own general payment
    c = _manager()

    r = c.post(f"/supplier_payment/edit/{pay.id}", data={
        "supplier_id": str(sup_a.id),
        "purchase_id": str(pb.id),          # manipulated: B's bill
        "amount": "30",
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(pay)
    assert pay.purchase_id is None          # update was rejected, unchanged
    assert get_purchase_paid(pb.id) == 0.0  # B's bill untouched


# ── 5/6/7: edit_customer_receipt display scoping ────────────────────────────


def test_edit_customer_receipt_same_customers_sale_is_available(appctx):
    account_id = _books()
    item = make_item()
    cust_a = make_customer("Customer A")
    sa1 = add_sale(cust_a, item, price=100)
    sa2 = add_sale(cust_a, item, price=150)
    rcpt = add_customer_receipt(cust_a, sa1, 30, account_id)
    c = _manager()

    r = c.get(f"/api/customer/{cust_a.id}/outstanding-sales?exclude_receipt_id={rcpt.id}")
    ids = [row["id"] for row in r.get_json()["sales"]]
    assert sa2.id in ids


def test_edit_customer_receipt_another_customers_sale_is_not_available(appctx):
    account_id = _books()
    item = make_item()
    cust_a = make_customer("Customer A")
    cust_b = make_customer("Customer B")
    sa = add_sale(cust_a, item, price=100)
    sb = add_sale(cust_b, item, price=200)
    rcpt = add_customer_receipt(cust_a, sa, 30, account_id)
    c = _manager()

    r = c.get(f"/api/customer/{cust_a.id}/outstanding-sales?exclude_receipt_id={rcpt.id}")
    ids = [row["id"] for row in r.get_json()["sales"]]
    assert sb.id not in ids

    # The edit page no longer server-renders any sale options at all -- the
    # only sale id it can embed is the currentSaleId JS constant for the
    # receipt's own (A's) sale, never B's. (Not asserting the whole page is
    # free of the literal '<option value="{sb.id}">': other unrelated
    # dropdowns, e.g. the account selector, legitimately have options whose
    # ids collide with small sale ids in a fresh test DB.)
    body = c.get(f"/customer_receipt/edit/{rcpt.id}").get_data(as_text=True)
    assert f"const currentSaleId = {sa.id};" in body
    assert f"const currentSaleId = {sb.id};" not in body


def test_edit_customer_receipt_existing_adjustment_remains_available_even_if_now_fully_received(appctx):
    account_id = _books()
    item = make_item()
    cust_a = make_customer("Customer A")
    sa = add_sale(cust_a, item, price=100)
    rcpt = add_customer_receipt(cust_a, sa, 100, account_id)
    c = _manager()

    r = c.get(f"/api/customer/{cust_a.id}/outstanding-sales?exclude_receipt_id={rcpt.id}")
    rows = {row["id"]: row["due"] for row in r.get_json()["sales"]}
    assert sa.id in rows
    assert rows[sa.id] == 100.0

    body = c.get(f"/customer_receipt/edit/{rcpt.id}").get_data(as_text=True)
    assert f"const currentSaleId = {sa.id};" in body


# ── 8: edit_customer_receipt submission-time rejection ──────────────────────


def test_edit_customer_receipt_rejects_manipulated_cross_customer_update(appctx):
    account_id = _books()
    item = make_item()
    cust_a = make_customer("Customer A")
    cust_b = make_customer("Customer B")
    sb = add_sale(cust_b, item, price=200)
    rcpt = add_customer_receipt(cust_a, None, 30, account_id)
    c = _manager()

    r = c.post(f"/customer_receipt/edit/{rcpt.id}", data={
        "customer_id": str(cust_a.id),
        "sale_id": str(sb.id),              # manipulated: B's sale
        "amount": "30",
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(rcpt)
    assert rcpt.sale_id is None
    assert get_sale_received(sb.id) == 0.0


# ── 9: General / On-Account edit behavior remains intact ───────────────────


def test_edit_supplier_payment_general_on_account_still_works(appctx):
    account_id = _books()
    sup = make_supplier("Supplier A")
    pay = add_supplier_payment(sup, None, 50, account_id)
    c = _manager()

    r = c.post(f"/supplier_payment/edit/{pay.id}", data={
        "supplier_id": str(sup.id),
        "purchase_id": "",
        "amount": "75",
        "payment_date": "2026-01-02",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(pay)
    assert pay.purchase_id is None
    assert float(pay.amount) == 75.0


def test_edit_customer_receipt_general_on_account_still_works(appctx):
    account_id = _books()
    cust = make_customer("Customer A")
    rcpt = add_customer_receipt(cust, None, 50, account_id)
    c = _manager()

    r = c.post(f"/customer_receipt/edit/{rcpt.id}", data={
        "customer_id": str(cust.id),
        "sale_id": "",
        "amount": "75",
        "payment_date": "2026-01-02",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(rcpt)
    assert rcpt.sale_id is None
    assert float(rcpt.amount) == 75.0


# ── No party selected on the edit forms exposes nothing unrelated ──────────


def test_no_supplier_id_outstanding_purchases_api_requires_a_real_supplier(appctx):
    c = _manager()
    r = c.get("/api/supplier/999999/outstanding-purchases")
    assert r.status_code == 404


def test_no_customer_id_outstanding_sales_api_requires_a_real_customer(appctx):
    c = _manager()
    r = c.get("/api/customer/999999/outstanding-sales")
    assert r.status_code == 404
