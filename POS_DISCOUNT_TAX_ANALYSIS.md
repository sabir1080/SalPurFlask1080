# POS DISCOUNT & TAX FLOW - COMPLETE ANALYSIS & GAP REPORT

**Date:** 2026-07-28  
**Status:** ANALYSIS COMPLETE - AWAITING APPROVAL FOR IMPLEMENTATION

---

## EXECUTIVE SUMMARY

The backend infrastructure for discount and tax is **complete and working**. However, the **frontend UI and invoice display are incomplete**:

- ✅ Backend: Calculation logic exists (calc_discount_tax function)
- ✅ Backend: Database fields exist (tax_percent, tax_amount, discount_type, discount_value, discount_amount)
- ✅ Backend: Checkout accepts tax/discount data
- ✅ Backend: Accounting posts correctly
- ❌ Frontend: No UI controls for discount input
- ❌ Frontend: No UI controls for tax input
- ❌ Frontend: Tax defaults to 0 (loads from nowhere)
- ❌ Invoice: Discount/tax not clearly displayed
- ❌ Receipt: No discount/tax display

---

## CURRENT IMPLEMENTATION STATE

### PART 1: Backend - Complete ✅

#### 1.1 Calculation Function Exists
**File:** `salpurflask/models/models.py:2451-2462`

```python
def calc_discount_tax(gross, discount_type, discount_value, tax_percent):
    """Returns (discount_amt, tax_amt, net_total)"""
    gross = float(gross or 0)
    if discount_type == "fixed":
        disc = min(discount_value, gross)
    else:
        disc = gross * discount_value / 100
    taxable = gross - disc
    tax = taxable * tax_percent / 100
    return round(disc, 4), round(tax, 4), round(taxable + tax, 4)
```

**Status:** WORKING ✅

#### 1.2 Database Fields Exist
**File:** `salpurflask/models/models.py` - SaleItem model

```python
discount_type = db.Column(db.String(20), default="percent")
discount_value = db.Column(db.Numeric(14, 4), default=0)
discount_amount = db.Column(db.Numeric(14, 4), default=0)
tax_percent = db.Column(db.Numeric(5, 2), default=0)
tax_amount = db.Column(db.Numeric(14, 4), default=0)
amount = db.Column(db.Numeric(14, 4))  # Final: gross - discount + tax
```

**Status:** COMPLETE ✅

#### 1.3 Checkout Receives & Calculates
**File:** `salpurflask/sales/routes.py:551-594` - pos_checkout()

```python
gross = qty_i * price_f
d_type = str(ln.get("discount_type") or "percent")
d_val = float(ln.get("discount_value") or 0)
tax_pct = float(ln.get("tax_percent") or 0)
disc_amt, tax_amt, net = calc_discount_tax(gross, d_type, d_val, tax_pct)

db.session.add(SaleItem(
    ...,
    discount_type=d_type,
    discount_value=d_val,
    discount_amount=disc_amt,
    tax_percent=tax_pct,
    tax_amount=tax_amt,
    amount=net
))
```

**Status:** WORKING ✅

#### 1.4 Hold Bill Preserves Discount/Tax
**File:** `salpurflask/sales/routes.py:654-705` - pos_hold()

```python
enriched_lines.append({
    ...,
    "discount_type": str(ln.get("discount_type") or "percent"),
    "discount_value": float(ln.get("discount_value") or 0),
    "tax_percent": float(ln.get("tax_percent") or 0),
})
```

**Status:** COMPLETE ✅

#### 1.5 Invoice Template Displays
**File:** `templates/invoice_sale.html:138-213`

```jinja2
{% if row_disc > 0 %}
    − {{ row_disc | fmt_num }}
    {% if si.discount_type == 'percent' %}<small>({{ si.discount_value | pct }}%)</small>{% endif %}
{% endif %}

{% if row_tax > 0 %}
    + {{ row_tax | fmt_num }}
    <small>({{ si.tax_percent | pct }}%)</small>
{% endif %}

{% if ns.total_tax > 0 %}
<tr class="text-info">
    <td>Total Tax</td>
    <td class="text-end">+ {{ ns.total_tax | fmt_num }}</td>
</tr>
{% endif %}
```

**Status:** IMPLEMENTED but shows "—" when values are 0 ✅

---

### PART 2: Frontend - INCOMPLETE ❌

#### 2.1 POS Cart Object
**File:** `templates/pos.html:142-248`

**Current state:**
```javascript
cart = {
    id, item_id, name, price, qty, stock, unit,
    unit_id, unit_name, unit_factor,
    discount_type, discount_value,  // ← STORED but not editable
    tax_percent                      // ← STORED but not editable
}
```

**Problem:**
- Fields exist in cart object
- Fields NOT editable via UI
- Defaults to: discount_value=0, tax_percent=0
- No UI to change these values

#### 2.2 Cart Total Calculation
**File:** `templates/pos.html:189-195`

**Current state:**
```javascript
const cartTotal = () => cart.reduce((s, l) => {
    const gross = l.price * l.qty;
    const discountAmt = l.discount_type === 'fixed' 
        ? Math.min(l.discount_value || 0, gross) 
        : (gross * (l.discount_value || 0) / 100);
    const taxable = gross - discountAmt;
    const tax = taxable * (l.tax_percent || 0) / 100;
    return s + (taxable + tax);
}, 0);
```

**Problem:**
- Correctly CALCULATES discount/tax
- But since discount_value and tax_percent are always 0, result is just gross sum
- No breakdown shown to user

#### 2.3 Cart Display
**File:** `templates/pos.html:199-219` - render()

**Current display:**
```
Item Name
  Meta: price each · stock in stock · unit
  [Qty Input]  Total Amount   [X]
```

**Missing:**
- No discount display per item
- No tax display per item
- No breakdown of: subtotal - discount + tax

#### 2.4 Totals Section
**File:** `templates/pos.html:72-75`

**Current display:**
```
Total: Rs 0.00  (just the grand total)
```

**Missing:**
- Subtotal line
- Discount line
- Tax line
- Grand total line

---

### PART 3: Invoice Display - INCOMPLETE ❌

#### 3.1 Normal Invoice (invoice_sale.html)
**Current state:** Displays tax and discount IF values > 0

**Issue:** Shows "—" when discount=0 or tax=0 (customer sees blank columns)

**What's missing:**
- Clear "Subtotal" row before discount/tax
- Clear "Grand Total" row after tax
- Professional layout with borders

#### 3.2 POS Receipt (pos_receipt.html)
**Current display:**
```
Item: Amoxicillin 250mg
  1 Pcs x 25.00           25.00

─────────────────────────────────
TOTAL                      25.00
Paid                       25.00
```

**Missing:**
- Subtotal line
- Discount line (if applicable)
- Tax line (if applicable)
- Grand Total clearly marked

---

### PART 4: Business Configuration - INCOMPLETE ❌

**File:** `salpurflask/models/business_config.py`

**Current state:**
- BusinessCategory model exists
- config_data JSON field exists
- NO tax_percent or default_tax setting

**Missing:**
- Default tax rate configuration
- Tax category per item (optional)
- Discount rules (optional)

**Note:** This is a NICE-TO-HAVE, not required for Phase 1

---

## GAP ANALYSIS SUMMARY

### What Works
| Component | Status | Details |
|-----------|--------|---------|
| Database fields | ✅ | All fields present (tax_percent, discount_amount, etc.) |
| Calculation function | ✅ | calc_discount_tax() correctly computes |
| Backend checkout | ✅ | Receives and stores discount/tax |
| Hold bill preservation | ✅ | Preserves discount/tax through resume |
| Accounting posting | ✅ | GL entries posted correctly |
| Invoice template logic | ✅ | Displays when values exist |

### What's Missing
| Component | Status | Details |
|-----------|--------|---------|
| **POS UI: Discount input** | ❌ | No field to input/select discount |
| **POS UI: Tax input** | ❌ | No field to select tax rate |
| **POS UI: Cart breakdown** | ❌ | No display of subtotal/discount/tax |
| **POS UI: Totals display** | ❌ | Only shows grand total, no breakdown |
| **POS Receipt: Detail** | ❌ | Doesn't show discount/tax lines |
| **Business config** | ❌ | No default tax rate setting |

---

## FILES TO CHANGE

### Part 1: Frontend Input Controls
**File:** `templates/pos.html`

**Changes needed:**
1. Add discount input section (after cart display)
   - Input field for discount amount or percentage
   - Toggle between "percent" and "fixed"
   
2. Add tax input section
   - Input field for tax percentage
   - Option: Auto-load from business config
   
3. Add totals display
   - Subtotal = gross of all items
   - Total Discount = sum of all item discounts
   - Total Tax = sum of all item taxes
   - Grand Total = subtotal - discount + tax

4. Update cart item render
   - Show each item's discount (if > 0)
   - Show each item's tax (if > 0)

### Part 2: Checkout Payload
**File:** `templates/pos.html` (JavaScript)

**Changes needed:**
1. Update checkout payload to include:
   - Global discount (if entered)
   - Global tax rate (if entered)
   - Or per-item discount/tax

2. OR: Apply discount/tax to each item in cart before sending

### Part 3: Invoice Templates
**File:** `templates/invoice_sale.html`

**Changes needed:**
1. Add "Subtotal" row before discount/tax rows
2. Format discount/tax rows clearly
3. Add "Grand Total" row clearly marked
4. Ensure display is professional (borders, alignment)

**File:** `templates/pos_receipt.html`

**Changes needed:**
1. Add discount breakdown (if any)
2. Add tax breakdown (if any)
3. Clear line for subtotal
4. Clear line for grand total

### Part 4: Business Configuration (Optional)
**File:** `salpurflask/models/business_config.py` OR `salpurflask/services/config_service.py`

**Changes needed:**
1. Add default_tax_percent setting
2. Add ability to load default tax in POS UI

---

## IMPLEMENTATION PLAN

### Phase 1: Frontend Input UI (templates/pos.html)

**Step 1.1:** Add global discount section
- Input field + toggle (percent/fixed)
- Update all items with same discount
- Show total discount amount

**Step 1.2:** Add global tax section  
- Input field for tax percentage
- Option to load from config (future)
- Show total tax amount

**Step 1.3:** Add professional totals display
```
Subtotal:        Rs 1,000.00
Discount:        Rs   (50.00)
Taxable:         Rs   950.00
Tax (10%):       Rs +  95.00
───────────────────────────
Grand Total:     Rs 1,045.00
```

**Step 1.4:** Update checkout payload
- Collect discount and tax from UI
- Apply to each cart item
- Send in checkout request

### Phase 2: Invoice Display (invoice_sale.html + pos_receipt.html)

**Step 2.1:** Update invoice_sale.html
- Add clear "Subtotal" row
- Format discount row with "—" replaced by amount or dashes
- Format tax row with "—" replaced by amount or dashes
- Add clear "Grand Total" row

**Step 2.2:** Update pos_receipt.html
- Add subtotal line in thermal format
- Add discount line (if > 0)
- Add tax line (if > 0)
- Clear grand total marked

### Phase 3: Business Configuration (Future - Not Required)

**Step 3.1:** Add default tax config
- Config screen to set default tax %
- POS loads default on page load
- User can override per transaction

---

## FLOW DIAGRAM

### Current Flow (Broken for discount/tax)
```
POS UI
├─ Scan item
│  └─ Add to cart (discount=0, tax=0)
├─ No discount input
├─ No tax input
├─ Cart total (just gross)
├─ Checkout
│  └─ Send: discount=0, tax=0
└─ Invoice shows: "—" for discount/tax
```

### Target Flow (With Fix)
```
POS UI
├─ Scan item
│  └─ Add to cart
├─ [NEW] Discount input → Apply to cart items
├─ [NEW] Tax input → Apply to cart items
├─ [NEW] Display breakdown:
│  ├─ Subtotal: 1000
│  ├─ Discount: -50
│  ├─ Tax: +95
│  └─ Grand Total: 1045
├─ Checkout
│  └─ Send: discount_value=50, tax_percent=10
├─ Invoice [NEW]
│  ├─ Subtotal: 1000
│  ├─ Discount: 50 (5%)
│  ├─ Tax: 95 (10%)
│  └─ Grand Total: 1045
└─ Hold Bill → Resume → All fields preserved ✅
```

---

## HOLD BILL COMPATIBILITY

**Current state:** ✅ ALREADY WORKING

Hold bill enrichment already includes:
```python
"discount_type": str(ln.get("discount_type") or "percent"),
"discount_value": float(ln.get("discount_value") or 0),
"tax_percent": float(ln.get("tax_percent") or 0),
```

When resuming hold:
```javascript
discount_type: line.discount_type || 'percent',
discount_value: line.discount_value || 0,
tax_percent: line.tax_percent || 0,
```

**No changes needed for hold bill** - It will automatically preserve whatever discount/tax values are set in UI.

---

## IMPLEMENTATION CONSIDERATIONS

### Design Decision: Global vs Per-Item

**Current design:** Each cart item CAN have its own discount/tax
- SaleItem has: discount_type, discount_value, tax_percent
- This allows item-level control

**Simple Implementation:** Global discount/tax for entire transaction
- Add discount/tax input fields once (not per item)
- Apply same discount to all items
- Much simpler UI

**Recommended for Phase 1:** Global approach
- Simpler UI/UX
- Meets 95% of use cases (most POS apply tax at checkout level, not item level)
- Can add per-item controls in Phase 2

### Data Flow Choice

**Option A: Apply in Frontend**
```javascript
// Before checkout
cart.forEach(item => {
    item.discount_value = globalDiscount;
    item.tax_percent = globalTax;
});
// Send to backend
```

**Option B: Apply in Backend**
```javascript
// Send discount/tax separately
POST /checkout {
    items: [...],
    global_discount: 50,
    global_tax_percent: 10
}

// Backend applies to each item before saving
```

**Recommended:** Option A (Frontend)
- Simpler backend logic (no change to pos_checkout)
- User sees exact final amount before confirming
- Better UX

---

## APPROVAL CHECKLIST

Before implementation, please confirm:

- [ ] Approve global discount/tax approach (not per-item)?
- [ ] Approve frontend-side discount application?
- [ ] Update both invoice_sale.html and pos_receipt.html?
- [ ] Make business config default tax optional (Phase 2)?
- [ ] Any specific UI/styling requirements?

---

## ESTIMATED EFFORT

- **Frontend UI:** 1-2 hours
- **Invoice display:** 30-45 minutes
- **Testing:** 30-45 minutes
- **Total:** 2-3 hours

---

**Status:** AWAITING APPROVAL TO PROCEED WITH IMPLEMENTATION

---
