"""Option A, supplier side: a Purchase with existing SupplierPayment(s) may
still be reversed, but only after an explicit confirmation --
reverse_document_route rejects a POST /document/purchase/<id>/reverse
missing confirm_payment_warning=1 when the purchase has active
(non-reversed) payments, and proceeds normally when it doesn't (or when the
flag is present). Mirrors test_sale_reversal_payment_warning.py exactly;
see that file for the Sale-side version of the same feature and the
_RoleClient g._login_user-clearing rationale.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Supplier, Purchase, SupplierPayment,
    JournalEntry, StockMovement,
    get_total_payable, seed_chart_of_accounts, seed_fixed_asset_accounts,
    seed_fiscal_year, seed_financial_account_links, post_item_opening,
    sync_supplier_opening,
    total_supplier_ledger_balance, get_supplier_balance,
)
from salpurflask.models.business_config import BusinessCategory


class _RoleClient:
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


def _supplier(name="Supplier A"):
    s = Supplier(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


def _purchase_via_form(client, supplier, item, qty, price):
    """Uses the real /purchase form route -- the existing supported way to
    create a Purchase, matching how the Sale-side tests use /pos/checkout."""
    return client.post("/purchase", data={
        "supplier_id": str(supplier.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "purchase_price[]": str(price),
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)


def _pay_via_form(client, supplier, purchase_id, amount, account_id):
    """Uses the real /supplier_payment form route to raise a SupplierPayment
    against a specific purchase -- the existing supported workflow."""
    return client.post("/supplier_payment", data={
        "supplier_id": str(supplier.id), "purchase_id": str(purchase_id),
        "amount": str(amount), "payment_date": "2026-01-01",
        "payment_method": "Cash", "account_id": str(account_id),
    }, follow_redirects=True)


def _reverse(client, purchase_id, confirm=None):
    data = {}
    if confirm is not None:
        data["confirm_payment_warning"] = confirm
    resp = client.post(f"/document/purchase/{purchase_id}/reverse", data=data, follow_redirects=True)
    db.session.expire_all()
    return resp


def _latest_purchase_id():
    return Purchase.query.order_by(Purchase.id.desc()).first().id


# ── 1: no payment -> unchanged behavior ─────────────────────────────────────


def test_purchase_without_payment_reverses_normally_with_no_confirmation_needed(appctx):
    _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=2, price=100)
    purchase_id = _latest_purchase_id()

    rr = _reverse(admin, purchase_id)
    assert rr.status_code == 200

    pur = db.session.get(Purchase, purchase_id)
    assert pur.is_reversed is True


# ── 2/3: purchase with payment(s) requires confirmation ─────────────────────


def test_purchase_with_one_payment_requires_confirmation(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=2, price=100)
    purchase_id = _latest_purchase_id()
    _pay_via_form(admin, sup, purchase_id, 100, account_id)
    assert SupplierPayment.query.filter_by(purchase_id=purchase_id).count() == 1

    rr = _reverse(admin, purchase_id)   # no confirmation
    assert rr.status_code == 200
    body = rr.get_data(as_text=True).lower()
    assert "confirm" in body or "payment" in body

    pur = db.session.get(Purchase, purchase_id)
    assert pur.is_reversed is False   # rejected, unchanged


def test_purchase_with_multiple_payments_requires_confirmation(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=4, price=100)
    purchase_id = _latest_purchase_id()
    _pay_via_form(admin, sup, purchase_id, 150, account_id)
    _pay_via_form(admin, sup, purchase_id, 100, account_id)
    assert SupplierPayment.query.filter_by(purchase_id=purchase_id).count() == 2

    rr = _reverse(admin, purchase_id)
    assert rr.status_code == 200

    pur = db.session.get(Purchase, purchase_id)
    assert pur.is_reversed is False


# ── 4: cancel / no confirmation -> purchase unchanged, nothing else changes ─


def test_no_confirmation_leaves_everything_unchanged(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=3, price=100)
    purchase_id = _latest_purchase_id()
    _pay_via_form(admin, sup, purchase_id, 300, account_id)

    je_before = JournalEntry.query.count()
    sm_before = StockMovement.query.count()
    sp_before = SupplierPayment.query.count()

    _reverse(admin, purchase_id)   # cancel-equivalent: no confirm flag sent

    pur = db.session.get(Purchase, purchase_id)
    assert pur.is_reversed is False
    assert JournalEntry.query.count() == je_before
    assert StockMovement.query.count() == sm_before
    assert SupplierPayment.query.count() == sp_before
    assert get_total_payable() == 300.0   # still counted as active cost


# ── 5/6: confirmed reversal succeeds, payment untouched ─────────────────────


def test_confirmed_reversal_succeeds(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=2, price=100)
    purchase_id = _latest_purchase_id()
    _pay_via_form(admin, sup, purchase_id, 200, account_id)

    rr = _reverse(admin, purchase_id, confirm="1")
    assert rr.status_code == 200

    pur = db.session.get(Purchase, purchase_id)
    assert pur.is_reversed is True
    assert Purchase.query.count() == 1   # not deleted


def test_existing_payment_unchanged_after_confirmed_reversal(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=2, price=100)
    purchase_id = _latest_purchase_id()
    _pay_via_form(admin, sup, purchase_id, 200, account_id)
    payment = SupplierPayment.query.filter_by(purchase_id=purchase_id).first()
    payment_id, payment_amount = payment.id, float(payment.amount)

    _reverse(admin, purchase_id, confirm="1")

    reloaded = db.session.get(SupplierPayment, payment_id)
    assert reloaded is not None                     # not deleted
    assert float(reloaded.amount) == payment_amount   # not altered
    assert reloaded.is_reversed is False             # not auto-reversed
    assert reloaded.purchase_id == purchase_id       # still linked to the (now reversed) purchase


# ── 7/8: reversed purchase excluded from cost/payables ──────────────────────


def test_reversed_purchase_with_payment_excluded_from_cost_and_payable(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=2, price=100)
    purchase_id = _latest_purchase_id()
    _pay_via_form(admin, sup, purchase_id, 200, account_id)
    assert get_total_payable() == 200.0

    _reverse(admin, purchase_id, confirm="1")

    assert get_total_payable() == 0.0   # same query backs both the Dashboard's
                                         # Total Purchase Cost and Payable "Total"


# ── 9: supplier ledger reflects the resulting credit ────────────────────────


def test_supplier_ledger_reflects_credit_after_confirmed_reversal(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=2, price=100)   # purchase 200, paid 200
    purchase_id = _latest_purchase_id()
    _pay_via_form(admin, sup, purchase_id, 200, account_id)

    _reverse(admin, purchase_id, confirm="1")

    # Purchase's credit (200) is gone; the payment's debit (200) stays -> -200.
    assert get_supplier_balance(sup.id) == -200.0
    assert total_supplier_ledger_balance() == -200.0


# ── 10: backend rejects manipulated request missing confirmation ───────────


def test_backend_rejects_manipulated_request_missing_confirmation(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=2, price=100)
    purchase_id = _latest_purchase_id()
    _pay_via_form(admin, sup, purchase_id, 200, account_id)

    for bad_data in ({}, {"confirm_payment_warning": "0"}, {"confirm_payment_warning": "true"},
                     {"confirmed": "1"}):
        rr = admin.post(f"/document/purchase/{purchase_id}/reverse", data=bad_data, follow_redirects=True)
        db.session.expire_all()
        pur = db.session.get(Purchase, purchase_id)
        assert pur.is_reversed is False, f"bypassed with {bad_data!r}"


# ── 11: no duplicate entries ─────────────────────────────────────────────────


def test_no_duplicate_entries_from_a_rejected_then_confirmed_reversal(appctx):
    account_id = _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=2, price=100)
    purchase_id = _latest_purchase_id()
    _pay_via_form(admin, sup, purchase_id, 200, account_id)

    je_before = JournalEntry.query.count()
    sm_before = StockMovement.query.count()
    sp_before = SupplierPayment.query.count()

    _reverse(admin, purchase_id)                 # rejected: no confirmation
    _reverse(admin, purchase_id)                 # rejected again
    _reverse(admin, purchase_id, confirm="1")    # succeeds once

    pur = db.session.get(Purchase, purchase_id)
    assert pur.is_reversed is True

    assert JournalEntry.query.count() == je_before + 1
    assert StockMovement.query.count() == sm_before + 1
    assert SupplierPayment.query.count() == sp_before

    # A second confirmed attempt must not double-reverse.
    _reverse(admin, purchase_id, confirm="1")
    assert JournalEntry.query.count() == je_before + 1
    assert StockMovement.query.count() == sm_before + 1


# ── 12: existing purchase reversal tests still pass (payment-less case) ────


def test_existing_reversal_workflow_for_purchase_without_payment_still_works(appctx):
    _books()
    item = _item()
    sup = _supplier()
    admin = _admin()

    _purchase_via_form(admin, sup, item, qty=3, price=100)
    purchase_id = _latest_purchase_id()
    assert get_total_payable() == 300.0

    rr = _reverse(admin, purchase_id)
    assert rr.status_code == 200

    pur = db.session.get(Purchase, purchase_id)
    assert pur.is_reversed is True
    assert get_total_payable() == 0.0
