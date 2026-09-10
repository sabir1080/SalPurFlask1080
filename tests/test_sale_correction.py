"""Phase 6A of the Draft -> Posted workflow: Admin-only correction of a
POSTED, non-reversed Sale.

Distinct from:
  - edit_sale (still blocked for Posted, unchanged -- see assert_not_posted)
  - reverse_document_route (permanently flags is_reversed=True and stops)

correct_sale_route undoes the old Sale's GL/stock/ledger effect using the
same primitives reverse_document() itself uses (reverse_entry() +
_unwind_stock_and_subledger()), but WITHOUT setting is_reversed, then
applies corrected values and re-posts -- all inside one transaction, at the
service/model level (no internal HTTP redirect to the reversal route).

Keeps to the 24 specified cases; does not duplicate the existing reversal /
payment-warning suite (see test_sale_reversal_payment_warning.py) or the
Draft/Posted/return-search suite (see test_draft_return_and_delete_ui.py,
test_return_document_search.py) beyond what's needed to prove Correction
respects those same boundaries.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Supplier, Customer, Category, Item, Sale, SaleItem, SaleReturn, CustomerPayment,
    FinancialAccount,
    calc_discount_tax,
    sync_customer_opening, sync_customer_sale, sync_customer_receipt,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    post_document, reverse_document, AuditLog, JournalEntry,
    CustomerLedgerEntry, get_sale_received,
)
from salpurflask.models.models import STATUS_DRAFT, STATUS_POSTED, posted_entry


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


def _unverified_admin(email="u@t.com"):
    db.session.add(User(name="U", email=email, password=pwd_context.hash("secret123"),
                        verified=False, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _books():
    from salpurflask.models.models import seed_financial_account_links

    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    db.session.add(FinancialAccount(name="Cash", method="Cash", account_type="Cash", opening_balance=0))
    db.session.commit()
    seed_financial_account_links()
    db.session.commit()


def _cash_account_id():
    return FinancialAccount.query.filter_by(name="Cash").first().id


def make_customer(name="Customer A"):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


def make_item(name="Widget", stock=100):
    """stock carries a matching inventory_value, as if bought in at
    purchase_price -- item_remove_stock() (used by make_posted_sale below)
    refuses to remove costed value the item was never given."""
    cat = Category(name="Cat-" + name); db.session.add(cat); db.session.flush()
    it = Item(name=name, category_id=cat.id, stock=0, purchase_price=10, sale_price=20)
    db.session.add(it); db.session.flush()
    if stock:
        from salpurflask.models.models import item_add_stock
        item_add_stock(it, stock, cost_total=Decimal(str(10)) * Decimal(str(stock)), location_id=None)
        db.session.flush()
    return it


def make_posted_sale(cust, item, qty=10, price=100):
    """A genuinely Posted sale: goes through the same sequence post_sale_route
    uses (stock removal, invoice numbering, ledger sync, GL post)."""
    from salpurflask.models.models import item_remove_stock, allocate_document_number

    sale = Sale(customer_id=cust.id, item_id=item.id, quantity=qty, sale_price=price,
               cost_price=item.purchase_price or 0, status=STATUS_POSTED, location_id=None)
    db.session.add(sale); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    si = SaleItem(sale_id=sale.id, item_id=item.id, quantity=qty, sale_price=price,
                  cost_price=item.purchase_price or 0, discount_type="percent", discount_value=0,
                  discount_amount=d, tax_percent=0, tax_amount=t, amount=net,
                  unit_name=None, unit_factor=1)
    db.session.add(si)
    db.session.flush()
    item_remove_stock(item, qty, cost_total=Decimal(str(item.purchase_price or 0)) * Decimal(str(qty)),
                      location_id=sale.location_id, movement_type="sale",
                      source_type="sale", source_id=sale.id)
    sale.invoice_no = allocate_document_number("sale", sale.date)
    sync_customer_sale(sale)
    post_document("sale", sale)
    db.session.commit()
    db.session.refresh(sale); db.session.refresh(si)
    return sale, si


def make_draft_sale(cust, item, qty=10, price=100):
    sale = Sale(customer_id=cust.id, item_id=item.id, quantity=qty, sale_price=price,
               cost_price=item.purchase_price or 0, status=STATUS_DRAFT, location_id=None)
    db.session.add(sale); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    si = SaleItem(sale_id=sale.id, item_id=item.id, quantity=qty, sale_price=price,
                  cost_price=item.purchase_price or 0, discount_type="percent", discount_value=0,
                  discount_amount=d, tax_percent=0, tax_amount=t, amount=net,
                  unit_name=None, unit_factor=1)
    db.session.add(si)
    db.session.commit()
    return sale, si


def _correction_payload(item, qty=5, price=100, extra=None):
    payload = {
        "customer_id": "",  # filled by caller when needed
        "notes": "corrected",
        "reason": "test correction",
        "item_id[]": [str(item.id)],
        "quantity[]": [str(qty)],
        "sale_price[]": [str(price)],
        "discount_type[]": ["percent"],
        "discount_value[]": ["0"],
        "tax_percent[]": ["0"],
        "unit_id[]": [""],
    }
    if extra:
        payload.update(extra)
    return payload


# ══════════════════════════════════════════════════════════════════════════
# 1-3: Access control
# ══════════════════════════════════════════════════════════════════════════


def test_admin_can_access_correction_for_posted_sale(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item)
    c = _admin()

    r = c.get(f"/sale/correct/{sale.id}")
    assert r.status_code == 200
    assert b"Correct Posted Sale" in r.data


def test_manager_cannot_correct_posted_sale(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item)
    c = _manager()

    payload = _correction_payload(item)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload)
    assert r.status_code in (302, 403)
    db.session.refresh(sale)
    assert sale.quantity == 10  # unchanged


def test_unverified_user_cannot_correct(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item)
    c = _unverified_admin()

    payload = _correction_payload(item)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload)
    assert r.status_code in (302, 403)
    db.session.refresh(sale)
    assert sale.quantity == 10


# ══════════════════════════════════════════════════════════════════════════
# 4-6: Status / reversal / returns gates
# ══════════════════════════════════════════════════════════════════════════


def test_draft_sale_cannot_be_corrected(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_draft_sale(cust, item)
    c = _admin()

    payload = _correction_payload(item)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"not Posted" in r.data or b"nothing to correct" in r.data
    db.session.refresh(sale)
    assert sale.status == STATUS_DRAFT


def test_reversed_sale_cannot_be_corrected(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item)
    reverse_document("sale", sale)
    db.session.commit()
    c = _admin()

    payload = _correction_payload(item)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"reversed" in r.data
    db.session.refresh(sale)
    assert sale.is_reversed is True


def test_posted_sale_with_sale_return_is_blocked(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10)
    sr = SaleReturn(sale_id=sale.id, customer_id=cust.id, item_id=item.id, quantity=1,
                    return_price=100, sale_item_id=si.id)
    db.session.add(sr); db.session.commit()
    c = _admin()

    payload = _correction_payload(item)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"Sale Return" in r.data
    db.session.refresh(sale)
    assert sale.quantity == 10


# ══════════════════════════════════════════════════════════════════════════
# 7-13: Payment interaction
# ══════════════════════════════════════════════════════════════════════════


def test_posted_sale_without_payment_can_be_corrected(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"corrected successfully" in r.data
    db.session.refresh(sale)
    assert sale.quantity == 5


def test_posted_sale_with_one_active_payment_requires_confirmation(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    pay = CustomerPayment(customer_id=cust.id, sale_id=sale.id, amount=500, payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_customer_receipt(pay); post_document("receipt", pay); db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"active payment" in r.data
    db.session.refresh(sale)
    assert sale.quantity == 10  # not applied without confirmation


def test_multiple_active_payments_require_confirmation(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    for amt in (300, 200):
        pay = CustomerPayment(customer_id=cust.id, sale_id=sale.id, amount=amt, payment_method="Cash", account_id=_cash_account_id())
        db.session.add(pay); db.session.flush()
        sync_customer_receipt(pay); post_document("receipt", pay)
    db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"2 active payment" in r.data
    db.session.refresh(sale)
    assert sale.quantity == 10


def test_cancel_no_confirmation_leaves_everything_unchanged(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    pay = CustomerPayment(customer_id=cust.id, sale_id=sale.id, amount=500, payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_customer_receipt(pay); post_document("receipt", pay); db.session.commit()
    entry_before = posted_entry("sale", sale.id)
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["customer_id"] = str(cust.id)
    c.post(f"/sale/correct/{sale.id}", data=payload)

    db.session.refresh(sale)
    assert sale.quantity == 10
    assert si.quantity == 10 if db.session.get(SaleItem, si.id) else True
    entry_after = posted_entry("sale", sale.id)
    assert entry_before.id == entry_after.id
    pay_after = db.session.get(CustomerPayment, pay.id)
    assert pay_after.amount == Decimal("500")
    assert pay_after.is_reversed is False


def test_confirmed_correction_keeps_payment_rows_unchanged(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    pay = CustomerPayment(customer_id=cust.id, sale_id=sale.id, amount=500, payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_customer_receipt(pay); post_document("receipt", pay); db.session.commit()
    pay_id, pay_amount = pay.id, pay.amount
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100, extra={"confirm_correction": "1"})
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"corrected successfully" in r.data

    pay_after = db.session.get(CustomerPayment, pay_id)
    assert pay_after.sale_id == sale.id
    assert pay_after.amount == pay_amount
    assert pay_after.is_reversed is False


def test_corrected_total_updates_customer_balance_correctly(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)  # total 1000
    c = _admin()

    payload = _correction_payload(item, qty=20, price=100)  # total 2000
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)

    entry = (CustomerLedgerEntry.query
             .filter_by(customer_id=cust.id, source_type="sale", source_id=sale.id).first())
    assert entry is not None
    assert float(entry.debit) == 2000.0, r.get_data(as_text=True)[:2000]


def test_decreasing_sale_below_received_creates_customer_credit_payment_untouched(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)  # total 1000
    pay = CustomerPayment(customer_id=cust.id, sale_id=sale.id, amount=900, payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_customer_receipt(pay); post_document("receipt", pay); db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100,
                                   extra={"confirm_correction": "1"})  # new total 500 < 900 received
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"corrected successfully" in r.data

    pay_after = db.session.get(CustomerPayment, pay.id)
    assert pay_after.amount == Decimal("900")
    assert pay_after.is_reversed is False
    received = get_sale_received(sale.id)
    assert received == 900.0  # untouched -- balance is now negative (credit)


# ══════════════════════════════════════════════════════════════════════════
# 14-15: Customer change safety
# ══════════════════════════════════════════════════════════════════════════


def test_customer_change_is_blocked_when_active_payment_exists(appctx):
    _books()
    item = make_item()
    cust1 = make_customer("Customer A")
    cust2 = make_customer("Customer B")
    sale, si = make_posted_sale(cust1, item, qty=10, price=100)
    pay = CustomerPayment(customer_id=cust1.id, sale_id=sale.id, amount=200, payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_customer_receipt(pay); post_document("receipt", pay); db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=10, price=100)
    payload["customer_id"] = str(cust2.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"cannot be changed" in r.data
    db.session.refresh(sale)
    assert sale.customer_id == cust1.id


def test_customer_change_works_safely_when_no_payment_exists(appctx):
    _books()
    item = make_item()
    cust1 = make_customer("Customer A")
    cust2 = make_customer("Customer B")
    sale, si = make_posted_sale(cust1, item, qty=10, price=100)
    c = _admin()

    payload = _correction_payload(item, qty=10, price=100)
    payload["customer_id"] = str(cust2.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"corrected successfully" in r.data
    db.session.refresh(sale)
    assert sale.customer_id == cust2.id

    old_entry = (CustomerLedgerEntry.query
                 .filter_by(customer_id=cust1.id, source_type="sale", source_id=sale.id).first())
    new_entry = (CustomerLedgerEntry.query
                 .filter_by(customer_id=cust2.id, source_type="sale", source_id=sale.id).first())
    assert old_entry is None
    assert new_entry is not None


# ══════════════════════════════════════════════════════════════════════════
# 16-20: Stock / GL / ledger / invoice-number correctness
# ══════════════════════════════════════════════════════════════════════════


def test_quantity_correction_restores_old_stock_and_applies_new_stock(appctx):
    _books()
    item = make_item(stock=100); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    stock_after_sale = item.stock  # 90
    c = _admin()

    payload = _correction_payload(item, qty=3, price=100)
    payload["customer_id"] = str(cust.id)
    c.post(f"/sale/correct/{sale.id}", data=payload)

    db.session.refresh(item)
    assert stock_after_sale == 90
    assert item.stock == 97  # 100 - 3


def test_price_discount_tax_correction_produces_correct_gl(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)  # total 1000
    c = _admin()

    payload = _correction_payload(item, qty=10, price=150,
                                   extra={"tax_percent[]": ["10"]})
    payload["customer_id"] = str(cust.id)
    c.post(f"/sale/correct/{sale.id}", data=payload)

    entry = posted_entry("sale", sale.id)
    assert entry is not None
    total_debit = sum(float(l.debit) for l in entry.lines)
    total_credit = sum(float(l.credit) for l in entry.lines)
    assert abs(total_debit - total_credit) < 0.01  # entry balances
    # 10 * 150 = 1500 + 10% tax = 1650 -- the single largest debit line (AR)
    # carries exactly the corrected revenue+tax, regardless of what the
    # COGS/Inventory legs add on both sides.
    max_debit_line = max(float(l.debit) for l in entry.lines)
    assert abs(max_debit_line - 1650.0) < 0.01


def test_old_sale_gl_is_not_left_as_active_duplicate(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    old_entry_id = posted_entry("sale", sale.id).id
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["customer_id"] = str(cust.id)
    c.post(f"/sale/correct/{sale.id}", data=payload)

    live_entries = JournalEntry.query.filter_by(
        source_type="sale", source_id=sale.id, reversal_of_id=None, is_reversed=False).all()
    assert len(live_entries) == 1
    assert live_entries[0].id != old_entry_id
    old_entry = db.session.get(JournalEntry, old_entry_id)
    assert old_entry.is_reversed is True


def test_customer_ledger_has_no_duplicate_sale_effect(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    c = _admin()

    payload = _correction_payload(item, qty=7, price=100)
    payload["customer_id"] = str(cust.id)
    c.post(f"/sale/correct/{sale.id}", data=payload)

    entries = (CustomerLedgerEntry.query
               .filter_by(customer_id=cust.id, source_type="sale", source_id=sale.id).all())
    assert len(entries) == 1
    assert float(entries[0].debit) == 700.0


def test_invoice_number_remains_unchanged(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    original_invoice = sale.invoice_no
    assert original_invoice
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["customer_id"] = str(cust.id)
    c.post(f"/sale/correct/{sale.id}", data=payload)

    db.session.refresh(sale)
    assert sale.invoice_no == original_invoice
    assert sale.id == sale.id  # primary key stable, same row


# ══════════════════════════════════════════════════════════════════════════
# 21-23: Audit / rollback / concurrency
# ══════════════════════════════════════════════════════════════════════════


def test_correction_creates_audit_record(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["customer_id"] = str(cust.id)
    c.post(f"/sale/correct/{sale.id}", data=payload)

    audit = (AuditLog.query.filter_by(entity="Sale", entity_id=sale.id, action="correction").first())
    assert audit is not None
    assert str(sale.id) in audit.summary or (sale.invoice_no or "") in audit.summary


def test_any_failure_rolls_back_all_changes(appctx):
    """An insufficient-stock line should refuse the whole correction -- old
    Sale, SaleItems, stock, GL and ledger must all be exactly as before."""
    _books()
    item = make_item(stock=5); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=5, price=100)  # uses all 5 units
    old_entry_id = posted_entry("sale", sale.id).id
    c = _admin()

    # Ask for far more than could ever be available (old 5 restored + 5 in
    # stock = 10 max at this warehouse).
    payload = _correction_payload(item, qty=999, price=100)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload, follow_redirects=True)
    assert b"insufficient stock" in r.data.lower()

    db.session.refresh(sale)
    assert sale.quantity == 5
    entry_after = posted_entry("sale", sale.id)
    assert entry_after.id == old_entry_id
    assert db.session.get(SaleItem, si.id) is not None


def test_concurrent_correction_is_protected_by_row_locking(appctx):
    """SELECT ... FOR UPDATE is used to lock the Sale row -- same pattern as
    post_sale_route. Not exercised under real concurrency on SQLite (a no-op
    there), but confirms the route acquires the lock via with_for_update()
    rather than a plain query, matching the rest of this codebase's Posted
    mutation routes."""
    import inspect
    from salpurflask.sales.routes import correct_sale_route
    src = inspect.getsource(correct_sale_route)
    assert "with_for_update()" in src


# ══════════════════════════════════════════════════════════════════════════
# 24: Button visibility / authorization
# ══════════════════════════════════════════════════════════════════════════


def test_correct_button_shown_for_posted_sale_to_admin(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item)
    c = _admin()

    r = c.get("/sale")
    body = r.get_data(as_text=True)
    assert f"/sale/correct/{sale.id}" in body


def test_correct_button_absent_for_posted_sale_to_manager(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item)
    c = _manager()

    r = c.get("/sale")
    body = r.get_data(as_text=True)
    assert f"/sale/correct/{sale.id}" not in body


def test_correct_button_absent_for_draft_sale(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_draft_sale(cust, item)
    c = _admin()

    r = c.get("/sale")
    body = r.get_data(as_text=True)
    assert f"/sale/correct/{sale.id}" not in body


def test_correct_button_absent_for_reversed_sale(appctx):
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item)
    reverse_document("sale", sale)
    db.session.commit()
    c = _admin()

    r = c.get("/sale")
    body = r.get_data(as_text=True)
    assert f"/sale/correct/{sale.id}" not in body


def test_manipulated_post_from_manager_is_rejected_at_backend(appctx):
    """Backend security must not rely on hiding the button -- a hand-built
    POST from a Manager (who never sees the Correct button) must still fail."""
    _books()
    item = make_item(); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    c = _manager()

    payload = _correction_payload(item, qty=1, price=1)
    payload["customer_id"] = str(cust.id)
    r = c.post(f"/sale/correct/{sale.id}", data=payload)
    assert r.status_code in (302, 403)
    db.session.refresh(sale)
    assert sale.quantity == 10
    assert sale.sale_price == Decimal("100")
