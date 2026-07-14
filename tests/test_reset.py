"""Factory reset: the trial data has to go completely before the system changes hands,
and what is left has to be a working, empty install — not a stripped one."""
from datetime import datetime
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Supplier, Customer, Category, Item, Purchase, Sale, FinancialAccount,
    Account, JournalEntry, DocumentSequence, FiscalYear, AccountingPeriod,
    AuditLog, TaxCode, FixedAsset,
    FACTORY_RESET_PHRASE, TRANSACTION_RESET_PHRASE, factory_reset, reset_transactions,
    _KEEP_ON_FACTORY_RESET, _KEEP_ON_TRANSACTION_RESET, _KEEP_ON_SEED,
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
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()
    cash = FinancialAccount.query.filter_by(name="Cash").first()
    cash.opening_balance = Decimal("500000")
    post_account_opening(cash)

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


def test_transaction_reset_keeps_the_parties_and_items(appctx):
    c = _traded_system()
    assert Sale.query.count() == 1

    r = c.post("/admin/reset_transactions",
               data={"confirm": TRANSACTION_RESET_PHRASE}, follow_redirects=True)
    assert r.status_code == 200

    # what the client asked to keep
    assert Supplier.query.one().name == "Abc Traders"
    assert Customer.query.one().name == "Ahmed Brothers"
    assert Item.query.one().name == "Widget"
    assert Category.query.count() == 1

    # and what they asked to go
    for model in (Purchase, Sale, JournalEntry, FixedAsset, DocumentSequence):
        assert model.query.count() == 0, model.__name__


def test_transaction_reset_clears_the_opening_balances_it_kept_the_parties_for(appctx):
    """The easy half is deleting the rows. An opening balance is not a static field —
    it is *posted*, so leaving it on the supplier would leave a payable in the accounts
    with no supplier ledger underneath it. Item stock is the same: the Inventory account
    goes with the journals, so any stock left on an item contradicts it at once."""
    c = _traded_system()
    sup = Supplier.query.one()
    sup.opening_balance = Decimal("75000")
    db.session.commit()

    item = Item.query.one()
    assert item.stock == 60                      # 100 bought, 40 sold

    c.post("/admin/reset_transactions",
           data={"confirm": TRANSACTION_RESET_PHRASE}, follow_redirects=True)

    db.session.expire_all()
    assert Supplier.query.one().opening_balance == 0
    assert Customer.query.one().opening_balance == 0
    assert all(fa.opening_balance == 0 for fa in FinancialAccount.query.all())

    item = Item.query.one()
    assert item.stock == 0
    assert item.inventory_value == 0

    # and the ledger agrees: nothing owed, nothing owned, nothing owing
    pos = accounting_position(datetime.now())
    assert pos["total_assets"] == 0
    assert pos["total_liabilities"] == 0
    assert pos["total_equity"] == 0
    assert total_cash_bank_balance() == 0


def test_transaction_reset_leaves_a_system_that_can_still_trade(appctx):
    """Emptying it is no good if nothing can be posted afterwards."""
    c = _traded_system()
    c.post("/admin/reset_transactions",
           data={"confirm": TRANSACTION_RESET_PHRASE}, follow_redirects=True)

    assert Account.query.count() > 0
    assert AccountingPeriod.query.count() == 12
    assert not any(p.is_closed for p in AccountingPeriod.query.all())
    assert FinancialAccount.query.count() == 4
    assert allocate_document_number("sale", datetime.now()).endswith("000001")

    # buy and sell again, on the suppliers and items that were kept
    sup, item, cus = Supplier.query.one(), Item.query.one(), Customer.query.one()
    today = datetime.now().strftime("%Y-%m-%d")
    c.post("/purchase", data={
        "supplier_id": sup.id, "date": today, "notes": "",
        "item_id[]": item.id, "quantity[]": "10", "purchase_price[]": "100",
        "discount_type[]": "", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)

    db.session.expire_all()
    assert Item.query.one().stock == 10
    assert Purchase.query.one().invoice_no.endswith("000001")


def test_a_closed_year_is_reopened_or_nothing_could_ever_post_again(appctx):
    """Closing a year is undone by the reset that deleted its closing entry. Left closed,
    the books would be sealed against a business that has not traded yet."""
    c = _traded_system()
    fy = FiscalYear.query.first()
    fy.is_closed = True
    for p in fy.periods:
        p.is_closed = True
    db.session.commit()

    c.post("/admin/reset_transactions",
           data={"confirm": TRANSACTION_RESET_PHRASE}, follow_redirects=True)

    db.session.expire_all()
    assert not FiscalYear.query.first().is_closed
    assert not any(p.is_closed for p in AccountingPeriod.query.all())


def test_the_wrong_phrase_deletes_no_transactions_either(appctx):
    c = _traded_system()
    before = (Sale.query.count(), JournalEntry.query.count(), Item.query.one().stock)

    for phrase in ("", "DELETE ALL DATA", "delete all transactions"):
        r = c.post("/admin/reset_transactions", data={"confirm": phrase},
                   follow_redirects=True)
        assert "Nothing was deleted" in r.get_data(as_text=True)

    db.session.expire_all()
    assert (Sale.query.count(), JournalEntry.query.count(),
            Item.query.one().stock) == before


def test_the_keep_lists_name_tables_that_actually_exist(appctx):
    """Every wipe in this app names what it *keeps* and deletes the rest, which fails safe:
    a table nobody remembered is deleted, not silently preserved. The one thing that can
    still go wrong is a typo in the keep list — "expense_categories" instead of
    "expense_category" would not error, it would just quietly delete the thing it was
    written to protect.
    """
    real = {t.name for t in db.metadata.sorted_tables}
    for label, keep in (("factory", _KEEP_ON_FACTORY_RESET),
                        ("transaction", _KEEP_ON_TRANSACTION_RESET),
                        ("seed", _KEEP_ON_SEED)):
        unknown = keep - real
        assert not unknown, f"{label} reset keeps tables that do not exist: {sorted(unknown)}"


def test_nothing_that_holds_money_survives_a_wipe(appctx):
    """The CLI wipes used to carry hand-written lists of models to delete, and both had
    rotted: fixed assets, their depreciation charges and the invoice-number counter were
    added long afterwards and appeared in neither. They survived a wipe that claimed to be
    total — which is how a freshly seeded demo ended up holding six delivery vans."""
    for table in ("fixed_asset", "depreciation_charge", "document_sequence",
                  "journal_entry", "journal_line", "purchase", "sale"):
        assert table not in _KEEP_ON_TRANSACTION_RESET, table
        assert table not in _KEEP_ON_SEED, table


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
