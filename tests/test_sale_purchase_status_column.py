"""Phase 1 of the Draft -> Posted workflow: Sale.status / Purchase.status
foundation. Nothing yet reads or branches on this column -- these tests only
lock in that it exists, defaults existing/new rows to "posted", accepts
"draft", and that the migrate_database() ALTER TABLE backfills old rows
missing the column to "posted" without touching anything else.
"""
import sqlite3
import tempfile
import os
from datetime import date
from decimal import Decimal

from app import (
    app as flask_app, db, migrate_database,
    Item, Supplier, Customer, Purchase, Sale,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    post_item_opening, sync_supplier_opening, sync_customer_opening,
)
from salpurflask.models.models import STATUS_DRAFT, STATUS_POSTED, SALE_PURCHASE_STATUSES
from salpurflask.models.business_config import BusinessCategory


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    db.session.commit()


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


def _customer(name="Customer A"):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


# ── 1/2: the constant and its two supported values ──────────────────────────


def test_status_constant_has_exactly_draft_and_posted():
    assert set(SALE_PURCHASE_STATUSES) == {"draft", "posted"}
    assert STATUS_DRAFT == "draft"
    assert STATUS_POSTED == "posted"


# ── 3: Sale supports both values ─────────────────────────────────────────────


def test_sale_defaults_to_posted(appctx):
    _books()
    cust = _customer()
    sal = Sale(customer_id=cust.id, date=date(2026, 1, 1))
    db.session.add(sal); db.session.commit()
    assert sal.status == STATUS_POSTED


def test_sale_accepts_draft(appctx):
    _books()
    cust = _customer()
    sal = Sale(customer_id=cust.id, date=date(2026, 1, 1), status=STATUS_DRAFT)
    db.session.add(sal); db.session.commit()
    db.session.refresh(sal)
    assert sal.status == STATUS_DRAFT


# ── 4: Purchase supports both values ─────────────────────────────────────────


def test_purchase_defaults_to_posted(appctx):
    _books()
    sup = _supplier()
    item = _item()
    pur = Purchase(supplier_id=sup.id, item_id=item.id, quantity=1,
                    purchase_price=Decimal("10"), date=date(2026, 1, 1))
    db.session.add(pur); db.session.commit()
    assert pur.status == STATUS_POSTED


def test_purchase_accepts_draft(appctx):
    _books()
    sup = _supplier()
    item = _item()
    pur = Purchase(supplier_id=sup.id, item_id=item.id, quantity=1,
                    purchase_price=Decimal("10"), date=date(2026, 1, 1),
                    status=STATUS_DRAFT)
    db.session.add(pur); db.session.commit()
    db.session.refresh(pur)
    assert pur.status == STATUS_DRAFT


# ── 5: existing/default behavior is unchanged -- real creation routes still
#      produce "posted" rows, since nothing yet passes status= explicitly ──


def test_purchase_created_via_the_real_route_is_posted(appctx):
    """Uses the actual /purchase form route. At the time this test was
    written (Phase 1) it proved Phase 1 alone changed nothing about how a
    normal Purchase is created. Phase 3 deliberately changed that: /purchase
    now creates a Draft (status="draft", no invoice number, no stock/ledger/
    GL effect) -- see tests/test_draft_purchase.py for the full Draft
    contract this route now has to uphold."""
    from app import pwd_context, User

    _books()
    sup = _supplier()
    item = _item()
    u = User(name="M", email="m@t.com", password=pwd_context.hash("secret123"),
             verified=True, role="manager")
    db.session.add(u); db.session.commit()

    client = flask_app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True

    client.post("/purchase", data={
        "supplier_id": str(sup.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": "5", "purchase_price[]": "10",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0",
    }, follow_redirects=True)

    pur = Purchase.query.order_by(Purchase.id.desc()).first()
    assert pur is not None
    assert pur.status == STATUS_DRAFT


def test_sale_created_via_pos_checkout_is_posted(appctx):
    """Uses the actual /pos/checkout route -- proves Phase 1 changed nothing
    about POS Sale creation either."""
    import json
    from app import pwd_context, User, FinancialAccount, seed_financial_account_links

    _books()
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()

    item = _item()
    u = User(name="M", email="m@t.com", password=pwd_context.hash("secret123"),
             verified=True, role="manager")
    db.session.add(u); db.session.commit()

    client = flask_app.test_client()
    client.post("/signin", data={"email": "m@t.com", "password": "secret123"})

    cash_id = FinancialAccount.query.filter_by(name="Cash").first().id
    resp = client.post("/pos/checkout", data=json.dumps({
        "items": [{"item_id": item.id, "qty": 2, "price": 20}],
        "account_id": cash_id, "amount_paid": 40,
    }), content_type="application/json")
    assert resp.status_code == 200

    sal = Sale.query.order_by(Sale.id.desc()).first()
    assert sal is not None
    assert sal.status == STATUS_POSTED


# ── 6: migration backfills pre-existing rows to "posted" ────────────────────


def test_migrate_database_backfills_status_on_a_database_missing_the_column():
    """Simulates an existing deployment's database: purchase/sale tables exist
    (with the real full schema, minus the status column) and have rows in
    them, as if Phase 1 hasn't been deployed there yet. Runs the real
    migrate_database() against it, out-of-process (SQLAlchemy caches an
    engine per Flask app at first use, so swapping the URI in this same
    process wouldn't actually retarget it) and asserts every pre-existing row
    reads back as "posted", exactly like a row created before is_reversed
    existed reads back as is_reversed=False."""
    import subprocess
    import sys

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        # Build the real schema (via create_all, same as any first boot) in a
        # throwaway app instance, then drop just the status column to emulate
        # a database from before this migration existed.
        setup_uri = "sqlite:///" + tmp.name.replace("\\", "/")
        setup_env = dict(os.environ)
        setup_env["DATABASE_URL"] = setup_uri
        subprocess.run(
            [sys.executable, "-c",
             "from app import app, db\n"
             "with app.app_context():\n"
             "    db.create_all()\n"],
            env=setup_env, check=True, capture_output=True, text=True, cwd=os.getcwd(),
        )

        conn = sqlite3.connect(tmp.name)
        conn.execute("ALTER TABLE purchase RENAME TO purchase_old")
        cols = [row[1] for row in conn.execute("PRAGMA table_info(purchase_old)") if row[1] != "status"]
        conn.execute(f"CREATE TABLE purchase AS SELECT {', '.join(cols)} FROM purchase_old")
        conn.execute("DROP TABLE purchase_old")

        conn.execute("ALTER TABLE sale RENAME TO sale_old")
        cols_sale = [row[1] for row in conn.execute("PRAGMA table_info(sale_old)") if row[1] != "status"]
        conn.execute(f"CREATE TABLE sale AS SELECT {', '.join(cols_sale)} FROM sale_old")
        conn.execute("DROP TABLE sale_old")

        conn.execute("INSERT INTO supplier (id, name, contact, address, opening_balance) "
                     "VALUES (1, 'S', 'x', 'x', 0)")
        conn.execute(
            "INSERT INTO item (id, name, unit, item_type, opening_stock, stock, "
            "reorder_level, inventory_value, default_tax_percent, is_taxable, "
            "purchase_price, sale_price) "
            "VALUES (1, 'I', 'Pcs', 'STOCK', 5, 5, 0, 50, 0, 1, 10, 20)")
        conn.execute("INSERT INTO customer (id, name, contact, address, opening_balance) "
                     "VALUES (1, 'C', 'x', 'x', 0)")
        conn.execute("INSERT INTO purchase (id, supplier_id, item_id, quantity, purchase_price, date) "
                     "VALUES (1, 1, 1, 5, 10.0, '2026-01-01 00:00:00')")
        conn.execute("INSERT INTO sale (id, customer_id, date) VALUES (1, 1, '2026-01-01 00:00:00')")
        conn.commit()
        conn.close()

        assert "status" not in [row[1] for row in sqlite3.connect(tmp.name)
                                 .execute("PRAGMA table_info(purchase)")]

        migrate_env = dict(os.environ)
        migrate_env["DATABASE_URL"] = setup_uri
        result = subprocess.run(
            [sys.executable, "-c",
             "from app import app, migrate_database\n"
             "with app.app_context():\n"
             "    migrate_database()\n"],
            env=migrate_env, capture_output=True, text=True, cwd=os.getcwd(),
        )
        assert result.returncode == 0, result.stderr

        conn2 = sqlite3.connect(tmp.name)
        cols = [row[1] for row in conn2.execute("PRAGMA table_info(purchase)")]
        assert "status" in cols
        row = conn2.execute("SELECT status FROM purchase WHERE id = 1").fetchone()
        assert row[0] == "posted"

        cols_sale = [row[1] for row in conn2.execute("PRAGMA table_info(sale)")]
        assert "status" in cols_sale
        row_sale = conn2.execute("SELECT status FROM sale WHERE id = 1").fetchone()
        assert row_sale[0] == "posted"
        conn2.close()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
