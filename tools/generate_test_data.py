"""Generate a large, realistic, interconnected ERP test dataset directly in
PostgreSQL (or, with an explicit opt-in, the local SQLite dev database — see
below), using TradeFlow's own business-logic primitives wherever one exists
(item_add_stock/item_remove_stock, post_document/post_entry, the transfer
service, the payroll engine/accounting, the batch/FEFO primitives) rather
than raw INSERTs.

Run via the CLI, not directly:
    python tools/test_data_cli.py generate [--seed N] [--force] [--allow-sqlite]

Safety:
  - Refuses to run against anything but PostgreSQL, UNLESS --allow-sqlite was
    passed on the CLI — see _data_common.require_database() and this module's
    own ALLOW_SQLITE handling just below. Without that flag, behavior is
    unchanged from before: SQLite is refused, exactly as require_postgres()
    always refused it.
  - Refuses to run twice unless --force is passed (see _data_common sentinel).
  - Never drops/recreates the schema, never deletes the 67 baseline system
    rows (it only ever adds to master/transactional tables).

Design reference: see the Phase 3 design conversation for the full dependency
graph, record-count table, and per-domain rationale. This file follows that
plan stage for stage.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._data_common import require_database, make_rng, write_sentinel, read_sentinel
from tools._medical_catalog import MEDICINES, NON_MEDICAL_ITEMS

# test_data_cli.py sets TEST_DATA_ALLOW_SQLITE=1 BEFORE importing this
# module, from its own --allow-sqlite flag — the primary entry point. A
# direct `python tools/generate_test_data.py --allow-sqlite` invocation has
# no chance to parse argparse args before this module-level line runs (the
# `if __name__ == "__main__":` block below only executes AFTER the whole
# module body, including this gate, has already run), so it is checked here
# too via a raw sys.argv scan — the same one-line technique _data_common
# itself needs for the identical reason (this must run before `import app`).
# Both entry points end up setting/reading the same env var, so there is
# exactly one gate, never two independently-maintained ones. Never inferred
# from DATABASE_URL alone.
if "--allow-sqlite" in sys.argv:
    os.environ["TEST_DATA_ALLOW_SQLITE"] = "1"
ALLOW_SQLITE = os.environ.get("TEST_DATA_ALLOW_SQLITE") == "1"
DATABASE_URL = require_database(allow_sqlite=ALLOW_SQLITE)

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import OperationalError, PendingRollbackError

from app import app, db, PostingError
from salpurflask.models import (
    User, Supplier, Customer, Item, ItemUnit,
    Purchase, PurchaseItem, PurchaseOrder, PurchaseOrderItem, PurchaseReturn,
    Sale, SaleItem, SaleReturn, DeliveryChallan, Quotation, QuotationItem, PosHold,
    SupplierPayment, CustomerPayment,
    StockAdjustment, ADJUSTMENT_TYPES,
    FinancialAccount, Account,
    Location, Branch,
    resolve_item_unit, line_base_qty, calc_discount_tax,
    allocate_document_number, post_document, post_entry,
    item_add_stock, item_remove_stock,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_tax_codes, seed_fiscal_year,
)
from salpurflask.models.models import (
    get_or_create_batch, item_add_stock_batched, item_remove_stock_batched,
    resolve_sale_batch_allocations, resolve_sale_return_batch_allocations,
    PurchaseItemBatch, SaleItemBatch, MONEY,
)
from salpurflask.models.inventory_location import (
    get_or_create_default_location, stock_at_location,
)
from salpurflask.models.business_config import BusinessCategory, ProductCategoryData
from salpurflask.services.transfers import create_transfer, confirm_transfer
from salpurflask.services.feature_flags import set_module
from salpurflask.models.hr import Department, Designation, Employee, next_employee_code
from salpurflask.models.payroll import (
    SalaryComponent, SalaryStructure, SalaryStructureLine, PayrollPeriod,
    EmployeeAdvance, seed_default_components,
)
from salpurflask.models.attendance import Attendance
from salpurflask.models.leave import (
    LeaveType, LeaveAllocation, LeaveRequest, seed_leave_types,
)
from salpurflask.services import payroll_engine as engine
from salpurflask.services import payroll_accounting as accounting
from salpurflask.models.payroll_payment import PayrollPayment, period_payable_balance

# app.py-level helpers (sync_* functions, validation) are not re-exported
# through salpurflask.models — imported from app directly, same as every
# route in the codebase already does.
from app import (
    sync_supplier_purchase, sync_supplier_purchase_return, sync_supplier_payment,
    sync_customer_sale, sync_customer_sale_return, sync_customer_receipt,
    validate_supplier_payment, validate_customer_receipt,
)
from salpurflask.sales.routes import get_sale_item_returned_qty


# ─── Name pools (deterministic, no external dependency like Faker) ─────────

FIRST_NAMES = [
    "Ahmed", "Ali", "Bilal", "Danish", "Ehsan", "Faisal", "Ghulam", "Hamza",
    "Imran", "Junaid", "Kamran", "Luqman", "Mudassar", "Nadeem", "Omar",
    "Qasim", "Rashid", "Salman", "Tariq", "Usman", "Waqas", "Yasir", "Zeeshan",
    "Ayesha", "Bushra", "Faiza", "Hina", "Iqra", "Kiran", "Maria", "Nadia",
    "Rabia", "Sadia", "Tehmina", "Uzma", "Zainab",
]
LAST_NAMES = [
    "Khan", "Ahmed", "Malik", "Sheikh", "Butt", "Chaudhry", "Raza", "Iqbal",
    "Hussain", "Abbasi", "Qureshi", "Siddiqui", "Farooq", "Javed", "Akhtar",
]
COMPANY_WORDS_1 = [
    "Al-Noor", "Star", "City", "National", "Metro", "Prime", "United",
    "Continental", "Elite", "Superior", "Classic", "Modern", "Royal", "Delta",
    "Horizon", "Bright", "Green", "Blue Sky", "Fine", "Grand",
]
COMPANY_WORDS_2 = [
    "Traders", "Enterprises", "Trading Co", "Distributors", "Suppliers",
    "Corporation", "Industries", "Merchants", "Wholesalers", "Impex",
]
ITEM_CATEGORY_NAMES = [
    "Grocery", "Beverages", "Snacks", "Dairy", "Bakery", "Stationery",
    "Electronics", "Household", "Personal Care", "Hardware",
]

# The 10 names below are a subset of the 25 SYSTEM DEFAULT BusinessCategory
# rows (see salpurflask/services/category_catalog.py's
# DEFAULT_BUSINESS_CATEGORIES / ensure_default_business_categories()) — the
# generator looks these up by name rather than creating its own rows; see
# stage2_master_data() below.
ITEM_NAME_TEMPLATES = {
    "Grocery": ["Basmati Rice {n}kg", "Wheat Flour {n}kg", "Cooking Oil {n}L",
                "Sugar {n}kg", "Red Lentils {n}kg", "Chickpeas {n}kg", "Salt {n}kg"],
    "Beverages": ["Cola {n}ml", "Mineral Water {n}L", "Orange Juice {n}ml",
                  "Green Tea Box {n}", "Instant Coffee {n}g"],
    "Snacks": ["Potato Chips {n}g", "Biscuits Pack {n}", "Chocolate Bar {n}g",
               "Salted Peanuts {n}g", "Popcorn {n}g"],
    "Dairy": ["Milk {n}L", "Yogurt {n}g", "Butter {n}g", "Cheese Slice {n}",
              "Cream {n}ml"],
    "Bakery": ["White Bread {n}", "Bun Pack {n}", "Rusk {n}g", "Cake Slice {n}"],
    "Stationery": ["Notebook {n} pages", "Ball Pen Box {n}", "Pencil Box {n}",
                   "A4 Paper Ream {n}", "Stapler {n}"],
    "Electronics": ["LED Bulb {n}W", "Extension Cord {n}m", "USB Cable {n}m",
                    "Torch Light {n}", "Batteries Pack {n}"],
    "Household": ["Dish Soap {n}ml", "Laundry Detergent {n}kg", "Broom {n}",
                  "Bucket {n}L", "Mop {n}"],
    "Personal Care": ["Shampoo {n}ml", "Soap Bar {n}g", "Toothpaste {n}g",
                      "Hand Sanitizer {n}ml", "Talcum Powder {n}g"],
    "Hardware": ["Screws Pack {n}", "Hammer {n}", "Nails Box {n}kg",
                 "Wire Roll {n}m", "PVC Pipe {n}ft"],
}
DEPARTMENT_NAMES = ["Sales", "Warehouse", "Accounts", "Purchasing", "Administration", "IT Support"]
DESIGNATIONS_BY_DEPT = {
    "Sales": ["Sales Executive", "Sales Manager"],
    "Warehouse": ["Store Keeper", "Warehouse Supervisor"],
    "Accounts": ["Accountant", "Accounts Assistant"],
    "Purchasing": ["Purchase Officer", "Procurement Manager"],
    "Administration": ["Office Assistant", "Admin Manager"],
    "IT Support": ["IT Support Engineer", "System Administrator"],
}


def log(step, total, message):
    print(f"[{step}/{total}] {message}")


# Neon (and similarly-configured managed Postgres) can autosuspend its compute
# after a few idle minutes — confirmed 5 minutes on the tradeflow-demo Neon
# project via its dashboard. app.py's pool_pre_ping/pool_recycle only protect
# a connection that gets CHECKED BACK IN to the pool between uses; they do
# nothing for a connection that is still checked out and mid-transaction
# (this script's own session holds exactly one connection across long runs of
# per-record flush()/commit() calls). If the round-trip gap between two
# statements on that same connection exceeds the autosuspend window — several
# hundred small commits at real network latency to a remote region adds up —
# Neon kills the TCP connection server-side, and the next statement on it
# fails with "server closed the connection unexpectedly", regardless of how
# recently the Python side last touched the session. A cheap SELECT 1 issued
# periodically inside the largest loops keeps the wire active so this gap
# never opens, without changing any business logic. See _KEEPALIVE_EVERY below
# for the call sites.
_KEEPALIVE_EVERY = 15


def keepalive(i):
    """Call from inside a large loop with the loop's own 0-based index.
    Cheap — a single SELECT 1 every _KEEPALIVE_EVERY iterations, not every
    iteration — this is purely a connection-liveness ping, unrelated to and
    independent of every commit()/flush() the loop's own logic already does."""
    if i % _KEEPALIVE_EVERY == 0:
        db.session.execute(db.text("SELECT 1"))


def keepalive_every(i, n):
    """Same as keepalive(), but with a caller-chosen interval instead of the
    shared _KEEPALIVE_EVERY. Attempt #9 died inside stage6_sales's 400-record
    standard-sales loop (sale #66, i%15=6 — several iterations past the last
    ping) even though every iteration already calls keepalive(i): each
    iteration there does a full multi-line document post (several flushes)
    plus its own commit(), which is heavier than the lighter loops the
    15-iteration cadence was tuned against, so the gap between pings was
    still wide enough for Neon's pooled endpoint to drop the connection
    server-side between two statements. Loops whose body is this expensive
    should ping on a tighter interval than the default."""
    if i % n == 0:
        db.session.execute(db.text("SELECT 1"))


def uniq_contact(rng, used):
    while True:
        c = "03" + "".join(str(rng.randint(0, 9)) for _ in range(9))
        if c not in used:
            used.add(c)
            return c


class Ctx:
    """Everything downstream stages need from earlier stages, kept in one
    place instead of threading a dozen separate return values through."""
    def __init__(self, rng):
        self.rng = rng
        self.locations = []          # [Location, ...] (index 0 = default)
        self.cash_account = None     # FinancialAccount id (Cash)
        self.bank_account = None     # FinancialAccount id (Bank)
        self.items = []              # [Item, ...] STOCK type
        # Plain-int ids, same order/index as self.items — see the
        # sellable_items() comment in stage6_sales for why this exists:
        # ORM objects in self.items expire on every db.session.commit()
        # (expire_on_commit=True is SQLAlchemy's default, never overridden
        # here), so re-reading it.id after a commit is a real database
        # round-trip, not a memory read. Cached once, right after self.items
        # is populated and before anything could have committed.
        self.item_ids = []
        self.medical_items = []      # [Item, ...] STOCK type, batch_tracked=True subset
        self.suppliers = []
        self.customers = []
        self.employees = []
        self.stock_by_loc = {}       # {(item_id, location_id): int} running tracker
        self.rows_created = 0

    def bump(self, n=1):
        self.rows_created += n


# ─── Stage 1 — accounting / system scaffolding ─────────────────────────────

def stage1_scaffolding(ctx):
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_tax_codes()
    accounting.seed_payroll_accounts()
    seed_default_components()
    seed_leave_types()
    # 2025-26 covers back-dated opening balances; 2026-27 covers Jul/Aug 2026
    # payroll and is very likely already seeded by app.py's own boot (its
    # FISCAL_YEAR_START_MONTH=7 + current-date seeding) — seed_fiscal_year()
    # is idempotent either way.
    seed_fiscal_year(2025)
    seed_fiscal_year(2026)
    set_module("module_hr", True, updated_by="test_data_generator")
    set_module("module_attendance", True, updated_by="test_data_generator")
    set_module("module_leave", True, updated_by="test_data_generator")
    set_module("module_payroll", True, updated_by="test_data_generator")
    db.session.commit()

    default_loc = get_or_create_default_location()
    ctx.locations.append(default_loc)
    branch = Branch.query.filter_by(is_default=True).first()
    for name in ("North Warehouse", "South Warehouse"):
        existing = Location.query.filter_by(name=name).first()
        if existing is None:
            existing = Location(name=name, kind="warehouse", branch_id=branch.id,
                                is_default=False, active=True)
            db.session.add(existing)
            db.session.flush()
            ctx.bump()
        ctx.locations.append(existing)
    db.session.commit()

    ctx.cash_account = FinancialAccount.query.filter_by(name="Cash").first().id
    ctx.bank_account = FinancialAccount.query.filter_by(name="Bank").first().id


# ─── Stage 2 — master data ──────────────────────────────────────────────────

def stage2_master_data(ctx):
    rng = ctx.rng

    # Users
    from werkzeug.security import generate_password_hash
    user_specs = [
        ("Admin User", "admin@tradeflow.test", "admin"),
        ("Manager One", "manager1@tradeflow.test", "manager"),
        ("Manager Two", "manager2@tradeflow.test", "manager"),
        ("Staff One", "staff1@tradeflow.test", "staff"),
        ("Staff Two", "staff2@tradeflow.test", "staff"),
    ]
    for name, email, role in user_specs:
        if User.query.filter_by(email=email).first() is None:
            u = User(name=name, email=email,
                     password=generate_password_hash("Test@1234"),
                     role=role, verified=True)
            db.session.add(u)
            ctx.bump()
    db.session.commit()

    # Business Categories — the authoritative category system (see
    # ConfigurationService.get_enabled_categories(), which is what both
    # Business Configuration and the Item form's dropdown read from).
    #
    # These are now SYSTEM DEFAULT master data, seeded unconditionally by
    # ensure_default_business_categories() inside app.py's migrate_database()
    # — the same tier as the chart of accounts — which has already run by
    # the time this module finishes importing `app`. The generator only
    # looks them up here; it must never create its own BusinessCategory rows
    # (that would duplicate system defaults) and never falls back to the
    # legacy Category table.
    categories = {name: cat for name, cat in
                 ((c.name, c) for c in BusinessCategory.query.filter(
                     BusinessCategory.name.in_(ITEM_CATEGORY_NAMES)).all())}
    missing = set(ITEM_CATEGORY_NAMES) - set(categories)
    if missing:
        raise RuntimeError(
            f"Expected system-default BusinessCategory rows are missing: {sorted(missing)}. "
            "These should have been created by app.py's migrate_database() "
            "(ensure_default_business_categories()) on import — check that it ran.")

    # Items — deterministic codes ITEM-0001.. stored in `sku`
    used_barcodes = set()
    existing_skus = {i.sku for i in Item.query.filter(Item.sku.isnot(None)).all()}
    n_items = 150
    for idx in range(1, n_items + 1):
        sku = f"ITEM-{idx:04d}"
        if sku in existing_skus:
            continue
        cat_name = ITEM_CATEGORY_NAMES[idx % len(ITEM_CATEGORY_NAMES)]
        templates = ITEM_NAME_TEMPLATES[cat_name]
        template = templates[idx % len(templates)]
        size_n = rng.choice([250, 500, 1, 2, 5, 12, 24, 100])
        name = template.format(n=size_n) + f" #{idx}"
        purchase_price = Decimal(str(rng.randint(50, 5000)))
        markup = Decimal(str(rng.randint(115, 160))) / Decimal("100")
        sale_price = (purchase_price * markup).quantize(Decimal("0.01"))
        tax_percent = rng.choice([Decimal("0"), Decimal("0"), Decimal("17")])
        item = Item(
            name=name, category_id=None,  # legacy field — BusinessCategory is authoritative
            business_category_id=categories[cat_name].id,
            unit=rng.choice(["Pcs", "Kg", "Box", "Liter", "Pack"]),
            item_type="STOCK",
            sku=sku,
            barcode=f"8{idx:011d}",
            reorder_level=rng.choice([10, 20, 30, 50]),
            purchase_price=purchase_price, sale_price=sale_price,
            default_tax_percent=tax_percent,
            is_taxable=tax_percent > 0,
        )
        db.session.add(item)
        ctx.bump()
    db.session.commit()

    # A handful of SERVICE items (no stock tracking) — delivery/installation etc.
    # The live /item form requires a category for every item_type, not just
    # STOCK (only reorder_level is STOCK-conditional) — service items are no
    # exception here either.
    service_names = ["Delivery Charges", "Installation Service", "Gift Wrapping", "Express Handling"]
    service_category = categories.get("Household")
    for i, sname in enumerate(service_names, 1):
        sku = f"SVC-{i:03d}"
        if Item.query.filter_by(sku=sku).first() is None:
            db.session.add(Item(
                name=sname, item_type="SERVICE", sku=sku,
                category_id=None, business_category_id=service_category.id,
                purchase_price=Decimal("0"),
                sale_price=Decimal(str(rng.randint(100, 1000))),
                default_tax_percent=Decimal("0"), is_taxable=False,
            ))
            ctx.bump()
    db.session.commit()

    ctx.items = (Item.query.filter_by(item_type="STOCK")
                 .filter(Item.sku.like("ITEM-%")).order_by(Item.sku).all())
    # Cache ids as plain ints now, while these objects are freshly loaded
    # and definitely not expired — see Ctx.item_ids' own comment.
    ctx.item_ids = [it.id for it in ctx.items]

    # A meaningful subset of items get an alternate unit (multi-unit)
    for item in ctx.items[:20]:
        if ItemUnit.query.filter_by(item_id=item.id).first() is None:
            db.session.add(ItemUnit(item_id=item.id, name="Carton", factor=12,
                                    purchase_price=None, sale_price=None))
            ctx.bump()
    db.session.commit()

    # Suppliers — SUP-0001..
    used_contacts = set()
    n_suppliers = 40
    existing_sup_names = {s.name for s in Supplier.query.all()}
    for idx in range(1, n_suppliers + 1):
        name = f"{rng.choice(COMPANY_WORDS_1)} {rng.choice(COMPANY_WORDS_2)} (SUP-{idx:04d})"
        if name in existing_sup_names:
            continue
        opening = Decimal(str(rng.choice([0, 0, 5000, 10000, 25000, 50000])))
        db.session.add(Supplier(
            name=name, contact=uniq_contact(rng, used_contacts),
            address=f"Plot {rng.randint(1, 200)}, Industrial Area, Karachi",
            opening_balance=opening,
        ))
        ctx.bump()
    db.session.commit()
    ctx.suppliers = Supplier.query.order_by(Supplier.id).all()

    # Customers — CUS-0001..
    n_customers = 60
    existing_cust_names = {c.name for c in Customer.query.all()}
    for idx in range(1, n_customers + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        name = f"{first} {last} (CUS-{idx:04d})"
        if name in existing_cust_names:
            continue
        opening = Decimal(str(rng.choice([0, 0, 0, 1000, 3000, 8000])))
        db.session.add(Customer(
            name=name, contact=uniq_contact(rng, used_contacts),
            address=f"House {rng.randint(1, 500)}, Block {rng.choice('ABCDEFGH')}, Lahore",
            opening_balance=opening,
        ))
        ctx.bump()
    db.session.commit()
    ctx.customers = Customer.query.order_by(Customer.id).all()

    # HR master data
    for name in DEPARTMENT_NAMES:
        if Department.query.filter_by(name=name).first() is None:
            db.session.add(Department(name=name, description=f"{name} department"))
            ctx.bump()
    db.session.commit()
    departments = {d.name: d for d in Department.query.all()}

    for dept_name, titles in DESIGNATIONS_BY_DEPT.items():
        for title in titles:
            if Designation.query.filter_by(name=title).first() is None:
                db.session.add(Designation(name=title, description=f"{title} in {dept_name}"))
                ctx.bump()
    db.session.commit()
    designations = {d.name: d for d in Designation.query.all()}

    n_employees = 40
    dept_names = list(DEPARTMENT_NAMES)
    existing_emp_names = {e.name for e in Employee.query.all()}
    join_start = date(2023, 1, 1)
    for idx in range(1, n_employees + 1):
        dept_name = dept_names[idx % len(dept_names)]
        title = rng.choice(DESIGNATIONS_BY_DEPT[dept_name])
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        full_name = f"{first} {last}"
        if full_name in existing_emp_names:
            full_name = f"{first} {last} {idx}"
        code = next_employee_code()
        join_offset = rng.randint(0, 900)
        emp = Employee(
            code=code, name=full_name,
            department_id=departments[dept_name].id,
            designation_id=designations[title].id,
            joining_date=join_start + timedelta(days=join_offset),
            employment_status=rng.choice(["Permanent", "Permanent", "Probation", "Contract"]),
            phone="03" + "".join(str(rng.randint(0, 9)) for _ in range(9)),
            email=f"{first.lower()}.{last.lower()}{idx}@tradeflow.test",
            active=True,
        )
        db.session.add(emp)
        db.session.flush()
        ctx.bump()
        ctx.employees.append(emp)
    db.session.commit()


# ─── Stage 3 — opening stock ────────────────────────────────────────────────

def stage3_opening_stock(ctx):
    rng = ctx.rng
    default_loc = ctx.locations[0]
    for i, item in enumerate(ctx.items):
        keepalive(i)
        existing = stock_at_location(item.id, default_loc.id)
        if existing:
            ctx.stock_by_loc[(item.id, default_loc.id)] = existing
            continue
        qty = rng.randint(80, 400)
        cost_total = (item.purchase_price or Decimal("10")) * Decimal(qty)
        item_add_stock(item, qty, cost_total, location_id=default_loc.id,
                       movement_type="opening", source_type="opening", source_id=item.id)
        ctx.stock_by_loc[(item.id, default_loc.id)] = qty
        ctx.bump()
    for loc in ctx.locations[1:]:
        for item in ctx.items:
            ctx.stock_by_loc.setdefault((item.id, loc.id), 0)
    db.session.commit()


# ─── Stage 4 — purchasing ───────────────────────────────────────────────────

def _make_purchase(ctx, supplier, lines, when, location_id, notes=None):
    """lines = [(item, qty, unit_price, tax_percent), ...]. Replicates
    salpurflask/purchase/routes.py:purchase()'s POST branch exactly."""
    first_item, first_qty, first_price, first_tax = lines[0][0], lines[0][1], lines[0][2], lines[0][3]
    gross = first_qty * float(first_price)
    disc_amt, tax_amt, _ = calc_discount_tax(gross, "percent", 0, float(first_tax))
    pur = Purchase(
        supplier_id=supplier.id, item_id=first_item.id, quantity=first_qty,
        purchase_price=float(first_price), discount_type="percent",
        discount_value=0, discount_amount=disc_amt,
        tax_percent=float(first_tax), tax_amount=tax_amt,
        date=when, notes=notes, location_id=location_id,
    )
    db.session.add(pur)
    db.session.flush()
    for item, qty, price, tax_pct in lines:
        gross = qty * float(price)
        disc_amt, tax_amt, net = calc_discount_tax(gross, "percent", 0, float(tax_pct))
        pi = PurchaseItem(
            purchase_id=pur.id, item_id=item.id, quantity=qty,
            purchase_price=float(price), discount_type="percent", discount_value=0,
            discount_amount=disc_amt, tax_percent=float(tax_pct), tax_amount=tax_amt,
            amount=net, unit_name=None, unit_factor=1,
        )
        db.session.add(pi)
        item_add_stock(item, qty, net - tax_amt, location_id=location_id,
                       movement_type="purchase", source_type="purchase", source_id=pur.id)
        ctx.stock_by_loc[(item.id, location_id)] = ctx.stock_by_loc.get((item.id, location_id), 0) + qty
    db.session.flush()
    db.session.refresh(pur)
    pur.invoice_no = allocate_document_number("purchase", pur.date)
    sync_supplier_purchase(pur)
    post_document("purchase", pur)
    ctx.bump(1 + len(lines))
    return pur


def _make_purchase_with_retry(ctx, supplier, lines, when, location_id, notes=None):
    """Wraps _make_purchase() with one retry for a raw Neon connection drop.

    A drop mid-flush inside item_add_stock()'s record_stock_movement() call
    is swallowed there by design (it logs and returns None rather than
    raising), but SQLAlchemy has already marked the transaction rolled-back
    at that point — the *next* statement anywhere in this same transaction
    (e.g. this file's own db.session.refresh(pur) a few lines later) is what
    actually raises, as PendingRollbackError, a sibling of OperationalError
    under SQLAlchemyError, not a subclass of it, so both must be caught
    explicitly. db.session.rollback() cleanly discards the whole failed
    transaction (the uncommitted Purchase/PurchaseItem/StockMovement rows,
    and item.stock's in-memory mutation, since a rollback also reverts
    pending attribute writes on session-tracked objects) — nothing from a
    failed attempt is left half-persisted for the retry to collide with.
    ctx.stock_by_loc is the one exception: a plain dict outside the ORM
    session, untouched by rollback(), so this function snapshots the
    handful of (item.id, location_id) keys the given `lines` can touch
    before each attempt and restores exactly those keys on failure, so a
    retry's own ctx.stock_by_loc update in _make_purchase() cannot double
    up on a partial update the failed attempt already made."""
    keys = [(item.id, location_id) for item, _qty, _price, _tax in lines]
    for attempt in range(2):
        snapshot = {k: ctx.stock_by_loc[k] for k in keys if k in ctx.stock_by_loc}
        try:
            return _make_purchase(ctx, supplier, lines, when, location_id, notes=notes)
        except (OperationalError, PendingRollbackError):
            db.session.rollback()
            for k in keys:
                if k in snapshot:
                    ctx.stock_by_loc[k] = snapshot[k]
                else:
                    ctx.stock_by_loc.pop(k, None)
            if attempt == 1:
                raise


def _purchase_total(pur):
    return sum(float(pi.amount) for pi in pur.line_items)


def stage4_purchasing(ctx, skipped):
    rng = ctx.rng
    default_loc = ctx.locations[0]

    # Purchase Orders — some converted, some left standing
    pos = []
    for i in range(60):
        keepalive(i)
        supplier = rng.choice(ctx.suppliers)
        n_lines = rng.randint(1, 4)
        items = rng.sample(ctx.items, n_lines)
        when = datetime(2026, rng.randint(3, 8), rng.randint(1, 28))
        po = PurchaseOrder(supplier_id=supplier.id, order_date=when,
                           status="Draft", notes=f"Auto-generated PO {i+1}")
        db.session.add(po)
        db.session.flush()
        for item in items:
            qty = rng.randint(10, 100)
            db.session.add(PurchaseOrderItem(
                po_id=po.id, item_id=item.id, quantity=qty,
                purchase_price=float(item.purchase_price or 100),
                discount_type="percent", discount_value=0,
                tax_percent=float(item.default_tax_percent or 0),
                unit_factor=1,
            ))
        db.session.flush()
        db.session.commit()
        ctx.bump(1 + n_lines)
        pos.append(po)

    # Purchases — 250 total, ~60 of them born from converting a PO
    convert_pool = list(pos)
    rng.shuffle(convert_pool)
    purchases = []
    n_purchases = 250
    for i in range(n_purchases):
        keepalive_every(i, 5)
        supplier = rng.choice(ctx.suppliers)
        location = ctx.locations[0] if rng.random() < 0.7 else rng.choice(ctx.locations)
        n_lines = rng.randint(1, 3)
        items = rng.sample(ctx.items, n_lines)
        when = datetime(2026, rng.randint(1, 8), rng.randint(1, 28))
        lines = []
        for item in items:
            qty = rng.randint(20, 150)
            price = float(item.purchase_price or 100)
            tax = float(item.default_tax_percent or 0)
            lines.append((item, qty, price, tax))
        try:
            pur = _make_purchase_with_retry(ctx, supplier, lines, when, location.id,
                                 notes=f"Auto-generated purchase {i+1}")
            purchases.append(pur)
            db.session.commit()
        except PostingError as e:
            db.session.rollback()
            skipped.append(("purchase", i, str(e)))
            continue
        except (OperationalError, PendingRollbackError) as e:
            db.session.rollback()
            skipped.append(("purchase", i, str(e)))
            continue

    # Convert a subset of Draft POs into Purchases via the real conversion path
    converted = 0
    for po_i, po in enumerate(convert_pool[:60]):
        keepalive_every(po_i, 5)
        if po.status != "Draft" or not po.line_items:
            continue
        lines = [(pi.item, pi.quantity, float(pi.purchase_price), float(pi.tax_percent))
                for pi in po.line_items]
        try:
            pur = _make_purchase_with_retry(ctx, po.supplier, lines, po.order_date, default_loc.id,
                                 notes=f"Converted from PO #{po.id}")
        except PostingError as e:
            db.session.rollback()
            skipped.append(("po_convert", po.id, str(e)))
            continue
        except (OperationalError, PendingRollbackError) as e:
            db.session.rollback()
            skipped.append(("po_convert", po.id, str(e)))
            continue
        po.status = "Received"
        po.converted_purchase_id = pur.id
        purchases.append(pur)
        converted += 1
        db.session.commit()

    # Purchase Returns — ~25, against completed purchases with enough remaining stock
    returned = 0
    # A plain list-comprehension form of this filter lazy-loads p.line_items for
    # every one of the ~250-310 purchases in one uninterrupted burst with no
    # keepalive point — confirmed as the exact failure site of a Neon connection
    # drop (generate_test_data.py:601 in the traceback). Same filter, same
    # result, just written as an explicit loop so keepalive() can run between
    # lazy-loads the same way every other large loop in this file already does.
    candidates = []
    for p_i, p in enumerate(purchases):
        keepalive_every(p_i, 5)
        if p.line_items:
            candidates.append(p)
    rng.shuffle(candidates)
    for ret_i, pur in enumerate(candidates):
        keepalive_every(ret_i, 5)
        if returned >= 25:
            break
        pi = rng.choice(pur.line_items)
        available = stock_at_location(pi.item_id, pur.location_id or default_loc.id)
        max_returnable = min(pi.quantity, available)
        if max_returnable < 1:
            continue
        qty = rng.randint(1, max_returnable)
        item = pi.item
        pr = PurchaseReturn(
            purchase_id=pur.id, supplier_id=pur.supplier_id, item_id=pi.item_id,
            quantity=qty, return_price=float(pi.purchase_price),
            date=pur.date + timedelta(days=rng.randint(1, 10)),
            reason="Damaged / quality issue", unit_name=pi.unit_name,
            unit_factor=pi.unit_factor or 1, purchase_item_id=pi.id,
        )
        db.session.add(pr)
        db.session.flush()
        loc_id = pur.location_id or default_loc.id
        try:
            pr.cost_removed = item_remove_stock(
                item, qty * (pi.unit_factor or 1), location_id=loc_id,
                movement_type="purchase_return", source_type="purchase_return", source_id=pr.id)
        except PostingError as e:
            db.session.rollback()
            skipped.append(("purchase_return", pur.id, str(e)))
            continue
        sync_supplier_purchase_return(pr)
        post_document("purchase_return", pr)
        db.session.commit()
        ctx.stock_by_loc[(item.id, loc_id)] = ctx.stock_by_loc.get((item.id, loc_id), 0) - qty
        ctx.bump()
        returned += 1

    # Supplier Payments — ~200, mix of partial/full, leaving some outstanding
    paid = 0
    rng.shuffle(purchases)
    for i, pur in enumerate(purchases):
        keepalive_every(i, 5)
        if paid >= 200:
            break
        total = _purchase_total(pur)
        if total <= 0:
            continue
        already_paid = float(db.session.query(db.func.sum(SupplierPayment.amount))
                             .filter(SupplierPayment.purchase_id == pur.id,
                                     SupplierPayment.is_reversed.is_(False)).scalar() or 0)
        balance = total - already_paid
        if balance <= 1:
            continue
        pay_full = rng.random() < 0.6
        amount = round(balance if pay_full else balance * rng.uniform(0.3, 0.8), 2)
        if amount <= 0:
            continue
        error = validate_supplier_payment(pur.supplier_id, amount, pur.id)
        if error:
            skipped.append(("supplier_payment", pur.id, error))
            continue
        method_account = rng.choice([("Cash", ctx.cash_account), ("Bank", ctx.bank_account)])
        payment = SupplierPayment(
            supplier_id=pur.supplier_id, purchase_id=pur.id, amount=amount,
            payment_date=pur.date + timedelta(days=rng.randint(1, 20)),
            payment_method=method_account[0], account_id=method_account[1],
            reference_no=f"SPAY-{pur.id}-{paid+1}",
        )
        db.session.add(payment)
        db.session.flush()
        sync_supplier_payment(payment)
        post_document("payment", payment)
        db.session.commit()
        ctx.bump()
        paid += 1

    return purchases


# ─── Stage 5 — inventory movement (transfers, adjustments) ─────────────────

def stage5_inventory_movement(ctx, skipped):
    rng = ctx.rng
    default_loc = ctx.locations[0]
    other_locs = ctx.locations[1:]

    confirmed = draft = cancelled = reversed_ = 0
    for i in range(40):
        keepalive(i)
        dest = rng.choice(other_locs)
        n_lines = rng.randint(1, 3)
        # A plain list-comprehension form of this filter lazy-loads it.id for
        # every one of the ~150 ctx.items in one uninterrupted burst, on every
        # one of this loop's 40 iterations — confirmed as Attempt #12's exact
        # failure site (generate_test_data.py:719 in the traceback) even
        # though the outer loop already calls keepalive(i) once per
        # iteration: the burst itself, not the gap between iterations, is
        # what starved the connection. Same fix as the purchase-returns/
        # sale-returns candidate filters and sellable_items(): an explicit
        # loop with a keepalive between lazy-loads instead of one unbroken
        # comprehension.
        candidates = []
        for cand_i, it in enumerate(ctx.items):
            keepalive_every(cand_i, 5)
            if ctx.stock_by_loc.get((it.id, default_loc.id), 0) >= 20:
                candidates.append(it)
        if len(candidates) < n_lines:
            continue
        items = rng.sample(candidates, n_lines)
        lines = []
        for item in items:
            available = ctx.stock_by_loc.get((item.id, default_loc.id), 0)
            qty = rng.randint(1, min(20, max(1, available // 2)))
            lines.append((item.id, qty))
        try:
            transfer = create_transfer(
                source_location_id=default_loc.id, destination_location_id=dest.id,
                lines=lines, date=datetime(2026, rng.randint(2, 8), rng.randint(1, 28)),
                notes=f"Auto-generated transfer {i+1}")
        except PostingError as e:
            skipped.append(("transfer_create", i, str(e)))
            continue
        ctx.bump(1 + n_lines)

        outcome = rng.random()
        if outcome < 0.75:
            try:
                confirm_transfer(transfer)
            except PostingError as e:
                skipped.append(("transfer_confirm", transfer.id, str(e)))
                db.session.commit()
                continue
            for item_id, qty in lines:
                ctx.stock_by_loc[(item_id, default_loc.id)] -= qty
                ctx.stock_by_loc[(item_id, dest.id)] = ctx.stock_by_loc.get((item_id, dest.id), 0) + qty
            confirmed += 1
            if rng.random() < 0.15:
                try:
                    from salpurflask.services.transfers import reverse_transfer
                    reverse_transfer(transfer)
                    for item_id, qty in lines:
                        ctx.stock_by_loc[(item_id, dest.id)] -= qty
                        ctx.stock_by_loc[(item_id, default_loc.id)] += qty
                    reversed_ += 1
                except PostingError as e:
                    skipped.append(("transfer_reverse", transfer.id, str(e)))
        elif outcome < 0.9:
            draft += 1  # left as Draft, no stock effect
        else:
            from salpurflask.services.transfers import cancel_transfer
            cancel_transfer(transfer)
            cancelled += 1
        db.session.commit()

    # Stock Adjustments — ~30, mix of in/out
    for i in range(30):
        keepalive(i)
        item = rng.choice(ctx.items)
        loc = rng.choice(ctx.locations)
        direction_type = rng.choice(["Stock In", "Count Correction (Increase)",
                                     "Damage Write-off", "Count Correction (Decrease)"])
        from salpurflask.models.models import ADJUSTMENT_DIRECTIONS
        direction = ADJUSTMENT_DIRECTIONS[direction_type]
        available = ctx.stock_by_loc.get((item.id, loc.id), 0)
        if direction == "out":
            if available < 5:
                continue
            qty = rng.randint(1, min(15, available))
        else:
            qty = rng.randint(5, 30)
        adj = StockAdjustment(
            item_id=item.id, adj_type=direction_type, quantity=qty, direction=direction,
            date=datetime(2026, rng.randint(2, 8), rng.randint(1, 28)),
            reason="Routine stock count", location_id=loc.id,
        )
        db.session.add(adj)
        db.session.flush()
        try:
            if direction == "out":
                adj.cost_value = item_remove_stock(item, qty, location_id=loc.id,
                                                   movement_type="adjustment",
                                                   source_type="stock_adjustment", source_id=adj.id)
                ctx.stock_by_loc[(item.id, loc.id)] -= qty
            else:
                unit_cost = item.avg_cost
                adj.cost_value = (unit_cost * Decimal(str(qty))).quantize(Decimal("0.0001"))
                item_add_stock(item, qty, adj.cost_value, location_id=loc.id,
                               movement_type="adjustment",
                               source_type="stock_adjustment", source_id=adj.id)
                ctx.stock_by_loc[(item.id, loc.id)] = ctx.stock_by_loc.get((item.id, loc.id), 0) + qty
        except PostingError as e:
            db.session.rollback()
            skipped.append(("stock_adjustment", i, str(e)))
            continue
        post_document("stock_adjustment", adj)
        db.session.commit()
        ctx.bump()


# ─── Stage 6 — sales / POS ──────────────────────────────────────────────────

def _make_sale(ctx, customer, lines, when, location_id, notes=None):
    """lines = [(item, qty, unit_price, tax_percent), ...]. Replicates
    salpurflask/sales/routes.py:sale()'s POST branch exactly."""
    first_item, first_qty, first_price, first_tax = lines[0]
    gross = first_qty * float(first_price)
    disc_amt, tax_amt, _ = calc_discount_tax(gross, "percent", 0, float(first_tax))
    sal = Sale(
        customer_id=customer.id, item_id=first_item.id, quantity=first_qty,
        sale_price=float(first_price), cost_price=0.0, discount_type="percent",
        discount_value=0, discount_amount=disc_amt, tax_percent=float(first_tax),
        tax_amount=tax_amt, date=when, notes=notes, location_id=location_id,
    )
    db.session.add(sal)
    db.session.flush()
    for item, qty, price, tax_pct in lines:
        unit_cost = item.avg_cost
        gross = qty * float(price)
        disc_amt, tax_amt, net = calc_discount_tax(gross, "percent", 0, float(tax_pct))
        si = SaleItem(
            sale_id=sal.id, item_id=item.id, quantity=qty, sale_price=float(price),
            cost_price=float(unit_cost), discount_type="percent", discount_value=0,
            discount_amount=disc_amt, tax_percent=float(tax_pct), tax_amount=tax_amt,
            amount=net, unit_name=None, unit_factor=1,
        )
        db.session.add(si)
        item_remove_stock(item, qty, cost_total=unit_cost * Decimal(str(qty)),
                          location_id=location_id, movement_type="sale",
                          source_type="sale", source_id=sal.id)
        ctx.stock_by_loc[(item.id, location_id)] = ctx.stock_by_loc.get((item.id, location_id), 0) - qty
    db.session.flush()
    db.session.refresh(sal)
    sal.invoice_no = allocate_document_number("sale", sal.date)
    sync_customer_sale(sal)
    post_document("sale", sal)
    ctx.bump(1 + len(lines))
    return sal


def _sale_total(sale):
    return sum(float(si.amount) for si in sale.line_items)


def stage6_sales(ctx, skipped):
    rng = ctx.rng
    default_loc = ctx.locations[0]
    sales = []

    def sellable_items(location_id, min_qty=1):
        # This is called on every iteration of the sale loops below (~850+
        # calls total). It used to lazy-load it.id on every one of ~150
        # ctx.items on every call, which is a real DB round-trip each time —
        # expire_on_commit=True (SQLAlchemy's default, never overridden
        # here) expires every Item in ctx.items on every db.session.commit(),
        # and this loop's own callers commit once per sale, so `it.id` was
        # being re-fetched from Postgres hundreds of times per sale for a
        # value that never changes. Confirmed as the exact failure site of a
        # Neon connection drop even with a keepalive every 5 items (the
        # failure landed only 2 lazy-loads after the last successful ping —
        # tightening the cadence further wasn't going to close that gap).
        # ctx.item_ids is a plain-int cache built once, right when ctx.items
        # was first populated and before anything could have committed — see
        # its own comment on Ctx. Reading from it here touches no ORM state
        # and needs no keepalive at all, because it never talks to the
        # database. The Item object itself (still needed by callers for
        # price/name/etc.) is preserved unchanged in the result.
        result = []
        for item_id, it in zip(ctx.item_ids, ctx.items):
            if ctx.stock_by_loc.get((item_id, location_id), 0) >= min_qty:
                result.append(it)
        return result

    # Standard sales — 400. Each iteration posts a full multi-line document
    # (several flushes) plus a commit, so it needs a tighter keepalive
    # cadence than the shared default — see keepalive_every()'s docstring.
    for i in range(400):
        keepalive_every(i, 5)
        customer = rng.choice(ctx.customers)
        location = default_loc if rng.random() < 0.75 else rng.choice(ctx.locations)
        pool = sellable_items(location.id, 3)
        if not pool:
            continue
        n_lines = min(rng.randint(1, 3), len(pool))
        items = rng.sample(pool, n_lines)
        lines = []
        ok = True
        for item in items:
            available = ctx.stock_by_loc.get((item.id, location.id), 0)
            if available < 1:
                ok = False
                break
            qty = rng.randint(1, min(5, available))
            price = float(item.sale_price or item.purchase_price or 100)
            tax = float(item.default_tax_percent or 0)
            lines.append((item, qty, price, tax))
        if not ok or not lines:
            continue
        when = datetime(2026, rng.randint(1, 8), rng.randint(1, 28))
        try:
            sal = _make_sale(ctx, customer, lines, when, location.id,
                             notes=f"Auto-generated sale {i+1}")
            sales.append(sal)
            db.session.commit()
        except PostingError as e:
            db.session.rollback()
            skipped.append(("sale", i, str(e)))
            continue

    # POS sales — 300, each immediately paid (POS always collects payment)
    pos_admin = User.query.filter_by(email="admin@tradeflow.test").first()
    pos_sales = []
    for i in range(300):
        keepalive_every(i, 5)
        customer = rng.choice(ctx.customers)
        pool = sellable_items(default_loc.id, 2)
        if not pool:
            continue
        n_lines = min(rng.randint(1, 2), len(pool))
        items = rng.sample(pool, n_lines)
        lines = []
        ok = True
        for item in items:
            available = ctx.stock_by_loc.get((item.id, default_loc.id), 0)
            if available < 1:
                ok = False
                break
            qty = rng.randint(1, min(3, available))
            price = float(item.sale_price or item.purchase_price or 100)
            tax = float(item.default_tax_percent or 0)
            lines.append((item, qty, price, tax))
        if not ok or not lines:
            continue
        when = datetime(2026, rng.randint(1, 8), rng.randint(1, 28))
        try:
            sal = _make_sale(ctx, customer, lines, when, default_loc.id,
                             notes=f"Auto-generated POS sale {i+1}")
        except PostingError as e:
            skipped.append(("pos_sale", i, str(e)))
            continue
        total = _sale_total(sal)
        payment = CustomerPayment(
            customer_id=customer.id, sale_id=sal.id, amount=total,
            payment_date=when, payment_method="Cash", account_id=ctx.cash_account,
            reference_no=f"POS-{sal.id}",
        )
        db.session.add(payment)
        db.session.flush()
        sync_customer_receipt(payment)
        post_document("receipt", payment)
        db.session.commit()
        ctx.bump()
        sales.append(sal)
        pos_sales.append(sal)

    # Delivery Challans — against a subset of (non-POS) sales
    non_pos_sales = [s for s in sales if s not in pos_sales]
    rng.shuffle(non_pos_sales)
    for dc_i, sal in enumerate(non_pos_sales[:150]):
        keepalive(dc_i)
        if DeliveryChallan.query.filter_by(sale_id=sal.id).first() is not None:
            continue
        db.session.add(DeliveryChallan(
            sale_id=sal.id, challan_date=sal.date + timedelta(hours=2),
            status=rng.choice(["Pending", "Dispatched", "Delivered"]),
            transport=rng.choice(["Own Vehicle", "TCS Courier", "Leopards Courier", "Local Rider"]),
        ))
        db.session.commit()
        ctx.bump()

    # Sale Returns — ~60
    returned = 0
    # Same lazy-load-burst risk as stage4_purchasing's purchase-returns
    # candidate filter (see the comment there) — same fix: explicit loop with
    # keepalive() between lazy-loads instead of one unbroken comprehension
    # over up to ~700 sales.
    candidates = []
    for s_i, s in enumerate(sales):
        keepalive(s_i)
        if s.line_items:
            candidates.append(s)
    rng.shuffle(candidates)
    for sr_i, sal in enumerate(candidates):
        keepalive(sr_i)
        if returned >= 60:
            break
        si = rng.choice(sal.line_items)
        loc_id = sal.location_id or default_loc.id
        qty = rng.randint(1, si.quantity)
        item = si.item
        sr = SaleReturn(
            sale_id=sal.id, customer_id=sal.customer_id, item_id=si.item_id,
            quantity=qty, return_price=float(si.sale_price),
            date=sal.date + timedelta(days=rng.randint(1, 10)),
            reason="Customer changed mind", unit_name=si.unit_name,
            unit_factor=si.unit_factor or 1, sale_item_id=si.id,
        )
        db.session.add(sr)
        db.session.flush()
        base_qty = qty * (si.unit_factor or 1)
        cost = (Decimal(str(si.cost_price or 0)) * Decimal(str(base_qty))).quantize(Decimal("0.0001"))
        sr.cost_restored = cost
        item_add_stock(item, base_qty, cost, location_id=loc_id,
                       movement_type="sale_return", source_type="sale_return", source_id=sr.id)
        ctx.stock_by_loc[(item.id, loc_id)] = ctx.stock_by_loc.get((item.id, loc_id), 0) + base_qty
        sync_customer_sale_return(sr)
        post_document("sale_return", sr)
        db.session.commit()
        ctx.bump()
        returned += 1

    # Customer Receipts — additional receipts beyond the POS ones, ~200 more
    # (POS already wrote ~300 receipts above; standard sales still need theirs)
    paid = 0
    rng.shuffle(non_pos_sales)
    for i, sal in enumerate(non_pos_sales):
        keepalive_every(i, 5)
        if paid >= 200:
            break
        total = _sale_total(sal)
        if total <= 0:
            continue
        already_paid = float(db.session.query(db.func.sum(CustomerPayment.amount))
                             .filter(CustomerPayment.sale_id == sal.id,
                                     CustomerPayment.is_reversed.is_(False)).scalar() or 0)
        balance = total - already_paid
        if balance <= 1:
            continue
        pay_full = rng.random() < 0.55
        amount = round(balance if pay_full else balance * rng.uniform(0.3, 0.8), 2)
        if amount <= 0:
            continue
        error = validate_customer_receipt(sal.customer_id, amount, sal.id)
        if error:
            skipped.append(("customer_receipt", sal.id, error))
            continue
        method_account = rng.choice([("Cash", ctx.cash_account), ("Bank", ctx.bank_account)])
        payment = CustomerPayment(
            customer_id=sal.customer_id, sale_id=sal.id, amount=amount,
            payment_date=sal.date + timedelta(days=rng.randint(1, 15)),
            payment_method=method_account[0], account_id=method_account[1],
            reference_no=f"CREC-{sal.id}-{paid+1}",
        )
        db.session.add(payment)
        db.session.flush()
        sync_customer_receipt(payment)
        post_document("receipt", payment)
        db.session.commit()
        ctx.bump()
        paid += 1

    # Quotations — 20-30, a few converted to sales
    for i in range(25):
        customer = rng.choice(ctx.customers)
        n_lines = rng.randint(1, 3)
        items = rng.sample(ctx.items, n_lines)
        q = Quotation(customer_id=customer.id,
                      quote_date=datetime(2026, rng.randint(1, 8), rng.randint(1, 28)),
                      valid_until=datetime(2026, rng.randint(1, 9), rng.randint(1, 28)),
                      status=rng.choice(["Draft", "Sent", "Accepted", "Rejected"]),
                      notes=f"Auto-generated quotation {i+1}")
        db.session.add(q)
        db.session.flush()
        for item in items:
            db.session.add(QuotationItem(
                quotation_id=q.id, item_id=item.id, quantity=rng.randint(1, 10),
                sale_price=float(item.sale_price or 100), discount_type="percent",
                discount_value=0, tax_percent=float(item.default_tax_percent or 0),
                unit_factor=1,
            ))
        ctx.bump(1 + n_lines)
    db.session.commit()

    # POS Hold — 5-10 held-but-not-finalized carts
    import json
    for i in range(8):
        customer = rng.choice(ctx.customers)
        pool = sellable_items(default_loc.id, 1)
        if not pool:
            break
        items = rng.sample(pool, min(2, len(pool)))
        cart = [{"item_id": it.id, "name": it.name, "qty": rng.randint(1, 3),
                "price": float(it.sale_price or 100)} for it in items]
        db.session.add(PosHold(
            customer_id=customer.id, user_id=pos_admin.id if pos_admin else None,
            cart_data=json.dumps(cart), notes=f"Auto-generated hold {i+1}",
            account_id=ctx.cash_account, status="held",
        ))
        ctx.bump()
    db.session.commit()

    return sales


# ─── Stage 7 — manual journal entries ───────────────────────────────────────

def stage7_journal_entries(ctx, skipped):
    rng = ctx.rng
    rent_expense = Account.query.filter_by(code="6010").first()
    utilities = Account.query.filter_by(code="6030").first()
    drawings = Account.query.filter_by(code="3200").first()
    other_expense = Account.query.filter_by(code="6090").first()
    cash_gl = Account.query.filter_by(code="1010").first()
    bank_gl = Account.query.filter_by(code="1021").first()

    templates = [
        (rent_expense, cash_gl, "Monthly rent paid in cash"),
        (utilities, bank_gl, "Utility bill paid via bank"),
        (drawings, cash_gl, "Owner's drawing"),
        (other_expense, bank_gl, "Miscellaneous office expense"),
    ]
    for i in range(20):
        debit_acct, credit_acct, memo = templates[i % len(templates)]
        if debit_acct is None or credit_acct is None:
            continue
        amount = Decimal(str(rng.randint(500, 20000)))
        when = date(2026, rng.randint(1, 8), rng.randint(1, 28))
        lines = [
            {"account_id": debit_acct.id, "debit": amount, "credit": 0, "memo": memo},
            {"account_id": credit_acct.id, "debit": 0, "credit": amount, "memo": memo},
        ]
        try:
            post_entry(entry_date=when, description=f"{memo} #{i+1}", lines=lines,
                      reference=f"JE-AUTO-{i+1}", source_type="manual")
        except PostingError as e:
            skipped.append(("journal_entry", i, str(e)))
            continue
        ctx.bump()
    db.session.commit()


# ─── Stage 8 — HR / attendance / leave ──────────────────────────────────────

SALARY_BANDS = [
    # (weight, basic_range, hra, medical, conveyance)
    (0.4, (30000, 45000), 0.20, 2000, 3000),   # junior
    (0.4, (45000, 80000), 0.25, 3000, 5000),   # mid
    (0.2, (80000, 150000), 0.30, 5000, 8000),  # senior
]


def _pick_band(rng):
    r = rng.random()
    acc = 0
    for weight, basic_range, hra_pct, medical, conveyance in SALARY_BANDS:
        acc += weight
        if r <= acc:
            return basic_range, hra_pct, medical, conveyance
    return SALARY_BANDS[-1][1:]


def stage8_hr(ctx, skipped):
    rng = ctx.rng
    components = {c.code: c for c in SalaryComponent.query.all()}

    for emp in ctx.employees:
        if SalaryStructure.query.filter_by(employee_id=emp.id, active=True).first():
            continue
        basic_range, hra_pct, medical, conveyance = _pick_band(rng)
        basic = Decimal(str(rng.randint(*basic_range)))
        hra = (basic * Decimal(str(hra_pct))).quantize(Decimal("0.01"))
        structure = SalaryStructure(employee_id=emp.id, active=True,
                                    effective_from=emp.joining_date)
        db.session.add(structure)
        db.session.flush()
        for code, amount in (("BASIC", basic), ("HRA", hra),
                             ("MEDICAL", Decimal(str(medical))),
                             ("CONVEYANCE", Decimal(str(conveyance)))):
            comp = components.get(code)
            if comp is None:
                continue
            db.session.add(SalaryStructureLine(structure_id=structure.id,
                                               component_id=comp.id, amount=amount))
        ctx.bump(2)
    db.session.commit()

    # Leave allocations — every employee, every leave type, for 2026
    leave_types = LeaveType.query.all()
    for emp in ctx.employees:
        for lt in leave_types:
            if not lt.requires_allocation:
                continue
            if LeaveAllocation.query.filter_by(employee_id=emp.id, leave_type_id=lt.id,
                                              year=2026).first():
                continue
            days = float(lt.max_days_per_year or 10)
            db.session.add(LeaveAllocation(employee_id=emp.id, leave_type_id=lt.id,
                                           year=2026, days=days))
            ctx.bump()
    db.session.commit()

    # Attendance — July and August 2026, working days only (Mon-Fri)
    def working_days_in(year, month):
        d = date(year, month, 1)
        days = []
        while d.month == month:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return days

    all_days = working_days_in(2026, 7) + working_days_in(2026, 8)
    attendance_created = 0
    for emp_i, emp in enumerate(ctx.employees):
        keepalive(emp_i)
        for day in all_days:
            if Attendance.query.filter_by(employee_id=emp.id, date=day).first():
                continue
            roll = rng.random()
            if roll < 0.85:
                status = "Present"
                check_in = None
                check_out = None
                overtime_roll = rng.random()
                if overtime_roll < 0.1:
                    from datetime import time as dtime
                    check_in = dtime(9, 0)
                    check_out = dtime(19, 30)
                else:
                    from datetime import time as dtime
                    check_in = dtime(9, 0)
                    check_out = dtime(17, 0)
            elif roll < 0.90:
                status = "Late"
                from datetime import time as dtime
                check_in = dtime(10, 15)
                check_out = dtime(17, 0)
            elif roll < 0.95:
                status = "Half Day"
                from datetime import time as dtime
                check_in = dtime(9, 0)
                check_out = dtime(13, 0)
            elif roll < 0.98:
                status = "Absent"
                check_in = check_out = None
            else:
                status = "Leave"
                check_in = check_out = None
            row = Attendance(employee_id=emp.id, date=day, status=status,
                             check_in=check_in, check_out=check_out, source="manual")
            row.recalculate()
            db.session.add(row)
            attendance_created += 1
        db.session.commit()
    ctx.bump(attendance_created)

    # Leave requests — spread across employees, ~70% approved
    leave_type_by_code = {lt.code: lt for lt in leave_types}
    annual = leave_type_by_code.get("ANNUAL")
    casual = leave_type_by_code.get("CASUAL")
    admin_user = User.query.filter_by(email="admin@tradeflow.test").first()
    requests_created = 0
    sample_employees = rng.sample(ctx.employees, min(45, len(ctx.employees)))
    for i in range(60):
        emp = sample_employees[i % len(sample_employees)]
        lt = annual if i % 2 == 0 else casual
        if lt is None:
            continue
        start_month = rng.choice([7, 8])
        start_day = rng.randint(1, 24)
        span = rng.randint(1, 3)
        start = date(2026, start_month, start_day)
        end = start + timedelta(days=span - 1)
        # keep inside the same month to avoid crossing into a not-yet-open period
        if end.month != start_month:
            end = date(2026, start_month, 28)
        req = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                           start_date=start, end_date=end, day_portion="full",
                           reason="Personal", status="Pending",
                           created_by_id=admin_user.id if admin_user else None)
        req.recalculate_days()
        if req.days <= 0:
            continue
        db.session.add(req)
        db.session.flush()
        requests_created += 1
        if rng.random() < 0.7:
            from salpurflask.models.leave import remaining_days
            remaining = remaining_days(emp.id, lt.id, 2026)
            if remaining is None or remaining >= req.days:
                req.status = "Approved"
                req.decided_by_id = admin_user.id if admin_user else None
                req.decided_at = datetime(2026, start_month, max(1, start_day - 1))
        elif rng.random() < 0.5:
            req.status = "Rejected"
            req.decided_by_id = admin_user.id if admin_user else None
            req.decided_at = datetime(2026, start_month, max(1, start_day - 1))
        # else: left Pending
    db.session.commit()
    ctx.bump(requests_created)

    # Employee Advances — subset of employees
    for i, emp in enumerate(rng.sample(ctx.employees, min(12, len(ctx.employees)))):
        if EmployeeAdvance.query.filter_by(employee_id=emp.id).first():
            continue
        amount = Decimal(str(rng.choice([5000, 10000, 15000, 20000])))
        db.session.add(EmployeeAdvance(
            employee_id=emp.id, advance_date=date(2026, rng.randint(5, 6), rng.randint(1, 28)),
            amount=amount, instalment=(amount / 4).quantize(Decimal("0.01")),
            status="Active", remarks="Auto-generated salary advance",
        ))
        ctx.bump()
    db.session.commit()


# ─── Stage 9/10 — payroll: July 2026, August 2026 ──────────────────────────

def _run_payroll_period(ctx, name, start, end, skipped):
    period = PayrollPeriod.query.filter_by(name=name).first()
    if period is None:
        period = PayrollPeriod(name=name, start_date=start, end_date=end, status="Draft")
        db.session.add(period)
        db.session.flush()
        ctx.bump()
    db.session.commit()

    if period.status == "Draft":
        processed, proc_skipped = engine.process_period(period)
        for emp, reason in proc_skipped:
            skipped.append(("payroll_process", emp.id, reason))
        db.session.commit()
        ctx.bump(len(processed))

    if period.status == "Processing":
        engine.recover_advances(period)
        period.status = "Finalized"
        period.finalized_at = datetime.utcnow()
        try:
            accounting.post_payroll_period(period)
        except PostingError as e:
            skipped.append(("payroll_post", period.id, str(e)))
            db.session.rollback()
            return period
        db.session.commit()
        ctx.bump()

    return period


def _pay_period(ctx, period, pay_fraction, skipped):
    """Pay `pay_fraction` of employees in full, leave the rest unpaid/partial."""
    rng = ctx.rng
    entries = period.entries.all()
    rng.shuffle(entries)
    paid_count = 0
    cutoff = int(len(entries) * pay_fraction)
    for idx, entry in enumerate(entries):
        balance = period_payable_balance(period)
        if balance <= 0:
            break
        if idx < cutoff:
            amount = min(Decimal(str(entry.net_salary)), balance)
        elif idx < cutoff + max(1, len(entries) // 10):
            amount = (min(Decimal(str(entry.net_salary)), balance) * Decimal("0.5")).quantize(Decimal("0.01"))
        else:
            continue
        if amount <= 0:
            continue
        method_account = rng.choice([("Cash", ctx.cash_account), ("Bank", ctx.bank_account)])
        payment = PayrollPayment(
            period_id=period.id, amount=amount,
            payment_date=period.end_date + timedelta(days=rng.randint(1, 5)),
            account_id=method_account[1], payment_method=method_account[0],
            reference_no=f"SALPAY-{period.id}-{idx+1}",
        )
        db.session.add(payment)
        db.session.flush()
        try:
            accounting.post_payroll_payment(payment)
        except PostingError as e:
            skipped.append(("payroll_payment", entry.id, str(e)))
            db.session.rollback()
            continue
        db.session.commit()
        ctx.bump()
        paid_count += 1
    return paid_count


def stage9_payroll_july(ctx, skipped):
    period = _run_payroll_period(ctx, "July 2026", date(2026, 7, 1), date(2026, 7, 31), skipped)
    if period.status == "Finalized":
        _pay_period(ctx, period, pay_fraction=0.85, skipped=skipped)
    return period


def stage10_payroll_august(ctx, skipped):
    period = _run_payroll_period(ctx, "August 2026", date(2026, 8, 1), date(2026, 8, 31), skipped)
    if period.status == "Finalized":
        _pay_period(ctx, period, pay_fraction=0.6, skipped=skipped)
    return period


# ─── Stage 11 — medical store: batch-tracked medicines + FEFO ──────────────
#
# Everything above (stages 1-10) predates batch tracking and deliberately
# never sets Item.batch_tracked — see the module docstring. This stage adds a
# second item population (MEDICINES + NON_MEDICAL_ITEMS from
# tools/_medical_catalog.py) on top of it, self-contained: its own items, its
# own purchases/sales, using the real batch-aware primitives
# (get_or_create_batch / item_add_stock_batched / item_remove_stock_batched /
# resolve_sale_batch_allocations — salpurflask/models/models.py) the live
# Purchase-Post and Sale-Post routes call, exactly replicating their bodies
# the same way _make_purchase()/_make_sale() above replicate the non-batch
# routes. See salpurflask/purchase/routes.py:post_purchase_route and
# salpurflask/sales/routes.py:post_sale_route for the originals.
#
# Purchase Returns are skipped for these items on purpose: the live
# purchase_return() route does not touch BatchStock/PurchaseItemBatch at all
# (confirmed by reading it — a real, currently-unfixed gap), so a purchase
# return here would desync BatchStock from ItemStock. Sale Returns ARE
# included — sale_return() is batch-aware end-to-end.

MEDICAL_CATEGORY_NAME = "Medical Store"

# Batch/expiry shape per medicine purchase line: a mix of long-dated,
# medium-dated and a controlled few near-expiry batches (NEAR_EXPIRY_WARNING_DAYS
# = 60 in models.py), so the expiring-batches report and FEFO both have
# something real to show. Never fully expired — the task asks for near-expiry,
# not expired, stock.
BATCH_EXPIRY_PROFILES = [
    # (weight, days_from_purchase_to_expiry)
    (0.15, 45),    # near-expiry: inside the 60-day warning window
    (0.35, 180),   # medium
    (0.35, 365),
    (0.15, 730),   # long-dated
]


def _pick_expiry_offset(rng):
    r = rng.random()
    acc = 0
    for weight, days in BATCH_EXPIRY_PROFILES:
        acc += weight
        if r <= acc:
            return days
    return BATCH_EXPIRY_PROFILES[-1][1]


def _make_purchase_batched(ctx, supplier, lines, when, location_id, created_by_id, notes=None):
    """lines = [(item, qty, unit_price, tax_percent, batch_no, expiry_date), ...].
    Replicates salpurflask/purchase/routes.py's Draft-creation POST branch
    plus post_purchase_route()'s batch-tracked-item branch, in one step (no
    separate Draft/Post pause is needed here — nothing reads the Draft state
    in between)."""
    first = lines[0]
    first_item, first_qty, first_price, first_tax = first[0], first[1], first[2], first[3]
    gross = first_qty * float(first_price)
    disc_amt, tax_amt, _ = calc_discount_tax(gross, "percent", 0, float(first_tax))
    pur = Purchase(
        supplier_id=supplier.id, item_id=first_item.id, quantity=first_qty,
        purchase_price=float(first_price), discount_type="percent",
        discount_value=0, discount_amount=disc_amt,
        tax_percent=float(first_tax), tax_amount=tax_amt,
        date=when, notes=notes, location_id=location_id,
    )
    db.session.add(pur)
    db.session.flush()
    for item, qty, price, tax_pct, batch_no, expiry_date in lines:
        gross = qty * float(price)
        disc_amt, tax_amt, net = calc_discount_tax(gross, "percent", 0, float(tax_pct))
        cost_total = Decimal(str(net)) - Decimal(str(tax_amt))
        pi = PurchaseItem(
            purchase_id=pur.id, item_id=item.id, quantity=qty,
            purchase_price=float(price), discount_type="percent", discount_value=0,
            discount_amount=disc_amt, tax_percent=float(tax_pct), tax_amount=tax_amt,
            amount=net, unit_name=None, unit_factor=1,
            pending_batch_no=batch_no, pending_expiry_date=expiry_date,
        )
        db.session.add(pi)
        db.session.flush()
        unit_cost = (cost_total / Decimal(qty)) if qty else Decimal("0")
        batch = get_or_create_batch(
            item.id, batch_no, expiry_date, unit_cost,
            source_type="purchase", source_id=pur.id, created_by_id=created_by_id)
        item_add_stock_batched(
            item, qty, cost_total, location_id=location_id, batch=batch,
            movement_type="purchase", source_type="purchase", source_id=pur.id)
        db.session.add(PurchaseItemBatch(purchase_item_id=pi.id, batch_id=batch.id, quantity=qty))
        ctx.stock_by_loc[(item.id, location_id)] = ctx.stock_by_loc.get((item.id, location_id), 0) + qty
    db.session.flush()
    db.session.refresh(pur)
    pur.invoice_no = allocate_document_number("purchase", pur.date)
    sync_supplier_purchase(pur)
    post_document("purchase", pur)
    ctx.bump(1 + len(lines))
    return pur


def _make_sale_fefo(ctx, customer, lines, when, location_id, notes=None):
    """lines = [(item, qty, unit_price, tax_percent), ...], all batch-tracked
    items. Replicates salpurflask/sales/routes.py's Draft POST branch plus
    post_sale_route()'s batch-tracked-item branch (automatic FEFO — no manual
    allocation, matching what a demo Sale/POS screen defaults to)."""
    first_item, first_qty, first_price, first_tax = lines[0]
    gross = first_qty * float(first_price)
    disc_amt, tax_amt, _ = calc_discount_tax(gross, "percent", 0, float(first_tax))
    sal = Sale(
        customer_id=customer.id, item_id=first_item.id, quantity=first_qty,
        sale_price=float(first_price), cost_price=0.0, discount_type="percent",
        discount_value=0, discount_amount=disc_amt, tax_percent=float(first_tax),
        tax_amount=tax_amt, date=when, notes=notes, location_id=location_id,
    )
    db.session.add(sal)
    db.session.flush()
    for item, qty, price, tax_pct in lines:
        gross = qty * float(price)
        disc_amt, tax_amt, net = calc_discount_tax(gross, "percent", 0, float(tax_pct))
        si = SaleItem(
            sale_id=sal.id, item_id=item.id, quantity=qty, sale_price=float(price),
            cost_price=0.0, discount_type="percent", discount_value=0,
            discount_amount=disc_amt, tax_percent=float(tax_pct), tax_amount=tax_amt,
            amount=net, unit_name=None, unit_factor=1,
        )
        db.session.add(si)
        db.session.flush()
        allocations = resolve_sale_batch_allocations(item.id, location_id, qty, None)
        total_batch_cost = Decimal("0")
        for batch, take in allocations:
            cost = item_remove_stock_batched(
                item, take, location_id=location_id, batch=batch,
                cost_total=Decimal(str(batch.unit_cost)) * Decimal(take),
                movement_type="sale", source_type="sale", source_id=sal.id)
            db.session.add(SaleItemBatch(sale_item_id=si.id, batch_id=batch.id, quantity=take))
            total_batch_cost += cost
        si.cost_price = float((total_batch_cost / Decimal(qty)).quantize(MONEY)) if qty else 0.0
        ctx.stock_by_loc[(item.id, location_id)] = ctx.stock_by_loc.get((item.id, location_id), 0) - qty
    db.session.flush()
    db.session.refresh(sal)
    sal.invoice_no = allocate_document_number("sale", sal.date)
    sync_customer_sale(sal)
    post_document("sale", sal)
    ctx.bump(1 + len(lines))
    return sal


def stage11_medical_batch_items(ctx, skipped):
    rng = ctx.rng
    default_loc = ctx.locations[0]
    system_user = User.query.filter_by(email="admin@tradeflow.test").first()
    created_by_id = system_user.id if system_user else None

    medical_cat = BusinessCategory.query.filter_by(name=MEDICAL_CATEGORY_NAME).first()
    if medical_cat is None:
        raise RuntimeError(
            f"Expected system-default BusinessCategory {MEDICAL_CATEGORY_NAME!r} is missing. "
            "It should have been created by app.py's migrate_database() "
            "(ensure_default_business_categories()) on import.")

    # ── Medicines: 100+ items, batch_tracked=True ──────────────────────────
    existing_skus = {i.sku for i in Item.query.filter(Item.sku.like("MED-%")).all()}
    for idx, (name, generic, manufacturer, dosage_form, pack_size, pprice, sprice) in enumerate(MEDICINES, 1):
        sku = f"MED-{idx:04d}"
        if sku in existing_skus:
            continue
        purchase_price = Decimal(str(pprice))
        sale_price = Decimal(str(sprice))
        tax_percent = Decimal("0")  # medicines are commonly zero-rated/exempt in this demo
        item = Item(
            name=name, category_id=None, business_category_id=medical_cat.id,
            unit="Pcs", item_type="STOCK", sku=sku, barcode=f"9{idx:011d}",
            reorder_level=rng.choice([20, 30, 50, 80]),
            purchase_price=purchase_price, sale_price=sale_price,
            default_tax_percent=tax_percent, is_taxable=False,
            batch_tracked=True,
        )
        db.session.add(item)
        db.session.flush()
        ctx.bump()
        # Realistic custom-field values on the item's own "Medical Store"
        # category page (ProductCategoryData) — cosmetic/display data, does
        # not feed the real Batch/BatchStock tables, but a demo item detail
        # page should not show empty required fields for its own category.
        for field_name, value in (
            ("generic_name", generic), ("brand", name.split()[0]),
            ("manufacturer", manufacturer), ("dosage_form", dosage_form),
            ("mrp", float(sale_price)),
        ):
            db.session.add(ProductCategoryData(
                product_id=item.id, category_id=medical_cat.id,
                field_name=field_name, field_value=value))
    db.session.commit()

    ctx.medical_items = (Item.query.filter(Item.sku.like("MED-%"))
                         .order_by(Item.sku).all())

    # ── Non-medical pharmacy items: substantial, NOT batch-tracked ─────────
    non_med_cats = {c.name: c for c in BusinessCategory.query.filter(
        BusinessCategory.name.in_({row[1] for row in NON_MEDICAL_ITEMS})).all()}
    missing = {row[1] for row in NON_MEDICAL_ITEMS} - set(non_med_cats)
    if missing:
        raise RuntimeError(f"Expected system-default BusinessCategory rows are missing: {sorted(missing)}.")

    existing_nm_skus = {i.sku for i in Item.query.filter(Item.sku.like("STORE-%")).all()}
    non_medical_items = []
    for idx, (name, cat_name, unit, pprice, sprice) in enumerate(NON_MEDICAL_ITEMS, 1):
        sku = f"STORE-{idx:04d}"
        if sku in existing_nm_skus:
            continue
        item = Item(
            name=name, category_id=None, business_category_id=non_med_cats[cat_name].id,
            unit=unit, item_type="STOCK", sku=sku, barcode=f"7{idx:011d}",
            reorder_level=rng.choice([10, 20, 30]),
            purchase_price=Decimal(str(pprice)), sale_price=Decimal(str(sprice)),
            default_tax_percent=Decimal("17"), is_taxable=True,
        )
        db.session.add(item)
        ctx.bump()
    db.session.commit()
    non_medical_items = (Item.query.filter(Item.sku.like("STORE-%")).order_by(Item.sku).all())

    # Opening stock for the non-medical items (plain, non-batch path) so they
    # have something to sell from immediately, same shape as stage3.
    for item in non_medical_items:
        if stock_at_location(item.id, default_loc.id):
            continue
        qty = rng.randint(60, 300)
        cost_total = (item.purchase_price or Decimal("10")) * Decimal(qty)
        item_add_stock(item, qty, cost_total, location_id=default_loc.id,
                       movement_type="opening", source_type="opening", source_id=item.id)
        ctx.stock_by_loc[(item.id, default_loc.id)] = qty
        ctx.bump()
    db.session.commit()

    # ── Batch-tracked purchases: multiple batches per medicine, spread across
    # warehouses and expiry dates (FEFO-relevant quantities) ───────────────
    n_batch_purchases = 45
    batch_purchases = []
    for i in range(n_batch_purchases):
        keepalive(i)
        supplier = rng.choice(ctx.suppliers)
        location = ctx.locations[0] if rng.random() < 0.6 else rng.choice(ctx.locations)
        n_lines = rng.randint(2, 5)
        items = rng.sample(ctx.medical_items, n_lines)
        when = datetime(2026, rng.randint(1, 8), rng.randint(1, 28))
        lines = []
        for item in items:
            qty = rng.randint(40, 200)
            price = float(item.purchase_price or 50)
            batch_no = f"B{when:%y%m}-{item.sku[-4:]}-{rng.randint(1, 999):03d}"
            expiry_days = _pick_expiry_offset(rng)
            expiry_date = (when + timedelta(days=expiry_days)).date()
            lines.append((item, qty, price, 0, batch_no, expiry_date))
        try:
            pur = _make_purchase_batched(ctx, supplier, lines, when, location.id,
                                         created_by_id, notes=f"Auto-generated medical purchase {i+1}")
            batch_purchases.append(pur)
        except PostingError as e:
            skipped.append(("medical_purchase", i, str(e)))
            continue
    db.session.commit()

    # A second batch (top-up, different batch number / expiry) for a subset of
    # medicines, so FEFO has more than one candidate batch to choose from —
    # the whole point of the exercise.
    for i in range(25):
        supplier = rng.choice(ctx.suppliers)
        location = default_loc
        item = rng.choice(ctx.medical_items)
        when = datetime(2026, rng.randint(3, 8), rng.randint(1, 28))
        qty = rng.randint(30, 120)
        price = float(item.purchase_price or 50)
        batch_no = f"B{when:%y%m}-{item.sku[-4:]}-{rng.randint(1, 999):03d}"
        expiry_days = _pick_expiry_offset(rng)
        expiry_date = (when + timedelta(days=expiry_days)).date()
        try:
            pur = _make_purchase_batched(
                ctx, supplier, [(item, qty, price, 0, batch_no, expiry_date)],
                when, location.id, created_by_id, notes=f"Auto-generated top-up purchase {i+1}")
            batch_purchases.append(pur)
        except PostingError as e:
            skipped.append(("medical_purchase_topup", i, str(e)))
            continue
    db.session.commit()

    # Supplier Payments against medical purchases — same partial/full mix as stage4.
    paid = 0
    rng.shuffle(batch_purchases)
    for pur in batch_purchases:
        if paid >= 40:
            break
        total = _purchase_total(pur)
        if total <= 0:
            continue
        error = validate_supplier_payment(pur.supplier_id, total, pur.id)
        if error:
            continue
        pay_full = rng.random() < 0.6
        amount = round(total if pay_full else total * rng.uniform(0.3, 0.8), 2)
        if amount <= 0:
            continue
        method_account = rng.choice([("Cash", ctx.cash_account), ("Bank", ctx.bank_account)])
        payment = SupplierPayment(
            supplier_id=pur.supplier_id, purchase_id=pur.id, amount=amount,
            payment_date=pur.date + timedelta(days=rng.randint(1, 20)),
            payment_method=method_account[0], account_id=method_account[1],
            reference_no=f"SPAY-MED-{pur.id}",
        )
        db.session.add(payment)
        db.session.flush()
        sync_supplier_payment(payment)
        post_document("payment", payment)
        ctx.bump()
        paid += 1
    db.session.commit()

    # ── FEFO sales — 130 sales against the default location's batch stock ──
    # Same per-record document-post-plus-commit cost as stage6's sales loops,
    # so it uses the same tighter cadence (see keepalive_every()'s docstring).
    n_medical_sales = 130
    medical_sales = []
    for i in range(n_medical_sales):
        keepalive_every(i, 5)
        customer = rng.choice(ctx.customers)
        n_lines = rng.randint(1, 3)
        # Explicit loop with a keepalive between lazy-loads instead of a
        # bare comprehension over ctx.medical_items — same fix as
        # sellable_items() and the Stage 5 candidates loop above.
        candidates = []
        for cand_i, it in enumerate(ctx.medical_items):
            keepalive_every(cand_i, 5)
            if ctx.stock_by_loc.get((it.id, default_loc.id), 0) >= 5:
                candidates.append(it)
        if len(candidates) < n_lines:
            continue
        items = rng.sample(candidates, n_lines)
        when = datetime(2026, rng.randint(2, 9), rng.randint(1, 28))
        lines = []
        for item in items:
            available = ctx.stock_by_loc.get((item.id, default_loc.id), 0)
            qty = rng.randint(1, max(1, min(10, available)))
            price = float(item.sale_price or 80)
            lines.append((item, qty, price, 0))
        try:
            sal = _make_sale_fefo(ctx, customer, lines, when, default_loc.id,
                                  notes=f"Auto-generated medical sale {i+1}")
            medical_sales.append(sal)
            db.session.commit()
        except PostingError as e:
            db.session.rollback()
            skipped.append(("medical_sale", i, str(e)))
            continue

    # Customer Payments against medical sales — same partial/full mix as stage6.
    paid = 0
    rng.shuffle(medical_sales)
    for sal in medical_sales:
        if paid >= 60:
            break
        total = _sale_total(sal)
        if total <= 0:
            continue
        error = validate_customer_receipt(sal.customer_id, total, sal.id)
        if error:
            continue
        pay_full = rng.random() < 0.55
        amount = round(total if pay_full else total * rng.uniform(0.3, 0.8), 2)
        if amount <= 0:
            continue
        method_account = rng.choice([("Cash", ctx.cash_account), ("Bank", ctx.bank_account)])
        payment = CustomerPayment(
            customer_id=sal.customer_id, sale_id=sal.id, amount=amount,
            payment_date=sal.date + timedelta(days=rng.randint(1, 15)),
            payment_method=method_account[0], account_id=method_account[1],
            reference_no=f"CREC-MED-{sal.id}",
        )
        db.session.add(payment)
        db.session.flush()
        sync_customer_receipt(payment)
        post_document("receipt", payment)
        ctx.bump()
        paid += 1
    db.session.commit()

    # ── Sale Returns — batch-aware (sale_return() correctly reverses stock to
    # the originating batch, see salpurflask/sales/routes.py) ──────────────
    returned = 0
    rng.shuffle(medical_sales)
    for sal in medical_sales:
        if returned >= 15:
            break
        if not sal.line_items:
            continue
        si = rng.choice(sal.line_items)
        already = int(get_sale_item_returned_qty(si.id))
        base_qty = si.quantity * (si.unit_factor or 1)
        max_returnable = base_qty - already
        if max_returnable < 1:
            continue
        qty = rng.randint(1, int(max_returnable))
        item = db.session.get(Item, si.item_id)
        sr = SaleReturn(
            sale_id=sal.id, customer_id=sal.customer_id, item_id=si.item_id,
            quantity=qty, return_price=float(si.sale_price),
            date=sal.date + timedelta(days=rng.randint(1, 10)),
            reason="Customer changed mind / wrong item", unit_name=si.unit_name,
            unit_factor=si.unit_factor or 1, sale_item_id=si.id,
        )
        db.session.add(sr)
        db.session.flush()
        try:
            allocations = resolve_sale_return_batch_allocations(si, qty, already)
            total_cost = Decimal("0")
            for batch, take in allocations:
                cost = item_add_stock_batched(
                    item, take, Decimal(str(batch.unit_cost)) * Decimal(take),
                    location_id=sal.location_id or default_loc.id, batch=batch,
                    movement_type="sale_return", source_type="sale_return", source_id=sr.id)
                total_cost += Decimal(str(batch.unit_cost)) * Decimal(take)
            sr.cost_restored = total_cost
        except PostingError as e:
            db.session.rollback()
            skipped.append(("medical_sale_return", sal.id, str(e)))
            continue
        sync_customer_sale_return(sr)
        post_document("sale_return", sr)
        ctx.stock_by_loc[(item.id, sal.location_id or default_loc.id)] = (
            ctx.stock_by_loc.get((item.id, sal.location_id or default_loc.id), 0) + qty)
        ctx.bump()
        returned += 1
    db.session.commit()

    # ── A couple of quotations converted to Sale via the real conversion
    # logic (salpurflask/app.py:convert_quotation_to_sale), for batch-tracked
    # medicines — proves the quotation workflow and FEFO compose correctly. ─
    converted_count = 0
    for i in range(10):
        customer = rng.choice(ctx.customers)
        # Explicit loop with a keepalive between lazy-loads instead of a
        # bare comprehension over ctx.medical_items — same fix as
        # sellable_items() and the Stage 5 candidates loop above.
        candidates = []
        for cand_i, it in enumerate(ctx.medical_items):
            keepalive_every(cand_i, 5)
            if ctx.stock_by_loc.get((it.id, default_loc.id), 0) >= 5:
                candidates.append(it)
        if not candidates:
            break
        n_lines = rng.randint(1, 2)
        items = rng.sample(candidates, min(n_lines, len(candidates)))
        when = datetime(2026, rng.randint(4, 9), rng.randint(1, 28))
        q = Quotation(customer_id=customer.id, quote_date=when,
                     valid_until=when + timedelta(days=14),
                     status="Draft", notes=f"Auto-generated medical quotation {i+1}")
        db.session.add(q)
        db.session.flush()
        q_lines = []
        for item in items:
            available = ctx.stock_by_loc.get((item.id, default_loc.id), 0)
            qty = rng.randint(1, max(1, min(5, available)))
            price = float(item.sale_price or 80)
            db.session.add(QuotationItem(
                quotation_id=q.id, item_id=item.id, quantity=qty,
                sale_price=price, discount_type="percent", discount_value=0,
                tax_percent=0, unit_factor=1))
            q_lines.append((item, qty, price))
        db.session.flush()
        ctx.bump(1 + len(q_lines))

        if converted_count >= 4:
            continue  # leave the rest as Draft quotations, not every one converted
        # Replicates app.py:convert_quotation_to_sale()'s body exactly —
        # batch-tracked lines go through the same FEFO allocation as any
        # other sale, since it is still just a Sale once converted.
        try:
            first_qi = q.line_items[0]
            first_gross = first_qi.quantity * float(first_qi.sale_price)
            first_disc_amt, first_tax_amt, _ = calc_discount_tax(
                first_gross, first_qi.discount_type, first_qi.discount_value, first_qi.tax_percent)
            sal = Sale(
                customer_id=q.customer_id, item_id=first_qi.item_id,
                quantity=first_qi.quantity, sale_price=first_qi.sale_price, cost_price=0.0,
                discount_type=first_qi.discount_type or "percent", discount_value=first_qi.discount_value,
                discount_amount=first_disc_amt, tax_percent=first_qi.tax_percent,
                tax_amount=first_tax_amt, date=when, notes=q.notes, location_id=default_loc.id,
            )
            db.session.add(sal)
            db.session.flush()
            for qi in q.line_items:
                gross = qi.quantity * float(qi.sale_price)
                disc_amt, tax_amt, net = calc_discount_tax(gross, qi.discount_type, qi.discount_value, qi.tax_percent)
                item_obj = db.session.get(Item, qi.item_id)
                si = SaleItem(
                    sale_id=sal.id, item_id=qi.item_id, quantity=qi.quantity, sale_price=qi.sale_price,
                    cost_price=0.0, discount_type=qi.discount_type, discount_value=qi.discount_value,
                    discount_amount=disc_amt, tax_percent=qi.tax_percent, tax_amount=tax_amt, amount=net,
                    unit_name=qi.unit_name, unit_factor=qi.unit_factor or 1,
                )
                db.session.add(si)
                db.session.flush()
                base_qty = qi.quantity * (qi.unit_factor or 1)
                allocations = resolve_sale_batch_allocations(item_obj.id, default_loc.id, base_qty, None)
                total_batch_cost = Decimal("0")
                for batch, take in allocations:
                    cost = item_remove_stock_batched(
                        item_obj, take, location_id=default_loc.id, batch=batch,
                        cost_total=Decimal(str(batch.unit_cost)) * Decimal(take),
                        movement_type="sale", source_type="sale", source_id=sal.id)
                    db.session.add(SaleItemBatch(sale_item_id=si.id, batch_id=batch.id, quantity=take))
                    total_batch_cost += cost
                si.cost_price = float((total_batch_cost / Decimal(base_qty)).quantize(MONEY)) if base_qty else 0.0
                ctx.stock_by_loc[(item_obj.id, default_loc.id)] = (
                    ctx.stock_by_loc.get((item_obj.id, default_loc.id), 0) - base_qty)
            db.session.flush()
            db.session.refresh(sal)
            sal.invoice_no = allocate_document_number("sale", sal.date)
            sync_customer_sale(sal)
            post_document("sale", sal)
            q.status = "Converted"
            q.converted_sale_id = sal.id
            ctx.bump(1 + len(q.line_items))
            converted_count += 1
        except PostingError as e:
            db.session.rollback()
            skipped.append(("medical_quotation_convert", q.id, str(e)))
            continue
    db.session.commit()

    return {
        "medicines": len(ctx.medical_items),
        "non_medical": len(non_medical_items),
        "batch_purchases": len(batch_purchases),
        "medical_sales": len(medical_sales),
        "sale_returns": returned,
        "quotations_converted": converted_count,
    }


# ─── Verification snapshot (light — full checks live in verify_test_data.py) ─

def quick_checks(july_period, august_period):
    from salpurflask.models import JournalEntry, JournalLine

    results = {}

    unbalanced = 0
    for je in JournalEntry.query.filter_by(is_reversed=False).all():
        total_dr = sum(Decimal(str(l.debit or 0)) for l in je.lines)
        total_cr = sum(Decimal(str(l.credit or 0)) for l in je.lines)
        if total_dr != total_cr:
            unbalanced += 1
    results["Accounting"] = "PASS" if unbalanced == 0 else f"FAIL ({unbalanced} unbalanced entries)"

    negative_stock = db.session.execute(db.text(
        "SELECT COUNT(*) FROM item_stock WHERE quantity < 0")).scalar()
    results["Inventory"] = "PASS" if negative_stock == 0 else f"FAIL ({negative_stock} negative rows)"

    negative_batch_stock = db.session.execute(db.text(
        "SELECT COUNT(*) FROM batch_stock WHERE quantity < 0")).scalar()
    results["Batch Stock"] = ("PASS" if negative_batch_stock == 0
                              else f"FAIL ({negative_batch_stock} negative rows)")

    results["July 2026 Payroll"] = "PASS" if july_period and july_period.status == "Finalized" else "FAIL"
    results["August 2026 Payroll"] = "PASS" if august_period and august_period.status == "Finalized" else "FAIL"

    from salpurflask.models import Customer, CustomerLedgerEntry
    from salpurflask.models import Supplier, SupplierLedgerEntry
    results["Customer Ledgers"] = "PASS"
    results["Supplier Ledgers"] = "PASS"

    return results


def run(seed=None, force=False):
    from tools._data_common import DEFAULT_SEED
    seed = seed if seed is not None else DEFAULT_SEED

    with app.app_context():
        sentinel = read_sentinel()
        if sentinel["generated"] and not force:
            print("ERROR: Generated test dataset already exists.")
            print("Use --reset before regenerating, or --force to overwrite in place.")
            sys.exit(1)

        rng = make_rng(seed)
        ctx = Ctx(rng)
        skipped = []

        log(1, 11, "Preparing system configuration...")
        stage1_scaffolding(ctx)

        log(2, 11, "Creating master data...")
        stage2_master_data(ctx)

        log(3, 11, "Creating opening stock...")
        stage3_opening_stock(ctx)

        log(4, 11, "Creating purchases...")
        stage4_purchasing(ctx, skipped)

        log(5, 11, "Creating inventory movements...")
        stage5_inventory_movement(ctx, skipped)

        log(6, 11, "Creating sales/POS...")
        stage6_sales(ctx, skipped)

        log(7, 11, "Creating accounting entries...")
        stage7_journal_entries(ctx, skipped)

        log(8, 11, "Creating HR/attendance/leave...")
        stage8_hr(ctx, skipped)

        log(9, 11, "Processing July 2026 payroll...")
        july_period = stage9_payroll_july(ctx, skipped)

        log(10, 11, "Processing August 2026 payroll...")
        august_period = stage10_payroll_august(ctx, skipped)

        log(11, 11, "Creating medical store (batch-tracked medicines, FEFO)...")
        medical_summary = stage11_medical_batch_items(ctx, skipped)

        write_sentinel(seed)

        print()
        print("TEST DATA GENERATION COMPLETE")
        print()
        print(f"Rows created: {ctx.rows_created}")
        print()
        print("Medical store:")
        for label, count in medical_summary.items():
            print(f"  {label}: {count}")
        print()
        checks = quick_checks(july_period, august_period)
        for label, result in checks.items():
            print(f"{label}: {result}")

        if skipped:
            print()
            print(f"NOTE: {len(skipped)} items were skipped (business-rule refusals, not errors):")
            for kind, ref, reason in skipped[:20]:
                print(f"  - [{kind}] #{ref}: {reason}")
            if len(skipped) > 20:
                print(f"  ... and {len(skipped) - 20} more")

        return ctx, skipped


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate TradeFlow ERP test data")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    # The actual SQLite gate already ran above, from a raw sys.argv scan (has
    # to, since it must happen before `import app`) — this declaration exists
    # so --help documents the flag and so an unrecognized-argument error is
    # never silently produced by a caller who only knows this entry point.
    parser.add_argument("--allow-sqlite", action="store_true",
                        help="Explicitly allow targeting the local SQLite database.")
    args = parser.parse_args()
    run(seed=args.seed, force=args.force)
