"""Phase 2 of the Draft -> Posted workflow: a Sale created through the normal
/sale route now starts as a Draft. A Draft must have zero accounting/stock/
ledger impact -- no invoice number, no JournalEntry, no customer ledger
entry, no receivable/revenue effect, no stock movement -- while still
creating correct SaleItems with the existing discount/tax/quantity math.

POS checkout and Quotation -> Sale conversion are audited but deliberately
left unchanged in this phase (see the Phase 2 report); their own existing
test files (test_pos.py, and quotation coverage) already lock in that they
keep posting immediately. This file only adds a POS/quotation smoke test
each, confirming that decision holds.

Purchase is untouched in this phase; test_purchase_reversal_payment_warning.py
and the rest of the Purchase suite already cover it and are not touched here.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Customer, Sale, SaleItem, CustomerPayment,
    JournalEntry, CustomerLedgerEntry, StockMovement,
    get_total_receivable, get_customer_balance, total_customer_ledger_balance,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening, sync_customer_opening,
)
from salpurflask.models.models import STATUS_DRAFT, STATUS_POSTED
from salpurflask.models.business_config import BusinessCategory


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


def _customer(name="Customer A"):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


class _RoleClient:
    """Flask-Login caches the resolved user on flask.g per app context, not
    per request -- running two different test_client()s (e.g. a manager and
    an admin) inside one appctx-scoped test resolves both to whichever
    logged in first unless that cache is cleared before every request, not
    just at login. See test_sale_reversal_payment_warning.py for the same
    pattern and its full rationale."""
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


def _manager(email="m@t.com"):
    u = User(name="M", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="manager")
    db.session.add(u); db.session.commit()
    return _login(u)


def _admin(email="a@t.com"):
    u = User(name="A", email=email, password=pwd_context.hash("secret123"),
             verified=True, role="admin")
    db.session.add(u); db.session.commit()
    return _login(u)


def _sale_via_form(client, customer, item, qty, price, disc_type="percent",
                    disc_value="0", tax="0"):
    return client.post("/sale", data={
        "customer_id": str(customer.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "sale_price[]": str(price),
        "discount_type[]": disc_type, "discount_value[]": disc_value, "tax_percent[]": tax,
    }, follow_redirects=True)


def _latest_sale():
    return Sale.query.order_by(Sale.id.desc()).first()


# ── Draft creation ───────────────────────────────────────────────────────────


def test_normal_sale_creation_produces_a_draft(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    _sale_via_form(client, cust, item, 3, 20)

    sal = _latest_sale()
    assert sal is not None
    assert sal.status == STATUS_DRAFT


def test_draft_sale_has_no_invoice_number(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    _sale_via_form(client, cust, item, 3, 20)

    sal = _latest_sale()
    assert sal.invoice_no is None


# ── Accounting isolation ─────────────────────────────────────────────────────


def test_draft_sale_creates_no_journal_entry(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    je_before = JournalEntry.query.count()
    _sale_via_form(client, cust, item, 3, 20)

    assert JournalEntry.query.count() == je_before


def test_draft_sale_creates_no_customer_ledger_entry(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    entries_before = CustomerLedgerEntry.query.filter_by(customer_id=cust.id).count()
    _sale_via_form(client, cust, item, 3, 20)

    assert CustomerLedgerEntry.query.filter_by(customer_id=cust.id).count() == entries_before


def test_draft_sale_does_not_increase_receivable(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    balance_before = get_customer_balance(cust.id)
    ledger_before = total_customer_ledger_balance()
    _sale_via_form(client, cust, item, 3, 20)

    assert get_customer_balance(cust.id) == balance_before
    assert total_customer_ledger_balance() == ledger_before


def test_draft_sale_does_not_increase_sales_revenue(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    revenue_before = get_total_receivable()
    _sale_via_form(client, cust, item, 3, 20)

    assert get_total_receivable() == revenue_before


# ── Inventory isolation ──────────────────────────────────────────────────────


def test_draft_sale_does_not_reduce_item_stock(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()

    _sale_via_form(client, cust, item, 5, 20)

    db.session.refresh(item)
    assert item.stock == 50


def test_draft_sale_creates_no_stock_movement(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()

    movements_before = StockMovement.query.filter_by(item_id=item.id).count()
    _sale_via_form(client, cust, item, 5, 20)

    assert StockMovement.query.filter_by(item_id=item.id).count() == movements_before


# ── Sale data integrity ──────────────────────────────────────────────────────


def test_draft_sale_still_creates_sale_items(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    _sale_via_form(client, cust, item, 4, 25)

    sal = _latest_sale()
    items = SaleItem.query.filter_by(sale_id=sal.id).all()
    assert len(items) == 1
    assert items[0].item_id == item.id


def test_draft_sale_quantity_is_correct(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    _sale_via_form(client, cust, item, 7, 20)

    sal = _latest_sale()
    si = SaleItem.query.filter_by(sale_id=sal.id).first()
    assert si.quantity == 7


def test_draft_sale_discount_calculation_is_correct(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    # 10 units @ 20 = 200 gross, 10% discount -> 20 discount, 180 net
    _sale_via_form(client, cust, item, 10, 20, disc_type="percent", disc_value="10")

    sal = _latest_sale()
    si = SaleItem.query.filter_by(sale_id=sal.id).first()
    assert float(si.discount_amount) == 20.0
    assert float(si.amount) == 180.0


def test_draft_sale_tax_calculation_is_correct(appctx):
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    # 10 units @ 20 = 200 gross, 5% tax -> 10 tax, 210 net (no discount)
    _sale_via_form(client, cust, item, 10, 20, tax="5")

    sal = _latest_sale()
    si = SaleItem.query.filter_by(sale_id=sal.id).first()
    assert float(si.tax_amount) == 10.0
    assert float(si.amount) == 210.0


# ── Creation-path decisions ──────────────────────────────────────────────────


def test_normal_sale_route_is_the_draft_path(appctx):
    """Restates test_normal_sale_creation_produces_a_draft as the explicit
    per-path assertion the report requires: /sale is the Draft path."""
    _books()
    cust = _customer()
    item = _item()
    client = _manager()

    _sale_via_form(client, cust, item, 1, 20)

    assert _latest_sale().status == STATUS_DRAFT


def test_pos_checkout_remains_posted_unchanged(appctx):
    """POS was audited and deliberately left unchanged in Phase 2 (it removes
    stock and posts immediately, before the Sale row even exists, and often
    creates a CustomerPayment in the same transaction -- converting it to
    Draft would need a separate hold/payment/posting redesign, out of scope
    here). This locks in that decision at the model level."""
    import json

    account_id = _books()
    cust = _customer()
    item = _item(stock=20)
    client = _manager()

    resp = client.post("/pos/checkout", data=json.dumps({
        "items": [{"item_id": item.id, "qty": 2, "price": 20}],
        "account_id": account_id, "amount_paid": 40, "customer_id": cust.id,
    }), content_type="application/json")
    assert resp.status_code == 200

    sal = _latest_sale()
    assert sal.status == STATUS_POSTED
    assert sal.invoice_no is not None
    db.session.refresh(item)
    assert item.stock == 18


def test_quotation_to_sale_conversion_remains_posted_unchanged(appctx):
    """Quotation -> Sale conversion was audited and deliberately left
    unchanged in Phase 2: converting a Quotation is the deliberate 'this is
    now a real sale' action (the Quotation itself already served the
    pre-commitment/Draft role), and it already posts unconditionally like
    /sale did before this phase -- changing it is a separate decision, not
    part of 'normal Sale creation'. This locks in that it still posts."""
    from app import Quotation, QuotationItem

    _books()
    cust = _customer()
    item = _item(stock=20)
    client = _manager()

    from datetime import date as _date
    q = Quotation(customer_id=cust.id, quote_date=_date(2026, 1, 1), notes="", status="Draft")
    db.session.add(q); db.session.flush()
    db.session.add(QuotationItem(
        quotation_id=q.id, item_id=item.id, quantity=3, sale_price=20,
        discount_type="percent", discount_value=0,
        tax_percent=0, unit_name="Pcs", unit_factor=1,
    ))
    db.session.commit()

    client.post(f"/quotations/{q.id}/convert", data={"sale_date": "2026-01-01"},
               follow_redirects=True)

    sal = _latest_sale()
    assert sal is not None
    assert sal.status == STATUS_POSTED
    assert sal.invoice_no is not None
    db.session.refresh(item)
    assert item.stock == 17


# ── Draft edit/delete compatibility (minimal, see Phase 2 report) ──────────


def test_draft_sale_can_be_deleted_without_corrupting_stock(appctx):
    """delete_sale's stock-restore loop assumed every deleted Sale had removed
    stock at creation -- true before Phase 2, false for a Draft. Guarded by
    status so deleting a never-stocked Draft does not add phantom stock."""
    _books()
    cust = _customer()
    item = _item(stock=50)
    manager = _manager()
    admin_client = _admin()

    _sale_via_form(manager, cust, item, 5, 20)
    sal = _latest_sale()
    assert sal.status == STATUS_DRAFT

    admin_client.post(f"/sale/{sal.id}/delete", follow_redirects=True)

    db.session.refresh(item)
    assert item.stock == 50
    assert db.session.get(Sale, sal.id) is None


def test_draft_sale_can_be_edited_without_corrupting_stock(appctx):
    """edit_sale's restore-old/apply-new stock dance assumed the sale being
    edited had already removed its old lines' stock -- also false for a
    Draft. Guarded the same way as delete: editing a Draft changes its lines
    without ever touching Item.stock."""
    _books()
    cust = _customer()
    item = _item(stock=50)
    manager = _manager()

    _sale_via_form(manager, cust, item, 5, 20)
    sal = _latest_sale()
    assert sal.status == STATUS_DRAFT

    manager.post(f"/sale/{sal.id}/edit", data={
        "customer_id": str(cust.id), "date": "2026-01-02", "notes": "edited",
        "item_id[]": str(item.id), "quantity[]": "8", "sale_price[]": "20",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)

    db.session.refresh(item)
    assert item.stock == 50
    db.session.refresh(sal)
    assert sal.status == STATUS_DRAFT
    si = SaleItem.query.filter_by(sale_id=sal.id).first()
    assert si.quantity == 8
