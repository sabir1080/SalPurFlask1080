"""Regression coverage for the <tfoot> Total/Avg/Max/Min rows added across 32
report-style Jinja templates (see templates/reports.html for the established
convention this follows).

Each test seeds the minimum realistic data needed to put at least one row in
the table the new footer summarizes (an empty table skips the tfoot via an
`{% if rows %}` guard), logs in as an admin/manager, GETs the route, and
asserts the page renders (status 200) with the tfoot's own text present.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app import (
    app as flask_app, db, User, pwd_context, Category, Item, Supplier, Customer,
    FinancialAccount, Expense, JournalEntry, JournalLine, Account, FixedAsset,
    PurchaseOrder, PurchaseOrderItem, Quotation, QuotationItem,
    PurchaseReturn, SaleReturn, PosHold, SupplierPayment, CustomerPayment,
    ACC_CASH_IN_HAND,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    get_account, ensure_gl_account_for_financial, post_account_opening,
    post_customer_receipt, post_supplier_payment,
    sync_customer_opening, sync_supplier_opening,
)
from salpurflask.models.models import Purchase, PurchaseItem, Sale, SaleItem
from salpurflask.models.inventory_location import (
    Transfer, TransferItem, InventoryReconciliation, InventoryReconciliationLine,
    get_or_create_default_location,
)
from salpurflask.models.hr import Employee
from salpurflask.models.attendance import Attendance
from salpurflask.models.leave import LeaveType, LeaveAllocation, LeaveRequest, seed_leave_types
from salpurflask.models.payroll import (
    SalaryComponent, SalaryStructure, SalaryStructureLine, PayrollPeriod,
    PayrollEntry, EmployeeAdvance, seed_default_components,
)
from salpurflask.services.feature_flags import set_module
from salpurflask.services.inventory_reconciliation import finalize_count


# ── generic helpers ─────────────────────────────────────────────────────────

def _login(user):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
    return c


_counter = {"n": 0}


def _admin():
    _counter["n"] += 1
    u = User(name="Admin", email=f"admin{_counter['n']}@tfoot.com",
             password=pwd_context.hash("secret123"), verified=True, role="admin")
    db.session.add(u)
    db.session.commit()
    return u


def _supplier(name="Acme Supplier"):
    s = Supplier(name=name, contact="03000000000", address="x", opening_balance=0)
    db.session.add(s)
    db.session.flush()
    return s


def _customer(name="Jane Customer"):
    c = Customer(name=name, contact="03000000000", address="x", opening_balance=0)
    db.session.add(c)
    db.session.flush()
    return c


def _category():
    cat = Category.query.first()
    if cat is None:
        cat = Category(name="Cat")
        db.session.add(cat)
        db.session.flush()
    return cat


def _item(name="Widget", **over):
    kw = dict(name=name, category_id=_category().id, stock=100, opening_stock=100,
              purchase_price=10, sale_price=20, item_type="STOCK", reorder_level=5)
    kw.update(over)
    it = Item(**kw)
    db.session.add(it)
    db.session.flush()
    return it


def _enable_hr_modules(**flags):
    for key in ("module_hr", "module_attendance", "module_payroll", "module_leave"):
        set_module(key, flags.get(key.replace("module_", ""), False), updated_by="test")


def _setup_gl(year=2026):
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(year)


# ── 1. account_ledger ────────────────────────────────────────────────────────

def test_account_ledger(appctx):
    admin = _admin()
    _setup_gl()
    cash_gl = get_account(ACC_CASH_IN_HAND)
    acct = FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                             opening_balance=1000, gl_account_id=cash_gl.id)
    db.session.add(acct)
    db.session.commit()
    post_account_opening(acct)
    db.session.commit()

    c = _customer()
    rcpt = CustomerPayment(customer_id=c.id, amount=500, payment_method="Cash",
                            payment_date=datetime(2026, 3, 1), account_id=acct.id)
    db.session.add(rcpt)
    db.session.flush()
    post_customer_receipt(rcpt)
    db.session.commit()

    cl = _login(admin)
    r = cl.get(f"/accounts/{acct.id}/ledger")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Total" in body


# ── 2. admin_financial_accounts ─────────────────────────────────────────────

def test_admin_financial_accounts(appctx):
    admin = _admin()
    _setup_gl()
    control = FinancialAccount(name="Bank Control", method="acct-ctrl1",
                                account_type="Bank", opening_balance=0, is_control=True)
    db.session.add(control)
    db.session.flush()
    child = FinancialAccount(name="Bank Sub 1", method="acct-sub1", account_type="Bank",
                              opening_balance=500, parent_id=control.id)
    standalone = FinancialAccount(name="Petty Cash", method="acct-standalone1",
                                   account_type="Cash", opening_balance=250)
    db.session.add_all([child, standalone])
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/admin/financial-accounts")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 3. attendance/index ──────────────────────────────────────────────────────

def test_attendance_index(appctx):
    admin = _admin()
    set_module("module_hr", True, updated_by="test")
    set_module("module_attendance", True, updated_by="test")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1), active=True)
    db.session.add(emp)
    db.session.commit()
    yesterday = date.today() - timedelta(days=1)
    att = Attendance(employee_id=emp.id, date=yesterday, status="Present",
                      working_hours=Decimal("8"), overtime_hours=Decimal("1"))
    db.session.add(att)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/attendance/")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 4. customer_ledger ───────────────────────────────────────────────────────

def test_customer_ledger(appctx):
    admin = _admin()
    _setup_gl()
    c = _customer()
    c.opening_balance = 1000
    db.session.commit()
    sync_customer_opening(c)
    db.session.commit()

    cl = _login(admin)
    r = cl.get(f"/customer/{c.id}/ledger")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Total" in body


# ── 5. customer_receipt ──────────────────────────────────────────────────────

def test_customer_receipt(appctx):
    admin = _admin()
    _setup_gl()
    c = _customer()
    p = CustomerPayment(customer_id=c.id, amount=750, payment_method="Cash",
                         payment_date=datetime(2026, 3, 1))
    db.session.add(p)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/customer_receipt")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 6. dashboard ─────────────────────────────────────────────────────────────

def test_dashboard_low_stock(appctx):
    admin = _admin()
    it = _item(name="LowStockItem", stock=2, reorder_level=10)

    cl = _login(admin)
    r = cl.get("/dashboard")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 7. expenses ──────────────────────────────────────────────────────────────

def test_expenses(appctx):
    admin = _admin()
    _setup_gl()
    e = Expense(description="Rent", amount=1500, date=datetime(2026, 3, 1),
                payment_method="Cash")
    db.session.add(e)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/expenses")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 8. expiring_batches ──────────────────────────────────────────────────────

def test_expiring_batches(appctx):
    from salpurflask.models import Batch, BatchStock
    admin = _admin()
    loc = get_or_create_default_location()
    it = _item(name="BatchedItem")
    db.session.commit()

    b1 = Batch(item_id=it.id, batch_no="B1", expiry_date=date.today() + timedelta(days=10),
               unit_cost=Decimal("10"))
    b2 = Batch(item_id=it.id, batch_no="B2", expiry_date=None, unit_cost=Decimal("12"))
    db.session.add_all([b1, b2])
    db.session.flush()
    db.session.add_all([
        BatchStock(batch_id=b1.id, location_id=loc.id, quantity=20),
        BatchStock(batch_id=b2.id, location_id=loc.id, quantity=15),
    ])
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/reports/expiring-batches")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 9. fixed_assets ──────────────────────────────────────────────────────────

def test_fixed_assets(appctx):
    admin = _admin()
    _setup_gl()
    asset = FixedAsset(name="Delivery Van", acquisition_date=datetime(2025, 1, 1),
                        cost=Decimal("500000"), salvage_value=Decimal("50000"),
                        useful_life_months=60)
    db.session.add(asset)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/fixed_assets")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 10. hr/employees ─────────────────────────────────────────────────────────

def test_hr_employees(appctx):
    admin = _admin()
    set_module("module_hr", True, updated_by="test")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1),
                    active=True, basic_salary=Decimal("30000"))
    db.session.add(emp)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/hr/employees")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 11. inventory_reconciliation_detail ─────────────────────────────────────

def test_inventory_reconciliation_detail(appctx):
    admin = _admin()
    loc = get_or_create_default_location()
    it = _item(name="ReconItem", stock=50)
    db.session.commit()

    recon = InventoryReconciliation(location_id=loc.id, date=datetime(2026, 3, 1),
                                     status="Draft")
    db.session.add(recon)
    db.session.flush()
    line = InventoryReconciliationLine(reconciliation_id=recon.id, item_id=it.id,
                                        physical_quantity=45)
    db.session.add(line)
    db.session.commit()

    finalize_count(recon, counted_by_id=admin.id)
    db.session.commit()
    assert recon.status == "Counted"

    cl = _login(admin)
    r = cl.get(f"/reconciliations/{recon.id}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Total" in body


# ── 12. item ─────────────────────────────────────────────────────────────────

def test_item_list(appctx):
    admin = _admin()
    _item(name="Item A", stock=10, opening_stock=10, reorder_level=5)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/item")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 13. journal ──────────────────────────────────────────────────────────────

def test_journal(appctx):
    admin = _admin()
    _setup_gl()
    cash_gl = get_account(ACC_CASH_IN_HAND)
    acct = FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                             opening_balance=2000, gl_account_id=cash_gl.id)
    db.session.add(acct)
    db.session.commit()
    post_account_opening(acct)
    db.session.commit()
    assert JournalEntry.query.count() >= 1

    cl = _login(admin)
    r = cl.get("/journal")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 14. leave/allocations ────────────────────────────────────────────────────

def test_leave_allocations(appctx):
    admin = _admin()
    set_module("module_hr", True, updated_by="test")
    set_module("module_leave", True, updated_by="test")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1), active=True)
    db.session.add(emp)
    db.session.commit()
    seed_leave_types()
    lt = LeaveType.query.filter_by(code="ANNUAL").one()
    this_year = date.today().year
    alloc = LeaveAllocation(employee_id=emp.id, leave_type_id=lt.id, year=this_year,
                             days=Decimal("24"))
    db.session.add(alloc)
    db.session.commit()

    cl = _login(admin)
    r = cl.get(f"/leave/allocations?year={this_year}")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 15. leave/index ──────────────────────────────────────────────────────────

def test_leave_index(appctx):
    admin = _admin()
    set_module("module_hr", True, updated_by="test")
    set_module("module_leave", True, updated_by="test")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1), active=True)
    db.session.add(emp)
    db.session.commit()
    seed_leave_types()
    lt = LeaveType.query.filter_by(code="ANNUAL").one()
    this_year = date.today().year
    alloc = LeaveAllocation(employee_id=emp.id, leave_type_id=lt.id, year=this_year,
                             days=Decimal("24"))
    db.session.add(alloc)
    db.session.commit()

    cl = _login(admin)
    r = cl.get(f"/leave/?year={this_year}")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 16. leave/requests ───────────────────────────────────────────────────────

def test_leave_requests(appctx):
    admin = _admin()
    set_module("module_hr", True, updated_by="test")
    set_module("module_leave", True, updated_by="test")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1), active=True)
    db.session.add(emp)
    db.session.commit()
    seed_leave_types()
    lt = LeaveType.query.filter_by(code="ANNUAL").one()
    req = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                        start_date=date(2026, 3, 2), end_date=date(2026, 3, 3),
                        day_portion="full", status="Pending")
    req.recalculate_days()
    db.session.add(req)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/leave/requests")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 17. payroll/advances ─────────────────────────────────────────────────────

def test_payroll_advances(appctx):
    admin = _admin()
    set_module("module_hr", True, updated_by="test")
    set_module("module_payroll", True, updated_by="test")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1), active=True)
    db.session.add(emp)
    db.session.commit()
    adv = EmployeeAdvance(employee_id=emp.id, advance_date=date(2026, 3, 1),
                           amount=Decimal("5000"), recovered=Decimal("1000"), status="Active")
    db.session.add(adv)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/payroll/advances")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 18. payroll/index ────────────────────────────────────────────────────────

def test_payroll_index(appctx):
    admin = _admin()
    set_module("module_hr", True, updated_by="test")
    set_module("module_payroll", True, updated_by="test")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1), active=True)
    db.session.add(emp)
    db.session.commit()
    period = PayrollPeriod(name="June 2026", start_date=date(2026, 6, 1),
                            end_date=date(2026, 6, 30), status="Draft")
    db.session.add(period)
    db.session.flush()
    entry = PayrollEntry(period_id=period.id, employee_id=emp.id,
                          gross_salary=Decimal("30000"), total_deductions=Decimal("2000"),
                          net_salary=Decimal("28000"))
    db.session.add(entry)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/payroll/")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 19. payroll/period_detail ────────────────────────────────────────────────

def test_payroll_period_detail(appctx):
    admin = _admin()
    _setup_gl()
    set_module("module_hr", True, updated_by="test")
    set_module("module_payroll", True, updated_by="test")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1), active=True)
    db.session.add(emp)
    db.session.commit()
    period = PayrollPeriod(name="June 2026", start_date=date(2026, 6, 1),
                            end_date=date(2026, 6, 30), status="Draft")
    db.session.add(period)
    db.session.flush()
    entry = PayrollEntry(period_id=period.id, employee_id=emp.id,
                          gross_salary=Decimal("30000"), total_deductions=Decimal("2000"),
                          net_salary=Decimal("28000"), payable_days=Decimal("30"),
                          overtime_hours=Decimal("2"))
    db.session.add(entry)
    db.session.commit()

    cl = _login(admin)
    r = cl.get(f"/payroll/periods/{period.id}")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 20. payroll/structures ───────────────────────────────────────────────────

def test_payroll_structures(appctx):
    admin = _admin()
    set_module("module_hr", True, updated_by="test")
    set_module("module_payroll", True, updated_by="test")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1), active=True)
    db.session.add(emp)
    db.session.commit()
    seed_default_components()
    basic = SalaryComponent.query.filter_by(code="BASIC").one()
    structure = SalaryStructure(employee_id=emp.id, active=True,
                                 effective_from=date(2025, 1, 1))
    db.session.add(structure)
    db.session.flush()
    db.session.add(SalaryStructureLine(structure_id=structure.id, component_id=basic.id,
                                        amount=Decimal("30000")))
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/payroll/structures")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 21. pos_held_bills ───────────────────────────────────────────────────────

def test_pos_held_bills(appctx):
    admin = _admin()
    c = _customer()
    db.session.commit()
    hold = PosHold(customer_id=c.id, user_id=admin.id,
                    cart_data=json.dumps([{"price": 100, "qty": 2}]), status="held")
    db.session.add(hold)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/pos/held-bills")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 22. purchase ─────────────────────────────────────────────────────────────

def test_purchase_list(appctx):
    admin = _admin()
    s = _supplier()
    it = _item(name="PurchasedItem")
    db.session.commit()
    p = Purchase(supplier_id=s.id, item_id=it.id, quantity=10,
                 purchase_price=Decimal("10"), date=datetime(2026, 3, 1))
    db.session.add(p)
    db.session.flush()
    db.session.add(PurchaseItem(purchase_id=p.id, item_id=it.id, quantity=10,
                                 purchase_price=Decimal("10"), amount=Decimal("100")))
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/purchase")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 23. purchase_orders ──────────────────────────────────────────────────────

def test_purchase_orders(appctx):
    admin = _admin()
    s = _supplier()
    it = _item(name="POItem")
    db.session.commit()
    po = PurchaseOrder(supplier_id=s.id, order_date=datetime(2026, 3, 1), status="Draft")
    db.session.add(po)
    db.session.flush()
    db.session.add(PurchaseOrderItem(po_id=po.id, item_id=it.id, quantity=5,
                                      purchase_price=Decimal("20")))
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/purchase_orders")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 24. purchase_return ──────────────────────────────────────────────────────

def test_purchase_return(appctx):
    admin = _admin()
    s = _supplier()
    it = _item(name="ReturnableItem")
    db.session.commit()
    p = Purchase(supplier_id=s.id, item_id=it.id, quantity=10,
                 purchase_price=Decimal("10"), date=datetime(2026, 3, 1))
    db.session.add(p)
    db.session.flush()
    pi = PurchaseItem(purchase_id=p.id, item_id=it.id, quantity=10,
                       purchase_price=Decimal("10"), amount=Decimal("100"))
    db.session.add(pi)
    db.session.commit()

    pr = PurchaseReturn(purchase_id=p.id, supplier_id=s.id, item_id=it.id, quantity=2,
                         return_price=Decimal("10"), date=datetime(2026, 3, 2),
                         purchase_item_id=pi.id)
    db.session.add(pr)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/purchase_return")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 25. quotations ───────────────────────────────────────────────────────────

def test_quotations(appctx):
    admin = _admin()
    c = _customer()
    it = _item(name="QuoteItem")
    db.session.commit()
    q = Quotation(customer_id=c.id, quote_date=datetime(2026, 3, 1), status="Draft")
    db.session.add(q)
    db.session.flush()
    db.session.add(QuotationItem(quotation_id=q.id, item_id=it.id, quantity=3,
                                  sale_price=Decimal("25")))
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/quotations")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 26. report_reconciliation ────────────────────────────────────────────────

def test_report_reconciliation(appctx):
    admin = _admin()
    _setup_gl()
    c = _customer()
    c.opening_balance = 1500
    s = _supplier()
    s.opening_balance = 800
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/reports/reconciliation")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 27. reports (PARTIES tab: supplier/customer balances) ──────────────────

def test_reports(appctx):
    admin = _admin()
    _setup_gl()
    s = _supplier()
    s.opening_balance = 1000
    c = _customer()
    c.opening_balance = 2000
    db.session.commit()

    cl = _login(admin)
    r = cl.post("/reports", data={"start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Total" in body


# ── 28. sale ─────────────────────────────────────────────────────────────────

def test_sale_list(appctx):
    admin = _admin()
    c = _customer()
    it = _item(name="SoldItem")
    db.session.commit()
    s = Sale(customer_id=c.id, item_id=it.id, quantity=5, sale_price=Decimal("20"),
              date=datetime(2026, 3, 1))
    db.session.add(s)
    db.session.flush()
    db.session.add(SaleItem(sale_id=s.id, item_id=it.id, quantity=5,
                             sale_price=Decimal("20"), amount=Decimal("100")))
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/sale")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 29. sale_return ──────────────────────────────────────────────────────────

def test_sale_return(appctx):
    admin = _admin()
    c = _customer()
    it = _item(name="ReturnableSoldItem")
    db.session.commit()
    s = Sale(customer_id=c.id, item_id=it.id, quantity=5, sale_price=Decimal("20"),
              date=datetime(2026, 3, 1))
    db.session.add(s)
    db.session.flush()
    si = SaleItem(sale_id=s.id, item_id=it.id, quantity=5,
                  sale_price=Decimal("20"), amount=Decimal("100"))
    db.session.add(si)
    db.session.commit()

    sr = SaleReturn(sale_id=s.id, customer_id=c.id, item_id=it.id, quantity=1,
                     return_price=Decimal("20"), date=datetime(2026, 3, 2),
                     sale_item_id=si.id)
    db.session.add(sr)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/sale-return")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 30. supplier_ledger ──────────────────────────────────────────────────────

def test_supplier_ledger(appctx):
    admin = _admin()
    _setup_gl()
    s = _supplier()
    s.opening_balance = 1200
    db.session.commit()
    sync_supplier_opening(s)
    db.session.commit()

    cl = _login(admin)
    r = cl.get(f"/supplier/{s.id}/ledger")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 31. supplier_payment ─────────────────────────────────────────────────────

def test_supplier_payment(appctx):
    admin = _admin()
    _setup_gl()
    s = _supplier()
    db.session.commit()
    p = SupplierPayment(supplier_id=s.id, amount=600, payment_method="Cash",
                         payment_date=datetime(2026, 3, 1))
    db.session.add(p)
    db.session.commit()

    cl = _login(admin)
    r = cl.get("/supplier_payment")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)


# ── 32. transfer_detail ──────────────────────────────────────────────────────

def test_transfer_detail(appctx):
    from salpurflask.models.inventory_location import Location

    admin = _admin()
    src = get_or_create_default_location()
    dest = Location(name="Warehouse 2", active=True, branch_id=src.branch_id)
    db.session.add(dest)
    db.session.flush()
    it = _item(name="TransferredItem", stock=50)
    db.session.commit()

    transfer = Transfer(source_location_id=src.id, destination_location_id=dest.id,
                         date=datetime(2026, 3, 1), status="Draft")
    db.session.add(transfer)
    db.session.flush()
    db.session.add(TransferItem(transfer_id=transfer.id, item_id=it.id, quantity=7))
    db.session.commit()

    cl = _login(admin)
    r = cl.get(f"/transfers/{transfer.id}")
    assert r.status_code == 200
    assert "Total" in r.get_data(as_text=True)
