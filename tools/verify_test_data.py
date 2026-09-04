"""Verify the generated ERP test dataset in PostgreSQL.

Run via the CLI, not directly:
    python tools/test_data_cli.py verify [--verbose]

Every check is read-only. Exits non-zero if any check fails.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._data_common import require_postgres, describe_database_url

DATABASE_URL = require_postgres()

from decimal import Decimal


class Result:
    def __init__(self):
        self.checks = []   # [(label, passed, detail)]

    def add(self, label, passed, detail=""):
        self.checks.append((label, passed, detail))

    @property
    def overall(self):
        return all(p for _, p, _ in self.checks)


def check_database(result, verbose):
    info = describe_database_url(DATABASE_URL)
    from app import db
    dialect = db.engine.dialect.name
    ok = dialect == "postgresql"
    result.add("Database", ok, f"dialect={dialect} host={info['host']} db={info['database']}")


def check_schema(result, verbose):
    from app import db
    inspector = db.inspect(db.engine)
    tables = set(inspector.get_table_names())
    expected = 72
    ok = len(tables) >= expected
    result.add("Schema", ok, f"{len(tables)} tables found (expected >= {expected})")

    # Foreign key sanity: every FK's target table must exist among our tables.
    fk_errors = []
    for t in tables:
        for fk in inspector.get_foreign_keys(t):
            target = fk.get("referred_table")
            if target and target not in tables:
                fk_errors.append(f"{t} -> {target}")
    result.add("Foreign keys", len(fk_errors) == 0,
              f"{len(fk_errors)} dangling references" if fk_errors else "all resolve")

    pk_missing = [t for t in tables if not inspector.get_pk_constraint(t).get("constrained_columns")]
    result.add("Primary keys", len(pk_missing) == 0,
              f"missing on: {pk_missing}" if pk_missing else "all tables have a PK")


def check_business_categories(result, verbose):
    """The authoritative category system — see the Category-architecture fix.
    BusinessCategory is what Business Configuration manages and what the
    live Item form's dropdown reads via ConfigurationService.get_enabled_categories();
    the legacy Category table must play no part in any generated Item.

    The categories checked here are SYSTEM DEFAULT MASTER DATA, seeded by
    app.py's migrate_database() (ensure_default_business_categories()) — not
    generator-owned test data — so this check passes on a freshly
    initialized database even before the Phase 3 generator ever runs."""
    from app import db
    from salpurflask.models.business_config import BusinessCategory
    from salpurflask.services.category_catalog import DEFAULT_BUSINESS_CATEGORIES

    default_slugs = [slug for _name, slug, _icon, _color, _priority in DEFAULT_BUSINESS_CATEGORIES]

    defaults = (BusinessCategory.query
               .filter(BusinessCategory.slug.in_(default_slugs))
               .all())
    found_slugs = {c.slug for c in defaults}
    missing_slugs = set(default_slugs) - found_slugs
    count_ok = len(defaults) == len(default_slugs)
    result.add("Business Categories: count", count_ok,
              f"{len(defaults)}/{len(default_slugs)} default categories present"
              + (f", missing: {sorted(missing_slugs)}" if missing_slugs else ""))

    disabled = [c.slug for c in defaults if not c.is_enabled]
    result.add("Business Categories: enabled", len(disabled) == 0,
              f"{len(disabled)} default categories are disabled: {disabled}" if disabled
              else "all default categories are enabled")

    invalid_fields = [c.slug for c in defaults if not c.name or not c.slug]
    result.add("Business Categories: required fields", len(invalid_fields) == 0,
              f"missing name/slug on: {invalid_fields}" if invalid_fields
              else "name and slug present on all default categories")

    not_tagged = [c.slug for c in defaults if not (c.config_data or {}).get("is_system_default")]
    result.add("Business Categories: tagged as system default", len(not_tagged) == 0,
              f"missing is_system_default tag: {not_tagged}" if not_tagged
              else "all default categories are tagged is_system_default")

    dup_names = _duplicates("business_category", "name")
    dup_slugs = _duplicates("business_category", "slug")
    result.add("Business Categories: no duplicates", len(dup_names) == 0 and len(dup_slugs) == 0,
              f"duplicate names={len(dup_names)} duplicate slugs={len(dup_slugs)}")


def check_default_product_fields(result, verbose):
    """The default ProductFields seeded for each of the 26 default
    categories — system master data, the same tier as the categories
    themselves. See ensure_default_product_fields()."""
    from salpurflask.models.business_config import BusinessCategory, ProductField
    from salpurflask.services.category_catalog import DEFAULT_PRODUCT_FIELDS

    missing = []
    wrong_required = []
    wrong_type = []
    wrong_options = []
    not_active = []
    not_tagged_default = []

    for slug, specs in DEFAULT_PRODUCT_FIELDS.items():
        cat = BusinessCategory.query.filter_by(slug=slug).first()
        if cat is None:
            missing.append((slug, "CATEGORY MISSING"))
            continue
        fields_by_name = {f.field_name: f for f in
                          ProductField.query.filter_by(category_id=cat.id).all()}
        for spec in specs:
            f = fields_by_name.get(spec["field_name"])
            if f is None:
                missing.append((slug, spec["field_name"]))
                continue
            if bool(f.is_required) != bool(spec.get("is_required", False)):
                wrong_required.append((slug, spec["field_name"]))
            if f.field_type != spec["field_type"]:
                wrong_type.append((slug, spec["field_name"]))
            if spec.get("options") and (f.options or None) != spec.get("options"):
                wrong_options.append((slug, spec["field_name"]))
            if not f.is_active:
                not_active.append((slug, spec["field_name"]))
            if not f.is_system_default:
                not_tagged_default.append((slug, spec["field_name"]))

    result.add("Default Product Fields: present", len(missing) == 0,
              f"{len(missing)} expected default fields missing"
              + (f": {missing[:10]}" if missing else ""))
    result.add("Default Product Fields: required flags correct", len(wrong_required) == 0,
              f"{len(wrong_required)} fields have the wrong is_required value"
              + (f": {wrong_required[:10]}" if wrong_required else ""))
    result.add("Default Product Fields: types correct", len(wrong_type) == 0,
              f"{len(wrong_type)} fields have the wrong field_type"
              + (f": {wrong_type[:10]}" if wrong_type else ""))
    result.add("Default Product Fields: options correct", len(wrong_options) == 0,
              f"{len(wrong_options)} select fields have unexpected options"
              + (f": {wrong_options[:10]}" if wrong_options else ""))
    result.add("Default Product Fields: active", len(not_active) == 0,
              f"{len(not_active)} default fields are inactive"
              + (f": {not_active[:10]}" if not_active else ""))
    result.add("Default Product Fields: tagged system default", len(not_tagged_default) == 0,
              f"{len(not_tagged_default)} default fields are not tagged is_system_default"
              + (f": {not_tagged_default[:10]}" if not_tagged_default else ""))

    # No duplicate (category_id, field_name) pairs — the DB's own
    # UniqueConstraint already prevents this at write time, but a direct
    # check here proves it holds, the same defense-in-depth pattern the
    # other duplicate checks in this file already use.
    dup_rows = _duplicates_two_cols("product_field", "category_id", "field_name")
    result.add("Default Product Fields: no duplicates", len(dup_rows) == 0,
              f"{len(dup_rows)} duplicate (category_id, field_name) pairs")


def check_master_data(result, verbose):
    from app import db
    from salpurflask.models import Item, Supplier, Customer
    from salpurflask.models.hr import Employee
    from salpurflask.models.business_config import BusinessCategory

    items = Item.query.filter(Item.sku.like("ITEM-%")).count()
    suppliers = Supplier.query.count()
    customers = Customer.query.count()
    employees = Employee.query.count()

    dup_skus = _duplicates("item", "sku", where="sku IS NOT NULL")
    dup_emp_codes = _duplicates("hr_employee", "code")

    # ── The hard rule, checked against the actual generated rows ──────────
    # Every STOCK/SERVICE item this generator created must carry a valid,
    # enabled business_category_id and a NULL legacy category_id — exactly
    # what the live /item form now enforces at the backend, never a
    # generator-only shortcut.
    generated_items = Item.query.filter(
        (Item.sku.like("ITEM-%")) | (Item.sku.like("SVC-%"))).all()

    no_business_category = [i.id for i in generated_items if i.business_category_id is None]
    has_legacy_category = [i.id for i in generated_items if i.category_id is not None]

    orphan_refs = []
    disabled_refs = []
    if generated_items:
        cat_ids = {i.business_category_id for i in generated_items if i.business_category_id is not None}
        cats_by_id = {c.id: c for c in BusinessCategory.query.filter(BusinessCategory.id.in_(cat_ids)).all()}
        for i in generated_items:
            if i.business_category_id is None:
                continue
            cat = cats_by_id.get(i.business_category_id)
            if cat is None:
                orphan_refs.append(i.id)
            elif not cat.is_enabled:
                disabled_refs.append(i.id)

    category_rule_ok = (not no_business_category and not has_legacy_category
                        and not orphan_refs and not disabled_refs)
    result.add("Items: BusinessCategory rule", category_rule_ok,
              f"missing_business_category={len(no_business_category)} "
              f"legacy_category_set={len(has_legacy_category)} "
              f"orphan_business_category_refs={len(orphan_refs)} "
              f"disabled_business_category_refs={len(disabled_refs)}")
    if verbose and not category_rule_ok:
        if no_business_category:
            result.checks[-1] = (result.checks[-1][0], result.checks[-1][1],
                                 result.checks[-1][2] + f"\n    items with no business_category_id: {no_business_category[:10]}")
        if has_legacy_category:
            result.checks[-1] = (result.checks[-1][0], result.checks[-1][1],
                                 result.checks[-1][2] + f"\n    items still using legacy category_id: {has_legacy_category[:10]}")

    ok = items > 0 and suppliers > 0 and customers > 0 and employees > 0 and not dup_skus and not dup_emp_codes and category_rule_ok
    detail = (f"items={items} suppliers={suppliers} customers={customers} "
             f"employees={employees} dup_skus={len(dup_skus)} dup_emp_codes={len(dup_emp_codes)}")
    result.add("Master Data", ok, detail)


def _duplicates(table, column, where=None):
    from app import db
    sql = f'SELECT "{column}", COUNT(*) c FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    sql += f' GROUP BY "{column}" HAVING COUNT(*) > 1'
    rows = db.session.execute(db.text(sql)).fetchall()
    return rows


def check_inventory(result, verbose):
    from app import db
    negative = db.session.execute(db.text(
        "SELECT COUNT(*) FROM item_stock WHERE quantity < 0")).scalar()

    # Stock movement consistency: for a sample check, item.stock should equal
    # the sum of that item's ItemStock rows across all locations.
    mismatch_rows = db.session.execute(db.text("""
        SELECT i.id, i.stock, COALESCE(SUM(s.quantity), 0) AS summed
        FROM item i
        LEFT JOIN item_stock s ON s.item_id = i.id
        WHERE i.item_type = 'STOCK'
        GROUP BY i.id, i.stock
        HAVING i.stock <> COALESCE(SUM(s.quantity), 0)
    """)).fetchall()

    ok = negative == 0 and len(mismatch_rows) == 0
    detail = f"negative_locations={negative} stock_mismatches={len(mismatch_rows)}"
    if verbose and mismatch_rows:
        for row in mismatch_rows[:10]:
            detail += f"\n    item#{row[0]}: Item.stock={row[1]} sum(ItemStock)={row[2]}"
    result.add("Inventory", ok, detail)


def check_purchases(result, verbose):
    from salpurflask.models import Purchase, PurchaseReturn
    dup_invoices = _duplicates("purchase", "invoice_no", where="invoice_no IS NOT NULL")
    count = Purchase.query.count()
    ok = count > 0 and not dup_invoices
    result.add("Purchases", ok, f"purchases={count} dup_invoice_numbers={len(dup_invoices)}")


def check_sales(result, verbose):
    from salpurflask.models import Sale
    dup_invoices = _duplicates("sale", "invoice_no", where="invoice_no IS NOT NULL")
    count = Sale.query.count()
    ok = count > 0 and not dup_invoices
    result.add("Sales", ok, f"sales={count} dup_invoice_numbers={len(dup_invoices)}")


def check_customer_ledger(result, verbose):
    # NOTE: app.py's get_customer_balance() reads the ledger's own running
    # balance_after when any ledger entry exists, and only falls back to
    # Customer.opening_balance when there are NO entries at all — opening
    # balance is never added on top of a populated ledger (confirmed in
    # app.py:811-824; there is also no "opening" SOURCE_TYPE entry ever
    # written to CustomerLedgerEntry for a plain opening_balance column).
    # So the correct reconciliation here is sale_total - receipts - returns,
    # WITHOUT opening_balance, whenever ledger entries exist for that customer.
    from app import db
    from salpurflask.models import Customer, CustomerLedgerEntry
    mismatches = []
    for cust in Customer.query.all():
        last = (CustomerLedgerEntry.query.filter_by(customer_id=cust.id)
               .order_by(CustomerLedgerEntry.entry_date.desc(),
                        CustomerLedgerEntry.id.desc()).first())
        if last is None:
            continue
        sale_total = db.session.execute(db.text("""
            SELECT COALESCE(SUM(si.amount), 0) FROM sale s
            JOIN sale_item si ON si.sale_id = s.id
            WHERE s.customer_id = :cid AND s.is_reversed = false
        """), {"cid": cust.id}).scalar() or 0
        receipts = db.session.execute(db.text("""
            SELECT COALESCE(SUM(amount), 0) FROM customer_payment
            WHERE customer_id = :cid AND is_reversed = false
        """), {"cid": cust.id}).scalar() or 0
        returns = db.session.execute(db.text("""
            SELECT COALESCE(SUM(quantity * return_price), 0) FROM sale_return
            WHERE customer_id = :cid AND is_reversed = false
        """), {"cid": cust.id}).scalar() or 0
        expected = Decimal(str(sale_total)) - Decimal(str(receipts)) - Decimal(str(returns))
        actual = Decimal(str(last.balance_after or 0))
        if abs(expected - actual) > Decimal("1.00"):
            mismatches.append((cust.id, str(expected), str(actual)))
    ok = len(mismatches) == 0
    detail = f"{len(mismatches)} customers with ledger drift > 1.00"
    if verbose and mismatches:
        for cid, exp, act in mismatches[:10]:
            detail += f"\n    customer#{cid}: expected={exp} actual={act}"
    result.add("Customer Ledger", ok, detail)


def check_supplier_ledger(result, verbose):
    # See the matching note in check_customer_ledger — opening_balance is a
    # fallback used only when no ledger entries exist, never added on top of
    # a populated ledger's own running balance_after.
    from app import db
    from salpurflask.models import Supplier, SupplierLedgerEntry
    mismatches = []
    for sup in Supplier.query.all():
        last = (SupplierLedgerEntry.query.filter_by(supplier_id=sup.id)
               .order_by(SupplierLedgerEntry.entry_date.desc(),
                        SupplierLedgerEntry.id.desc()).first())
        if last is None:
            continue
        purchase_total = db.session.execute(db.text("""
            SELECT COALESCE(SUM(pi.amount), 0) FROM purchase p
            JOIN purchase_item pi ON pi.purchase_id = p.id
            WHERE p.supplier_id = :sid AND p.is_reversed = false
        """), {"sid": sup.id}).scalar() or 0
        payments = db.session.execute(db.text("""
            SELECT COALESCE(SUM(amount), 0) FROM supplier_payment
            WHERE supplier_id = :sid AND is_reversed = false
        """), {"sid": sup.id}).scalar() or 0
        returns = db.session.execute(db.text("""
            SELECT COALESCE(SUM(quantity * return_price), 0) FROM purchase_return
            WHERE supplier_id = :sid AND is_reversed = false
        """), {"sid": sup.id}).scalar() or 0
        expected = Decimal(str(purchase_total)) - Decimal(str(payments)) - Decimal(str(returns))
        actual = Decimal(str(last.balance_after or 0))
        if abs(expected - actual) > Decimal("1.00"):
            mismatches.append((sup.id, str(expected), str(actual)))
    ok = len(mismatches) == 0
    detail = f"{len(mismatches)} suppliers with ledger drift > 1.00"
    if verbose and mismatches:
        for sid, exp, act in mismatches[:10]:
            detail += f"\n    supplier#{sid}: expected={exp} actual={act}"
    result.add("Supplier Ledger", ok, detail)


def check_accounting(result, verbose):
    from salpurflask.models import JournalEntry, JournalLine
    from app import db

    unbalanced = db.session.execute(db.text("""
        SELECT je.id FROM journal_entry je
        JOIN journal_line jl ON jl.entry_id = je.id
        GROUP BY je.id
        HAVING SUM(jl.debit) <> SUM(jl.credit)
    """)).fetchall()

    orphans = db.session.execute(db.text("""
        SELECT je.id FROM journal_entry je
        LEFT JOIN journal_line jl ON jl.entry_id = je.id
        WHERE jl.id IS NULL
    """)).fetchall()

    dup_postings = db.session.execute(db.text("""
        SELECT source_type, source_id, COUNT(*) c
        FROM journal_entry
        WHERE source_type IS NOT NULL AND source_id IS NOT NULL
          AND reversal_of_id IS NULL AND is_reversed = false
        GROUP BY source_type, source_id
        HAVING COUNT(*) > 1
    """)).fetchall()

    ok = len(unbalanced) == 0 and len(orphans) == 0 and len(dup_postings) == 0
    detail = (f"unbalanced={len(unbalanced)} orphan_entries={len(orphans)} "
             f"duplicate_postings={len(dup_postings)}")
    result.add("Accounting", ok, detail)


def check_hr(result, verbose):
    from salpurflask.models.hr import Employee, Department, Designation
    employees = Employee.query.count()
    ok = employees > 0
    result.add("HR", ok, f"employees={employees}")


def check_attendance(result, verbose):
    dup_rows = _duplicates_two_cols("hr_attendance", "employee_id", "date")
    ok = len(dup_rows) == 0
    result.add("Attendance", ok, f"duplicate (employee, date) rows: {len(dup_rows)}")


def _duplicates_two_cols(table, col_a, col_b):
    from app import db
    sql = (f'SELECT "{col_a}", "{col_b}", COUNT(*) c FROM "{table}" '
          f'GROUP BY "{col_a}", "{col_b}" HAVING COUNT(*) > 1')
    return db.session.execute(db.text(sql)).fetchall()


def check_leave(result, verbose):
    from app import db
    from salpurflask.models import JournalEntry
    from salpurflask.models.leave import LeaveAllocation
    negative_alloc = db.session.execute(db.text(
        "SELECT COUNT(*) FROM hr_leave_allocation WHERE days < 0")).scalar()

    # Approved leave compatible with finalized payroll: no Approved request
    # whose dates fall inside a period that was already Finalized BEFORE the
    # request was decided (the generator always approves before finalizing,
    # so this should be zero).
    bad_approvals = db.session.execute(db.text("""
        SELECT lr.id FROM hr_leave_request lr
        JOIN hr_payroll_period pp
          ON pp.status = 'Finalized'
         AND lr.start_date <= pp.end_date AND lr.end_date >= pp.start_date
        WHERE lr.status = 'Approved'
          AND lr.decided_at IS NOT NULL
          AND pp.finalized_at IS NOT NULL
          AND lr.decided_at > pp.finalized_at
    """)).fetchall()

    ok = negative_alloc == 0 and len(bad_approvals) == 0
    result.add("Leave", ok,
              f"negative_allocations={negative_alloc} post_finalize_approvals={len(bad_approvals)}")


def check_payroll(result, verbose):
    from salpurflask.models import PayrollPeriod, PayrollEntry
    from salpurflask.services import payroll_accounting as accounting
    from salpurflask.models.payroll_payment import period_payable_balance, period_paid_total

    july = PayrollPeriod.query.filter_by(name="July 2026").first()
    august = PayrollPeriod.query.filter_by(name="August 2026").first()

    detail_parts = []
    ok = True

    for label, period in (("July", july), ("August", august)):
        if period is None:
            ok = False
            detail_parts.append(f"{label}=MISSING")
            continue
        entries = period.entries.all()
        gross_ok = all(
            abs(Decimal(str(e.total_earnings)) - Decimal(str(e.total_deductions))
                - Decimal(str(e.net_salary))) < Decimal("0.01")
            for e in entries
        )
        status = accounting.accounting_status(period)
        paid = period_paid_total(period)
        balance = period_payable_balance(period)
        over_paid = balance < 0
        finalized = period.status == "Finalized"
        posted = status == "POSTED"
        this_ok = finalized and posted and gross_ok and not over_paid and len(entries) > 0
        ok = ok and this_ok
        detail_parts.append(
            f"{label}: entries={len(entries)} status={period.status} gl={status} "
            f"paid={paid} balance={balance} net_calc_ok={gross_ok}")

    result.add("July Payroll", july is not None and july.status == "Finalized",
              detail_parts[0] if detail_parts else "missing")
    result.add("August Payroll", august is not None and august.status == "Finalized",
              detail_parts[1] if len(detail_parts) > 1 else "missing")
    result.add("Payroll Accounting", ok, " | ".join(detail_parts))


def check_duplicates(result, verbose):
    dup_docnum_purchase = _duplicates("purchase", "invoice_no", where="invoice_no IS NOT NULL")
    dup_docnum_sale = _duplicates("sale", "invoice_no", where="invoice_no IS NOT NULL")
    dup_periods = _duplicates("hr_payroll_period", "name")
    dup_emp_codes = _duplicates("hr_employee", "code")
    dup_skus = _duplicates("item", "sku", where="sku IS NOT NULL")
    dup_attendance = _duplicates_two_cols("hr_attendance", "employee_id", "date")
    dup_category_names = _duplicates("business_category", "name")
    dup_category_slugs = _duplicates("business_category", "slug")

    total = (len(dup_docnum_purchase) + len(dup_docnum_sale) + len(dup_periods)
            + len(dup_emp_codes) + len(dup_skus) + len(dup_attendance)
            + len(dup_category_names) + len(dup_category_slugs))
    ok = total == 0
    result.add("Duplicate Check", ok, f"{total} duplicate rows found across all identity checks")


def run(verbose=False):
    from app import app

    with app.app_context():
        result = Result()
        check_database(result, verbose)
        check_schema(result, verbose)
        check_business_categories(result, verbose)
        check_default_product_fields(result, verbose)
        check_master_data(result, verbose)
        check_inventory(result, verbose)
        check_purchases(result, verbose)
        check_sales(result, verbose)
        check_customer_ledger(result, verbose)
        check_supplier_ledger(result, verbose)
        check_accounting(result, verbose)
        check_hr(result, verbose)
        check_attendance(result, verbose)
        check_leave(result, verbose)
        check_payroll(result, verbose)
        check_duplicates(result, verbose)

        print("=" * 40)
        print("TRADEFLOW TEST DATA VERIFICATION")
        print("=" * 40)
        print()
        for label, passed, detail in result.checks:
            status = "PASS" if passed else "FAIL"
            line = f"{label:<22} {status}"
            print(line)
            if verbose or not passed:
                for detail_line in detail.split("\n"):
                    print(f"    {detail_line}")
        print("=" * 40)
        overall = "PASS" if result.overall else "FAIL"
        print(f"OVERALL: {overall}")
        print("=" * 40)

        return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Verify TradeFlow ERP test data (PostgreSQL only)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    result = run(verbose=args.verbose)
    sys.exit(0 if result.overall else 1)
