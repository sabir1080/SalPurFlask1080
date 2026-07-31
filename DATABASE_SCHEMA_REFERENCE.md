# Local Database Complete Schema Reference

**Database:** SQLite (instance/database.db)
**Total Tables:** 46
**Total Columns:** 411
**Last Updated:** 2026-07-31

---

## Overview

| Metric | Value |
|--------|-------|
| Database Engine | SQLite |
| Location | instance/database.db |
| Total Tables | 46 |
| Total Columns | 411 |
| Populated Tables | 20+ |

---

## Table Listing (46 Tables)

### Core Business Tables

1. **ITEM** (14 columns, 2 rows)
   - Primary inventory table
   - Key fields: id, name, category_id, business_category_id, barcode, stock, purchase_price, sale_price, default_tax_percent, is_taxable

2. **SALE** (16 columns, 17 rows)
   - Sales transactions
   - Key fields: id, customer_id, item_id, quantity, sale_price, discount_type, discount_value, tax_percent, discount_amount, tax_amount, date, invoice_no

3. **SALE_ITEM** (14 columns, 24 rows)
   - Line items for multi-item sales
   - Key fields: id, sale_id, item_id, quantity, sale_price, discount_type, discount_amount, tax_percent, tax_amount, amount

4. **PURCHASE** (15 columns, 2 rows)
   - Purchase transactions
   - Key fields: id, supplier_id, item_id, quantity, purchase_price, discount_type, tax_percent, discount_amount, tax_amount, date, invoice_no

5. **PURCHASE_ITEM** (13 columns, 2 rows)
   - Line items for multi-item purchases
   - Key fields: id, purchase_id, item_id, quantity, purchase_price, discount_type, discount_amount, tax_percent, tax_amount, amount

### Party Tables

6. **CUSTOMER** (5 columns, 1 row)
   - Customer master data
   - Key fields: id, name, contact, address, opening_balance

7. **SUPPLIER** (5 columns, 3 rows)
   - Supplier master data
   - Key fields: id, name, contact, address, opening_balance

### Inventory Tables

8. **CATEGORY** (2 columns, 2 rows)
   - Basic item categories

9. **BUSINESS_CATEGORY** (11 columns, 18 rows)
   - Enhanced category system with metadata
   - Key fields: id, name, slug, description, icon, is_enabled, priority, color, config_data

10. **ITEM_UNIT** (6 columns, 0 rows)
    - Alternative units for items
    - Key fields: id, item_id, name, factor, purchase_price, sale_price

### Accounting Tables

11. **JOURNAL_ENTRY** (11 columns, 56 rows)
    - Double-entry accounting entries
    - Key fields: id, entry_date, reference, description, source_type, source_id, is_reversed

12. **JOURNAL_LINE** (6 columns, 156 rows)
    - Journal entry line items (debits/credits)
    - Key fields: id, entry_id, account_id, debit, credit, memo

13. **ACCOUNT** (10 columns, 33 rows)
    - Chart of accounts
    - Key fields: id, code, name, type, parent_id, is_group, is_control, is_active, cash_flow_section

14. **FINANCIAL_ACCOUNT** (9 columns, 4 rows)
    - Cash/bank accounts (physical accounts)
    - Key fields: id, name, method, account_type, opening_balance, is_active, is_control, parent_id, gl_account_id

15. **TAX_CODE** (3 columns, 2 rows)
    - Tax code master
    - Key fields: id, name, is_active

16. **TAX_COMPONENT** (6 columns, 2 rows)
    - Tax components and rates
    - Key fields: id, tax_code_id, name, rate, input_account_id, output_account_id

### Ledger Tables

17. **SUPPLIER_LEDGER_ENTRY** (10 columns, 3 rows)
    - Supplier account ledger (running balance)
    - Key fields: id, supplier_id, entry_date, entry_type, source_type, source_id, debit, credit, balance_after

18. **CUSTOMER_LEDGER_ENTRY** (10 columns, 40 rows)
    - Customer account ledger (running balance)
    - Key fields: id, customer_id, entry_date, entry_type, source_type, source_id, debit, credit, balance_after

### Payment Tables

19. **SUPPLIER_PAYMENT** (11 columns, 0 rows)
    - Supplier payment records
    - Key fields: id, supplier_id, purchase_id, amount, payment_date, payment_method, account_id, reference_no

20. **CUSTOMER_PAYMENT** (11 columns, 18 rows)
    - Customer payment records
    - Key fields: id, customer_id, sale_id, amount, payment_date, payment_method, account_id, reference_no

### Period & Fiscal Tables

21. **FISCAL_YEAR** (6 columns, 1 row)
    - Fiscal year configuration
    - Key fields: id, name, start_date, end_date, is_closed, closed_at

22. **ACCOUNTING_PERIOD** (6 columns, 12 rows)
    - Accounting periods within fiscal year
    - Key fields: id, fiscal_year_id, name, start_date, end_date, is_closed

### Document Tracking

23. **DOCUMENT_SEQUENCE** (5 columns, 2 rows)
    - Invoice/document number sequencing
    - Key fields: id, doc_type, year, prefix, next_number

### Returns & Adjustments

24. **SALE_RETURN** (14 columns, 0 rows)
    - Sale return records
    - Key fields: id, sale_id, customer_id, item_id, quantity, return_price, date, reason, cost_restored

25. **PURCHASE_RETURN** (14 columns, 0 rows)
    - Purchase return records
    - Key fields: id, purchase_id, supplier_id, item_id, quantity, return_price, date, reason

26. **STOCK_ADJUSTMENT** (10 columns, 0 rows)
    - Stock adjustment records
    - Key fields: id, item_id, adj_type, quantity, direction, date, reason, cost_value

27. **DELIVERY_CHALLAN** (8 columns, 0 rows)
    - Delivery tracking
    - Key fields: id, sale_id, challan_date, dispatch_date, delivery_date, status, transport

### POS & Order Management

28. **POS_HOLD** (11 columns, 0 rows)
    - Saved POS carts
    - Key fields: id, customer_id, user_id, cart_data, hold_time, notes, account_id, status

29. **PURCHASE_ORDER** (7 columns, 0 rows)
    - Purchase orders
    - Key fields: id, supplier_id, order_date, expected_date, status, notes, converted_purchase_id

30. **PURCHASE_ORDER_ITEM** (7 columns, 0 rows)
    - Purchase order line items

31. **QUOTATION** (7 columns, 0 rows)
    - Sales quotations
    - Key fields: id, customer_id, quote_date, valid_until, status, notes

32. **QUOTATION_ITEM** (10 columns, 0 rows)
    - Quotation line items

### Expense Management

33. **EXPENSE** (11 columns, 0 rows)
    - Expense records
    - Key fields: id, category_id, description, amount, date, payment_method, account_id

34. **EXPENSE_CATEGORY** (3 columns, 0 rows)
    - Expense categories

### Fixed Assets

35. **FIXED_ASSET** (13 columns, 0 rows)
    - Fixed asset register
    - Key fields: id, name, tag, acquisition_date, cost, salvage_value, method, useful_life_months, status

36. **DEPRECIATION_CHARGE** (5 columns, 0 rows)
    - Depreciation charge records
    - Key fields: id, asset_id, period_end, amount, entry_id

### Configuration & Customization

37. **PRODUCT_FIELD** (18 columns, 61 rows)
    - Custom product fields configuration
    - Key fields: id, category_id, field_name, field_label, field_type, is_required, validation_pattern, options

38. **PRODUCT_CATEGORY_DATA** (7 columns, 39 rows)
    - Custom data for products by category
    - Key fields: id, product_id, category_id, field_name, field_value

39. **CATEGORY_MENU_ITEM** (8 columns, 0 rows)
    - Menu configuration by category

40. **CATEGORY_REPORT** (9 columns, 0 rows)
    - Report configuration by category

41. **CATEGORY_VALIDATION** (8 columns, 0 rows)
    - Validation rules by category

42. **CONFIGURATION_SNAPSHOT** (6 columns, 5 rows)
    - Configuration history snapshots
    - Key fields: id, timestamp, enabled_categories, configuration_hash, changed_by

### Logging & Audit

43. **AUDIT_LOG** (8 columns, 70 rows)
    - User action audit trail
    - Key fields: id, created_at, user_id, user_name, action, entity, entity_id, summary

44. **IMPORT_LOG** (11 columns, 0 rows)
    - Data import history
    - Key fields: id, created_at, user_id, import_type, file_name, total_records, successful, failed, status

45. **RATE_LIMIT_HIT** (3 columns, 1 row)
    - Rate limiting records

### Authentication

46. **USER** (8 columns, 2 rows)
    - User accounts
    - Key fields: id, name, email, password, verified, role, reset_token, reset_token_expiry

---

## Field Type Reference

| Type | Description | Example |
|------|-------------|---------|
| INTEGER | Whole numbers | 1, 100, 50000 |
| VARCHAR(n) | Text up to n characters | VARCHAR(100) for names |
| NUMERIC(14,4) | Decimal with 14 total digits, 4 decimal places | 9999999999.9999 |
| DECIMAL(5,2) | Decimal with 5 total digits, 2 decimal places | 999.99 |
| BOOLEAN | True/False | true, false |
| DATETIME | Date and time | 2026-07-31 10:03:52 |
| DATE | Date only | 2026-07-31 |
| TEXT | Large text | Long descriptions |
| JSON | JSON data | {"key": "value"} |

---

## Key Relationships

### Sales Flow
```
SALE (header)
  ├─ customer_id → CUSTOMER
  ├─ item_id → ITEM (first item)
  └─ SALE_ITEM (additional items)
       └─ item_id → ITEM
```

### Purchase Flow
```
PURCHASE (header)
  ├─ supplier_id → SUPPLIER
  ├─ item_id → ITEM (first item)
  └─ PURCHASE_ITEM (additional items)
       └─ item_id → ITEM
```

### Accounting Flow
```
JOURNAL_ENTRY
  └─ JOURNAL_LINE (debit/credit)
       └─ account_id → ACCOUNT
            └─ GL posting to ACCOUNT tree
```

### Ledger Flow
```
SUPPLIER_LEDGER_ENTRY / CUSTOMER_LEDGER_ENTRY
  ├─ source_type: "purchase", "sale", "payment", "receipt", "opening"
  └─ source_id → reference to source transaction
```

---

## Sample Data Summary

| Table | Rows | Status |
|-------|------|--------|
| account | 33 | Chart of accounts seeded |
| audit_log | 70 | User action history |
| business_category | 18 | Product categories configured |
| customer | 1 | Test customer |
| customer_ledger_entry | 40 | Customer transactions |
| customer_payment | 18 | Customer payments |
| fiscal_year | 1 | FY 2025-2026 |
| item | 2 | Test products |
| journal_entry | 56 | Accounting entries |
| journal_line | 156 | GL line items |
| product_category_data | 39 | Custom product data |
| product_field | 61 | Custom field definitions |
| sale | 17 | Sales transactions |
| sale_item | 24 | Sale line items |
| supplier | 3 | Test suppliers |
| user | 2 | Admin users |

---

## Critical Columns

### For POS Operations
- `ITEM.business_category_id` - Required for category filtering
- `ITEM.default_tax_percent` - Default tax for items
- `ITEM.is_taxable` - Tax eligibility flag
- `SALE.invoice_no` - Invoice numbering

### For Accounting
- `ACCOUNT.code` - Account numbering
- `ACCOUNT.parent_id` - Account hierarchy
- `JOURNAL_ENTRY.source_type` - Entry classification
- `JOURNAL_LINE.debit/credit` - Accounting entries

### For Inventory
- `ITEM.stock` - Current stock level
- `ITEM.reorder_level` - Reorder point
- `ITEM.purchase_price/sale_price` - Pricing

### For Ledgers
- `SUPPLIER_LEDGER_ENTRY.balance_after` - Running supplier balance
- `CUSTOMER_LEDGER_ENTRY.balance_after` - Running customer balance

---

## Migration Phases (18 total in app.py)

The `migrate_database()` function handles:
- Phase 1-18: Incremental column additions
- Phase 1: business_category_id (ITEM table)
- Phase 13: CREATE TABLE business_category
- All phases are cumulative and idempotent

---

## Files Generated

1. **LOCAL_DATABASE_STRUCTURE.html** - Professional HTML documentation
2. **local_database_schema.json** - Machine-readable JSON format
3. **DATABASE_SCHEMA_REFERENCE.md** - This markdown file
4. **database_comparison_3way_LIVE.html** - Deployment comparison

---

**End of Schema Reference**
