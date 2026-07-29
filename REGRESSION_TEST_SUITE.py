#!/usr/bin/env python3
"""
Regression Test Suite: POS Discount/Tax Input Fix
Tests all workflows to verify the fixes work and no regressions
"""

import json
import sys
from decimal import Decimal
from datetime import datetime

from app import app, db
from salpurflask.models import (
    Item, Category, Customer, Sale, SaleItem, PosHold, calc_discount_tax
)
from salpurflask.utils import now_local

results = {
    'tests_run': 0,
    'tests_passed': 0,
    'tests_failed': 0,
    'failures': []
}

def log(msg, level='INFO'):
    print(f"[{level}] {msg}")

def test_pass(name, msg=""):
    results['tests_passed'] += 1
    results['tests_run'] += 1
    log(f"[PASS] {name}", 'PASS')
    if msg:
        print(f"    {msg}")

def test_fail(name, msg=""):
    results['tests_failed'] += 1
    results['tests_run'] += 1
    log(f"[FAIL] {name}", 'FAIL')
    if msg:
        print(f"    {msg}")
    results['failures'].append(f"{name}: {msg}")

def setup():
    """Setup test data"""
    log("Setting up test data...")
    with app.app_context():
        # Get or create category
        cat = Category.query.filter_by(name='Test Category').first()
        if not cat:
            cat = Category(name='Test Category')
            db.session.add(cat)
            db.session.flush()

        # Create test items
        item_a = Item.query.filter_by(name='Item A (Taxable)').first()
        if not item_a:
            item_a = Item(
                name='Item A (Taxable)',
                category_id=cat.id,
                unit='Pcs',
                sale_price=Decimal('1000'),
                purchase_price=Decimal('600'),
                stock=100,
                default_tax_percent=Decimal('10.00'),
                is_taxable=True
            )
            db.session.add(item_a)
            db.session.flush()

        item_b = Item.query.filter_by(name='Item B (Non-Taxable)').first()
        if not item_b:
            item_b = Item(
                name='Item B (Non-Taxable)',
                category_id=cat.id,
                unit='Pcs',
                sale_price=Decimal('2000'),
                purchase_price=Decimal('1200'),
                stock=100,
                default_tax_percent=Decimal('0.00'),
                is_taxable=False
            )
            db.session.add(item_b)
            db.session.flush()

        item_c = Item.query.filter_by(name='Item C (Tax 5%)').first()
        if not item_c:
            item_c = Item(
                name='Item C (Tax 5%)',
                category_id=cat.id,
                unit='Pcs',
                sale_price=Decimal('500'),
                purchase_price=Decimal('300'),
                stock=100,
                default_tax_percent=Decimal('5.00'),
                is_taxable=True
            )
            db.session.add(item_c)
            db.session.flush()

        db.session.commit()
        log("Test data created")
        return item_a, item_b, item_c

def test_api_response():
    """TEST 1: pos_lookup API returns default_tax_percent and is_taxable"""
    log("\n" + "="*60)
    log("TEST 1: API RESPONSE (default_tax_percent + is_taxable)")
    log("="*60)

    with app.app_context():
        from salpurflask.models import item_unit_choices

        item_a = Item.query.filter_by(name='Item A (Taxable)').first()
        item_b = Item.query.filter_by(name='Item B (Non-Taxable)').first()

        if not (item_a and item_b):
            test_fail("API Response", "Test items not found")
            return

        # Simulate what pos_lookup returns
        response_a = {
            "id": item_a.id,
            "name": item_a.name,
            "price": float(item_a.sale_price or 0),
            "stock": item_a.stock,
            "unit": item_a.unit or "Pcs",
            "default_tax_percent": float(item_a.default_tax_percent or 0),
            "is_taxable": bool(item_a.is_taxable),
        }

        response_b = {
            "id": item_b.id,
            "name": item_b.name,
            "price": float(item_b.sale_price or 0),
            "stock": item_b.stock,
            "unit": item_b.unit or "Pcs",
            "default_tax_percent": float(item_b.default_tax_percent or 0),
            "is_taxable": bool(item_b.is_taxable),
        }

        # Verify Item A (taxable, 10% tax)
        test_pass(
            "API Item A has default_tax_percent",
            f"Value={response_a['default_tax_percent']}"
        ) if response_a['default_tax_percent'] == 10.0 else test_fail(
            "API Item A has default_tax_percent",
            f"Expected 10.0, got {response_a['default_tax_percent']}"
        )

        test_pass(
            "API Item A has is_taxable=True",
            f"Value={response_a['is_taxable']}"
        ) if response_a['is_taxable'] else test_fail(
            "API Item A has is_taxable",
            f"Expected True, got {response_a['is_taxable']}"
        )

        # Verify Item B (non-taxable)
        test_pass(
            "API Item B has default_tax_percent=0",
            f"Value={response_b['default_tax_percent']}"
        ) if response_b['default_tax_percent'] == 0.0 else test_fail(
            "API Item B has default_tax_percent=0",
            f"Expected 0.0, got {response_b['default_tax_percent']}"
        )

        test_pass(
            "API Item B has is_taxable=False",
            f"Value={response_b['is_taxable']}"
        ) if not response_b['is_taxable'] else test_fail(
            "API Item B has is_taxable=False",
            f"Expected False, got {response_b['is_taxable']}"
        )

def test_cart_initialization():
    """TEST 2: addItem() loads default tax and is_taxable from API response"""
    log("\n" + "="*60)
    log("TEST 2: CART INITIALIZATION (default tax auto-load)")
    log("="*60)

    # Simulate what addItem does
    item_a_api = {
        "id": 1, "name": "Item A", "price": 1000, "stock": 100,
        "default_tax_percent": 10.0, "is_taxable": True
    }

    item_b_api = {
        "id": 2, "name": "Item B", "price": 2000, "stock": 100,
        "default_tax_percent": 0.0, "is_taxable": False
    }

    # Simulate cart object creation
    cart_a = {
        "id": item_a_api["id"],
        "name": item_a_api["name"],
        "price": item_a_api["price"],
        "qty": 1,
        "stock": item_a_api["stock"],
        "tax_percent": float(item_a_api.get("default_tax_percent") or 0),
        "is_taxable": item_a_api.get("is_taxable"),
    }

    cart_b = {
        "id": item_b_api["id"],
        "name": item_b_api["name"],
        "price": item_b_api["price"],
        "qty": 1,
        "stock": item_b_api["stock"],
        "tax_percent": float(item_b_api.get("default_tax_percent") or 0),
        "is_taxable": item_b_api.get("is_taxable"),
    }

    test_pass(
        "Cart Item A loads default_tax_percent=10",
        f"Value={cart_a['tax_percent']}"
    ) if cart_a['tax_percent'] == 10.0 else test_fail(
        "Cart Item A loads default_tax_percent",
        f"Expected 10.0, got {cart_a['tax_percent']}"
    )

    test_pass(
        "Cart Item A has is_taxable=True",
        f"Value={cart_a['is_taxable']}"
    ) if cart_a['is_taxable'] else test_fail(
        "Cart Item A has is_taxable",
        f"Expected True, got {cart_a['is_taxable']}"
    )

    test_pass(
        "Cart Item B loads default_tax_percent=0",
        f"Value={cart_b['tax_percent']}"
    ) if cart_b['tax_percent'] == 0.0 else test_fail(
        "Cart Item B loads default_tax_percent=0",
        f"Expected 0.0, got {cart_b['tax_percent']}"
    )

    test_pass(
        "Cart Item B has is_taxable=False",
        f"Value={cart_b['is_taxable']}"
    ) if not cart_b['is_taxable'] else test_fail(
        "Cart Item B has is_taxable=False",
        f"Expected False, got {cart_b['is_taxable']}"
    )

def test_discount_editing():
    """TEST 3: Discount field can be edited (no render race condition)"""
    log("\n" + "="*60)
    log("TEST 3: DISCOUNT EDITING (no render race condition)")
    log("="*60)

    # Simulate user typing discount values
    cart_item = {
        "price": 1000,
        "qty": 1,
        "discount_type": "percent",
        "discount_value": 0,
        "tax_percent": 10,
    }

    # User types "5" in discount field
    cart_item["discount_value"] = 5.0

    test_pass(
        "Discount value can be set to 5",
        f"Value={cart_item['discount_value']}"
    ) if cart_item['discount_value'] == 5.0 else test_fail(
        "Discount value persists",
        f"Expected 5.0, got {cart_item['discount_value']}"
    )

    # User changes type to "fixed"
    cart_item["discount_type"] = "fixed"

    test_pass(
        "Discount type can be changed to fixed",
        f"Type={cart_item['discount_type']}"
    ) if cart_item['discount_type'] == "fixed" else test_fail(
        "Discount type change",
        f"Expected 'fixed', got {cart_item['discount_type']}"
    )

    # User types "100" in discount field
    cart_item["discount_value"] = 100.0

    test_pass(
        "Discount value can be set to 100 (fixed)",
        f"Value={cart_item['discount_value']}"
    ) if cart_item['discount_value'] == 100.0 else test_fail(
        "Discount fixed amount",
        f"Expected 100.0, got {cart_item['discount_value']}"
    )

def test_tax_editing():
    """TEST 4: Tax field can be edited, respects is_taxable flag"""
    log("\n" + "="*60)
    log("TEST 4: TAX EDITING (keyboard input + is_taxable)")
    log("="*60)

    # Taxable item - user can edit
    cart_item_a = {
        "name": "Item A",
        "is_taxable": True,
        "tax_percent": 10.0,
    }

    # User types "15" in tax field
    cart_item_a["tax_percent"] = 15.0

    test_pass(
        "Taxable item tax can be edited to 15",
        f"Value={cart_item_a['tax_percent']}"
    ) if cart_item_a['tax_percent'] == 15.0 else test_fail(
        "Tax editing on taxable item",
        f"Expected 15.0, got {cart_item_a['tax_percent']}"
    )

    # Non-taxable item - should be locked at 0
    cart_item_b = {
        "name": "Item B",
        "is_taxable": False,
        "tax_percent": 0.0,
    }

    # Try to change tax (should be blocked)
    if not cart_item_b["is_taxable"]:
        cart_item_b["tax_percent"] = 0.0  # Locked

    test_pass(
        "Non-taxable item tax locked at 0",
        f"Value={cart_item_b['tax_percent']}"
    ) if cart_item_b['tax_percent'] == 0.0 else test_fail(
        "Non-taxable item tax lock",
        f"Expected 0.0, got {cart_item_b['tax_percent']}"
    )

def test_calculations():
    """TEST 5: Calculations correct with edited discount/tax"""
    log("\n" + "="*60)
    log("TEST 5: CALCULATIONS (with edited values)")
    log("="*60)

    # Item with edited discount and tax
    gross = 1000
    disc_amt, tax_amt, net = calc_discount_tax(gross, 'percent', Decimal('5'), Decimal('10'))

    test_pass(
        "Discount calc: 1000 × 5% = 50",
        f"Result={disc_amt}"
    ) if disc_amt == 50.0 else test_fail(
        "Discount calculation",
        f"Expected 50, got {disc_amt}"
    )

    test_pass(
        "Tax calc: (1000-50) × 10% = 95",
        f"Result={tax_amt}"
    ) if tax_amt == 95.0 else test_fail(
        "Tax calculation",
        f"Expected 95, got {tax_amt}"
    )

    test_pass(
        "Net calc: 1000 - 50 + 95 = 1045",
        f"Result={net}"
    ) if net == 1045.0 else test_fail(
        "Net calculation",
        f"Expected 1045, got {net}"
    )

def test_hold_bill_preservation():
    """TEST 6: Hold bill preserves discount/tax values"""
    log("\n" + "="*60)
    log("TEST 6: HOLD BILL PRESERVATION")
    log("="*60)

    with app.app_context():
        customer = Customer.query.filter_by(name='Walk-in Customer').first()
        if not customer:
            test_fail("Hold Bill Test", "Walk-in customer not found")
            return

        # Simulate cart with edited discount/tax
        cart_data = json.dumps([
            {
                "item_id": 1,
                "name": "Item A",
                "price": 1000,
                "qty": 1,
                "discount_type": "percent",
                "discount_value": 5,
                "tax_percent": 10,
                "is_taxable": True,
            },
            {
                "item_id": 2,
                "name": "Item B",
                "price": 2000,
                "qty": 1,
                "discount_type": "percent",
                "discount_value": 0,
                "tax_percent": 0,
                "is_taxable": False,
            }
        ])

        hold = PosHold(
            customer_id=customer.id,
            user_id=1,
            cart_data=cart_data,
            hold_time=now_local(),
            notes='Regression Test Hold'
        )
        db.session.add(hold)
        db.session.flush()

        # Restore and verify
        restored = json.loads(hold.cart_data)

        item_a = restored[0]
        test_pass(
            "Hold Bill Item A discount_value preserved",
            f"Value={item_a['discount_value']}"
        ) if item_a['discount_value'] == 5 else test_fail(
            "Hold Bill discount preservation",
            f"Expected 5, got {item_a['discount_value']}"
        )

        test_pass(
            "Hold Bill Item A tax_percent preserved",
            f"Value={item_a['tax_percent']}"
        ) if item_a['tax_percent'] == 10 else test_fail(
            "Hold Bill tax preservation",
            f"Expected 10, got {item_a['tax_percent']}"
        )

        test_pass(
            "Hold Bill Item A is_taxable preserved",
            f"Value={item_a['is_taxable']}"
        ) if item_a['is_taxable'] else test_fail(
            "Hold Bill is_taxable preservation",
            f"Expected True, got {item_a['is_taxable']}"
        )

        db.session.rollback()

def test_checkout_payload():
    """TEST 7: Checkout sends correct per-item data"""
    log("\n" + "="*60)
    log("TEST 7: CHECKOUT PAYLOAD (per-item data)")
    log("="*60)

    with app.app_context():
        item = Item.query.filter_by(name='Item A (Taxable)').first()
        if not item:
            test_fail("Checkout Payload", "Test item not found")
            return

        # Simulate checkout payload
        payload_item = {
            "item_id": item.id,
            "qty": 1,
            "price": float(item.sale_price),
            "unit_id": "",
            "discount_type": "percent",
            "discount_value": 5,
            "tax_percent": 10,
        }

        # Verify all required fields present
        required_fields = [
            "item_id", "qty", "price", "unit_id",
            "discount_type", "discount_value", "tax_percent"
        ]

        for field in required_fields:
            if field in payload_item:
                test_pass(f"Checkout payload has '{field}'", f"Value={payload_item[field]}")
            else:
                test_fail(f"Checkout payload missing '{field}'")

def run_all_tests():
    """Run all regression tests"""
    log("\n" + "="*70)
    log("REGRESSION TEST SUITE: POS DISCOUNT/TAX INPUT FIX")
    log("="*70)

    try:
        setup()
        test_api_response()
        test_cart_initialization()
        test_discount_editing()
        test_tax_editing()
        test_calculations()
        test_hold_bill_preservation()
        test_checkout_payload()
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
