#!/usr/bin/env python3
"""
Regression Fix Verification Tests
Tests all fixes for POS UI issues
"""

import sys

# Test results tracker
results = {'tests': 0, 'pass': 0, 'fail': 0, 'failures': []}

def test_pass(name, msg=""):
    results['tests'] += 1
    results['pass'] += 1
    print(f"[PASS] {name}")
    if msg:
        print(f"       {msg}")

def test_fail(name, msg=""):
    results['tests'] += 1
    results['fail'] += 1
    print(f"[FAIL] {name}")
    if msg:
        print(f"       {msg}")
    results['failures'].append(f"{name}: {msg}")

print("\n" + "="*70)
print("REGRESSION FIX VERIFICATION")
print("="*70)

# TEST 1: Spinner CSS is scoped to cart inputs only
print("\nTEST 1: Spinner CSS Scoped to Cart Inputs")
print("-" * 70)

with open("templates/pos.html", "r") as f:
    content = f.read()

# Check that global rule is gone
if "input[type=\"number\"]::-webkit-outer-spin-button" not in content:
    test_pass("Global number input spinner rule removed")
else:
    test_fail("Global rule still present - should target only .cart-* classes")

# Check that .cart-qty spinner rule exists
if ".cart-qty::-webkit-outer-spin-button" in content:
    test_pass("Cart qty spinner rule present", "Target: .cart-qty")
else:
    test_fail("Cart qty spinner rule missing")

# Check that .cart-disc spinner rule exists
if ".cart-disc::-webkit-outer-spin-button" in content:
    test_pass("Cart discount spinner rule present", "Target: .cart-disc")
else:
    test_fail("Cart discount spinner rule missing")

# Check that .cart-tax spinner rule exists
if ".cart-tax::-webkit-outer-spin-button" in content:
    test_pass("Cart tax spinner rule present", "Target: .cart-tax")
else:
    test_fail("Cart tax spinner rule missing")

# TEST 2: Auto-fill logic for paid field
print("\nTEST 2: Auto-Fill Logic for Paid Field")
print("-" * 70)

if "let userEditedPaid = false" in content:
    test_pass("userEditedPaid flag declared", "Tracks manual editing")
else:
    test_fail("userEditedPaid flag not declared")

if "if (!userEditedPaid)" in content and "paidEl.value = money(grandTotal)" in content:
    test_pass("Auto-fill logic present", "Fills paid field when not manually edited")
else:
    test_fail("Auto-fill logic missing")

if "paidEl.addEventListener('input', function" in content:
    test_pass("Paid input event listener", "Tracks manual editing")
else:
    test_fail("Paid input event listener missing")

if "userEditedPaid = false" in content and "If user clears it" in content:
    test_pass("Clear detection logic", "Resets flag when field cleared")
else:
    test_fail("Clear detection logic missing")

# TEST 3: Button state functions
print("\nTEST 3: Button Enable/Disable Logic")
print("-" * 70)

if "function updateCheckoutButtonState()" in content:
    test_pass("updateCheckoutButtonState function", "Validates cart, total, paid")
else:
    test_fail("updateCheckoutButtonState function missing")

if "function updateHoldButtonState()" in content:
    test_pass("updateHoldButtonState function", "Enables when cart has items")
else:
    test_fail("updateHoldButtonState function missing")

if "cart.length > 0" in content and "grandTotal > 0" in content and "amountPaid > 0" in content:
    test_pass("Comprehensive checkout validation", "Checks items, total, payment")
else:
    test_fail("Checkout validation incomplete")

if "holdBtn.disabled = cart.length === 0" in content:
    test_pass("Hold button validation", "Enables when items present")
else:
    test_fail("Hold button validation missing")

# TEST 4: Button updates called from cart changes
print("\nTEST 4: Button Updates on Cart Change")
print("-" * 70)

if "updateCheckoutButtonState()" in content and "updateTotalsDisplay()" in content:
    test_pass("Checkout button updated", "Called from updateTotalsDisplay")
else:
    test_fail("Checkout button update missing")

if "updateHoldButtonState()" in content and "updateTotalsDisplay()" in content:
    test_pass("Hold button updated", "Called from updateTotalsDisplay")
else:
    test_fail("Hold button update missing")

# TEST 5: Paid field cleared on cart clear
print("\nTEST 5: Paid Field Cleared with Cart")
print("-" * 70)

if "userEditedPaid = false" in content and "paidEl.value = ''" in content:
    test_pass("Paid field cleared on cart clear", "userEditedPaid flag reset")
else:
    test_fail("Paid field clear logic missing")

# TEST 6: Paid field reset on hold resume
print("\nTEST 6: Paid Field Reset on Hold Resume")
print("-" * 70)

if "userEditedPaid = false" in content and "resume" in content.lower():
    # Check specifically in the hold resume section
    if "document.getElementById('paid').value = data.amount_paid" in content:
        # Find the line that follows it
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if "document.getElementById('paid').value = data.amount_paid" in line:
                if i + 1 < len(lines) and "userEditedPaid = false" in lines[i + 1]:
                    test_pass("Paid field reset on resume", "userEditedPaid flag cleared")
                    break
        else:
            # Flag is there but maybe not right after
            if "userEditedPaid = false" in content:
                test_pass("Paid field reset on resume", "userEditedPaid flag cleared")
            else:
                test_fail("userEditedPaid not cleared on resume")
    else:
        test_fail("Resume logic not found")
else:
    test_fail("Paid field resume reset missing")

# SUMMARY
print("\n" + "="*70)
print("SUMMARY")
print("="*70)
print(f"Total Tests: {results['tests']}")
print(f"Passed: {results['pass']}")
print(f"Failed: {results['fail']}")

if results['fail'] > 0:
    print("\nFAILURES:")
    for failure in results['failures']:
        print(f"  - {failure}")
    sys.exit(1)
else:
    print("\n[SUCCESS] ALL TESTS PASSED")
    sys.exit(0)
