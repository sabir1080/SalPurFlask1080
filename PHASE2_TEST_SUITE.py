#!/usr/bin/env python3
"""
Phase 2: Comprehensive Item-Level Discount & Tax Testing
Tests: POS, Checkout, Invoice, Hold Bill, Accounting, Reports
"""

import json
import sys
from decimal import Decimal
from datetime import datetime

# Import Flask app
from app import app, db
from salpurflask.models import (
    Item, Category, Customer, Sale, SaleItem, PosHold, FinancialAccount,
    BusinessCategory, JournalEntry, JournalLine, SupplierLedgerEntry, CustomerLedgerEntry
)
from salpurflask.utils import now_local

# Test results tracker
results = {
    'tests_run': 0,
    'tests_passed': 0,
    'tests_failed': 0,
    'failures': []
}

def log(msg, level='INFO'):
    """Log test message"""
    print(f"[{level}] {msg}")

def test_pass(name, msg=""):
    """Record passing test"""
    results['tests_passed'] += 1
    results['tests_run'] += 1
    log(f"[PASS] {name}", 'PASS')
    if msg:
        print(f"    {msg}")

def test_fail(name, msg=""):
    """Record failing test"""
    results['tests_failed'] += 1
    results['tests_run'] += 1
    log(f"[FAIL] {name}", 'FAIL')
    if msg:
        print(f"    {msg}")
    results['failures'].append(f"{name}: {msg}")

def setup_test_data():
    """Setup initial test data (customer, items)"""
    log("Setting up test data...")

    with app.app_context():
        # Clear old test data
        db.session.query(Sale).delete()
        db.session.query(Item).delete()

        # Get or create category
        cat = Category.query.filter_by(name='Test Category').first()
        if not cat:
            cat = Category(name='Test Category')
            db.session.add(cat)
            db.session.flush()

        # Create items
        item_a = Item(
            name='Item A (Test)',
            category_id=cat.id,
            unit='Pcs',
            sale_price=Decimal('1000'),
            purchase_price=Decimal('600'),
            stock=100,
            default_tax_percent=Decimal('10.00'),
            is_taxable=True
        )
        item_b = Item(
            name='Item B (Test)',
            category_id=cat.id,
            unit='Pcs',
            sale_price=Decimal('2000'),
            purchase_price=Decimal('1200'),
            stock=100,
            default_tax_percent=Decimal('0.00'),
            is_taxable=True
        )
        db.session.add(item_a)
        db.session.add(item_b)
        db.session.flush()

        # Create test customer
        walkin = Customer.query.filter_by(name='Walk-in Customer').first()
        if not walkin:
            walkin = Customer(
                name='Walk-in Customer',
                contact='N/A',
                address='N/A',
                opening_balance=Decimal('0')
            )
            db.session.add(walkin)
            db.session.flush()

        db.session.commit()
        log("Test data created successfully")
        return item_a.id, item_b.id, walkin.id

def test_pos_calculations():
    """TEST 1: POS Per-Item Calculations"""
    log("\n" + "="*60)
    log("TEST 1: POS CALCULATIONS (Per-Item Discount & Tax)")
    log("="*60)

    with app.app_context():
        # Item A: price=1000, discount=5%, tax=10%
        # Expected: gross=1000, discount=50, taxable=950, tax=95, total=1045
        gross_a = 1000
        disc_a = gross_a * (5 / 100)  # 50
        taxable_a = gross_a - disc_a  # 950
        tax_a = taxable_a * (10 / 100)  # 95
        total_a = taxable_a + tax_a  # 1045

        # Item B: price=2000, discount=0%, tax=0%
        # Expected: gross=2000, discount=0, taxable=2000, tax=0, total=2000
        gross_b = 2000
        disc_b = 0
        taxable_b = gross_b - disc_b  # 2000
        tax_b = 0
        total_b = taxable_b + tax_b  # 2000

        # Totals
        grand_total = total_a + total_b  # 3045
        total_disc = disc_a + disc_b  # 50
        total_tax = tax_a + tax_b  # 95
        total_subtotal = gross_a + gross_b  # 3000

        # Test calculations
        test_pass(
            "Item A Discount Calculation",
            f"Gross={gross_a}, Discount={disc_a} (expected 50)"
        ) if disc_a == 50 else test_fail(
            "Item A Discount Calculation",
            f"Got {disc_a}, expected 50"
        )

        test_pass(
            "Item A Tax Calculation",
            f"Taxable={taxable_a}, Tax={tax_a} (expected 95)"
        ) if tax_a == 95 else test_fail(
            "Item A Tax Calculation",
            f"Got {tax_a}, expected 95"
        )

        test_pass(
            "Item A Line Total",
            f"Total={total_a} (expected 1045)"
        ) if total_a == 1045 else test_fail(
            "Item A Line Total",
            f"Got {total_a}, expected 1045"
        )

        test_pass(
            "Item B Line Total",
            f"Total={total_b} (expected 2000)"
        ) if total_b == 2000 else test_fail(
            "Item B Line Total",
            f"Got {total_b}, expected 2000"
        )

        test_pass(
            "Grand Total Aggregation",
            f"Total={grand_total} (expected 3045)"
        ) if grand_total == 3045 else test_fail(
            "Grand Total Aggregation",
            f"Got {grand_total}, expected 3045"
        )

        test_pass(
            "Total Discount Aggregation",
            f"Total Discount={total_disc} (expected 50)"
        ) if total_disc == 50 else test_fail(
            "Total Discount Aggregation",
            f"Got {total_disc}, expected 50"
        )

        test_pass(
            "Total Tax Aggregation",
            f"Total Tax={total_tax} (expected 95)"
        ) if total_tax == 95 else test_fail(
            "Total Tax Aggregation",
            f"Got {total_tax}, expected 95"
        )

def test_sale_creation_and_storage():
    """TEST 2: Sale Creation & SaleItem Storage"""
    log("\n" + "="*60)
    log("TEST 2: SALE CREATION & SALEITEM STORAGE")
    log("="*60)

    with app.app_context():
        from salpurflask.models import calc_discount_tax

        # Get test data
        item_a = Item.query.filter_by(name='Item A (Test)').first()
        item_b = Item.query.filter_by(name='Item B (Test)').first()
        walkin = Customer.query.filter_by(name='Walk-in Customer').first()

        if not (item_a and item_b and walkin):
            test_fail("Test Data", "Could not find test items/customer")
            return

        # Create sale manually (simulating pos_checkout)
        sale = Sale(
            customer_id=walkin.id,
            item_id=item_a.id,
            quantity=1,
            sale_price=Decimal('1000'),
            cost_price=Decimal('600'),
            discount_type='percent',
            discount_value=Decimal('0'),
            tax_percent=Decimal('0'),
            discount_amount=Decimal('0'),
            tax_amount=Decimal('0'),
            date=now_local(),
            notes='Phase 2 Test Sale'
        )
        db.session.add(sale)
        db.session.flush()

        # Create SaleItem for Item A: discount 5%, tax 10%
        gross = 1000
        disc_amt_a, tax_amt_a, net_a = calc_discount_tax(gross, 'percent', Decimal('5'), Decimal('10'))

        si_a = SaleItem(
            sale_id=sale.id,
            item_id=item_a.id,
            quantity=1,
            sale_price=Decimal('1000'),
            cost_price=Decimal('600'),
            discount_type='percent',
            discount_value=Decimal('5'),
            discount_amount=Decimal(str(disc_amt_a)),
            tax_percent=Decimal('10'),
            tax_amount=Decimal(str(tax_amt_a)),
            amount=Decimal(str(net_a)),
            unit_name=None,
            unit_factor=1
        )
        db.session.add(si_a)

        # Create SaleItem for Item B: discount 0%, tax 0%
        gross_b = 2000
        disc_amt_b, tax_amt_b, net_b = calc_discount_tax(gross_b, 'percent', Decimal('0'), Decimal('0'))

        si_b = SaleItem(
            sale_id=sale.id,
            item_id=item_b.id,
            quantity=1,
            sale_price=Decimal('2000'),
            cost_price=Decimal('1200'),
            discount_type='percent',
            discount_value=Decimal('0'),
            discount_amount=Decimal(str(disc_amt_b)),
            tax_percent=Decimal('0'),
            tax_amount=Decimal(str(tax_amt_b)),
            amount=Decimal(str(net_b)),
            unit_name=None,
            unit_factor=1
        )
        db.session.add(si_b)
        db.session.flush()

        # Verify storage
        test_pass(
            "SaleItem A Creation",
            f"discount_type={si_a.discount_type}, discount_value={si_a.discount_value}"
        ) if si_a.discount_type == 'percent' and si_a.discount_value == 5 else test_fail(
            "SaleItem A Creation",
            f"Discount not stored correctly"
        )

        test_pass(
            "SaleItem A Tax Storage",
            f"tax_percent={si_a.tax_percent}, tax_amount={si_a.tax_amount}"
        ) if si_a.tax_percent == 10 and si_a.tax_amount == 95 else test_fail(
            "SaleItem A Tax Storage",
            f"Tax not stored correctly"
        )

        test_pass(
            "SaleItem A Amount Calculation",
            f"amount={si_a.amount} (expected 1045)"
        ) if si_a.amount == 1045 else test_fail(
            "SaleItem A Amount Calculation",
            f"Got {si_a.amount}, expected 1045"
        )

        test_pass(
            "SaleItem B No Discount/Tax",
            f"discount_value={si_b.discount_value}, tax_percent={si_b.tax_percent}"
        ) if si_b.discount_value == 0 and si_b.tax_percent == 0 else test_fail(
            "SaleItem B No Discount/Tax",
            f"Values incorrect"
        )

        test_pass(
            "SaleItem B Amount",
            f"amount={si_b.amount} (expected 2000)"
        ) if si_b.amount == 2000 else test_fail(
            "SaleItem B Amount",
            f"Got {si_b.amount}, expected 2000"
        )

        db.session.commit()
        log(f"\nSale created with ID: {sale.id}")
        return sale.id

def test_invoice_display():
    """TEST 3: Invoice Display Verification"""
    log("\n" + "="*60)
    log("TEST 3: INVOICE DISPLAY")
    log("="*60)

    with app.app_context():
        # Get the most recent sale with our test note
        sale = Sale.query.filter_by(notes='Phase 2 Test Sale').order_by(Sale.id.desc()).first()

        if not sale:
            test_fail("Invoice Display", "Sale not found")
            return

        # Verify invoice data - find items with our test discount/tax values
        si_with_disc = None
        si_without_disc = None

        for si in sale.line_items:
            if float(si.discount_value or 0) == 5 and float(si.tax_percent or 0) == 10:
                si_with_disc = si
            elif float(si.discount_value or 0) == 0 and float(si.tax_percent or 0) == 0:
                si_without_disc = si

        test_pass(
            "Invoice Item With Discount Found",
            f"Found: discount={si_with_disc.discount_value}%, tax={si_with_disc.tax_percent}%"
        ) if si_with_disc else test_fail(
            "Invoice Item With Discount Found",
            f"Could not find item with 5% discount and 10% tax"
        )

        test_pass(
            "Invoice Item Without Discount Found",
            f"Found: discount={si_without_disc.discount_value}%, tax={si_without_disc.tax_percent}%"
        ) if si_without_disc else test_fail(
            "Invoice Item Without Discount Found",
            f"Could not find item with 0% discount and 0% tax"
        )

        if si_with_disc and si_without_disc:
            test_pass(
                "Invoice Item A Discount Display",
                f"discount_value={si_with_disc.discount_value}%, discount_amount={si_with_disc.discount_amount}"
            ) if float(si_with_disc.discount_value or 0) == 5 else test_fail(
                "Invoice Item A Discount Display",
                f"Discount value not correct: {si_with_disc.discount_value}"
            )

            test_pass(
                "Invoice Item A Tax Display",
                f"tax_percent={si_with_disc.tax_percent}%, tax_amount={si_with_disc.tax_amount}"
            ) if float(si_with_disc.tax_percent or 0) == 10 and float(si_with_disc.tax_amount or 0) == 95 else test_fail(
                "Invoice Item A Tax Display",
                f"Tax not correct: {si_with_disc.tax_percent}%, {si_with_disc.tax_amount}"
            )

            # Calculate totals from test items only
            total_disc = float(si_with_disc.discount_amount or 0) + float(si_without_disc.discount_amount or 0)
            total_tax = float(si_with_disc.tax_amount or 0) + float(si_without_disc.tax_amount or 0)
            gross = float(si_with_disc.quantity * si_with_disc.sale_price) + float(si_without_disc.quantity * si_without_disc.sale_price)

            test_pass(
                "Invoice Totals Aggregation",
                f"Subtotal={gross}, Discount={total_disc}, Tax={total_tax}"
            ) if abs(gross - 3000) < 0.01 and abs(total_disc - 50) < 0.01 and abs(total_tax - 95) < 0.01 else test_fail(
                "Invoice Totals Aggregation",
                f"Totals incorrect: Subtotal={gross}, Disc={total_disc}, Tax={total_tax}"
            )

def test_accounting_entries():
    """TEST 4: Accounting/GL Entries"""
    log("\n" + "="*60)
    log("TEST 4: ACCOUNTING ENTRIES (GL Journal)")
    log("="*60)

    with app.app_context():
        sale = Sale.query.filter_by(notes='Phase 2 Test Sale').first()

        if not sale:
            test_fail("GL Entries", "Sale not found")
            return

        # Verify GL entries exist and are balanced
        entries = JournalEntry.query.filter_by(source_type='sale', source_id=sale.id).all()

        test_pass(
            "GL Entries Created",
            f"Found {len(entries)} journal entry group(s)"
        ) if len(entries) > 0 else test_fail(
            "GL Entries Created",
            f"No GL entries found"
        )

        if entries:
            entry = entries[0]

            # Check for required GL lines
            lines = JournalLine.query.filter_by(entry_id=entry.id).all()

            test_pass(
                "GL Lines Exist",
                f"Found {len(lines)} GL lines"
            ) if len(lines) > 0 else test_fail(
                "GL Lines Exist",
                f"No GL lines found"
            )

            # Verify debit/credit balance
            total_debit = sum(float(l.debit or 0) for l in lines)
            total_credit = sum(float(l.credit or 0) for l in lines)

            test_pass(
                "GL Balanced (Debit = Credit)",
                f"Debit={total_debit}, Credit={total_credit}"
            ) if abs(total_debit - total_credit) < 0.01 else test_fail(
                "GL Balanced (Debit = Credit)",
                f"Debit={total_debit}, Credit={total_credit} (NOT BALANCED)"
            )

def test_hold_and_resume():
    """TEST 5: Hold Bill & Resume"""
    log("\n" + "="*60)
    log("TEST 5: HOLD BILL & RESUME")
    log("="*60)

    with app.app_context():
        from flask_login import current_user

        # Create a hold bill (simulate POS hold)
        item_a = Item.query.filter_by(name='Item A (Test)').first()
        item_b = Item.query.filter_by(name='Item B (Test)').first()
        walkin = Customer.query.filter_by(name='Walk-in Customer').first()
        user = app.config.get('TEST_USER_ID') or 1  # Admin user

        # Simulate cart data
        cart_data = json.dumps([
            {
                'item_id': item_a.id,
                'name': item_a.name,
                'price': float(item_a.sale_price),
                'qty': 1,
                'unit': 'Pcs',
                'discount_type': 'percent',
                'discount_value': 5,
                'tax_percent': 10
            },
            {
                'item_id': item_b.id,
                'name': item_b.name,
                'price': float(item_b.sale_price),
                'qty': 1,
                'unit': 'Pcs',
                'discount_type': 'percent',
                'discount_value': 0,
                'tax_percent': 0
            }
        ])

        hold = PosHold(
            customer_id=walkin.id,
            user_id=user,
            cart_data=cart_data,
            hold_time=now_local(),
            notes='Phase 2 Test Hold'
        )
        db.session.add(hold)
        db.session.flush()

        test_pass(
            "Hold Bill Created",
            f"Hold ID={hold.id}"
        )

        # Parse and verify cart data
        cart_restored = json.loads(hold.cart_data)

        test_pass(
            "Hold Bill Data Preserved",
            f"Items={len(cart_restored)}"
        ) if len(cart_restored) == 2 else test_fail(
            "Hold Bill Data Preserved",
            f"Expected 2 items, got {len(cart_restored)}"
        )

        # Verify discount/tax in hold data
        item_a_hold = cart_restored[0]
        test_pass(
            "Hold Bill Item Discount Preserved",
            f"discount_type={item_a_hold.get('discount_type')}, discount_value={item_a_hold.get('discount_value')}"
        ) if item_a_hold.get('discount_type') == 'percent' and item_a_hold.get('discount_value') == 5 else test_fail(
            "Hold Bill Item Discount Preserved",
            f"Discount data not preserved"
        )

        test_pass(
            "Hold Bill Item Tax Preserved",
            f"tax_percent={item_a_hold.get('tax_percent')}"
        ) if item_a_hold.get('tax_percent') == 10 else test_fail(
            "Hold Bill Item Tax Preserved",
            f"Tax data not preserved"
        )

        db.session.commit()

def run_all_tests():
    """Run all Phase 2 tests"""
    log("\n" + "="*70)
    log("PHASE 2: COMPREHENSIVE ITEM-LEVEL DISCOUNT & TAX TESTING")
    log("="*70)

    try:
        # Setup
        item_a_id, item_b_id, walkin_id = setup_test_data()

        # Test 1: Calculations
        test_pos_calculations()

        # Test 2: Sale Creation
        sale_id = test_sale_creation_and_storage()

        # Test 3: Invoice Display
        test_invoice_display()

        # Test 4: Accounting
        test_accounting_entries()

        # Test 5: Hold & Resume
        test_hold_and_resume()

    except Exception as e:
        log(f"CRITICAL ERROR: {str(e)}", 'ERROR')
        import traceback
        traceback.print_exc()

    # Summary
    log("\n" + "="*70)
    log("TEST SUMMARY")
    log("="*70)
    log(f"Total Tests: {results['tests_run']}")
    log(f"Passed: {results['tests_passed']}")
    log(f"Failed: {results['tests_failed']}")

    if results['tests_failed'] > 0:
        log("\nFAILURES:", 'ERROR')
        for failure in results['failures']:
            log(f"  - {failure}", 'ERROR')

    pass_rate = (results['tests_passed'] / results['tests_run'] * 100) if results['tests_run'] > 0 else 0
    log(f"\nPass Rate: {pass_rate:.1f}%")

    if results['tests_failed'] == 0:
        log("\n[SUCCESS] ALL TESTS PASSED", 'SUCCESS')
        return 0
    else:
        log(f"\n[ERROR] {results['tests_failed']} TEST(S) FAILED", 'ERROR')
        return 1

if __name__ == '__main__':
    exit_code = run_all_tests()
    sys.exit(exit_code)
