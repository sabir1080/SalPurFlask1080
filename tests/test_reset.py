"""Factory reset: the trial data has to go completely before the system changes hands,
and what is left has to be a working, empty install — not a stripped one."""
from datetime import datetime
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Supplier, Customer, Category, Item, Purchase, Sale, FinancialAccount,
    Account, JournalEntry, DocumentSequence, FiscalYear, AccountingPeriod,
    AuditLog, TaxCode, FixedAsset,
    FACTORY_RESET_PHRASE, factory_reset,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_account_opening, allocate_document_number,
    accounting_position, total_cash_bank_balance,
)


def _admin(email="a@t.com"):
    db.session.add(User(name="A", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _traded_system():
    """A system with a full trading history behind it — the state a tested app is in."""
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(datetime.now().year)
    db.session.add(FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                                    opening_balance=Decimal("500000")))
    db.session.commit()
    seed_financial_account_links()
    post_account_opening(FinancialAccount.query.filter_by(name="Cash").first())

    sup = Supplier(name="Abc Traders", contact="03000000000", address="x", opening_balance=0)
    cus = Customer(name="Ahmed Brothers", contact="03000000000", address="x", opening_balance=0)
    cat = Category(name="Hardware")
    db.session.add_all([sup, cus, cat])
    db.session.flush()
    item = Item(name="Widget", category_id=cat.id, unit="Pcs", purchase_price=Decimal("100"),
                sale_price=Decimal("250"), stock=0, reorder_level=0, inventory_value=0)
    db.session.add(item)
    db.session.commit()

    c = _admin()
    today = datetime.now().strftime("%Y-%m-%d")
    c.post("/purchase", data={
        "supplier_id": sup.id, "date": today, "notes": "",
        "item_id[]": item.id, "quantity[]": "100", "purchase_price[]": "100",
        "discount_type[]": "", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)
    c.post("/sale", data={
        "customer_id": cus.id, "date": today, "notes": "",
        "item_id[]": item.id, "quantity[]": "40", "sale_price[]": "250",
        "discount_type[]": "", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)
    return c


def test_reset_leaves_no_trace_of_the_trial_data(appctx):
    c = _traded_system()
    assert Sale.query.count() == 1 and JournalEntry.query.count() > 0

    r = c.post("/admin/reset", data={"confirm": FACTORY_RESET_PHRASE}, follow_redirects=True)
    assert r.status_code == 200

    for model in (Supplier, Customer, Item, Category, Purchase, Sale,
                  JournalEntry, FixedAsset, DocumentSequence):
        assert model.query.count() == 0, model.__name__

    # the money is gone from the ledger too, not merely from the lists
    pos = accounting_position(datetime.now())
    assert pos["total_assets"] == 0
    assert pos["total_equity"] == 0
    assert total_cash_bank_balance() == 0
    assert all(a.opening_balance == 0 for a in FinancialAccount.query.all())


def test_reset_leaves_a_working_system_not_a_stripped_one(appctx):
    """Deleting the data is half the job. What is left has to be usable on Monday
    morning: a chart of accounts to post to, an open year to post into, and the cash
    accounts — otherwise the new owner's first invoice fails."""
    c = _traded_system()
    c.post("/admin/reset", data={"confirm": FACTORY_RESET_PHRASE}, follow_redirects=True)

    assert Account.query.count() > 0
    assert TaxCode.query.count() > 0
    assert FiscalYear.query.filter_by(name=str(datetime.now().year)).first() is not None
    assert AccountingPeriod.query.count() == 12
    assert FinancialAccount.query.count() == 4
    assert all(fa.gl_account_id for fa in FinancialAccount.query.all())

    # the administrator is still signed in — wiping users would lock them out mid-request
    assert User.query.count() == 1
    assert c.get("/dashboard").status_code == 200

    # and the reset itself is the first thing the (now empty) audit log says
    assert AuditLog.query.count() == 1
    assert "deleted" in AuditLog.query.first().summary


def test_invoice_numbering_starts_again_from_one(appctx):
    """A sequence left mid-count would hand the new owner INV-2026-000002 as their very
    first invoice."""
    c = _traded_system()
    assert Sale.query.first().invoice_no.endswith("000001")

    c.post("/admin/reset", data={"confirm": FACTORY_RESET_PHRASE}, follow_redirects=True)

    assert allocate_document_number("sale", datetime.now()).endswith("000001")


def test_the_wrong_phrase_deletes_nothing(appctx):
    c = _traded_system()
    before = (Supplier.query.count(), Sale.query.count(), JournalEntry.query.count())

    for phrase in ("", "delete all data", "DELETE ALL DAT", "yes"):
        r = c.post("/admin/reset", data={"confirm": phrase}, follow_redirects=True)
        assert "Nothing was deleted" in r.get_data(as_text=True)

    assert (Supplier.query.count(), Sale.query.count(),
            JournalEntry.query.count()) == before


def test_only_an_admin_can_reset(appctx):
    """Signs in nobody but the manager. Flask-Login caches the signed-in user on `g`,
    which is bound to the *app* context, and the test client reuses the one this fixture
    is holding — so an admin signed in earlier in the same test would still be the
    current user inside the manager's request, and this would pass while proving
    nothing."""
    db.session.add(Supplier(name="Abc Traders", contact="03000000000", address="x",
                            opening_balance=0))
    db.session.add(User(name="M", email="m@t.com", password=pwd_context.hash("secret123"),
                        verified=True, role="manager"))
    db.session.commit()

    c = flask_app.test_client()
    c.post("/signin", data={"email": "m@t.com", "password": "secret123"})
    with c.session_transaction() as s:
        assert db.session.get(User, int(s["_user_id"])).role == "manager"

    r = c.post("/admin/reset", data={"confirm": FACTORY_RESET_PHRASE}, follow_redirects=True)
    assert "do not have permission" in r.get_data(as_text=True)
    assert Supplier.query.count() == 1        # a manager cannot wipe the company
