# TradeFlow ERP v1.0.0 - Comprehensive Testing Checklist

**Test Date**: _______________  
**Tester Name**: _______________  
**Environment**: Local / Render / TradeFlow / SalPurFlask  
**Browser**: _______________  
**Theme During Test**: Light / Dark  

---

## SECTION 1: AUTHENTICATION & LOGIN

### AUTH-001: Sign In - Valid Credentials
- [ ] Navigate to /signin
- [ ] Enter valid email and password
- [ ] Click "Sign In"
- [ ] Expected: Redirect to dashboard
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### AUTH-002: Sign In - Invalid Credentials
- [ ] Navigate to /signin
- [ ] Enter wrong password (try 5 times)
- [ ] Expected: Error message, rate limiting after multiple attempts
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### AUTH-003: Theme Persistence
- [ ] Login to app
- [ ] Switch to light/dark theme
- [ ] Logout and login again
- [ ] Expected: Theme preference is remembered
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### AUTH-004: Session Timeout
- [ ] Login successfully
- [ ] Wait or close tab (7-day session)
- [ ] Try to access protected page
- [ ] Expected: Redirect to signin
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 2: DASHBOARD

### DASH-001: Dashboard Load
- [ ] After login, verify dashboard loads
- [ ] Check all widgets: Total Sales, Total Purchases, Total Customers, Total Suppliers
- [ ] Check recent transactions table
- [ ] Expected: All data displays correctly
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### DASH-002: Navigation Bar
- [ ] Check sidebar menu items are all clickable
- [ ] Check dropdown menus: Parties, Inventory, Transactions, Accounting, Admin
- [ ] Expected: All menu items work, no broken links
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### DASH-003: Search Functionality
- [ ] Use global search box
- [ ] Search for a customer/supplier/item
- [ ] Expected: Results appear, links work
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 3: CUSTOMERS

### CUST-001: Create New Customer
- [ ] Click Parties → Customers
- [ ] Click "Add Customer" button
- [ ] Fill: Name, Contact, Address, Opening Balance
- [ ] Click Save
- [ ] Expected: Customer created, listed in table
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### CUST-002: View Customer Details
- [ ] Click on any customer
- [ ] Verify all fields display correctly
- [ ] Check ledger tab
- [ ] Expected: Customer info and ledger visible
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### CUST-003: Edit Customer
- [ ] Edit an existing customer
- [ ] Change name/contact
- [ ] Save changes
- [ ] Expected: Changes saved, reflected in list
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### CUST-004: Bulk Import Customers
- [ ] Go to Inventory → Bulk Import
- [ ] Select "Customers"
- [ ] Upload CSV file with customer data
- [ ] Expected: Customers imported successfully
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 4: SUPPLIERS

### SUPP-001: Create New Supplier
- [ ] Click Parties → Suppliers
- [ ] Click "Add Supplier" button
- [ ] Fill: Name, Contact, Address, Opening Balance
- [ ] Save
- [ ] Expected: Supplier created
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### SUPP-002: View Supplier Ledger
- [ ] Click on any supplier
- [ ] Check ledger tab
- [ ] Verify balance calculations
- [ ] Expected: Ledger shows all transactions
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 5: INVENTORY

### INV-001: Create Item
- [ ] Click Inventory → Items
- [ ] Click "Add Item"
- [ ] Fill: Name, Category, Unit, Stock, Purchase Price, Sale Price, Barcode
- [ ] Save
- [ ] Expected: Item created with barcode
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### INV-002: Stock Adjustment
- [ ] Click Inventory → Stock Adjustments
- [ ] Create adjustment: select item, qty
- [ ] Save
- [ ] Check stock updated
- [ ] Check ledger updated
- [ ] Expected: Stock and ledger reflect adjustment
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### INV-003: Bulk Import Items
- [ ] Go to Inventory → Bulk Import
- [ ] Select "Items"
- [ ] Upload CSV with items
- [ ] Expected: Items imported with correct stock and pricing
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### INV-004: Print Labels
- [ ] Click Inventory → Print Labels
- [ ] Select items
- [ ] Generate labels PDF
- [ ] Expected: PDF generates with barcodes
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 6: SALES

### SALE-001: Create New Sale
- [ ] Click Transactions → Sales (or Sales icon)
- [ ] Click "New Sale Invoice"
- [ ] Select Customer
- [ ] Add line items: select item, qty, price
- [ ] Verify tax calculation
- [ ] Save
- [ ] Expected: Invoice created, number assigned, stock reduced
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### SALE-002: Sale with Discount
- [ ] Create new sale
- [ ] Add discount % or amount
- [ ] Verify grand total recalculates
- [ ] Save
- [ ] Expected: Discount applied correctly
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### SALE-003: Edit Sale
- [ ] Edit existing sale
- [ ] Change qty, add item, change discount
- [ ] Save
- [ ] Expected: Changes saved, stock adjusted accordingly
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### SALE-004: View Sale Details
- [ ] Click on any sale
- [ ] Check all details: customer, items, calculations
- [ ] Click "View Invoice" to see print layout
- [ ] Expected: All details visible and correct
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### SALE-005: Quotation to Invoice
- [ ] Create quotation
- [ ] Convert to invoice
- [ ] Expected: Quotation type changes, stock adjustments applied
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 7: PURCHASES

### PUR-001: Create New Purchase
- [ ] Click Transactions → Purchases
- [ ] Click "New Purchase"
- [ ] Select Supplier
- [ ] Add line items
- [ ] Save
- [ ] Expected: Purchase created, stock increased
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### PUR-002: Purchase with Returns
- [ ] Create purchase
- [ ] Create purchase return
- [ ] Verify stock reduced from original
- [ ] Expected: Return processes correctly
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### PUR-003: Supplier Payment
- [ ] After purchase, click "Add Payment"
- [ ] Select payment method (Cash, Bank, Cheque, Online)
- [ ] Enter amount
- [ ] Save
- [ ] Expected: Payment recorded, ledger updated
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 8: POS (POINT OF SALE)

### POS-001: Create Sale via POS
- [ ] Click POS from sidebar
- [ ] Select customer (or Walk-in)
- [ ] Add items by scanning barcode or clicking
- [ ] Verify qty and pricing
- [ ] Complete sale
- [ ] Expected: Sale created, receipt printed option available
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### POS-002: Hold and Retrieve Bill
- [ ] Create sale in POS
- [ ] Click "Hold Bill"
- [ ] Create another sale
- [ ] Retrieve first bill
- [ ] Complete sale
- [ ] Expected: Bills independently managed
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### POS-003: POS Discount
- [ ] Create sale
- [ ] Apply discount
- [ ] Verify calculation
- [ ] Complete
- [ ] Expected: Discount applied to final amount
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 9: PAYMENTS & RECEIPTS

### PAY-001: Customer Receipt
- [ ] Click Accounting → Customer Receipts
- [ ] Select customer with outstanding balance
- [ ] Enter amount received
- [ ] Save
- [ ] Expected: Receipt recorded, ledger updated, balance reduced
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### PAY-002: Supplier Payment
- [ ] Click Accounting → Supplier Payments
- [ ] Select supplier with outstanding balance
- [ ] Enter amount paid
- [ ] Select payment method
- [ ] Save
- [ ] Expected: Payment recorded, ledger updated
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### PAY-003: Payment Reversal
- [ ] Make a payment/receipt
- [ ] Delete/reverse it
- [ ] Check ledger updates
- [ ] Expected: Transaction reversed, balances restored
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 10: LEDGERS

### LED-001: Customer Ledger
- [ ] Click on any customer
- [ ] View ledger tab
- [ ] Check: opening balance, sales, receipts, closing balance
- [ ] Expected: All transactions listed chronologically, balance correct
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### LED-002: Supplier Ledger
- [ ] Click on any supplier
- [ ] View ledger tab
- [ ] Verify opening balance, purchases, payments, closing balance
- [ ] Expected: Ledger accurate
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### LED-003: GL Account Ledger
- [ ] Go to Accounting → Chart of Accounts
- [ ] Click on any account
- [ ] View ledger
- [ ] Expected: All GL transactions visible
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 11: FINANCIAL REPORTS

### REP-001: Trial Balance
- [ ] Go to Reports → Trial Balance
- [ ] Check date range
- [ ] Verify: All accounts listed, debit/credit columns, totals equal
- [ ] Expected: Trial Balance balanced (Dr = Cr)
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### REP-002: Profit & Loss
- [ ] Go to Reports → Profit & Loss
- [ ] Select period
- [ ] Verify: Revenue, COGS, Expenses, Net Profit calculated
- [ ] Expected: P&L statement correct
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### REP-003: Balance Sheet
- [ ] Go to Reports → Balance Sheet
- [ ] Verify: Assets, Liabilities, Equity sections
- [ ] Check: Assets = Liabilities + Equity
- [ ] Expected: Balance Sheet equation holds
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### REP-004: Cash Flow
- [ ] Go to Reports → Cash Flow
- [ ] Check: Operating, Investing, Financing activities
- [ ] Expected: Cash flow statement generated correctly
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### REP-005: Stock Report
- [ ] Go to Reports → Stock Report
- [ ] Filter by category if needed
- [ ] Check: Item name, qty, value, valuation method
- [ ] Expected: All items listed with correct stock and value
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### REP-006: Aging Analysis
- [ ] Go to Reports → Aging Analysis
- [ ] Select Customer or Supplier
- [ ] Verify receivables/payables by age buckets
- [ ] Expected: Aging report shows outstanding amounts by period
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 12: ACCOUNTS (CASH & BANK)

### ACC-001: View Accounts
- [ ] Go to Accounting → Cash & Bank Accounts
- [ ] View all accounts (Bank, Cash, Cheque, Online)
- [ ] Check opening balances and current balances
- [ ] Expected: All accounts listed with correct balances
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### ACC-002: Account Ledger
- [ ] Click on any account
- [ ] View account ledger
- [ ] Verify transactions are posted correctly
- [ ] Expected: Ledger shows all cash/bank movements
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 13: SETTINGS

### SET-001: Company Settings
- [ ] Go to Admin → Settings → Company Settings
- [ ] Verify company name, logo, address
- [ ] Expected: Settings display correctly
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### SET-002: Tax Codes
- [ ] Go to Admin → Settings → Tax Codes
- [ ] Create new tax code
- [ ] Use in sale/purchase
- [ ] Expected: Tax calculated correctly in transactions
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### SET-003: Fiscal Year
- [ ] Go to Admin → Settings → Fiscal Year
- [ ] Verify current fiscal year
- [ ] Expected: Year displayed, used for reports
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 14: BACKUP & RESTORE

### BACK-001: Create Backup
- [ ] Go to Admin → System → Backup Database
- [ ] Click "Download Backup (.json)"
- [ ] File downloads
- [ ] Expected: JSON backup file created successfully
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### BACK-002: Restore Backup
- [ ] Create a test backup
- [ ] Go to Restore tab
- [ ] Upload backup file
- [ ] Confirm restore
- [ ] Expected: Database restored to backup state
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 15: USER MANAGEMENT

### USER-001: Create User
- [ ] Go to Admin → Users
- [ ] Click "Create User"
- [ ] Fill email, set role (Admin/Manager/User)
- [ ] Save
- [ ] Expected: User created, can login
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### USER-002: Change User Role
- [ ] Edit existing user
- [ ] Change role
- [ ] Save
- [ ] Login as that user, verify permissions
- [ ] Expected: Role change takes effect
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### USER-003: Delete User
- [ ] Create test user
- [ ] Delete user
- [ ] Verify user cannot login
- [ ] Expected: User deleted successfully
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 16: THEME & UI (ALL PAGES)

### UI-LIGHT-001: Light Theme - All Pages Readable
- [ ] Switch to light theme
- [ ] Navigate through: Dashboard, Sales, Purchases, Customers, Reports
- [ ] Check: Text visibility, button clarity, form input readability
- [ ] Expected: No white-on-white or low-contrast issues
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### UI-DARK-001: Dark Theme - All Pages Readable
- [ ] Switch to dark theme
- [ ] Navigate through same pages
- [ ] Check: Text visibility, button contrast, alert boxes readable
- [ ] Expected: All text readable, good contrast
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### UI-002: File Upload Inputs
- [ ] Go to Bulk Import page
- [ ] Check file input in both themes
- [ ] Expected: "No file chosen" text visible in both
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### UI-003: Tables in Both Themes
- [ ] View any data table (Sales, Purchases, Customers)
- [ ] Check in light and dark theme
- [ ] Expected: Headers clear, rows readable, alternating colors work
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### UI-004: Buttons in Both Themes
- [ ] Check primary, secondary, danger, warning buttons
- [ ] Both themes
- [ ] Expected: All buttons visible, clickable, text readable
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### UI-005: Alerts in Both Themes
- [ ] Find info, warning, danger, success alerts
- [ ] Both themes
- [ ] Expected: Alert text readable, color scheme appropriate
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### UI-006: Badges Visibility
- [ ] Check status badges (Paid, Partial, Pending)
- [ ] Both themes
- [ ] Expected: Badge text readable
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 17: BROWSER & RESPONSIVE

### RESP-001: Desktop (1920x1080)
- [ ] Test at full desktop resolution
- [ ] Check layout, no horizontal scroll
- [ ] Expected: All elements fit, no layout breaks
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### RESP-002: Tablet (768px width)
- [ ] Resize browser to tablet width
- [ ] Check responsive menu, layout
- [ ] Expected: App adapts to tablet view
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### RESP-003: Mobile (375px width)
- [ ] Resize to mobile width
- [ ] Check menu collapses, inputs stack
- [ ] Expected: Mobile-friendly layout
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## SECTION 18: PERFORMANCE

### PERF-001: Page Load Speed
- [ ] Measure load time of main pages
- [ ] Dashboard: ___ seconds
- [ ] Sales page: ___ seconds
- [ ] Reports: ___ seconds
- [ ] Expected: All < 3 seconds
- **Status**: Pass / Fail / Block
- **Notes**: ___________

### PERF-002: Large Report Generation
- [ ] Generate Trial Balance for full year
- [ ] Generate P&L for full year
- [ ] Expected: Reports generate within reasonable time (< 5 sec)
- **Status**: Pass / Fail / Block
- **Notes**: ___________

---

## FINAL SUMMARY

**Total Test Cases**: ___  
**Passed**: ___  
**Failed**: ___  
**Blocked**: ___  

**Overall Status**: ✓ Ready for Release / ⚠ Minor Issues / ✗ Blocking Issues

**Critical Issues Found**:
```
[List any critical issues below]

1. ___________
2. ___________
3. ___________
```

**Recommended Actions**:
- [ ] Release as v1.0.0 Stable
- [ ] Fix listed issues, re-test
- [ ] Need more testing time

**Sign-off**:
Tester: _______________ Date: _______________

QA Lead: _______________ Date: _______________
