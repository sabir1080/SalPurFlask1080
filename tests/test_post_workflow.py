"""Phase 4 of the Draft -> Posted workflow: the explicit Post action.

A Draft Sale/Purchase (Phase 2/3) is created with no invoice number, stock,
ledger or GL effect. Posting it -- POST /sale/<id>/post or
POST /purchase/<id>/post -- runs the exact sequence /sale and /purchase
used to run inline before Phase 2/3: allocate_document_number(), the
stock helper, sync_customer_sale()/sync_supplier_purchase(), and
post_document(), then flips status to "posted". Nothing here is new
business logic -- see salpurflask/sales/routes.py:post_sale_route and
salpurflask/purchase/routes.py:post_purchase_route.

Also covers the backfill_document_numbers() Draft-exclusion fix and the
payment/receipt Draft-target rejection (both audit findings from the
Phase 4 forensic audit).
"""
from datetime import date
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Customer, Supplier, Sale, SaleItem, Purchase, PurchaseItem,
    JournalEntry, CustomerLedgerEntry, SupplierLedgerEntry, StockMovement,
    CustomerPayment, SupplierPayment,
    AccountingPeriod,
    get_total_receivable, get_total_payable,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening,
    sync_customer_opening, sync_supplier_opening,
    backfill_document_numbers,
)
from salpurflask.models.models import STATUS_DRAFT, STATUS_POSTED
from salpurflask.models.business_config import BusinessCategory
from salpurflask.models.inventory_location import stock_at_location, get_or_create_default_location


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


def _supplier(name="Supplier A"):
    s = Supplier(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


class _RoleClient:
    """Flask-Login caches the resolved user on flask.g per app context, not
    per request -- see tests/test_draft_sale.py's identical helper for the
    full rationale."""
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


def _sale_via_form(client, customer, item, qty, price, location_id=None):
    data = {
        "customer_id": str(customer.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "sale_price[]": str(price),
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }
    if location_id is not None:
        data["location_id"] = str(location_id)
    return client.post("/sale", data=data, follow_redirects=True)


def _purchase_via_form(client, supplier, item, qty, price, location_id=None):
    data = {
        "supplier_id": str(supplier.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "purchase_price[]": str(price),
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }
    if location_id is not None:
        data["location_id"] = str(location_id)
    return client.post("/purchase", data=data, follow_redirects=True)


def _latest_sale():
    return Sale.query.order_by(Sale.id.desc()).first()


def _latest_purchase():
    return Purchase.query.order_by(Purchase.id.desc()).first()


def _close_period_for(when):
    p = (AccountingPeriod.query
         .filter(AccountingPeriod.start_date <= when, AccountingPeriod.end_date >= when)
         .first())
    p.is_closed = True
    db.session.commit()
    return p


# ══════════════════════════════════════════════════════════════════════════
# Sale Post
# ══════════════════════════════════════════════════════════════════════════


def test_draft_sale_can_be_posted(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    assert sal.status == STATUS_DRAFT

    r = client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(sal)
    assert sal.status == STATUS_POSTED


def test_sale_invoice_number_assigned_only_at_post(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    assert sal.invoice_no is None

    client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    db.session.refresh(sal)
    assert sal.invoice_no is not None
    assert sal.invoice_no.startswith("INV-")


def test_posting_a_sale_decreases_stock_at_correct_location(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    default = get_or_create_default_location()
    assert stock_at_location(item.id, default.id) == 50

    client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    db.session.refresh(item)
    assert stock_at_location(item.id, default.id) == 45
    assert item.stock == 45


def test_posting_a_sale_creates_stock_movement(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    movements_before = StockMovement.query.filter_by(item_id=item.id).count()

    client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    rows = StockMovement.query.filter_by(item_id=item.id, movement_type="sale").all()
    assert len(rows) == movements_before + 1


def test_posting_a_sale_creates_customer_ledger_entry(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()

    client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    entry = CustomerLedgerEntry.query.filter_by(source_type="sale", source_id=sal.id).first()
    assert entry is not None
    assert float(entry.debit) == 100.0  # 5 * 20


def test_posting_a_sale_creates_journal_entry(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    je_before = JournalEntry.query.count()

    client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    assert JournalEntry.query.filter_by(source_type="sale", source_id=sal.id).count() == 1
    assert JournalEntry.query.count() == je_before + 1


def test_posting_a_sale_increases_receivable(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    assert get_total_receivable() == 0.0

    client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    assert get_total_receivable() == 100.0


def test_second_post_of_same_sale_does_not_duplicate_anything(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()

    client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    db.session.refresh(sal)
    invoice_no_after_first = sal.invoice_no
    je_count = JournalEntry.query.count()
    stock_after_first = item.stock

    r = client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(item)
    db.session.refresh(sal)
    assert sal.invoice_no == invoice_no_after_first
    assert JournalEntry.query.count() == je_count
    assert item.stock == stock_after_first


def test_posting_a_sale_with_insufficient_stock_rolls_back_everything(appctx):
    _books()
    cust = _customer()
    item = _item(stock=3)
    client = _manager()
    _sale_via_form(client, cust, item, 3, 20)   # Draft creation allows this (no stock check blocks Draft save)
    sal = _latest_sale()

    # Stock changes out from under the Draft before it is posted -- no
    # ItemStock row exists yet (lazily created on first use), so dropping
    # Item.stock to 0 is enough: _item_stock_row() seeds the location row
    # from Item.stock the first time anything touches it.
    item.stock = 0
    db.session.commit()

    je_before = JournalEntry.query.count()
    r = client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(sal)
    assert sal.status == STATUS_DRAFT
    assert sal.invoice_no is None
    assert JournalEntry.query.count() == je_before
    assert CustomerLedgerEntry.query.filter_by(source_type="sale", source_id=sal.id).first() is None


def test_posting_a_sale_in_a_closed_period_rolls_back_everything(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    _close_period_for(date(2026, 1, 1))

    je_before = JournalEntry.query.count()
    r = client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    assert r.status_code in (200, 400, 500)
    db.session.refresh(sal)
    db.session.refresh(item)
    assert sal.status == STATUS_DRAFT
    assert sal.invoice_no is None
    assert item.stock == 50
    assert JournalEntry.query.count() == je_before


def test_posting_a_sale_uses_its_own_stored_location(appctx):
    """A brand-new second warehouse starts at zero stock -- /sale's own
    pre-flight availability check (unchanged by Phase 2/4) refuses to even
    save a Draft against a location with nothing in it, so this test first
    posts a Purchase into loc2 to give it real stock, then posts a Sale
    against that same location and confirms only loc2 moves.

    The item starts at stock=0 (not the usual _item() default): the very
    first ItemStock row ever created for an item is seeded from Item.stock
    as a backward-compat rule (see _item_stock_row()'s docstring), which
    would otherwise leak a nonzero opening balance into whichever location
    happens to be touched first -- irrelevant noise for a test whose point
    is which location received the goods, not how a legacy item behaves."""
    from salpurflask.models.inventory_location import Branch, Location

    _books()
    cust = _customer()
    sup = _supplier()
    item = _item(stock=0)
    client = _manager()
    default = get_or_create_default_location()
    branch = Branch.query.filter_by(is_default=True).first()
    loc2 = Location(name="Second Warehouse", kind="warehouse", branch_id=branch.id)
    db.session.add(loc2); db.session.commit()

    _purchase_via_form(client, sup, item, 20, 10, location_id=loc2.id)
    pur = _latest_purchase()
    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    assert stock_at_location(item.id, loc2.id) == 20

    _sale_via_form(client, cust, item, 5, 20, location_id=loc2.id)
    sal = _latest_sale()
    assert sal is not None
    assert sal.location_id == loc2.id

    client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    assert stock_at_location(item.id, loc2.id) == 15
    assert stock_at_location(item.id, default.id) == 0


# ══════════════════════════════════════════════════════════════════════════
# Purchase Post
# ══════════════════════════════════════════════════════════════════════════


def test_draft_purchase_can_be_posted(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()
    assert pur.status == STATUS_DRAFT

    r = client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(pur)
    assert pur.status == STATUS_POSTED


def test_purchase_invoice_number_assigned_only_at_post(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()
    assert pur.invoice_no is None

    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    db.session.refresh(pur)
    assert pur.invoice_no is not None
    assert pur.invoice_no.startswith("PUR-")


def test_posting_a_purchase_increases_stock_at_correct_location(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()
    default = get_or_create_default_location()
    assert stock_at_location(item.id, default.id) == 50

    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    db.session.refresh(item)
    assert stock_at_location(item.id, default.id) == 55
    assert item.stock == 55


def test_posting_a_purchase_creates_stock_movement(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()
    movements_before = StockMovement.query.filter_by(item_id=item.id).count()

    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    rows = StockMovement.query.filter_by(item_id=item.id, movement_type="purchase").all()
    assert len(rows) == movements_before + 1


def test_posting_a_purchase_creates_supplier_ledger_entry(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()

    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    entry = SupplierLedgerEntry.query.filter_by(source_type="purchase", source_id=pur.id).first()
    assert entry is not None
    assert float(entry.credit) == 50.0  # 5 * 10


def test_posting_a_purchase_creates_journal_entry(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()
    je_before = JournalEntry.query.count()

    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    assert JournalEntry.query.filter_by(source_type="purchase", source_id=pur.id).count() == 1
    assert JournalEntry.query.count() == je_before + 1


def test_posting_a_purchase_increases_payable(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()
    assert get_total_payable() == 0.0

    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    assert get_total_payable() == 50.0


def test_second_post_of_same_purchase_does_not_duplicate_anything(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()

    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    db.session.refresh(pur)
    invoice_no_after_first = pur.invoice_no
    je_count = JournalEntry.query.count()
    stock_after_first = item.stock

    r = client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(item)
    db.session.refresh(pur)
    assert pur.invoice_no == invoice_no_after_first
    assert JournalEntry.query.count() == je_count
    assert item.stock == stock_after_first


def test_posting_a_purchase_in_a_closed_period_rolls_back_everything(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()
    _close_period_for(date(2026, 1, 1))

    je_before = JournalEntry.query.count()
    r = client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    assert r.status_code in (200, 400, 500)
    db.session.refresh(pur)
    db.session.refresh(item)
    assert pur.status == STATUS_DRAFT
    assert pur.invoice_no is None
    assert item.stock == 50
    assert JournalEntry.query.count() == je_before


def test_posting_a_purchase_uses_its_own_stored_location(appctx):
    """Before Post, this item has no ItemStock row anywhere, so
    stock_at_location() treats Item.stock as implicitly living at the
    default location (a backward-compat rule -- see stock_at_location()'s
    own docstring). Posting the Purchase creates the item's first-ever
    ItemStock row, at loc2; from that point on, "no row at the default
    location" means a real, explicit zero there, not the old fallback --
    proving the Purchase's own stored location (loc2), not the default,
    is what actually received the goods."""
    from salpurflask.models.inventory_location import Branch, Location

    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    default = get_or_create_default_location()
    branch = Branch.query.filter_by(is_default=True).first()
    loc2 = Location(name="Second Warehouse", kind="warehouse", branch_id=branch.id)
    db.session.add(loc2); db.session.commit()

    _purchase_via_form(client, sup, item, 5, 10, location_id=loc2.id)
    pur = _latest_purchase()
    assert pur.location_id == loc2.id

    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    assert stock_at_location(item.id, loc2.id) == 55
    assert stock_at_location(item.id, default.id) == 0


# ══════════════════════════════════════════════════════════════════════════
# Numbering
# ══════════════════════════════════════════════════════════════════════════


def test_backfill_document_numbers_never_numbers_a_draft(appctx):
    _books()
    cust = _customer()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 1, 20)
    _purchase_via_form(client, sup, item, 1, 10)
    sal = _latest_sale()
    pur = _latest_purchase()
    assert sal.status == STATUS_DRAFT
    assert pur.status == STATUS_DRAFT

    numbered = backfill_document_numbers()

    db.session.refresh(sal)
    db.session.refresh(pur)
    assert sal.invoice_no is None
    assert pur.invoice_no is None
    assert numbered == 0


def test_two_posted_sales_get_sequential_numbers(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 1, 20)
    first = _latest_sale()
    _sale_via_form(client, cust, item, 1, 20)
    second = _latest_sale()

    client.post(f"/sale/{first.id}/post", follow_redirects=True)
    client.post(f"/sale/{second.id}/post", follow_redirects=True)
    db.session.refresh(first)
    db.session.refresh(second)
    assert first.invoice_no == "INV-2026-000001"
    assert second.invoice_no == "INV-2026-000002"


def test_a_failed_post_does_not_permanently_consume_its_number(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    _close_period_for(date(2026, 1, 1))

    client.post(f"/sale/{sal.id}/post", follow_redirects=True)   # fails: closed period
    db.session.refresh(sal)
    assert sal.invoice_no is None

    # Reopen and post a second, brand-new Draft -- it must get 000001, proving
    # the failed attempt above never actually consumed a number.
    p = (AccountingPeriod.query
         .filter(AccountingPeriod.start_date <= date(2026, 1, 1),
                 AccountingPeriod.end_date >= date(2026, 1, 1)).first())
    p.is_closed = False
    db.session.commit()

    _sale_via_form(client, cust, item, 1, 20)
    fresh = _latest_sale()
    client.post(f"/sale/{fresh.id}/post", follow_redirects=True)
    db.session.refresh(fresh)
    assert fresh.invoice_no == "INV-2026-000001"


# ══════════════════════════════════════════════════════════════════════════
# Payments must not target a Draft
# ══════════════════════════════════════════════════════════════════════════


def test_draft_purchase_excluded_from_supplier_payment_bill_picker(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()
    assert pur.status == STATUS_DRAFT

    r = client.get(f"/api/supplier/{sup.id}/outstanding-purchases")
    assert r.status_code == 200
    ids = [row["id"] for row in r.get_json()["purchases"]]
    assert pur.id not in ids


def test_draft_sale_excluded_from_customer_receipt_bill_picker(appctx):
    _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    assert sal.status == STATUS_DRAFT

    r = client.get(f"/api/customer/{cust.id}/outstanding-sales")
    assert r.status_code == 200
    ids = [row["id"] for row in r.get_json()["sales"]]
    assert sal.id not in ids


def test_direct_payment_against_draft_purchase_is_rejected(appctx):
    account_id = _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()

    r = client.post("/supplier_payment", data={
        "supplier_id": str(sup.id), "purchase_id": str(pur.id),
        "amount": "10", "payment_date": "2026-01-01",
        "payment_method": "Cash", "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    assert SupplierPayment.query.filter_by(purchase_id=pur.id).count() == 0


def test_direct_receipt_against_draft_sale_is_rejected(appctx):
    account_id = _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()

    r = client.post("/customer_receipt", data={
        "customer_id": str(cust.id), "sale_id": str(sal.id),
        "amount": "10", "payment_date": "2026-01-01",
        "payment_method": "Cash", "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    assert CustomerPayment.query.filter_by(sale_id=sal.id).count() == 0


def test_posted_purchase_still_accepts_a_payment(appctx):
    """Existing Posted-document payment behavior must remain intact."""
    account_id = _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()
    _purchase_via_form(client, sup, item, 5, 10)
    pur = _latest_purchase()
    client.post(f"/purchase/{pur.id}/post", follow_redirects=True)
    db.session.refresh(pur)
    assert pur.status == STATUS_POSTED

    r = client.post("/supplier_payment", data={
        "supplier_id": str(sup.id), "purchase_id": str(pur.id),
        "amount": "20", "payment_date": "2026-01-01",
        "payment_method": "Cash", "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    assert SupplierPayment.query.filter_by(purchase_id=pur.id).count() == 1


def test_posted_sale_still_accepts_a_receipt(appctx):
    """Existing Posted-document receipt behavior must remain intact."""
    account_id = _books()
    cust = _customer()
    item = _item(stock=50)
    client = _manager()
    _sale_via_form(client, cust, item, 5, 20)
    sal = _latest_sale()
    client.post(f"/sale/{sal.id}/post", follow_redirects=True)
    db.session.refresh(sal)
    assert sal.status == STATUS_POSTED

    r = client.post("/customer_receipt", data={
        "customer_id": str(cust.id), "sale_id": str(sal.id),
        "amount": "20", "payment_date": "2026-01-01",
        "payment_method": "Cash", "account_id": str(account_id),
    }, follow_redirects=True)
    assert r.status_code == 200
    assert CustomerPayment.query.filter_by(sale_id=sal.id).count() == 1
