"""Phase 3 of the Draft -> Posted workflow: a Purchase created through the
normal /purchase route now starts as a Draft. A Draft must have zero
accounting/stock/supplier-ledger impact -- no invoice number, no
JournalEntry, no supplier ledger entry, no payable/cost effect, no stock
increase, no StockMovement -- while still creating correct PurchaseItems
with the existing discount/tax/quantity math.

PO -> Purchase conversion is audited but deliberately left unchanged (see
the Phase 3 report): the PurchaseOrder itself already serves the pre-
commitment/Draft role, and converting it ("Received") represents goods
that have actually arrived -- a completed real-world event, mirroring the
Quotation -> Sale decision from Phase 2. This file adds a conversion smoke
test confirming that decision holds.

Sale is untouched in this phase; test_draft_sale.py and the rest of the
Sale suite already cover it and are not touched here.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Supplier, Purchase, PurchaseItem, SupplierPayment,
    JournalEntry, SupplierLedgerEntry, StockMovement,
    get_total_payable, get_supplier_balance, total_supplier_ledger_balance,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening, sync_supplier_opening,
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


def _supplier(name="Supplier A"):
    s = Supplier(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


class _RoleClient:
    """Flask-Login caches the resolved user on flask.g per app context, not
    per request -- see test_draft_sale.py's identical helper for the full
    rationale."""
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


def _purchase_via_form(client, supplier, item, qty, price, disc_type="percent",
                        disc_value="0", tax="0"):
    return client.post("/purchase", data={
        "supplier_id": str(supplier.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": str(qty), "purchase_price[]": str(price),
        "discount_type[]": disc_type, "discount_value[]": disc_value, "tax_percent[]": tax,
    }, follow_redirects=True)


def _latest_purchase():
    return Purchase.query.order_by(Purchase.id.desc()).first()


# ── Draft creation ───────────────────────────────────────────────────────────


def test_normal_purchase_creation_produces_a_draft(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    _purchase_via_form(client, sup, item, 3, 10)

    pur = _latest_purchase()
    assert pur is not None
    assert pur.status == STATUS_DRAFT


def test_draft_purchase_has_no_invoice_number(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    _purchase_via_form(client, sup, item, 3, 10)

    pur = _latest_purchase()
    assert pur.invoice_no is None


# ── Accounting isolation ─────────────────────────────────────────────────────


def test_draft_purchase_creates_no_journal_entry(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    je_before = JournalEntry.query.count()
    _purchase_via_form(client, sup, item, 3, 10)

    assert JournalEntry.query.count() == je_before


def test_draft_purchase_creates_no_supplier_ledger_entry(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    entries_before = SupplierLedgerEntry.query.filter_by(supplier_id=sup.id).count()
    _purchase_via_form(client, sup, item, 3, 10)

    assert SupplierLedgerEntry.query.filter_by(supplier_id=sup.id).count() == entries_before


def test_draft_purchase_does_not_increase_payable(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    balance_before = get_supplier_balance(sup.id)
    ledger_before = total_supplier_ledger_balance()
    _purchase_via_form(client, sup, item, 3, 10)

    assert get_supplier_balance(sup.id) == balance_before
    assert total_supplier_ledger_balance() == ledger_before


def test_draft_purchase_does_not_affect_purchase_cost_totals(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    cost_before = get_total_payable()
    _purchase_via_form(client, sup, item, 3, 10)

    assert get_total_payable() == cost_before


# ── Inventory isolation ──────────────────────────────────────────────────────


def test_draft_purchase_does_not_increase_item_stock(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()

    _purchase_via_form(client, sup, item, 5, 10)

    db.session.refresh(item)
    assert item.stock == 50


def test_draft_purchase_creates_no_stock_movement(appctx):
    _books()
    sup = _supplier()
    item = _item(stock=50)
    client = _manager()

    movements_before = StockMovement.query.filter_by(item_id=item.id).count()
    _purchase_via_form(client, sup, item, 5, 10)

    assert StockMovement.query.filter_by(item_id=item.id).count() == movements_before


# ── Purchase data integrity ──────────────────────────────────────────────────


def test_draft_purchase_still_creates_purchase_items(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    _purchase_via_form(client, sup, item, 4, 25)

    pur = _latest_purchase()
    items = PurchaseItem.query.filter_by(purchase_id=pur.id).all()
    assert len(items) == 1
    assert items[0].item_id == item.id


def test_draft_purchase_quantity_is_correct(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    _purchase_via_form(client, sup, item, 7, 10)

    pur = _latest_purchase()
    pi = PurchaseItem.query.filter_by(purchase_id=pur.id).first()
    assert pi.quantity == 7


def test_draft_purchase_discount_calculation_is_correct(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    # 10 units @ 10 = 100 gross, 10% discount -> 10 discount, 90 net
    _purchase_via_form(client, sup, item, 10, 10, disc_type="percent", disc_value="10")

    pur = _latest_purchase()
    pi = PurchaseItem.query.filter_by(purchase_id=pur.id).first()
    assert float(pi.discount_amount) == 10.0
    assert float(pi.amount) == 90.0


def test_draft_purchase_tax_calculation_is_correct(appctx):
    _books()
    sup = _supplier()
    item = _item()
    client = _manager()

    # 10 units @ 10 = 100 gross, 5% tax -> 5 tax, 105 net (no discount)
    _purchase_via_form(client, sup, item, 10, 10, tax="5")

    pur = _latest_purchase()
    pi = PurchaseItem.query.filter_by(purchase_id=pur.id).first()
    assert float(pi.tax_amount) == 5.0
    assert float(pi.amount) == 105.0


# ── PO -> Purchase conversion (audited decision) ─────────────────────────────


def test_po_to_purchase_conversion_remains_posted_unchanged(appctx):
    """PO -> Purchase conversion was audited and deliberately left unchanged
    in Phase 3: the PurchaseOrder itself already serves the pre-commitment/
    Draft role (PO_STATUSES includes "Draft"), and converting it to
    "Received" represents goods that have actually arrived -- a completed
    real-world event, exactly like the Quotation -> Sale decision in Phase
    2. This locks in that the resulting Purchase still posts immediately."""
    from app import PurchaseOrder, PurchaseOrderItem

    _books()
    sup = _supplier()
    item = _item(stock=20)
    client = _admin()

    po = PurchaseOrder(supplier_id=sup.id, notes="", status="Draft")
    db.session.add(po); db.session.flush()
    db.session.add(PurchaseOrderItem(
        po_id=po.id, item_id=item.id, quantity=5, purchase_price=10,
        discount_type="percent", discount_value=0, discount_amount=0,
        tax_percent=0, tax_amount=0, unit_name="Pcs", unit_factor=1,
    ))
    db.session.commit()

    client.post(f"/purchase_orders/{po.id}/convert", data={"purchase_date": "2026-01-01"},
               follow_redirects=True)

    pur = _latest_purchase()
    assert pur is not None
    assert pur.status == STATUS_POSTED
    assert pur.invoice_no is not None
    db.session.refresh(item)
    assert item.stock == 25


# ── Draft edit/delete compatibility (minimal, see Phase 3 report) ──────────


def test_draft_purchase_can_be_deleted_without_corrupting_stock(appctx):
    """delete_purchase's stock-removal loop assumed every deleted Purchase
    had already added stock at creation -- true before Phase 3, false for a
    Draft. Guarded by status so deleting a never-stocked Draft does not
    decrement phantom stock."""
    _books()
    sup = _supplier()
    item = _item(stock=50)
    manager = _manager()
    admin_client = _admin()

    _purchase_via_form(manager, sup, item, 5, 10)
    pur = _latest_purchase()
    assert pur.status == STATUS_DRAFT

    admin_client.post(f"/purchase/delete/{pur.id}", follow_redirects=True)

    db.session.refresh(item)
    assert item.stock == 50
    assert db.session.get(Purchase, pur.id) is None


def test_draft_purchase_can_be_edited_without_corrupting_stock(appctx):
    """edit_purchase's remove-old/re-add-new stock dance assumed the
    purchase being edited had already added its old lines' stock -- also
    false for a Draft. Guarded the same way as delete: editing a Draft
    changes its lines without ever touching Item.stock."""
    _books()
    sup = _supplier()
    item = _item(stock=50)
    manager = _manager()

    _purchase_via_form(manager, sup, item, 5, 10)
    pur = _latest_purchase()
    assert pur.status == STATUS_DRAFT

    manager.post(f"/purchase/edit/{pur.id}", data={
        "supplier_id": str(sup.id), "date": "2026-01-02", "notes": "edited",
        "item_id[]": str(item.id), "quantity[]": "8", "purchase_price[]": "10",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)

    db.session.refresh(item)
    assert item.stock == 50
    db.session.refresh(pur)
    assert pur.status == STATUS_DRAFT
    pi = PurchaseItem.query.filter_by(purchase_id=pur.id).first()
    assert pi.quantity == 8
