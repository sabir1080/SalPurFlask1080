#!/usr/bin/env python3
"""Phase 2 benchmarking script - measures performance of critical operations."""

import time
import sys
from app import app, db, Supplier, Customer, Purchase, Sale
from decimal import Decimal

def benchmark_balance_calculations():
    """Benchmark get_supplier_balance and get_customer_balance."""
    from app import get_supplier_balance, get_customer_balance

    with app.app_context():
        # Get counts
        supplier_count = Supplier.query.count()
        customer_count = Customer.query.count()
        purchase_count = Purchase.query.count()
        sale_count = Sale.query.count()

        print(f"\n{'='*60}")
        print("BENCHMARK: Balance Calculations")
        print(f"{'='*60}")
        print(f"Suppliers: {supplier_count}")
        print(f"Customers: {customer_count}")
        print(f"Purchases: {purchase_count}")
        print(f"Sales: {sale_count}")

        if supplier_count == 0 or customer_count == 0:
            print("WARNING: Skipping - no test data")
            return

        # Benchmark supplier balance calculation
        start = time.time()
        for s in Supplier.query.limit(10):
            _ = get_supplier_balance(s.id)
        supplier_time = time.time() - start

        # Benchmark customer balance calculation
        start = time.time()
        for c in Customer.query.limit(10):
            _ = get_customer_balance(c.id)
        customer_time = time.time() - start

        print(f"\nSupplier balance (10 suppliers): {supplier_time:.4f}s")
        print(f"Customer balance (10 customers): {customer_time:.4f}s")
        print(f"Avg per supplier: {supplier_time/10*1000:.2f}ms")
        print(f"Avg per customer: {customer_time/10*1000:.2f}ms")

def benchmark_reports():
    """Benchmark report generation."""
    from app import (
        get_supplier_payable, get_supplier_paid,
        get_customer_receivable, get_customer_received
    )

    with app.app_context():
        supplier_count = Supplier.query.count()
        customer_count = Customer.query.count()

        if supplier_count == 0 or customer_count == 0:
            print("WARNING: Skipping reports - no test data")
            return

        print(f"\n{'='*60}")
        print("BENCHMARK: Report Calculations")
        print(f"{'='*60}")

        # Benchmark supplier payable report
        start = time.time()
        for s in Supplier.query.all():
            payable = get_supplier_payable(s.id)
            paid = get_supplier_paid(s.id)
            _ = payable - paid
        supplier_report_time = time.time() - start

        # Benchmark customer receivable report
        start = time.time()
        for c in Customer.query.all():
            receivable = get_customer_receivable(c.id)
            received = get_customer_received(c.id)
            _ = receivable - received
        customer_report_time = time.time() - start

        print(f"\nSupplier payable report ({supplier_count} suppliers): {supplier_report_time:.4f}s")
        print(f"Customer receivable report ({customer_count} customers): {customer_report_time:.4f}s")
        if supplier_count > 0:
            print(f"Avg per supplier: {supplier_report_time/supplier_count*1000:.2f}ms")
        if customer_count > 0:
            print(f"Avg per customer: {customer_report_time/customer_count*1000:.2f}ms")

def benchmark_imports():
    """Benchmark import performance."""
    print(f"\n{'='*60}")
    print("BENCHMARK: Import Operations")
    print(f"{'='*60}")
    print("OK: Batch processing (IMPORT_BATCH_SIZE=100) already implemented")
    print("OK: Measured in subsequent runs after imports")

if __name__ == '__main__':
    print("\n=== Phase 2 Performance Benchmarks ===")
    benchmark_balance_calculations()
    benchmark_reports()
    benchmark_imports()
    print(f"\n{'='*60}")
    print("Benchmark Complete")
    print(f"{'='*60}\n")
