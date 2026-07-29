#!/usr/bin/env python
"""Quick check: Why is tax not showing on invoice?"""
import os
os.environ["DATABASE_URL"] = "sqlite:///instance/database.db"
os.environ.setdefault("SECRET_KEY", "dev")
os.environ.setdefault("SECURITY_PASSWORD_SALT", "dev")
os.environ["FISCAL_YEAR_START_MONTH"] = "1"
os.environ["APP_TIMEZONE"] = "UTC"
os.environ["CURRENCY"] = "Rs"

from app import app, db, Sale, SaleItem

with app.app_context():
    # Get the latest sale
    sale = db.session.query(Sale).order_by(Sale.id.desc()).first()

    if sale:
        print(f"\nLatest Sale ID: {sale.id}")
        print(f"Invoice No: {sale.invoice_no}")
        print(f"Date: {sale.date}")
        print(f"Notes: {sale.notes}")
        print(f"\nLine Items:")

        for si in sale.line_items:
            print(f"\n  Item: {si.item.name}")
            print(f"    Quantity: {si.quantity}")
            print(f"    Sale Price: {si.sale_price}")
            print(f"    Discount Type: {si.discount_type}")
            print(f"    Discount Value: {si.discount_value}")
            print(f"    Discount Amount: {si.discount_amount}")
            print(f"    TAX PERCENT: {si.tax_percent}")
            print(f"    TAX AMOUNT: {si.tax_amount}")
            print(f"    Amount: {si.amount}")

            # Check calculation
            gross = si.quantity * si.sale_price
            print(f"\n    Calculation:")
            print(f"      Gross: {gross}")
            print(f"      Tax %: {si.tax_percent}")
            print(f"      Tax Amount (DB): {si.tax_amount}")
            print(f"      Expected Tax: {gross * si.tax_percent / 100 if si.tax_percent else 0}")
    else:
        print("No sales found")
