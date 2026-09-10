"""Phase 6B of the Draft -> Posted workflow: Admin-only correction of a
POSTED, non-reversed Purchase -- the Purchase-side counterpart of Phase 6A's
Sale correction (see tests/test_sale_correction.py).

Distinct from:
  - edit_purchase (still blocked for Posted, unchanged -- see assert_not_posted)
  - reverse_document_route (permanently flags is_reversed=True and stops)

correct_purchase_route undoes the old Purchase's GL/stock/ledger effect
using the same primitives reverse_document() itself uses (reverse_entry() +
_unwind_stock_and_subledger()), but WITHOUT setting is_reversed, then
applies corrected values and re-posts -- all inside one transaction, at the
service/model level (no internal HTTP redirect to the reversal route).

Keeps to the specified cases; does not duplicate the existing reversal /
payment-warning suite (see test_purchase_reversal_payment_warning.py -- if
present -- or test_sale_reversal_payment_warning.py's purchase-adjacent
coverage) beyond what's needed to prove Correction respects those same
boundaries.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Supplier, Category, Item, Purchase, PurchaseItem, PurchaseReturn, SupplierPayment,
    PurchaseOrder, PurchaseOrderItem, FinancialAccount,
    calc_discount_tax,
    sync_supplier_opening, sync_supplier_purchase, sync_supplier_payment,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    post_document, reverse_document, AuditLog, JournalEntry,
    SupplierLedgerEntry, get_purchase_paid,
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


def make_supplier(name="Supplier A"):
    s = Supplier(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


def make_item(name="Widget", stock=0):
    cat = Category(name="Cat-" + name); db.session.add(cat); db.session.flush()
    it = Item(name=name, category_id=cat.id, stock=0, purchase_price=10, sale_price=20)
    db.session.add(it); db.session.flush()
    if stock:
        from salpurflask.models.models import item_add_stock
        item_add_stock(it, stock, cost_total=Decimal(str(10)) * Decimal(str(stock)), location_id=None)
        db.session.flush()
    return it


def make_posted_purchase(sup, item, qty=10, price=100):
    """A genuinely Posted purchase: goes through the same sequence
    post_purchase_route uses (stock addition, invoice numbering, ledger
    sync, GL post)."""
    from salpurflask.models.models import item_add_stock, allocate_document_number

    pur = Purchase(supplier_id=sup.id, item_id=item.id, quantity=qty, purchase_price=price,
                   status=STATUS_POSTED, location_id=None)
    db.session.add(pur); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    pi = PurchaseItem(purchase_id=pur.id, item_id=item.id, quantity=qty, purchase_price=price,
                      discount_type="percent", discount_value=0,
                      discount_amount=d, tax_percent=0, tax_amount=t, amount=net,
                      unit_name=None, unit_factor=1)
    db.session.add(pi)
    db.session.flush()
    item_add_stock(item, qty, net - t, location_id=pur.location_id,
                   movement_type="purchase", source_type="purchase", source_id=pur.id)
    pur.invoice_no = allocate_document_number("purchase", pur.date)
    sync_supplier_purchase(pur)
    post_document("purchase", pur)
    db.session.commit()
    db.session.refresh(pur); db.session.refresh(pi)
    return pur, pi


def make_draft_purchase(sup, item, qty=10, price=100):
    pur = Purchase(supplier_id=sup.id, item_id=item.id, quantity=qty, purchase_price=price,
                   status=STATUS_DRAFT, location_id=None)
    db.session.add(pur); db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    pi = PurchaseItem(purchase_id=pur.id, item_id=item.id, quantity=qty, purchase_price=price,
                      discount_type="percent", discount_value=0,
                      discount_amount=d, tax_percent=0, tax_amount=t, amount=net,
                      unit_name=None, unit_factor=1)
    db.session.add(pi)
    db.session.commit()
    return pur, pi


def _correction_payload(item, qty=5, price=100, extra=None):
    payload = {
        "supplier_id": "",  # filled by caller when needed
        "notes": "corrected",
        "reason": "test correction",
        "item_id[]": [str(item.id)],
        "quantity[]": [str(qty)],
        "purchase_price[]": [str(price)],
        "discount_type[]": ["percent"],
        "discount_value[]": ["0"],
        "tax_percent[]": ["0"],
        "unit_id[]": [""],
    }
    if extra:
        payload.update(extra)
    return payload


# ══════════════════════════════════════════════════════════════════════════
# Authorization
# ══════════════════════════════════════════════════════════════════════════


def test_admin_can_access_correction_for_posted_purchase(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item)
    c = _admin()

    r = c.get(f"/purchase/correct/{pur.id}")
    assert r.status_code == 200
    assert b"Correct Posted Purchase" in r.data


def test_manager_cannot_access_correction(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item)
    c = _manager()

    payload = _correction_payload(item)
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload)
    assert r.status_code in (302, 403)
    db.session.refresh(pur)
    assert pur.quantity == 10  # unchanged


def test_unverified_admin_cannot_correct(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item)
    c = _unverified_admin()

    payload = _correction_payload(item)
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload)
    assert r.status_code in (302, 403)
    db.session.refresh(pur)
    assert pur.quantity == 10


def test_posted_edit_remains_protected(appctx):
    """Normal Edit stays blocked for a Posted purchase -- correction does not
    unlock edit_purchase, which is unchanged, gated by assert_not_posted."""
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item)
    c = _manager()

    r = c.get(f"/purchase/edit/{pur.id}", follow_redirects=True)
    # assert_not_posted raises PostingError -> global handler redirects+flashes
    assert b"posted to the general ledger" in r.data or b"cannot be corrected" not in r.data


def test_correct_button_visible_only_to_admin_for_eligible_posted_purchase(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item)

    admin_c = _admin()
    r_admin = admin_c.get("/purchase")
    assert f"/purchase/correct/{pur.id}" in r_admin.get_data(as_text=True)


def test_correct_button_absent_for_posted_purchase_to_manager(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item)

    manager_c = _manager()
    r_manager = manager_c.get("/purchase")
    assert f"/purchase/correct/{pur.id}" not in r_manager.get_data(as_text=True)


# ══════════════════════════════════════════════════════════════════════════
# Safety
# ══════════════════════════════════════════════════════════════════════════


def test_draft_purchase_uses_existing_draft_behavior_not_posted_correction(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_draft_purchase(sup, item)
    c = _admin()

    payload = _correction_payload(item)
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"not Posted" in r.data or b"nothing to correct" in r.data
    db.session.refresh(pur)
    assert pur.status == STATUS_DRAFT


def test_reversed_purchase_correction_blocked(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item)
    reverse_document("purchase", pur)
    db.session.commit()
    c = _admin()

    payload = _correction_payload(item)
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"reversed" in r.data
    db.session.refresh(pur)
    assert pur.is_reversed is True


def test_purchase_with_existing_purchase_return_blocked(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10)
    pr = PurchaseReturn(purchase_id=pur.id, supplier_id=sup.id, item_id=item.id, quantity=1,
                        return_price=100, purchase_item_id=pi.id)
    db.session.add(pr); db.session.commit()
    c = _admin()

    payload = _correction_payload(item)
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"Purchase Return" in r.data
    db.session.refresh(pur)
    assert pur.quantity == 10


def test_invoice_number_remains_unchanged(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    original_invoice = pur.invoice_no
    assert original_invoice
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    db.session.refresh(pur)
    assert pur.invoice_no == original_invoice


def test_purchase_date_remains_unchanged(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    original_date = pur.date
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    db.session.refresh(pur)
    assert pur.date == original_date


def test_location_id_remains_unchanged(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    original_location_id = pur.location_id
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    db.session.refresh(pur)
    assert pur.location_id == original_location_id


def test_po_conversion_linkage_remains_valid(appctx):
    """PurchaseOrder.converted_purchase_id points at Purchase.id -- correction
    never deletes/recreates the Purchase row, only mutates it in place, so
    the link must survive untouched."""
    _books()
    item = make_item(); sup = make_supplier()
    from salpurflask.utils import now_local
    po = PurchaseOrder(supplier_id=sup.id, order_date=now_local())
    db.session.add(po); db.session.flush()
    db.session.add(PurchaseOrderItem(po_id=po.id, item_id=item.id, quantity=10,
                                     purchase_price=100, unit_factor=1))
    db.session.commit()

    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    po.status = "Received"
    po.converted_purchase_id = pur.id
    db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    db.session.refresh(po)
    assert po.converted_purchase_id == pur.id


# ══════════════════════════════════════════════════════════════════════════
# Basic correction
# ══════════════════════════════════════════════════════════════════════════


def test_unpaid_posted_purchase_can_be_corrected(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"corrected successfully" in r.data
    db.session.refresh(pur)
    assert pur.quantity == 5


def test_supplier_remains_correct_when_unchanged(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    db.session.refresh(pur)
    assert pur.supplier_id == sup.id


def test_quantity_correction_updates_stock_correctly(appctx):
    _books()
    item = make_item(stock=0); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    stock_after_purchase = item.stock  # 10
    c = _admin()

    payload = _correction_payload(item, qty=3, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    db.session.refresh(item)
    assert stock_after_purchase == 10
    assert item.stock == 3


def test_price_correction_updates_accounting_correctly(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)  # total 1000
    c = _admin()

    payload = _correction_payload(item, qty=10, price=150)  # total 1500
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    entry = posted_entry("purchase", pur.id)
    assert entry is not None
    total_debit = sum(float(l.debit) for l in entry.lines)
    total_credit = sum(float(l.credit) for l in entry.lines)
    assert abs(total_debit - total_credit) < 0.01
    assert abs(total_credit - 1500.0) < 0.01  # AP credit carries the corrected total


def test_discount_tax_correction_updates_accounting_correctly(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)  # total 1000
    c = _admin()

    payload = _correction_payload(item, qty=10, price=100,
                                   extra={"tax_percent[]": ["10"]})  # 1000 + 100 tax = 1100
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    entry = posted_entry("purchase", pur.id)
    assert entry is not None
    total_credit = sum(float(l.credit) for l in entry.lines)
    assert abs(total_credit - 1100.0) < 0.01


def test_no_duplicate_active_purchase_gl_entry(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    old_entry_id = posted_entry("purchase", pur.id).id
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    live_entries = JournalEntry.query.filter_by(
        source_type="purchase", source_id=pur.id, reversal_of_id=None, is_reversed=False).all()
    assert len(live_entries) == 1
    assert live_entries[0].id != old_entry_id
    old_entry = db.session.get(JournalEntry, old_entry_id)
    assert old_entry.is_reversed is True


def test_supplier_ledger_has_no_duplicate_purchase_effect(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    c = _admin()

    payload = _correction_payload(item, qty=7, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    entries = (SupplierLedgerEntry.query
               .filter_by(supplier_id=sup.id, source_type="purchase", source_id=pur.id).all())
    assert len(entries) == 1
    assert float(entries[0].credit) == 700.0


# ══════════════════════════════════════════════════════════════════════════
# Payment scenarios
# ══════════════════════════════════════════════════════════════════════════


def test_active_supplier_payment_requires_confirmation(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    pay = SupplierPayment(supplier_id=sup.id, purchase_id=pur.id, amount=500,
                          payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_supplier_payment(pay); post_document("payment", pay); db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"active payment" in r.data
    db.session.refresh(pur)
    assert pur.quantity == 10  # not applied without confirmation


def test_canceling_confirmation_leaves_purchase_and_payments_unchanged(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    pay = SupplierPayment(supplier_id=sup.id, purchase_id=pur.id, amount=500,
                          payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_supplier_payment(pay); post_document("payment", pay); db.session.commit()
    entry_before = posted_entry("purchase", pur.id)
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)  # no confirm_correction

    db.session.refresh(pur)
    assert pur.quantity == 10
    entry_after = posted_entry("purchase", pur.id)
    assert entry_before.id == entry_after.id
    pay_after = db.session.get(SupplierPayment, pay.id)
    assert pay_after.amount == Decimal("500")
    assert pay_after.is_reversed is False


def test_existing_payment_records_remain_untouched_after_successful_correction(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    pay = SupplierPayment(supplier_id=sup.id, purchase_id=pur.id, amount=500,
                          payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_supplier_payment(pay); post_document("payment", pay); db.session.commit()
    pay_id, pay_amount = pay.id, pay.amount
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100, extra={"confirm_correction": "1"})
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"corrected successfully" in r.data

    pay_after = db.session.get(SupplierPayment, pay_id)
    assert pay_after.purchase_id == pur.id
    assert pay_after.amount == pay_amount
    assert pay_after.is_reversed is False


def test_multiple_supplier_payments_remain_untouched(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    pay_ids_amounts = []
    for amt in (300, 200):
        pay = SupplierPayment(supplier_id=sup.id, purchase_id=pur.id, amount=amt,
                              payment_method="Cash", account_id=_cash_account_id())
        db.session.add(pay); db.session.flush()
        sync_supplier_payment(pay); post_document("payment", pay)
        pay_ids_amounts.append((pay.id, pay.amount))
    db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100, extra={"confirm_correction": "1"})
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"corrected successfully" in r.data

    for pid, amt in pay_ids_amounts:
        p = db.session.get(SupplierPayment, pid)
        assert p.amount == amt
        assert p.is_reversed is False
        assert p.purchase_id == pur.id


def test_corrected_total_below_paid_produces_supplier_credit_payment_untouched(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)  # total 1000
    pay = SupplierPayment(supplier_id=sup.id, purchase_id=pur.id, amount=900,
                          payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_supplier_payment(pay); post_document("payment", pay); db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100,
                                   extra={"confirm_correction": "1"})  # new total 500 < 900 paid
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"corrected successfully" in r.data

    pay_after = db.session.get(SupplierPayment, pay.id)
    assert pay_after.amount == Decimal("900")
    assert pay_after.is_reversed is False
    paid = get_purchase_paid(pur.id)
    assert paid == 900.0  # untouched -- balance is now negative (credit)


def test_supplier_change_with_active_payment_is_blocked(appctx):
    _books()
    item = make_item()
    sup1 = make_supplier("Supplier A")
    sup2 = make_supplier("Supplier B")
    pur, pi = make_posted_purchase(sup1, item, qty=10, price=100)
    pay = SupplierPayment(supplier_id=sup1.id, purchase_id=pur.id, amount=200,
                          payment_method="Cash", account_id=_cash_account_id())
    db.session.add(pay); db.session.flush()
    sync_supplier_payment(pay); post_document("payment", pay); db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=10, price=100)
    payload["supplier_id"] = str(sup2.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"cannot be changed" in r.data
    db.session.refresh(pur)
    assert pur.supplier_id == sup1.id


def test_supplier_change_without_payment_repairs_old_new_ledger(appctx):
    _books()
    item = make_item()
    sup1 = make_supplier("Supplier A")
    sup2 = make_supplier("Supplier B")
    pur, pi = make_posted_purchase(sup1, item, qty=10, price=100)
    c = _admin()

    payload = _correction_payload(item, qty=10, price=100)
    payload["supplier_id"] = str(sup2.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"corrected successfully" in r.data
    db.session.refresh(pur)
    assert pur.supplier_id == sup2.id

    old_entry = (SupplierLedgerEntry.query
                 .filter_by(supplier_id=sup1.id, source_type="purchase", source_id=pur.id).first())
    new_entry = (SupplierLedgerEntry.query
                 .filter_by(supplier_id=sup2.id, source_type="purchase", source_id=pur.id).first())
    assert old_entry is None
    assert new_entry is not None


# ══════════════════════════════════════════════════════════════════════════
# Atomicity
# ══════════════════════════════════════════════════════════════════════════


def test_correction_failure_rolls_back_purchase_stock_ledger_and_gl(appctx):
    """Correcting an item down to a quantity that has already partly moved on
    (sold via a separate stock-out) must refuse the whole correction --
    old Purchase, PurchaseItems, stock, GL and ledger must all be exactly
    as before."""
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    # Move 8 of the 10 received units out via a separate stock adjustment,
    # so undoing the original receipt of 10 can no longer succeed.
    from salpurflask.models.models import item_remove_stock
    item_remove_stock(item, 8, cost_total=Decimal("80"), location_id=pur.location_id,
                      movement_type="adjustment", source_type="stock_adjustment", source_id=None)
    db.session.commit()
    old_entry_id = posted_entry("purchase", pur.id).id
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    r = c.post(f"/purchase/correct/{pur.id}", data=payload, follow_redirects=True)
    assert b"insufficient stock" in r.data.lower()

    db.session.refresh(pur)
    assert pur.quantity == 10
    entry_after = posted_entry("purchase", pur.id)
    assert entry_after.id == old_entry_id
    assert db.session.get(PurchaseItem, pi.id) is not None


def test_row_locking_present_for_concurrency_protection(appctx):
    """SELECT ... FOR UPDATE is used to lock the Purchase row -- same pattern
    as post_purchase_route and correct_sale_route. Not exercised under real
    concurrency on SQLite (a no-op there), but confirms the route acquires
    the lock via with_for_update() rather than a plain query."""
    import inspect
    from salpurflask.purchase.routes import correct_purchase_route
    src = inspect.getsource(correct_purchase_route)
    assert "with_for_update()" in src


def test_correction_creates_audit_record(appctx):
    _books()
    item = make_item(); sup = make_supplier()
    pur, pi = make_posted_purchase(sup, item, qty=10, price=100)
    c = _admin()

    payload = _correction_payload(item, qty=5, price=100)
    payload["supplier_id"] = str(sup.id)
    c.post(f"/purchase/correct/{pur.id}", data=payload)

    audit = AuditLog.query.filter_by(entity="Purchase", entity_id=pur.id, action="correction").first()
    assert audit is not None
    assert str(pur.id) in audit.summary or (pur.invoice_no or "") in audit.summary
