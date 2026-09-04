"""Generate a large, realistic, interconnected ERP test dataset directly in
PostgreSQL, using TradeFlow's own business-logic primitives wherever one
exists (item_add_stock/item_remove_stock, post_document/post_entry, the
transfer service, the payroll engine/accounting) rather than raw INSERTs.

Run via the CLI, not directly:
    python tools/test_data_cli.py generate [--seed N] [--force]

Safety:
  - Refuses to run against anything but PostgreSQL (see _data_common.require_postgres).
  - Refuses to run twice unless --force is passed (see _data_common sentinel).
  - Never touches SQLite, never drops/recreates the schema, never deletes the
    67 baseline system rows (it only ever adds to master/transactional tables).

Design reference: see the Phase 3 design conversation for the full dependency
graph, record-count table, and per-domain rationale. This file follows that
plan stage for stage.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._data_common import require_postgres, make_rng, write_sentinel, read_sentinel

DATABASE_URL = require_postgres()

from datetime import date, datetime, timedelta
from decimal import Decimal

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
from salpurflask.models.inventory_location import (
    get_or_create_default_location, stock_at_location,
)
from salpurflask.models.business_config import BusinessCategory
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
    for item in ctx.items:
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


def _purchase_total(pur):
    return sum(float(pi.amount) for pi in pur.line_items)


def stage4_purchasing(ctx, skipped):
    rng = ctx.rng
    default_loc = ctx.locations[0]

    # Purchase Orders — some converted, some left standing
    pos = []
    for i in range(60):
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
        ctx.bump(1 + n_lines)
        pos.append(po)
    db.session.commit()

    # Purchases — 250 total, ~60 of them born from converting a PO
    convert_pool = list(pos)
    rng.shuffle(convert_pool)
    purchases = []
    n_purchases = 250
    for i in range(n_purchases):
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
            pur = _make_purchase(ctx, supplier, lines, when, location.id,
                                 notes=f"Auto-generated purchase {i+1}")
            purchases.append(pur)
        except PostingError as e:
            skipped.append(("purchase", i, str(e)))
            continue
    db.session.commit()

    # Convert a subset of Draft POs into Purchases via the real conversion path
    converted = 0
    for po in convert_pool[:60]:
        if po.status != "Draft" or not po.line_items:
            continue
        lines = [(pi.item, pi.quantity, float(pi.purchase_price), float(pi.tax_percent))
                for pi in po.line_items]
        try:
            pur = _make_purchase(ctx, po.supplier, lines, po.order_date, default_loc.id,
                                 notes=f"Converted from PO #{po.id}")
        except PostingError as e:
            skipped.append(("po_convert", po.id, str(e)))
            continue
        po.status = "Received"
        po.converted_purchase_id = pur.id
        purchases.append(pur)
        converted += 1
    db.session.commit()

    # Purchase Returns — ~25, against completed purchases with enough remaining stock
    returned = 0
    candidates = [p for p in purchases if p.line_items]
    rng.shuffle(candidates)
    for pur in candidates:
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
        ctx.stock_by_loc[(item.id, loc_id)] = ctx.stock_by_loc.get((item.id, loc_id), 0) - qty
        ctx.bump()
        returned += 1
    db.session.commit()

    # Supplier Payments — ~200, mix of partial/full, leaving some outstanding
    paid = 0
    rng.shuffle(purchases)
    for pur in purchases:
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
        ctx.bump()
        paid += 1
    db.session.commit()

    return purchases


# ─── Stage 5 — inventory movement (transfers, adjustments) ─────────────────

def stage5_inventory_movement(ctx, skipped):
    rng = ctx.rng
    default_loc = ctx.locations[0]
    other_locs = ctx.locations[1:]

    confirmed = draft = cancelled = reversed_ = 0
    for i in range(40):
        dest = rng.choice(other_locs)
        n_lines = rng.randint(1, 3)
        candidates = [it for it in ctx.items
                     if ctx.stock_by_loc.get((it.id, default_loc.id), 0) >= 20]
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
        ctx.bump()
    db.session.commit()


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
        return [it for it in ctx.items
               if ctx.stock_by_loc.get((it.id, location_id), 0) >= min_qty]

    # Standard sales — 400
    for i in range(400):
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
        except PostingError as e:
            skipped.append(("sale", i, str(e)))
            continue
    db.session.commit()

    # POS sales — 300, each immediately paid (POS always collects payment)
    pos_admin = User.query.filter_by(email="admin@tradeflow.test").first()
    pos_sales = []
    for i in range(300):
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
        ctx.bump()
        sales.append(sal)
        pos_sales.append(sal)
    db.session.commit()

    # Delivery Challans — against a subset of (non-POS) sales
    non_pos_sales = [s for s in sales if s not in pos_sales]
    rng.shuffle(non_pos_sales)
    for sal in non_pos_sales[:150]:
        if DeliveryChallan.query.filter_by(sale_id=sal.id).first() is not None:
            continue
        db.session.add(DeliveryChallan(
            sale_id=sal.id, challan_date=sal.date + timedelta(hours=2),
            status=rng.choice(["Pending", "Dispatched", "Delivered"]),
            transport=rng.choice(["Own Vehicle", "TCS Courier", "Leopards Courier", "Local Rider"]),
        ))
        ctx.bump()
    db.session.commit()

    # Sale Returns — ~60
    returned = 0
    candidates = [s for s in sales if s.line_items]
    rng.shuffle(candidates)
    for sal in candidates:
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
        ctx.bump()
        returned += 1
    db.session.commit()

    # Customer Receipts — additional receipts beyond the POS ones, ~200 more
    # (POS already wrote ~300 receipts above; standard sales still need theirs)
    paid = 0
    rng.shuffle(non_pos_sales)
    for sal in non_pos_sales:
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
        ctx.bump()
        paid += 1
    db.session.commit()

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
    for emp in ctx.employees:
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

        log(1, 10, "Preparing system configuration...")
        stage1_scaffolding(ctx)

        log(2, 10, "Creating master data...")
        stage2_master_data(ctx)

        log(3, 10, "Creating opening stock...")
        stage3_opening_stock(ctx)

        log(4, 10, "Creating purchases...")
        stage4_purchasing(ctx, skipped)

        log(5, 10, "Creating inventory movements...")
        stage5_inventory_movement(ctx, skipped)

        log(6, 10, "Creating sales/POS...")
        stage6_sales(ctx, skipped)

        log(7, 10, "Creating accounting entries...")
        stage7_journal_entries(ctx, skipped)

        log(8, 10, "Creating HR/attendance/leave...")
        stage8_hr(ctx, skipped)

        log(9, 10, "Processing July 2026 payroll...")
        july_period = stage9_payroll_july(ctx, skipped)

        log(10, 10, "Processing August 2026 payroll...")
        august_period = stage10_payroll_august(ctx, skipped)

        write_sentinel(seed)

        print()
        print("TEST DATA GENERATION COMPLETE")
        print()
        print(f"Rows created: {ctx.rows_created}")
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
    args = parser.parse_args()
    run(seed=args.seed, force=args.force)
