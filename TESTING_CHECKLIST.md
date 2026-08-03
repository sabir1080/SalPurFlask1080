# TradeFlow ERP v1.0.0 - Manual Testing Checklist

## Module 1: AUTHENTICATION & SESSION MANAGEMENT

### AUTH-001: User Registration
- **Priority**: Critical
- **Steps**:
  1. Navigate to /signin
  2. Click "Don't have an account? Sign up"
  3. Enter email, password (min 6 chars), confirm password
  4. Submit form
  5. Check email for verification link
  6. Click verification link
  7. Navigate to /signin, login with new credentials
- **Expected**: Account created, email sent, login successful, redirect to dashboard

### AUTH-002: User Login
- **Priority**: Critical
- **Steps**:
  1. Navigate to /signin
  2. Enter valid email and password
  3. Click Sign In
  4. Verify redirect to dashboard
- **Expected**: Session created, user redirected to dashboard, navbar shows user email

### AUTH-003: Invalid Login Attempt
- **Priority**: Critical
- **Steps**:
  1. Navigate to /signin
  2. Enter valid email, wrong password
  3. Submit
  4. Attempt login again (5 times)
- **Expected**: First 4 attempts fail with error message, 5th attempt triggers rate limit

### AUTH-004: Session Timeout
- **Priority**: High
- **Steps**:
  1. Login successfully
  2. Close browser without logout (or wait 7 days)
  3. Return and try to access protected page
- **Expected**: Redirect to /signin

### AUTH-005: CSRF Protection
- **Priority**: Critical
- **Steps**:
  1. Login
  2. Open browser dev tools, Network tab
  3. Perform any POST action (create customer, purchase, etc)
  4. Verify CSRF token is sent in form/headers
- **Expected**: POST request includes csrf_token, no CSRF errors

### AUTH-006: Logout
- **Priority**: High
- **Steps**:
  1. Login
  2. Click user dropdown, select Logout
- **Expected**: Session destroyed, redirect to /signin

### AUTH-007: Role-Based Access (Admin Only)
- **Priority**: Critical
- **Steps**:
  1. Login as non-admin user
  2. Try to access /admin/users directly
- **Expected**: 403 Forbidden error

### AUTH-008: Role-Based Access (Manager Only)
- **Priority**: High
- **Steps**:
  1. Login as non-manager user
  2. Try to access purchase creation page
- **Expected**: Either 403 or limited functionality

### AUTH-009: Email Verification Required
- **Priority**: High
- **Steps**:
  1. Register new account
  2. Try to access dashboard before email verification
  3. Complete email verification
  4. Try accessing dashboard again
- **Expected**: Redirect to verification page until email confirmed

### AUTH-010: Password Reset
- **Priority**: High
- **Steps**:
  1. Go to /signin, click "Forgot password?"
  2. Enter email address
  3. Check email for reset link
  4. Click link, set new password
  5. Login with new password
- **Expected**: Email sent, password reset successful, old password no longer works

---

## Module 2: DASHBOARD

### DASH-001: Dashboard Load
- **Priority**: Critical
- **Steps**:
  1. Login as any user
  2. Verify redirect to /dashboard
  3. Check page loads without errors
- **Expected**: Dashboard displayed with all widgets

### DASH-002: Summary Metrics
- **Priority**: High
- **Steps**:
  1. Dashboard loaded
  2. Verify metrics displayed: Total Suppliers, Total Customers, Total Inventory, Total Cash & Bank
- **Expected**: All metrics show values (could be 0 for new instance)

### DASH-003: Recent Transactions Widget
- **Priority**: High
- **Steps**:
  1. Dashboard loaded
  2. Create a purchase, sale, payment, receipt
  3. Refresh dashboard
  4. Verify transactions appear in recent activity
- **Expected**: Latest 5-10 transactions displayed

### DASH-004: Navigation Menu
- **Priority**: High
- **Steps**:
  1. Dashboard loaded
  2. Verify navbar has all main modules (Accounting, Inventory, Purchases, Sales, Reports)
  3. Click each menu item
- **Expected**: All menu items navigate to correct pages, no 404s

### DASH-005: Dark Mode Toggle
- **Priority**: Medium
- **Steps**:
  1. Dashboard loaded
  2. Click theme toggle (if visible)
  3. Refresh page
  4. Verify theme persists
- **Expected**: Theme toggles, persists on refresh

---

## Module 3: POS (POINT OF SALE)

### POS-001: Create Sale (Basic)
- **Priority**: Critical
- **Steps**:
  1. Navigate to POS
  2. Select a customer (or create new)
  3. Add 1 item to cart (qty 1)
  4. Verify cart shows item, price, total
  5. Proceed to checkout
  6. Select payment method (Cash)
  7. Complete sale
- **Expected**: Sale created, receipt shown, item stock decreases

### POS-002: Cart Quantity Update
- **Priority**: High
- **Steps**:
  1. In POS, add item to cart
  2. Change quantity from 1 to 5
  3. Verify line total updates (5 * unit_price)
  4. Add another item, verify grand total
- **Expected**: Totals recalculate in real-time

### POS-003: Apply Discount
- **Priority**: High
- **Steps**:
  1. POS with 1 item in cart (price 100)
  2. Apply 10% discount
  3. Verify new total = 90
- **Expected**: Discount applied, total reflects

### POS-004: Tax Calculation
- **Priority**: High
- **Steps**:
  1. POS with taxable item in cart
  2. Verify tax is calculated and shown
  3. Total = subtotal + tax
- **Expected**: Tax correctly calculated based on tax code

### POS-005: Held Bills
- **Priority**: High
- **Steps**:
  1. In POS, add items to cart
  2. Click "Hold Bill" button (if exists)
  3. Navigate away
  4. Return to POS
  5. Load the held bill
- **Expected**: Bill saved and restored with same items

### POS-006: Multiple Payment Methods
- **Priority**: Medium
- **Steps**:
  1. POS checkout
  2. Select payment method = "Bank"
  3. Complete sale
  4. Repeat with "Cheque", "Online"
- **Expected**: All payment methods accepted, sale completes

### POS-007: Insufficient Stock
- **Priority**: High
- **Steps**:
  1. Select item with qty = 2
  2. Try to add qty = 5 to cart
- **Expected**: Either error shown or max qty capped

### POS-008: Receipt Printing
- **Priority**: Medium
- **Steps**:
  1. Complete POS sale
  2. Verify receipt displays correctly
  3. Click Print button
- **Expected**: Receipt template opens in print dialog

---

## Module 4: PURCHASES

### PUR-001: Create Purchase Order
- **Priority**: Critical
- **Steps**:
  1. Navigate to Purchases
  2. Click "New Purchase"
  3. Select supplier
  4. Add 1 line item (item, qty 10, price 50)
  5. Verify subtotal, tax, total
  6. Submit
- **Expected**: Purchase created, stock increased by 10

### PUR-002: Purchase Line Item Calculations
- **Priority**: High
- **Steps**:
  1. Create purchase with 2 items
  2. Item 1: qty=5, price=100, discount=10%, tax=17%
  3. Item 2: qty=2, price=200, discount=5%, tax=17%
  4. Verify each line's amount = (qty * price - discount) + tax
  5. Verify total = sum of all line amounts
- **Expected**: All calculations correct

### PUR-003: Edit Purchase
- **Priority**: High
- **Steps**:
  1. Create purchase
  2. Click Edit
  3. Change qty of line item from 5 to 8
  4. Save
  5. Verify stock adjusted (added 3 more units)
- **Expected**: Purchase updated, stock adjusted, ledger reflects change

### PUR-004: Delete Purchase
- **Priority**: High
- **Steps**:
  1. Create purchase with qty 10
  2. Note item stock increased by 10
  3. Delete purchase
  4. Verify stock decreased by 10 (reverted)
- **Expected**: Purchase deleted, stock reverted, ledger entry removed

### PUR-005: Purchase Return
- **Priority**: High
- **Steps**:
  1. Create purchase (qty 10)
  2. Navigate to Purchase Returns
  3. Select the purchase
  4. Return qty 2 at same price
  5. Verify stock decreased by 2
- **Expected**: Return recorded, stock decreased, GL ledger updated

### PUR-006: Supplier Payment
- **Priority**: Critical
- **Steps**:
  1. Create purchase (qty 10, price 100 = 1000 total)
  2. Navigate to Supplier Payments
  3. Select supplier
  4. Verify outstanding = 1000
  5. Pay 500
  6. Verify outstanding = 500
- **Expected**: Payment recorded, balance updated

### PUR-007: Payment Reversal
- **Priority**: High
- **Steps**:
  1. Create purchase, make payment
  2. Verify balance is reduced
  3. Reverse the payment (if delete option available)
  4. Verify balance restored
- **Expected**: Payment reversed, balance restored

### PUR-008: Purchase Ledger
- **Priority**: Medium
- **Steps**:
  1. Create purchase from Supplier A
  2. Make partial payment
  3. Create another purchase from Supplier A
  4. Navigate to Supplier Ledger (Supplier A)
  5. Verify both purchases and payment shown in ledger
  6. Verify running balance calculation
- **Expected**: All transactions shown in chronological order, balance recalculated

---

## Module 5: SALES

### SALES-001: Create Sale Invoice
- **Priority**: Critical
- **Steps**:
  1. Navigate to Sales
  2. Click "New Sale"
  3. Select customer
  4. Add 1 line item (qty 5, price 100)
  5. Verify total = 500
  6. Submit
- **Expected**: Sale created, stock decreased by 5

### SALES-002: Sale with Discount
- **Priority**: High
- **Steps**:
  1. Create sale (qty 10, price 100)
  2. Apply 10% discount on line
  3. Verify amount = (10*100) - (10% of 1000) = 900
- **Expected**: Discount correctly applied

### SALES-003: Sale Quotation Conversion
- **Priority**: High
- **Steps**:
  1. Navigate to Quotations
  2. Create quotation (qty 10, item, price)
  3. Convert to sale
  4. Verify sale created with same items
- **Expected**: Quotation becomes sale, stock decreases

### SALES-004: Edit Sale
- **Priority**: High
- **Steps**:
  1. Create sale (qty 10)
  2. Edit and change qty to 15
  3. Verify stock adjustment (additional 5 units)
- **Expected**: Sale updated, stock adjusted

### SALES-005: Sale Return
- **Priority**: High
- **Steps**:
  1. Create sale (qty 10)
  2. Navigate to Sale Returns
  3. Return qty 3
  4. Verify stock increased by 3
- **Expected**: Return recorded, stock increased

### SALES-006: Customer Receipt
- **Priority**: Critical
- **Steps**:
  1. Create sale (qty 10, price 100 = 1000 total)
  2. Navigate to Customer Receipts
  3. Select customer
  4. Verify outstanding = 1000
  5. Receive payment of 600
  6. Verify outstanding = 400
- **Expected**: Receipt recorded, balance updated

### SALES-007: Multiple Receipts
- **Priority**: High
- **Steps**:
  1. Create 1 sale = 500
  2. Create 1 sale = 300 (same customer)
  3. Navigate to Bulk Receipt
  4. Select customer
  5. Verify total outstanding = 800
  6. Receive 800
  7. Verify outstanding = 0
- **Expected**: All sales shown, combined receipt accepted

### SALES-008: Customer Aging Report
- **Priority**: Medium
- **Steps**:
  1. Create sale from Customer A
  2. Receive partial payment
  3. Navigate to Aging Reports
  4. Verify customer shown with correct outstanding amount
- **Expected**: Aging report shows customer with correct balance

---

## Module 6: INVENTORY

### INV-001: Create Item
- **Priority**: Critical
- **Steps**:
  1. Navigate to Items
  2. Click "Add Item"
  3. Enter name, SKU, category, unit (Pcs)
  4. Set purchase_price 50, sale_price 100, reorder_level 5
  5. Submit
- **Expected**: Item created, visible in items list

### INV-002: Item with Barcode
- **Priority**: High
- **Steps**:
  1. Create item with barcode field populated
  2. View item, click "Print Label"
  3. Verify barcode displays
- **Expected**: Barcode/QR code generated and displayed

### INV-003: Stock Adjustment
- **Priority**: High
- **Steps**:
  1. Create item with qty 0
  2. Navigate to Stock Adjustments
  3. Add adjustment (+10 units, reason = "Opening balance")
  4. Verify item stock now = 10
- **Expected**: Stock adjusted, GL ledger entry created

### INV-004: Low Stock Alert
- **Priority**: Medium
- **Steps**:
  1. Create item with reorder_level = 5, stock = 3
  2. View item or stock report
  3. Verify alert shown (or badge indicates low stock)
- **Expected**: Alert or visual indicator shown

### INV-005: Item Categories
- **Priority**: High
- **Steps**:
  1. Navigate to Categories
  2. Create category "Electronics"
  3. Create item and assign to this category
  4. Filter items by category
- **Expected**: Category created, item assigned, filter works

### INV-006: Bulk Import Items
- **Priority**: High
- **Steps**:
  1. Navigate to Bulk Import
  2. Prepare CSV: name, SKU, category, purchase_price, sale_price
  3. Upload file
  4. Verify items imported
- **Expected**: Items imported with no errors, all fields populated

### INV-007: Stock Ledger
- **Priority**: Medium
- **Steps**:
  1. Create item
  2. Make purchase (+10)
  3. Make sale (-5)
  4. Make adjustment (+2)
  5. View item ledger
  6. Verify all transactions shown, running balance correct
- **Expected**: All transactions shown, balance recalculated

### INV-008: Item Cost Valuation
- **Priority**: High
- **Steps**:
  1. Create item with cost 100
  2. Purchase qty 5 (cost 100 each) = 500 inventory value
  3. Verify item shows total cost 500
  4. Purchase qty 5 at cost 120 = additional 600
  5. Verify weighted average cost = (500+600)/10 = 110
- **Expected**: Inventory value calculated correctly

---

## Module 7: CUSTOMERS

### CUST-001: Create Customer
- **Priority**: Critical
- **Steps**:
  1. Navigate to Customers
  2. Click "Add Customer"
  3. Enter name, contact, address
  4. Set opening_balance 0
  5. Submit
- **Expected**: Customer created, visible in list

### CUST-002: Customer with Opening Balance
- **Priority**: High
- **Steps**:
  1. Create customer with opening_balance 1000
  2. View customer ledger
  3. Verify opening entry shows 1000
- **Expected**: Opening balance recorded as ledger entry

### CUST-003: Edit Customer
- **Priority**: High
- **Steps**:
  1. Create customer
  2. Edit name and contact
  3. Save
  4. Verify changes persisted
- **Expected**: Customer updated

### CUST-004: Customer Ledger
- **Priority**: Medium
- **Steps**:
  1. Create customer
  2. Create 2 sales (500, 300)
  3. View customer ledger
  4. Verify running balance: opening -> 500 -> 800
- **Expected**: All transactions shown, balance correct

### CUST-005: Customer Statement Export
- **Priority**: Medium
- **Steps**:
  1. Select customer with transactions
  2. Click "Export Statement" or similar
  3. Verify CSV/Excel downloaded
- **Expected**: File downloaded with all transactions

---

## Module 8: SUPPLIERS

### SUP-001: Create Supplier
- **Priority**: Critical
- **Steps**:
  1. Navigate to Suppliers
  2. Click "Add Supplier"
  3. Enter name, contact, address, opening_balance 0
  4. Submit
- **Expected**: Supplier created

### SUP-002: Supplier with Opening Balance
- **Priority**: High
- **Steps**:
  1. Create supplier with opening_balance 500
  2. View supplier ledger
  3. Verify opening entry shows 500
- **Expected**: Opening balance recorded

### SUP-003: Supplier Ledger
- **Priority**: Medium
- **Steps**:
  1. Create supplier
  2. Create 2 purchases (1000, 500)
  3. View supplier ledger
  4. Verify running balance: opening -> 1000 -> 1500
- **Expected**: Balance calculated correctly

---

## Module 9: PAYMENTS

### PAY-001: Supplier Payment Recording
- **Priority**: Critical
- **Steps**:
  1. Create purchase 1000
  2. Navigate to Supplier Payments
  3. Enter payment 500
  4. Verify balance = 500
- **Expected**: Payment recorded, balance updated

### PAY-002: Customer Receipt Recording
- **Priority**: Critical
- **Steps**:
  1. Create sale 1000
  2. Navigate to Customer Receipts
  3. Receive 600
  4. Verify balance = 400
- **Expected**: Receipt recorded, balance updated

### PAY-003: Multiple Payment Methods
- **Priority**: High
- **Steps**:
  1. Supplier payment via "Cash"
  2. Supplier payment via "Bank"
  3. Supplier payment via "Cheque"
  4. Supplier payment via "Online"
- **Expected**: All methods recorded successfully

### PAY-004: Payment Reversal
- **Priority**: High
- **Steps**:
  1. Create payment
  2. Delete/reverse payment
  3. Verify balance restored
- **Expected**: Payment reversed, balance adjusted

---

## Module 10: LEDGERS (GL, SUPPLIER, CUSTOMER)

### LED-001: GL Account Balance
- **Priority**: Critical
- **Steps**:
  1. Create purchase (GL account: Payables)
  2. View GL account ledger
  3. Verify balance matches purchase amount
- **Expected**: Balance correct

### LED-002: Supplier Ledger Reconciliation
- **Priority**: High
- **Steps**:
  1. Create multiple purchases and payments from 1 supplier
  2. Verify ledger balance = total purchases - total payments
- **Expected**: Balance reconciles

### LED-003: Customer Ledger Reconciliation
- **Priority**: High
- **Steps**:
  1. Create multiple sales and receipts from 1 customer
  2. Verify ledger balance = total sales - total receipts
- **Expected**: Balance reconciles

---

## Module 11: JOURNAL ENTRIES

### JNL-001: Create Journal Entry
- **Priority**: High
- **Steps**:
  1. Navigate to Journal Entries (Accounting)
  2. Click "New Entry"
  3. Enter date, description
  4. Add 2 lines: Debit GL Account A (100), Credit GL Account B (100)
  5. Verify total debits = total credits = 100
  6. Submit
- **Expected**: Entry created, GL accounts updated

### JNL-002: Unbalanced Entry Rejection
- **Priority**: High
- **Steps**:
  1. Create entry with debit 100, credit 50
  2. Try to submit
- **Expected**: Error shown: "Debits and credits do not match"

### JNL-003: Journal Entry Reversal
- **Priority**: High
- **Steps**:
  1. Create journal entry
  2. View entry, click Reverse
  3. Verify reverse entry created (same accounts, opposite amounts)
- **Expected**: Reverse entry created

---

## Module 12: TRIAL BALANCE

### TB-001: Trial Balance Generation
- **Priority**: Critical
- **Steps**:
  1. Create some purchases, sales, payments
  2. Navigate to Reports > Trial Balance
  3. Select date range
  4. Verify all GL accounts shown with balances
  5. Verify total debits = total credits
- **Expected**: Trial balance generated, balanced

### TB-002: Trial Balance for Period
- **Priority**: High
- **Steps**:
  1. Create transaction on Jan 1
  2. Create transaction on Feb 1
  3. Generate trial balance for Jan only
  4. Verify Feb transaction not included
- **Expected**: Only selected period transactions shown

---

## Module 13: PROFIT & LOSS

### PL-001: P&L Report Generation
- **Priority**: Critical
- **Steps**:
  1. Create sale 1000 (COGS 600)
  2. Create expense 200 (Rent)
  3. Navigate to Reports > Profit & Loss
  4. Select date range
  5. Verify: Revenue 1000, COGS 600, GP 400, Expenses 200, NP 200
- **Expected**: P&L calculated correctly

### PL-002: P&L by Period Comparison
- **Priority**: High
- **Steps**:
  1. Create transactions in Jan and Feb
  2. Generate P&L for Jan and Feb
  3. Verify different totals for each period
- **Expected**: Each period shows correct numbers

---

## Module 14: BALANCE SHEET

### BS-001: Balance Sheet Generation
- **Priority**: Critical
- **Steps**:
  1. Create purchase (creates Payable)
  2. Create sale (creates Receivable)
  3. Navigate to Reports > Balance Sheet
  4. Verify: Assets (Cash, Receivables, Inventory), Liabilities (Payables), Equity
- **Expected**: Balance sheet generated, Assets = Liabilities + Equity

### BS-002: Balance Sheet Equation
- **Priority**: Critical
- **Steps**:
  1. Generate balance sheet
  2. Verify total assets = total liabilities + total equity
- **Expected**: Equation balanced

---

## Module 15: CASH FLOW

### CF-001: Cash Flow Statement
- **Priority**: High
- **Steps**:
  1. Create purchase and supplier payment (cash out)
  2. Create sale and customer receipt (cash in)
  3. Navigate to Reports > Cash Flow
  4. Verify Operating Activities section shows net cash flow
- **Expected**: Cash flow calculated

---

## Module 16: GENERAL REPORTS

### REP-001: Stock Report
- **Priority**: High
- **Steps**:
  1. Navigate to Reports
  2. Select "Stock Report"
  3. Verify all items shown with: qty, cost, sale price, inventory value
- **Expected**: Stock report generated

### REP-002: Aging Analysis
- **Priority**: High
- **Steps**:
  1. Create sale, no payment (Current)
  2. Create sale 60 days ago, no payment (60+ days)
  3. Navigate to Aging Reports
  4. Verify 2 age buckets shown
- **Expected**: Aging analysis calculated

### REP-003: Export Reports to CSV
- **Priority**: High
- **Steps**:
  1. Generate any report
  2. Click "Export to CSV"
  3. Verify file downloads
- **Expected**: CSV file downloaded with correct data

### REP-004: Export Reports to Excel
- **Priority**: High
- **Steps**:
  1. Generate any report
  2. Click "Export to Excel"
  3. Verify file downloads
- **Expected**: Excel file downloaded

---

## Module 17: BACKUP & RESTORE

### BCK-001: Database Backup
- **Priority**: Critical
- **Steps**:
  1. Navigate to Admin > Backup
  2. Click "Backup Database"
  3. Verify backup file downloads (JSON format)
  4. Check file contains transactions
- **Expected**: Backup created successfully

### BCK-002: Database Restore
- **Priority**: Critical
- **Steps**:
  1. Create some transactions
  2. Create backup
  3. Delete all transactions
  4. Restore from backup
  5. Verify transactions returned
- **Expected**: Restore successful, all data recovered

### BCK-003: Backup Format Integrity
- **Priority**: High
- **Steps**:
  1. Create backup
  2. Open backup file (JSON)
  3. Verify structure: { version, data: { users, suppliers, ... } }
- **Expected**: Backup format valid

---

## Module 18: IMPORT/EXPORT

### IMP-001: Bulk Import Suppliers
- **Priority**: High
- **Steps**:
  1. Prepare CSV: name, contact, address
  2. Navigate to Bulk Import
  3. Select "Suppliers"
  4. Upload CSV
  5. Verify suppliers imported
- **Expected**: Suppliers created from CSV

### IMP-002: Bulk Import Customers
- **Priority**: High
- **Steps**:
  1. Prepare CSV: name, contact, address
  2. Bulk Import > Customers
  3. Upload
- **Expected**: Customers imported

### IMP-003: Bulk Import Items
- **Priority**: High
- **Steps**:
  1. Prepare CSV: name, SKU, category, cost, price
  2. Bulk Import > Items
  3. Upload
- **Expected**: Items imported

### IMP-004: Export Suppliers to CSV
- **Priority**: Medium
- **Steps**:
  1. Navigate to Suppliers
  2. Click "Export to CSV"
- **Expected**: CSV downloaded with all suppliers

### IMP-005: Export Customers to CSV
- **Priority**: Medium
- **Steps**:
  1. Navigate to Customers
  2. Click "Export to CSV"
- **Expected**: CSV downloaded

---

## Module 19: USER MANAGEMENT

### USR-001: Create Admin User
- **Priority**: Critical
- **Steps**:
  1. Login as admin
  2. Navigate to Admin > Users
  3. Create new user with role "Admin"
  4. Verify user can access admin panel
- **Expected**: Admin user created with full access

### USR-002: Create Manager User
- **Priority**: High
- **Steps**:
  1. Create user with role "Manager"
  2. Login as manager
  3. Verify can create purchases, sales
- **Expected**: Manager created, limited access works

### USR-003: Create Regular User
- **Priority**: High
- **Steps**:
  1. Create user with role "User"
  2. Login as user
  3. Verify cannot access admin panel
- **Expected**: Regular user created, read-only access

### USR-004: Change User Role
- **Priority**: High
- **Steps**:
  1. Create user as "User"
  2. Edit and change role to "Manager"
  3. Logout and login
  4. Verify new permissions
- **Expected**: Role updated, permissions change

### USR-005: Delete User
- **Priority**: High
- **Steps**:
  1. Create user
  2. Delete user
  3. Try to login with deleted user
- **Expected**: Login fails, user removed

---

## Module 20: SETTINGS & CONFIGURATION

### SET-001: Company Settings
- **Priority**: High
- **Steps**:
  1. Navigate to Settings
  2. Verify company name, address, phone, email displayed
  3. Update company name
  4. Refresh page
  5. Verify change persisted
- **Expected**: Settings saved

### SET-002: Tax Code Configuration
- **Priority**: High
- **Steps**:
  1. Navigate to Settings > Tax Codes
  2. View default tax code (Standard)
  3. Verify rate = expected (e.g., 17%)
- **Expected**: Tax code configured

### SET-003: Fiscal Year Configuration
- **Priority**: High
- **Steps**:
  1. Navigate to Settings
  2. Verify fiscal year start month (e.g., July for Pakistan)
- **Expected**: Fiscal year configured

### SET-004: Account Creation
- **Priority**: High
- **Steps**:
  1. Navigate to Settings > Accounts
  2. Create cash account
  3. Verify account appears in dropdowns
- **Expected**: Account created and usable

---

## CROSS-MODULE INTEGRATION TESTS

### INT-001: Purchase to Supplier Payment Flow
- **Priority**: Critical
- **Steps**:
  1. Create purchase from Supplier A (1000)
  2. View supplier balance (should be 1000)
  3. Create payment 600
  4. View supplier balance (should be 400)
  5. Check GL: Payables account shows 1000
  6. Check Supplier Ledger: running balance correct
- **Expected**: All modules synchronized

### INT-002: Sale to Customer Receipt Flow
- **Priority**: Critical
- **Steps**:
  1. Create sale from Customer A (500)
  2. View customer balance (should be 500)
  3. Create receipt 300
  4. View customer balance (should be 200)
  5. Check GL: Receivables account shows 500
  6. Check Customer Ledger: running balance correct
- **Expected**: All modules synchronized

### INT-003: Inventory Transaction Impact
- **Priority**: Critical
- **Steps**:
  1. Create item (qty 0)
  2. Purchase 10 units (cost 100 each)
  3. Verify inventory value = 1000
  4. Sale 5 units (price 150 each)
  5. Verify remaining inventory value = 5 * 100 = 500
  6. Verify GL: Inventory account reflects changes
- **Expected**: Inventory values correct across modules

### INT-004: Trial Balance to Financial Reports
- **Priority**: High
- **Steps**:
  1. Create transactions to establish GL accounts
  2. Generate Trial Balance
  3. Generate P&L using same period
  4. Generate Balance Sheet
  5. Verify Trial Balance total debits = total credits
  6. Verify Balance Sheet: Assets = Liabilities + Equity
- **Expected**: All reports balanced

### INT-005: End-to-End Transaction
- **Priority**: Critical
- **Steps**:
  1. Create supplier, customer, items
  2. Purchase items from supplier (GL: Inventory +, Payables +)
  3. Adjust inventory (GL: Inventory adjustment)
  4. Sell items to customer (GL: Receivables +, Revenue +)
  5. Receive customer payment (GL: Cash +, Receivables -)
  6. Pay supplier (GL: Cash -, Payables -)
  7. Generate P&L: Revenue = X, COGS = Y, GP = X-Y
  8. Generate Balance Sheet: verify balanced
  9. Generate Trial Balance: verify total debits = total credits
- **Expected**: Complete cycle works, all GL entries correct

---

## EDGE CASES & ERROR SCENARIOS

### ERR-001: Negative Inventory
- **Priority**: High
- **Steps**:
  1. Create item with qty 5
  2. Try to sale qty 10
- **Expected**: Either error or warning shown

### ERR-002: Duplicate SKU
- **Priority**: High
- **Steps**:
  1. Create item with SKU "A1"
  2. Try to create another item with SKU "A1"
- **Expected**: Error shown

### ERR-003: Invalid Email
- **Priority**: Medium
- **Steps**:
  1. Try to create customer with invalid email format
- **Expected**: Validation error shown (if email used)

### ERR-004: Division by Zero
- **Priority**: High
- **Steps**:
  1. Try to calculate weighted average cost with 0 quantity
- **Expected**: No error, shows N/A or 0

### ERR-005: Concurrent Transaction
- **Priority**: High
- **Steps**:
  1. Open 2 browser tabs
  2. Tab 1: Create purchase, don't save
  3. Tab 2: Create purchase, save
  4. Tab 1: Try to save
- **Expected**: Either merge or conflict detected

---

## PERFORMANCE & LOAD TESTS

### PERF-001: Large Report Generation
- **Priority**: Medium
- **Steps**:
  1. Create 1000 transactions
  2. Generate Trial Balance
  3. Measure time to generate
- **Expected**: Completes in < 5 seconds

### PERF-002: Large Ledger Display
- **Priority**: Medium
- **Steps**:
  1. Supplier with 500 transactions
  2. View supplier ledger
- **Expected**: Page loads smoothly, pagination works

---

## SECURITY TESTS

### SEC-001: SQL Injection Attempt
- **Priority**: Critical
- **Steps**:
  1. Create customer with name: "'; DROP TABLE customer; --"
  2. Verify customer created safely (name stored as literal)
- **Expected**: No SQL executed, name stored as literal string

### SEC-002: XSS Attempt
- **Priority**: Critical
- **Steps**:
  1. Create item with name: "<script>alert('XSS')</script>"
  2. View item, verify no alert shown
- **Expected**: Script not executed, HTML escaped

### SEC-003: CSRF Token Validation
- **Priority**: Critical
- **Steps**:
  1. Create purchase form
  2. Remove CSRF token from form
  3. Submit
- **Expected**: Error or 403 returned

---

END OF CHECKLIST

Report results for each module as you complete testing. Format:

Module Name: PASS/FAIL
- Passed: X/Y tests
- Failed: (list any failures)
- Notes: (edge cases, observations)
