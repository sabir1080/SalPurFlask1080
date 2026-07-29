# ITEM-LEVEL DISCOUNT & TAX SYSTEM - COMPLETE ANALYSIS

**Date:** 2026-07-28  
**Status:** ANALYSIS COMPLETE - READY FOR IMPLEMENTATION  
**Scope:** Convert from global invoice-level to item-level discount & tax

---

## EXECUTIVE SUMMARY

Current system has item-level **database fields** but **global UI** (applies same discount/tax to all items). The migration requires:

1. **Database:** ✅ READY (all fields exist)
2. **Migration:** NONE NEEDED (fields already in schema)
3. **UI Changes:** REQUIRED (move discount/tax from global to per-item)
4. **Business Logic:** REQUIRES UPDATE (item-level product defaults)
5. **Backward Compatibility:** ✅ SAFE (new fields have defaults)

---

## CURRENT STATE ANALYSIS

### 1. Database Schema - COMPLETE ✅

**SaleItem Model** (lines 307-328):
```python
discount_type = db.Column(db.String(10), default="percent")
discount_value = db.Column(db.Numeric(14, 4), default=0.0)
discount_amount = db.Column(db.Numeric(14, 4), default=0.0)
tax_percent = db.Column(db.Numeric(14, 4), default=0.0)
tax_amount = db.Column(db.Numeric(14, 4), default=0.0)
amount = db.Column(db.Numeric(14, 4))
```

**Status:** ✅ All fields exist. No schema migration needed.

### 2. PurchaseItem Model - COMPLETE ✅

Same fields exist for purchases:
```python
discount_type, discount_value, discount_amount
tax_percent, tax_amount, amount
```

### 3. Sale/Purchase Header Models - PROBLEMATIC ⚠️

**Sale Model** (lines 256-274) has DUPLICATE fields:
```python
class Sale(db.Model):
    discount_type = db.Column(...)
    discount_value = db.Column(...)
    tax_percent = db.Column(...)
    discount_amount = db.Column(...)
    tax_amount = db.Column(...)
    line_items = db.relationship("SaleItem", ...)  # ← Items have own discount/tax
```

**Problem:** Both Sale header AND SaleItem have discount/tax fields.
- Sale header fields are GLOBAL (currently used in UI)
- SaleItem fields are ITEM-LEVEL (not used in current UI)

**Current Behavior:**
- UI applies discount/tax to Sale header only
- Copies same values to ALL SaleItems
- No per-item control

### 4. Item Model - MISSING ❌

**No default tax fields:**
```python
class Item(db.Model):
    # Missing:
    # - default_tax_percent
    # - tax_category_id
    # - is_taxable (boolean)
```

**Impact:** Cannot auto-populate item tax in POS.

### 5. Calculation Function - PERFECT ✅

**calc_discount_tax()** (lines 2451-2462):
```python
def calc_discount_tax(gross, discount_type, discount_value, tax_percent):
    """Returns (discount_amt, tax_amt, net_total)"""
    # Discount applied first
    # Tax applied to taxable (after discount)
    # Returns all three amounts
```

**Status:** ✅ Already supports item-level calculation. Reusable as-is.

### 6. Current UI Flow - GLOBAL (To be changed)

**POS Screen:**
- Single global discount input
- Single global tax input
- Applied to ALL items equally
- Per-item fields in DB ignored

**Normal Sales:**
- Single global discount/tax fields
- Applied to all items
- Per-item fields in DB ignored

### 7. Invoices - READY ✅

Invoice template already iterates items and shows per-item fields:
```jinja2
{% for si in sale.line_items %}
  Item: {{ si.item.name }}
  Discount: {{ si.discount_amount or "—" }}
  Tax: {{ si.tax_amount or "—" }}
  Total: {{ si.amount }}
{% endfor %}
```

**Status:** ✅ Template already supports per-item display. Just needs data.

### 8. Accounting - READY ✅

GL posting uses SaleItem.tax_amount correctly:
```python
# Lines in journal_entry are posted per-item
# tax_amount in each SaleItem drives the posting
```

**Status:** ✅ Already calculates per-item GL entries.

---

## REQUIRED CHANGES

### CHANGE 1: Add Default Tax to Item Model

**File:** `salpurflask/models/models.py`  
**Location:** Item class (~line 57-92)

**Add fields:**
```python
class Item(db.Model):
    # ... existing fields ...
    
    # NEW: Default tax configuration
    default_tax_percent = db.Column(db.Numeric(5, 2), nullable=False, default=0.0)
    is_taxable = db.Column(db.Boolean, nullable=False, default=True)
    tax_category_id = db.Column(db.Integer, db.ForeignKey("tax_category.id"), nullable=True)
```

**Database Migration:**
```sql
ALTER TABLE item ADD COLUMN default_tax_percent DECIMAL(5,2) NOT NULL DEFAULT 0.0;
ALTER TABLE item ADD COLUMN is_taxable BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE item ADD COLUMN tax_category_id INTEGER REFERENCES tax_category(id);
```

**Note:** This is a NEW table reference, will need TaxCategory model.

### CHANGE 2: Normal Sales Form - Move Discount/Tax to Per-Item

**File:** `templates/edit_sale.html` (or wherever sale form is)

**Current:**
```html
Global Discount [__]%
Global Tax [__]%
```

**New:**
```html
Per-Item Form:
  Item [dropdown]
  Qty [__]
  Price [__]
  Discount Type [% | Rs]
  Discount Value [__]
  Tax % [__]  ← Auto-filled from item.default_tax_percent
  → Shows calculated: Discount Amt, Tax Amt, Line Total
```

### CHANGE 3: POS Screen - Move Discount/Tax to Per-Item

**File:** `templates/pos.html`

**Current:**
```html
Global Discount [__]%
Global Tax [__]%
Cart Total [amount]
```

**New:**
```html
Cart per-item:
  Item Name | Qty | Price | Discount | Tax % | Tax Amt | Line Total
  Item A    | 2   | 500   | 5%      | 10%  | 95    | 1045
  Item B    | 1   | 300   | 0%      | 0%   | 0     | 300
  
  Subtotal: 1345
  Total Discount: 50
  Total Tax: 95
  Grand Total: 1390
```

### CHANGE 4: Checkout Route - Use Per-Item Fields

**File:** `salpurflask/sales/routes.py` - `pos_checkout()` function

**Current:**
```python
# Applies global discount/tax to all items
disc_amt, tax_amt, net = calc_discount_tax(gross, "percent", 0, 0)
```

**New:**
```python
# Each item has its own discount/tax
for item in items:
    gross = item['qty'] * item['price']
    disc_amt, tax_amt, net = calc_discount_tax(
        gross,
        item.get('discount_type', 'percent'),
        item.get('discount_value', 0),
        item.get('tax_percent', 0)
    )
    # Store in SaleItem as per-item values
```

### CHANGE 5: Normal Sales Route - Use Per-Item Fields

**File:** `salpurflask/sales/routes.py` - `sale()` function

**Current:**
```python
# Creates sale with global fields, copies to all items
for item in items:
    si = SaleItem(
        ...,
        discount_type="percent",
        discount_value=0,
        tax_percent=0
    )
```

**New:**
```python
# Each item gets its own discount/tax from form
for item in items:
    si = SaleItem(
        ...,
        discount_type=item['discount_type'],
        discount_value=item['discount_value'],
        tax_percent=item['tax_percent'],
        # Calculate amounts
        discount_amount=calc_discount_tax(gross, ...)[0],
        tax_amount=calc_discount_tax(gross, ...)[1],
        amount=calc_discount_tax(gross, ...)[2]
    )
```

### CHANGE 6: Hold Bill - Already Supports Item-Level ✅

**PosHold enrichment** already preserves per-item fields:
```python
"discount_type": line.discount_type,
"discount_value": line.discount_value,
"tax_percent": line.tax_percent,
```

**No changes needed** - just needs item-level UI to set values.

### CHANGE 7: Invoices - Already Supports Item-Level ✅

Template already shows per-item:
```jinja2
{{ si.discount_amount }} ({{ si.discount_value }}%)
{{ si.tax_amount }} ({{ si.tax_percent }}%)
```

**No changes needed** - just needs data.

### CHANGE 8: Accounting - Already Works Per-Item ✅

GL posting already uses SaleItem.tax_amount per line.

**No changes needed** - calculations already correct.

---

## DATABASE MIGRATION REQUIREMENTS

### STEP 1: Create TaxCategory Model (Optional)

For future tax categorization (HST, GST, VAT, etc.)

```python
class TaxCategory(db.Model):
    __tablename__ = "tax_category"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    tax_rate = db.Column(db.Numeric(5, 2), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
```

**Migration:**
```sql
CREATE TABLE tax_category (
    id INTEGER PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    tax_rate DECIMAL(5,2) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);

ALTER TABLE item ADD COLUMN tax_category_id INTEGER REFERENCES tax_category(id);
```

### STEP 2: Add Item Tax Fields

```sql
ALTER TABLE item 
ADD COLUMN default_tax_percent DECIMAL(5,2) NOT NULL DEFAULT 0.0,
ADD COLUMN is_taxable BOOLEAN NOT NULL DEFAULT TRUE;
```

### STEP 3: Seed Default Tax Categories (Optional)

```sql
INSERT INTO tax_category (name, tax_rate) VALUES
('Tax Exempt', 0.00),
('GST 5%', 5.00),
('HST 13%', 13.00),
('VAT 15%', 15.00);
```

### Database Changes Summary

| Table | Column | Type | Default | Purpose |
|-------|--------|------|---------|---------|
| item | default_tax_percent | DECIMAL(5,2) | 0.0 | Auto-populate POS tax |
| item | is_taxable | BOOLEAN | TRUE | Whether item is taxable |
| item | tax_category_id | INTEGER FK | NULL | Link to tax category |
| tax_category | (new table) | - | - | Tax rate master data |

**Backward Compatibility:** ✅ All defaults are 0/NULL, existing items work unchanged.

---

## IMPLEMENTATION FLOW CHANGES

### Normal Sale Entry Flow

**BEFORE:**
```
Form → Sale (global discount/tax) → SaleItems (copy global values)
```

**AFTER:**
```
Form (per-item discount/tax) → Sale (minimal/none) → SaleItems (per-item values)
       ↑ each row has own discount/tax
       ↑ tax auto-loaded from item.default_tax_percent
```

### POS Flow

**BEFORE:**
```
Scan items → Global discount/tax input → Apply to all → Checkout
```

**AFTER:**
```
Scan items → Each item shows tax from item.default_tax_percent
         → User can override per-item discount/tax
         → Checkout sends per-item values
```

### Data Calculation

**BEFORE:**
```
Gross = qty * price
Discount = global × all items
Tax = global × all items
Per-item amount = (gross - discount + tax)
```

**AFTER:**
```
Per-item calculation:
  Gross = qty * price
  Discount = item_discount × item (per-item)
  Tax = (gross - discount) × item_tax% (per-item)
  Amount = gross - discount + tax
```

---

## SAFE IMPLEMENTATION STRATEGY

### Phase 1: Add Item Model Fields (Safe ✅)

1. Add `default_tax_percent` to Item
2. Add `is_taxable` to Item
3. Add migration
4. Existing items get default (0%, taxable=TRUE)
5. No UI changes yet
6. Old code continues working

**Risk:** NONE - additive only

### Phase 2: Update Forms (Controlled ✅)

1. Update normal sales form:
   - Replace global discount/tax with per-item
   - Auto-fill tax from item.default_tax_percent
   - Allow override

2. Update POS checkout:
   - Replace global controls with per-item UI
   - Auto-load tax from item
   - Allow per-item adjustment

3. Routes process per-item values

**Risk:** LOW - old data structure still works, new code uses per-item

### Phase 3: Resume & Invoice (No Code Risk ✅)

1. Hold bills already store per-item values
2. Resume already restores per-item values
3. Invoices already display per-item
4. GL posting already per-item

**Risk:** NONE - code already supports this

### Phase 4: Optional Tax Categories (Future)

If needed later:
- Create TaxCategory model
- Link to Item
- Populate from tax_category table

---

## BACKWARD COMPATIBILITY VERIFICATION

### Existing Sales Data

**Old sales will:**
- ✅ Still display correctly (SaleItem.tax_amount stored)
- ✅ GL entries still correct (posted from SaleItem)
- ✅ Reports still accurate (sum per-item amounts)

**Old POS holds will:**
- ✅ Still resume correctly (per-item fields preserved)
- ✅ Checkout works (calculates from each item's fields)

### Migrations

**Safe approach:**
```sql
-- Safe: additive only
ALTER TABLE item ADD COLUMN default_tax_percent DECIMAL(5,2) DEFAULT 0.0;
ALTER TABLE item ADD COLUMN is_taxable BOOLEAN DEFAULT TRUE;

-- All existing items:
-- - default_tax_percent = 0.0 (no tax)
-- - is_taxable = TRUE (allow taxing if set)
```

**No existing data needs to change.**

---

## FILES TO CHANGE

### Backend

1. **salpurflask/models/models.py**
   - Add TaxCategory model (optional)
   - Add default_tax_percent to Item
   - Add is_taxable to Item
   - Add tax_category_id to Item

2. **app.py** (or migration file)
   - Add database migration for new Item columns
   - Optionally seed TaxCategory data

3. **salpurflask/sales/routes.py**
   - Update `sale()` function for per-item discount/tax
   - Update `pos_checkout()` function for per-item values
   - No changes to `pos_hold()` (already correct)

### Frontend

4. **templates/edit_sale.html** (if exists) or form template
   - Change to per-item input rows
   - Add tax auto-fill from item
   - Show per-item calculation

5. **templates/pos.html**
   - Remove global discount/tax controls
   - Add per-item display in cart
   - Allow per-item override
   - Calculate per-item totals

6. **templates/invoice_sale.html**
   - No changes (already supports per-item display)

7. **templates/pos_receipt.html**
   - No changes (already supports per-item display)

---

## TEST SCENARIOS

### Test 1: Single Item with Default Tax

**Setup:**
- Item A: price=1000, default_tax_percent=10%

**Expected:**
- POS shows: 1000 × 10% tax = 100 → Total 1100
- Manual override to 0% works
- Invoice shows: Price 1000, Tax 100

### Test 2: Multiple Items Different Tax

**Setup:**
- Item A: 1000, tax=10%
- Item B: 500, tax=0%

**Expected:**
- Total: 1000 + 100 (tax on A) + 500 + 0 (tax on B) = 1600
- Invoice shows per-item tax
- GL entries separate by item

### Test 3: Per-Item Discount + Tax

**Setup:**
- Item: 1000, discount=5%, tax=10%

**Expected:**
- Gross: 1000
- Discount: 50 (1000×5%)
- Taxable: 950
- Tax: 95 (950×10%)
- Total: 1045

### Test 4: Hold → Resume → Checkout

**Setup:**
- Add items with different discount/tax
- Hold bill
- Resume
- Verify discount/tax preserved
- Checkout

**Expected:**
- All values preserved through cycle
- GL correct
- Invoice correct

### Test 5: Old Data Still Works

**Setup:**
- Create sale with old code (global discount/tax)
- View in new system

**Expected:**
- Displays correctly
- GL still balanced
- Reports still accurate

---

## PERMISSIONS & VALIDATION

### User Roles

**Cashier/Operator:**
- Can select item
- Can set quantity
- Tax auto-filled from item (cannot change without manager approval)
- Discount: limited to % within set range

**Manager/Admin:**
- Full access to all fields
- Can override item tax
- Can set any discount

### Validation

- Tax % between 0-100
- Discount value >= 0
- Discount <= gross (prevent negative)
- Quantity > 0

---

## REMAINING UNKNOWNS

1. **Current sales form location**
   - Need to find where normal sales entry form is
   - May be in different template/file

2. **Discount override permissions**
   - How to enforce manager-only override
   - Current role system capabilities

3. **Tax category implementation**
   - Optional now, but needed for future tax complexity
   - Seeding data needed

---

## RISK SUMMARY

| Risk | Level | Mitigation |
|------|-------|-----------|
| Breaking old data | NONE | DB fields have defaults, old data preserved |
| GL unbalanced | NONE | Calculation function unchanged |
| Backward compat | NONE | Additive changes only |
| Performance | LOW | Same calculation, just per-item |
| User confusion | LOW | UI clearly shows per-item values |

---

## MIGRATION CHECKLIST

- [ ] Add Item model fields (default_tax_percent, is_taxable, tax_category_id)
- [ ] Create database migration
- [ ] Test backward compatibility
- [ ] Update normal sales form (per-item discount/tax)
- [ ] Update POS UI (per-item controls)
- [ ] Update checkout route (per-item calculation)
- [ ] Update normal sales route (per-item calculation)
- [ ] Test all scenarios (1-5)
- [ ] Verify invoices display correctly
- [ ] Verify GL entries balanced
- [ ] Verify reports accurate
- [ ] Verify hold/resume works
- [ ] Update documentation

---

**Status:** READY FOR IMPLEMENTATION

All components analyzed. No showstoppers. Database ready. Calculation function ready. Invoice templates ready. GL posting ready. UI needs updates.

---
