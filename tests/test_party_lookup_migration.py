"""Targeted lookup migration — Purchase Order's Item selector, and every
Customer/Supplier <select> in transactional forms. See
static/js/party_lookup.js (new) and static/js/item_lookup.js (existing).

The critical regression this guards: none of these pages may embed the
complete Customer/Supplier/Item master list just because they were opened.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context, Customer, Supplier,
    seed_chart_of_accounts, seed_fiscal_year, post_item_opening,
)
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory


def _login(role="manager", email=None):
    email = email or f"{role}@t.com"
    u = User(name=role.capitalize(), email=email, password=pwd_context.hash("secret123"),
            verified=True, role=role)
    db.session.add(u)
    db.session.commit()
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True
    return c


def _category(slug="garments-apparel"):
    cat = BusinessCategory.query.filter_by(slug=slug).first()
    if cat is None:
        cat = BusinessCategory(name=slug, slug=slug, is_enabled=True)
        db.session.add(cat)
        db.session.commit()
    return cat


def _bulk_parties(n):
    customers, suppliers = [], []
    for i in range(n):
        c = Customer(name=f"Bulk Customer {i:04d}", contact="0300", address="X", opening_balance=0)
        s = Supplier(name=f"Bulk Supplier {i:04d}", contact="0300", address="X", opening_balance=0)
        db.session.add(c)
        db.session.add(s)
        customers.append(c)
        suppliers.append(s)
    db.session.commit()
    return customers, suppliers


def _bulk_items(n, cat):
    items = []
    for i in range(n):
        it = Item(name=f"Bulk PO Item {i:04d}", business_category_id=cat.id, unit="Pcs",
                 purchase_price=Decimal("10"), sale_price=Decimal("20"),
                 stock=10, opening_stock=0, reorder_level=5)
        db.session.add(it)
        items.append(it)
    db.session.commit()
    return items


# ── Purchase Order item selector ────────────────────────────────────────────

def test_purchase_orders_page_does_not_embed_full_item_catalogue(appctx):
    cat = _category()
    items = _bulk_items(120, cat)
    client = _login("manager")
    html = client.get("/purchase_orders").get_data(as_text=True)
    assert "ITEM_UNITS" not in html
    assert items[0].name not in html
    assert items[-1].name not in html
    assert "item-picker-btn" in html
    assert "ItemLookup.open" in html


def test_purchase_order_can_still_be_created_after_migration(appctx):
    cat = _category()
    it = Item(name="PO Item", business_category_id=cat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             stock=0, opening_stock=0, reorder_level=5)
    db.session.add(it)
    supp = Supplier(name="PO Supplier", contact="0300", address="X", opening_balance=0)
    db.session.add(supp)
    db.session.commit()

    client = _login("manager")
    resp = client.post("/purchase_orders", data={
        "supplier_id": str(supp.id), "order_date": "2026-01-15",
        "item_id[]": [str(it.id)], "quantity[]": ["3"], "purchase_price[]": ["10"],
        "discount_type[]": ["percent"], "discount_value[]": ["0"],
        "tax_percent[]": ["0"], "unit_id[]": [""],
    })
    assert resp.status_code == 302
    from salpurflask.models.models import PurchaseOrder
    po = PurchaseOrder.query.filter_by(supplier_id=supp.id).first()
    assert po is not None
    assert po.line_items[0].item_id == it.id
    assert po.line_items[0].quantity == 3


# ── No-full-master-list regression across every migrated Customer/Supplier
#    selector ───────────────────────────────────────────────────────────────

def test_sale_page_does_not_embed_full_customer_list(appctx):
    customers, _ = _bulk_parties(120)
    client = _login("manager")
    html = client.get("/sale").get_data(as_text=True)
    assert customers[0].name not in html
    assert "PartyLookup.open" in html


def test_purchase_page_does_not_embed_full_supplier_list(appctx):
    _, suppliers = _bulk_parties(120)
    client = _login("manager")
    html = client.get("/purchase").get_data(as_text=True)
    assert suppliers[0].name not in html
    assert "PartyLookup.open" in html


def test_purchase_orders_page_does_not_embed_full_supplier_list(appctx):
    _, suppliers = _bulk_parties(120)
    client = _login("manager")
    html = client.get("/purchase_orders").get_data(as_text=True)
    assert suppliers[0].name not in html


def test_customer_receipt_page_does_not_embed_full_customer_list(appctx):
    customers, _ = _bulk_parties(120)
    client = _login("manager")
    html = client.get("/customer_receipt").get_data(as_text=True)
    assert customers[0].name not in html
    assert "PartyLookup.open" in html


def test_supplier_payment_page_does_not_embed_full_supplier_list(appctx):
    _, suppliers = _bulk_parties(120)
    client = _login("manager")
    html = client.get("/supplier_payment").get_data(as_text=True)
    assert suppliers[0].name not in html
    assert "PartyLookup.open" in html


def test_quotations_page_does_not_embed_full_customer_list(appctx):
    customers, _ = _bulk_parties(120)
    client = _login("manager")
    html = client.get("/quotations").get_data(as_text=True)
    assert customers[0].name not in html
    assert "PartyLookup.open" in html


def test_customer_bulk_receipt_page_does_not_embed_full_customer_list(appctx):
    customers, _ = _bulk_parties(120)
    client = _login("manager")
    html = client.get("/customer_bulk_receipt").get_data(as_text=True)
    assert customers[0].name not in html
    assert "PartyLookup.open" in html


def test_supplier_bulk_payment_page_does_not_embed_full_supplier_list(appctx):
    _, suppliers = _bulk_parties(120)
    client = _login("manager")
    html = client.get("/supplier_bulk_payment").get_data(as_text=True)
    assert suppliers[0].name not in html
    assert "PartyLookup.open" in html


def test_pos_page_does_not_embed_full_customer_or_item_list(appctx):
    customers, _ = _bulk_parties(60)
    cat = _category()
    items = _bulk_items(60, cat)
    client = _login("manager")
    html = client.get("/pos").get_data(as_text=True)
    assert customers[0].name not in html
    assert items[0].name not in html
    assert "PartyLookup.open" in html


def test_party_lookup_js_loaded_on_all_migrated_pages(appctx):
    client = _login("manager")
    for path in ("/sale", "/purchase", "/purchase_orders", "/customer_receipt",
                "/supplier_payment", "/quotations", "/customer_bulk_receipt",
                "/supplier_bulk_payment", "/pos"):
        html = client.get(path).get_data(as_text=True)
        assert "party_lookup.js" in html, f"{path} did not load party_lookup.js"


# ── Selected party/item correctly reaches the form (backend still works) ────

def test_customer_receipt_can_still_be_recorded_after_migration(appctx):
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    from app import FinancialAccount
    acct = FinancialAccount(name="Cash", method="Cash", account_type="Cash", opening_balance=0)
    db.session.add(acct)
    db.session.commit()

    cust = Customer(name="Receipt Customer", contact="0300", address="X", opening_balance=1000)
    db.session.add(cust)
    db.session.commit()

    client = _login("manager")
    resp = client.post("/customer_receipt", data={
        "customer_id": str(cust.id), "amount": "500", "payment_date": "2026-01-15",
        "payment_method": "Cash", "account_id": str(acct.id),
    })
    assert resp.status_code == 302
    from app import CustomerPayment
    payment = CustomerPayment.query.filter_by(customer_id=cust.id).first()
    assert payment is not None
    assert float(payment.amount) == 500.0


def test_supplier_payment_can_still_be_recorded_after_migration(appctx):
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    from app import FinancialAccount
    acct = FinancialAccount(name="Cash", method="Cash", account_type="Cash", opening_balance=0)
    db.session.add(acct)
    db.session.commit()

    supp = Supplier(name="Payment Supplier", contact="0300", address="X", opening_balance=1000)
    db.session.add(supp)
    db.session.commit()

    client = _login("manager")
    resp = client.post("/supplier_payment", data={
        "supplier_id": str(supp.id), "amount": "500", "payment_date": "2026-01-15",
        "payment_method": "Cash", "account_id": str(acct.id),
    })
    assert resp.status_code == 302
    from app import SupplierPayment
    payment = SupplierPayment.query.filter_by(supplier_id=supp.id).first()
    assert payment is not None
    assert float(payment.amount) == 500.0


def test_quotation_can_still_be_created_with_selected_customer(appctx):
    cat = _category()
    it = Item(name="Quotable Item 2", business_category_id=cat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             stock=10, opening_stock=10, reorder_level=5)
    db.session.add(it)
    cust = Customer(name="Quote Customer 2", contact="0300", address="X", opening_balance=0)
    db.session.add(cust)
    db.session.commit()

    client = _login("manager")
    resp = client.post("/quotations", data={
        "customer_id": str(cust.id), "quote_date": "2026-01-15",
        "item_id[]": [str(it.id)], "quantity[]": ["1"], "sale_price[]": ["20"],
        "discount_type[]": ["percent"], "discount_value[]": ["0"],
        "tax_percent[]": ["0"], "unit_id[]": [""],
    })
    assert resp.status_code == 302
    from salpurflask.models.models import Quotation
    q = Quotation.query.filter_by(customer_id=cust.id).first()
    assert q is not None
    assert q.line_items[0].item_id == it.id


# ── Unchanged selectors (confidence check — these were intentionally left
#    alone, they are small reference-data dropdowns, not master-data lists) ──

def test_payment_method_dropdown_unaffected(appctx):
    """Sanity check: PAYMENT_METHODS is a small fixed tuple, not a migrated
    master-data selector — confirms the migration didn't touch it."""
    client = _login("manager")
    html = client.get("/customer_receipt").get_data(as_text=True)
    assert "Cash" in html and "Bank" in html
