"""Dashboard revenue/receivable must exclude reversed sales and purchases.

Root cause: get_total_receivable() summed every SaleItem.amount in the table
with no join back to Sale and no Sale.is_reversed filter — same bug in
get_total_payable() for PurchaseItem/Purchase. dashboard.py's
total_sale_revenue / total_purchase_cost duplicated that exact same
unfiltered query a second time, and the monthly_sales chart query had the
identical gap. reverse_document() (the real reversal workflow, exercised
here through POST /document/sale/<id>/reverse) already correctly unwinds
the GL/subledger and recalculates the customer/supplier ledger — this was
never a GL bug, only the reporting layer's raw SaleItem/PurchaseItem sums
never looked at is_reversed at all.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Customer, Supplier, Category, Item, FinancialAccount, Sale, Purchase,
    PurchaseItem, SaleItem,
    get_total_receivable, get_total_payable,
    total_customer_ledger_balance,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening,
    sync_supplier_opening, sync_supplier_purchase, calc_discount_tax,
    post_document,
)
from salpurflask.models.business_config import BusinessCategory


class _RoleClient:
    """Wraps a Flask test client so every request first clears
    flask.g._login_user.

    Flask-Login caches current_user on flask.g once per lookup
    (login_manager._load_user), and the appctx fixture holds one single
    app_context() open for an entire test -- so that cache is shared by
    every test_client() in the test, not reset per "request" the way it
    would be for two real, separate processes. test_accounting_authorization
    .py's _login() clears it once at login time, which is enough when a
    test only ever uses ONE client afterward. This test needs a manager
    client to create a sale and an admin client to reverse it, live at the
    same time -- the manager's own later requests (e.g. the checkout call)
    re-populate g with the manager, so the cache has to be cleared again
    right before the admin client's next request too, not just once up
    front. Not related to the bug under test: this is purely a test-harness
    concern for running two differently-authenticated clients in one
    app-context-scoped test, which no existing test in this suite needed
    to do before."""

    def __init__(self, client):
        self._client = client

    def _clear_g(self):
        from flask import g
        try:
            del g._login_user
        except AttributeError:
            pass

    def get(self, *a, **kw):
        self._clear_g()
        return self._client.get(*a, **kw)

    def post(self, *a, **kw):
        self._clear_g()
        return self._client.post(*a, **kw)


def _login(user):
    from flask import g
    try:
        del g._login_user
    except AttributeError:
        pass
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
    return _RoleClient(c)


def _admin(email="admin@t.com"):
    u = User(name="A", email=email, password=pwd_context.hash("secret123"),
            verified=True, role="admin")
    db.session.add(u); db.session.commit()
    return _login(u)


def _manager(email="m@t.com"):
    u = User(name="M", email=email, password=pwd_context.hash("secret123"),
            verified=True, role="manager")
    db.session.add(u); db.session.commit()
    return _login(u)


def _books():
    """Chart, open fiscal year, funded Cash account — same minimum setup
    test_pos.py's _world() uses to let a POS sale actually post."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()
    return FinancialAccount.query.filter_by(name="Cash").first().id


def _item(name="Widget", stock=100):
    bcat = BusinessCategory(name="Cat-" + name, slug="cat-" + name.lower(), is_enabled=True)
    db.session.add(bcat); db.session.flush()
    it = Item(name=name, business_category_id=bcat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             opening_stock=stock, stock=stock, inventory_value=Decimal(str(stock * 10)))
    db.session.add(it); db.session.flush()
    post_item_opening(it)
    db.session.commit()
    return it


def _checkout(client, item, qty, price, account_id, amount_paid=None):
    """amount_paid defaults to full payment. Passing 0 leaves the sale fully
    outstanding and, notably, creates no CustomerPayment/receipt at all --
    which matters for tests that compare against the GL-backed customer
    ledger balance, since reversing a Sale does not also reverse any
    receipt raised against it (a separate document, its own reversal
    action) and a fully-paid sale would leave that receipt's ledger entry
    stranded with nothing to offset."""
    import json
    if amount_paid is None:
        amount_paid = qty * price
    return client.post("/pos/checkout", data=json.dumps({
        "items": [{"item_id": item.id, "qty": qty, "price": price}],
        "account_id": account_id, "amount_paid": amount_paid,
    }), content_type="application/json")


def _reverse_sale(client, sale_id):
    """Always sends confirm_payment_warning=1: these tests exercise the
    revenue/receivable exclusion, not the payment-warning gate added in
    reverse_document_route (see test_sale_reversal_payment_warning.py for
    that). A sale checked out with full payment now requires this flag to
    reverse at all; sending it unconditionally keeps this helper working
    for both the paid and unpaid sales these tests create, without
    depending on which one a given test happens to use."""
    resp = client.post(f"/document/sale/{sale_id}/reverse",
                       data={"confirm_payment_warning": "1"}, follow_redirects=True)
    db.session.expire_all()
    return resp


def _supplier():
    s = Supplier(name="Sup", contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


def _add_purchase(sup, item, qty, price):
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


# ── Revenue ──────────────────────────────────────────────────────────────────


def test_normal_sale_is_included_in_total_sale_revenue(appctx):
    account_id = _books()
    item = _item()
    c = _manager()
    r = _checkout(c, item, qty=3, price=100, account_id=account_id)
    assert r.status_code == 200

    assert get_total_receivable() == 300.0


def test_reversed_sale_is_excluded_from_total_sale_revenue(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=3, price=100, account_id=account_id)
    sale_id = r.get_json()["sale_id"]
    assert get_total_receivable() == 300.0

    rr = _reverse_sale(admin, sale_id)
    assert rr.status_code == 200

    sale = db.session.get(Sale, sale_id)
    assert sale.is_reversed is True          # row stays, flagged reversed
    assert Sale.query.count() == 1           # not deleted

    assert get_total_receivable() == 0.0     # no longer counted as active revenue


def test_normal_plus_reversed_sale_only_normal_contributes(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r1 = _checkout(mgr, item, qty=3, price=100, account_id=account_id)   # 300, will reverse
    r2 = _checkout(mgr, item, qty=2, price=100, account_id=account_id)   # 200, stays active
    sale1_id = r1.get_json()["sale_id"]

    _reverse_sale(admin, sale1_id)

    assert get_total_receivable() == 200.0   # only the untouched sale counts


# ── Receivable ───────────────────────────────────────────────────────────────


def test_normal_outstanding_sale_contributes_to_receivable(appctx):
    account_id = _books()
    item = _item()
    c = _manager()
    r = _checkout(c, item, qty=3, price=100, account_id=account_id)
    assert r.status_code == 200

    # Same query the Dashboard's Receivable "Total" line reads.
    assert get_total_receivable() == 300.0


def test_reversed_sale_does_not_remain_an_active_receivable(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    # amount_paid=0: an outstanding sale, no receipt raised against it, so
    # comparing against the GL ledger balance isn't muddied by a stranded
    # receipt (see _checkout's docstring).
    r = _checkout(mgr, item, qty=5, price=100, account_id=account_id, amount_paid=0)
    sale_id = r.get_json()["sale_id"]
    _reverse_sale(admin, sale_id)

    assert get_total_receivable() == 0.0
    # The GL-backed ledger balance (the Dashboard headline figure) was
    # already correct before this fix -- reverse_document() recalculates it.
    # Confirms this fix didn't need to touch that layer, and that both
    # numbers now agree instead of one silently including a reversed sale.
    assert total_customer_ledger_balance() == 0.0


def test_normal_plus_reversed_only_valid_amount_remains_receivable(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r1 = _checkout(mgr, item, qty=4, price=100, account_id=account_id)   # 400, will reverse
    r2 = _checkout(mgr, item, qty=1, price=100, account_id=account_id)   # 100, stays active
    sale1_id = r1.get_json()["sale_id"]

    _reverse_sale(admin, sale1_id)

    assert get_total_receivable() == 100.0


# ── Purchase side (payable) — same bug pattern, same fix ────────────────────


def test_normal_purchase_is_included_in_total_payable(appctx):
    _books()
    item = _item()
    sup = _supplier()
    _add_purchase(sup, item, qty=5, price=50)

    assert get_total_payable() == 250.0


def test_reversed_purchase_is_excluded_from_total_payable(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    pur = _add_purchase(sup, item, qty=5, price=50)
    post_document("purchase", pur)
    db.session.commit()
    assert get_total_payable() == 250.0

    r = admin.post(f"/document/purchase/{pur.id}/reverse", follow_redirects=True)
    assert r.status_code == 200

    db.session.expire_all()
    reloaded = db.session.get(Purchase, pur.id)
    assert reloaded.is_reversed is True
    assert Purchase.query.count() == 1

    assert get_total_payable() == 0.0


# ── Preserved behavior ───────────────────────────────────────────────────────


def test_reversed_sale_remains_visible_in_sales_history(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=2, price=100, account_id=account_id)
    sale_id = r.get_json()["sale_id"]
    _reverse_sale(admin, sale_id)

    body = mgr.get("/sale").get_data(as_text=True)
    assert str(sale_id) in body or "Reversed" in body
    assert Sale.query.filter_by(id=sale_id, is_reversed=True).count() == 1


def test_dashboard_page_still_renders_after_a_reversal(appctx):
    """The route itself, not just the helper functions -- confirms nothing
    in the dashboard view broke and the corrected total actually reaches
    the rendered page."""
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=3, price=100, account_id=account_id)
    sale_id = r.get_json()["sale_id"]
    _reverse_sale(admin, sale_id)

    resp = admin.get("/dashboard")
    assert resp.status_code == 200
