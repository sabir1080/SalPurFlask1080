"""Edge case tests for discounts, taxes, multi-unit items, and negative stock."""

import pytest
from decimal import Decimal
from app import (
    db, Item, Category, Purchase, Sale, Supplier, Customer,
    purchase_total, sale_total, get_purchase_paid, get_sale_received
)


@pytest.fixture
def supplier_and_customer(appctx):
    """Create test supplier and customer."""
    sup = Supplier(name="Test Supplier", contact="123", address="Test", opening_balance=0)
    cust = Customer(name="Test Customer", contact="456", address="Test", opening_balance=0)
    db.session.add(sup)
    db.session.add(cust)
    db.session.commit()
    yield sup, cust


@pytest.fixture
def item(appctx):
    """Create test item with category."""
    cat = Category(name="Test Category")
    db.session.add(cat)
    db.session.commit()

    it = Item(
        name="Test Item",
        category_id=cat.id,
        unit="pcs",
        stock=100,
        purchase_price=Decimal("100"),
        sale_price=Decimal("150"),
        inventory_value=Decimal("10000")  # 100 units @ 100 each
    )
    db.session.add(it)
    db.session.commit()
    yield it


# ─── DISCOUNT TESTS ───────────────────────────────────────────────────────────

class TestDiscounts:
    """Test discount calculations in various scenarios."""

    def test_zero_discount(self, appctx, supplier_and_customer, item):
        """Verify purchase with zero discount calculates correctly."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=10,
            purchase_price=Decimal("100"),
            discount_type="percent",
            discount_value=Decimal("0"),
            tax_percent=Decimal("0"),
        )
        db.session.add(pur)
        db.session.commit()

        # Total = 10 * 100 = 1000 (no discount)
        total = purchase_total(pur)
        assert total == Decimal("1000.00"), f"Expected 1000, got {total}"

    def test_percentage_discount_50(self, appctx, supplier_and_customer, item):
        """Verify 50% discount calculation."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=10,
            purchase_price=Decimal("100"),
            discount_type="percent",
            discount_value=Decimal("50"),  # 50% discount
            tax_percent=Decimal("0"),
        )
        db.session.add(pur)
        db.session.commit()

        # Total = (10 * 100) * (1 - 0.50) = 500
        total = purchase_total(pur)
        assert total == Decimal("500.00"), f"Expected 500, got {total}"

    def test_fixed_discount(self, appctx, supplier_and_customer, item):
        """Verify fixed amount discount calculation."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=10,
            purchase_price=Decimal("100"),
            discount_type="fixed",
            discount_value=Decimal("200"),  # Fixed discount of 200
            tax_percent=Decimal("0"),
        )
        db.session.add(pur)
        db.session.commit()

        # Total = (10 * 100) - 200 = 800
        total = purchase_total(pur)
        assert total == Decimal("800.00"), f"Expected 800, got {total}"

    def test_discount_greater_than_amount(self, appctx, supplier_and_customer, item):
        """Verify discount cannot make total negative."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=10,
            purchase_price=Decimal("100"),
            discount_type="fixed",
            discount_value=Decimal("1500"),  # Discount > amount
            tax_percent=Decimal("0"),
        )
        db.session.add(pur)
        db.session.commit()

        total = purchase_total(pur)
        assert total >= Decimal("0"), f"Total should not be negative, got {total}"


# ─── TAX TESTS ─────────────────────────────────────────────────────────────────

class TestTaxCalculations:
    """Test tax calculations in various scenarios."""

    def test_zero_tax(self, appctx, supplier_and_customer, item):
        """Verify purchase with zero tax."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=10,
            purchase_price=Decimal("100"),
            discount_type="percent",
            discount_value=Decimal("0"),
            tax_percent=Decimal("0"),
        )
        db.session.add(pur)
        db.session.commit()

        total = purchase_total(pur)
        assert total == Decimal("1000.00")

    def test_tax_17_percent(self, appctx, supplier_and_customer, item):
        """Verify 17% GST calculation."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=10,
            purchase_price=Decimal("100"),
            discount_type="percent",
            discount_value=Decimal("0"),
            tax_percent=Decimal("17"),  # 17% GST
        )
        db.session.add(pur)
        db.session.commit()

        # Total = (10 * 100) * (1 + 0.17) = 1000 * 1.17 = 1170
        total = purchase_total(pur)
        assert total == Decimal("1170.00"), f"Expected 1170, got {total}"

    def test_discount_then_tax(self, appctx, supplier_and_customer, item):
        """Verify tax is applied after discount."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=10,
            purchase_price=Decimal("100"),
            discount_type="percent",
            discount_value=Decimal("10"),  # 10% discount
            tax_percent=Decimal("5"),  # 5% tax
        )
        db.session.add(pur)
        db.session.commit()

        # Total = (10 * 100) * (1 - 0.10) * (1 + 0.05)
        # = 1000 * 0.90 * 1.05 = 945
        total = purchase_total(pur)
        assert total == Decimal("945.00"), f"Expected 945, got {total}"


# ─── REVERSAL TESTS ───────────────────────────────────────────────────────────

class TestReversals:
    """Test document reversal functionality."""

    def test_reversed_purchase_flag(self, appctx, supplier_and_customer, item):
        """Verify reversed purchase is marked correctly."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=10,
            purchase_price=Decimal("100"),
            discount_type="percent",
            discount_value=Decimal("0"),
            tax_percent=Decimal("0"),
            is_reversed=False,
        )
        db.session.add(pur)
        db.session.commit()

        pur.is_reversed = True
        db.session.commit()

        refreshed = db.session.get(Purchase, pur.id)
        assert refreshed.is_reversed is True

    def test_sale_reversal_flag(self, appctx, supplier_and_customer, item):
        """Verify reversed sale is marked correctly."""
        sup, cust = supplier_and_customer
        sal = Sale(
            customer_id=cust.id,
            item_id=item.id,
            quantity=10,
            sale_price=Decimal("150"),
            cost_price=Decimal("100"),
            discount_type="percent",
            discount_value=Decimal("0"),
            tax_percent=Decimal("0"),
            is_reversed=False,
        )
        db.session.add(sal)
        db.session.commit()

        sal.is_reversed = True
        db.session.commit()

        refreshed = db.session.get(Sale, sal.id)
        assert refreshed.is_reversed is True


# ─── NUMERIC EDGE CASE TESTS ───────────────────────────────────────────────────

class TestNumericEdgeCases:
    """Test edge cases with very small/large numbers and precision."""

    def test_very_small_quantity(self, appctx, supplier_and_customer, item):
        """Verify purchase with quantity 1."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=1,
            purchase_price=Decimal("100.5678"),
            discount_type="percent",
            discount_value=Decimal("0"),
            tax_percent=Decimal("0"),
        )
        db.session.add(pur)
        db.session.commit()

        total = purchase_total(pur)
        # Should preserve decimal precision
        assert abs(float(total) - 100.57) < 0.01, f"Expected ~100.57, got {total}"

    def test_very_large_quantity(self, appctx, supplier_and_customer, item):
        """Verify purchase with very large quantity."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=999999,
            purchase_price=Decimal("100"),
            discount_type="percent",
            discount_value=Decimal("0"),
            tax_percent=Decimal("0"),
        )
        db.session.add(pur)
        db.session.commit()

        total = purchase_total(pur)
        assert total == Decimal("99999900.00")

    def test_decimal_precision(self, appctx, supplier_and_customer, item):
        """Verify decimal precision in calculations."""
        sup, cust = supplier_and_customer
        pur = Purchase(
            supplier_id=sup.id,
            item_id=item.id,
            quantity=3,
            purchase_price=Decimal("33.33"),
            discount_type="percent",
            discount_value=Decimal("0"),
            tax_percent=Decimal("0"),
        )
        db.session.add(pur)
        db.session.commit()

        total = purchase_total(pur)
        # 3 * 33.33 = 99.99
        assert abs(float(total) - 99.99) < 0.01, f"Expected ~99.99, got {total}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
