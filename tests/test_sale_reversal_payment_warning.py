"""Option A: a Sale with existing CustomerPayment(s) may still be reversed,
but only after an explicit confirmation -- reverse_document_route rejects a
POST /document/sale/<id>/reverse missing confirm_payment_warning=1 when the
sale has active (non-reversed) payments, and proceeds normally when it
doesn't (or when the flag is present). The confirmation is enforced in the
route itself, not only by the template's modal, so a hand-built request
cannot bypass it.

Reusing test_dashboard_reversed_sale_exclusion.py's login/checkout helpers
verbatim, including the _RoleClient g._login_user-clearing wrapper -- see
that file's docstring for why it's needed whenever a test keeps a manager
client and an admin client both live in one appctx-scoped test.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Sale, CustomerPayment, JournalEntry, StockMovement,
    get_total_receivable,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening,
    total_customer_ledger_balance, get_customer_balance,
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


def _checkout(client, item, qty, price, account_id, amount_paid=None):
    import json
    if amount_paid is None:
        amount_paid = qty * price
    return client.post("/pos/checkout", data=json.dumps({
        "items": [{"item_id": item.id, "qty": qty, "price": price}],
        "account_id": account_id, "amount_paid": amount_paid,
    }), content_type="application/json")


def _reverse(client, sale_id, confirm=None):
    data = {}
    if confirm is not None:
        data["confirm_payment_warning"] = confirm
    resp = client.post(f"/document/sale/{sale_id}/reverse", data=data, follow_redirects=True)
    db.session.expire_all()
    return resp


# ── 1: no payment -> unchanged behavior ─────────────────────────────────────


def test_sale_without_payment_reverses_normally_with_no_confirmation_needed(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=2, price=100, account_id=account_id, amount_paid=0)
    sale_id = r.get_json()["sale_id"]

    rr = _reverse(admin, sale_id)   # no confirm_payment_warning at all
    assert rr.status_code == 200

    sale = db.session.get(Sale, sale_id)
    assert sale.is_reversed is True


# ── 2/3: sale with payment(s) requires confirmation ─────────────────────────


def test_sale_with_one_payment_requires_confirmation(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=2, price=100, account_id=account_id)   # fully paid -> 1 receipt
    sale_id = r.get_json()["sale_id"]
    assert CustomerPayment.query.filter_by(sale_id=sale_id).count() == 1

    rr = _reverse(admin, sale_id)   # no confirmation
    assert rr.status_code == 200
    assert "confirm" in rr.get_data(as_text=True).lower() or "payment" in rr.get_data(as_text=True).lower()

    sale = db.session.get(Sale, sale_id)
    assert sale.is_reversed is False   # rejected, unchanged


def test_sale_with_multiple_payments_requires_confirmation(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=4, price=100, account_id=account_id, amount_paid=150)  # partial
    sale_id = r.get_json()["sale_id"]
    # A second, separate receipt against the same sale via the receipt form.
    mgr.post("/customer_receipt", data={
        "customer_id": str(db.session.get(Sale, sale_id).customer_id),
        "sale_id": str(sale_id),
        "amount": "100",
        "payment_date": "2026-01-01",
        "payment_method": "Cash",
        "account_id": str(account_id),
    }, follow_redirects=True)
    assert CustomerPayment.query.filter_by(sale_id=sale_id).count() == 2

    rr = _reverse(admin, sale_id)
    assert rr.status_code == 200

    sale = db.session.get(Sale, sale_id)
    assert sale.is_reversed is False


# ── 4: cancel / no confirmation -> sale unchanged, nothing else changes ────


def test_no_confirmation_leaves_everything_unchanged(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=3, price=100, account_id=account_id)
    sale_id = r.get_json()["sale_id"]

    je_before = JournalEntry.query.count()
    sm_before = StockMovement.query.count()
    cp_before = CustomerPayment.query.count()

    _reverse(admin, sale_id)   # cancel-equivalent: no confirm flag sent

    sale = db.session.get(Sale, sale_id)
    assert sale.is_reversed is False
    assert JournalEntry.query.count() == je_before
    assert StockMovement.query.count() == sm_before
    assert CustomerPayment.query.count() == cp_before
    assert get_total_receivable() == 300.0   # still counted as active revenue


# ── 5/6: confirmed reversal succeeds, payment untouched ─────────────────────


def test_confirmed_reversal_succeeds(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=2, price=100, account_id=account_id)
    sale_id = r.get_json()["sale_id"]

    rr = _reverse(admin, sale_id, confirm="1")
    assert rr.status_code == 200

    sale = db.session.get(Sale, sale_id)
    assert sale.is_reversed is True
    assert Sale.query.count() == 1   # not deleted


def test_existing_payment_unchanged_after_confirmed_reversal(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=2, price=100, account_id=account_id)
    sale_id = r.get_json()["sale_id"]
    payment = CustomerPayment.query.filter_by(sale_id=sale_id).first()
    payment_id, payment_amount = payment.id, float(payment.amount)

    _reverse(admin, sale_id, confirm="1")

    reloaded = db.session.get(CustomerPayment, payment_id)
    assert reloaded is not None                    # not deleted
    assert float(reloaded.amount) == payment_amount  # not altered
    assert reloaded.is_reversed is False            # not auto-reversed
    assert reloaded.sale_id == sale_id              # still linked to the (now reversed) sale


# ── 7/8: reversed sale excluded from revenue/receivables ────────────────────


def test_reversed_sale_with_payment_excluded_from_revenue_and_receivable(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=2, price=100, account_id=account_id)
    sale_id = r.get_json()["sale_id"]
    assert get_total_receivable() == 200.0

    _reverse(admin, sale_id, confirm="1")

    assert get_total_receivable() == 0.0   # same query backs both the Dashboard's
                                            # Total Sale Revenue and Receivable "Total"


# ── 9: customer ledger reflects the resulting credit ────────────────────────


def test_customer_ledger_reflects_credit_after_confirmed_reversal(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=2, price=100, account_id=account_id)   # sale 200, paid 200
    sale_id = r.get_json()["sale_id"]
    customer_id = db.session.get(Sale, sale_id).customer_id

    _reverse(admin, sale_id, confirm="1")

    # Sale's debit (200) is gone; the receipt's credit (200) stays -> -200.
    assert get_customer_balance(customer_id) == -200.0
    assert total_customer_ledger_balance() == -200.0


# ── 10: existing (payment-less) reversal tests still pass ───────────────────


def test_existing_reversal_workflow_for_sale_without_payment_still_works(appctx):
    """Mirrors test_dashboard_reversed_sale_exclusion.py's own reversal test
    to confirm this change didn't alter that path."""
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=3, price=100, account_id=account_id, amount_paid=0)
    sale_id = r.get_json()["sale_id"]
    assert get_total_receivable() == 300.0

    rr = _reverse(admin, sale_id)
    assert rr.status_code == 200

    sale = db.session.get(Sale, sale_id)
    assert sale.is_reversed is True
    assert get_total_receivable() == 0.0


# ── 11: direct/manipulated request cannot bypass the confirmation ──────────


def test_backend_rejects_manipulated_request_missing_confirmation(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=2, price=100, account_id=account_id)
    sale_id = r.get_json()["sale_id"]

    # Simulates a crafted request that sends some other field, or the wrong
    # value, instead of the real confirm_payment_warning=1.
    for bad_data in ({}, {"confirm_payment_warning": "0"}, {"confirm_payment_warning": "true"},
                     {"confirmed": "1"}):
        rr = admin.post(f"/document/sale/{sale_id}/reverse", data=bad_data, follow_redirects=True)
        db.session.expire_all()
        sale = db.session.get(Sale, sale_id)
        assert sale.is_reversed is False, f"bypassed with {bad_data!r}"


# ── 12: no duplicate GL/stock/receipt/ledger entries ────────────────────────


def test_no_duplicate_entries_from_a_rejected_then_confirmed_reversal(appctx):
    account_id = _books()
    item = _item()
    mgr = _manager()
    admin = _admin()

    r = _checkout(mgr, item, qty=2, price=100, account_id=account_id)
    sale_id = r.get_json()["sale_id"]

    je_before = JournalEntry.query.count()
    sm_before = StockMovement.query.count()
    cp_before = CustomerPayment.query.count()

    _reverse(admin, sale_id)                 # rejected: no confirmation
    _reverse(admin, sale_id)                 # rejected again
    _reverse(admin, sale_id, confirm="1")    # succeeds once

    sale = db.session.get(Sale, sale_id)
    assert sale.is_reversed is True

    # Exactly one reversal's worth of new rows: one journal entry (the
    # reversal), one stock movement (goods added back), no new payments.
    assert JournalEntry.query.count() == je_before + 1
    assert StockMovement.query.count() == sm_before + 1
    assert CustomerPayment.query.count() == cp_before

    # A second confirmed attempt must not double-reverse.
    rr = _reverse(admin, sale_id, confirm="1")
    assert JournalEntry.query.count() == je_before + 1
    assert StockMovement.query.count() == sm_before + 1
