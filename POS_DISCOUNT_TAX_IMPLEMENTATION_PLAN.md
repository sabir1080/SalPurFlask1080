# POS DISCOUNT & TAX - IMPLEMENTATION PLAN

**Status:** READY FOR APPROVAL  
**Scope:** Frontend UI + Invoice Display (Backend is complete)  
**Complexity:** Medium  
**Risk:** Low (Backend working, UI only)

---

## SUMMARY

Backend infrastructure is complete and verified working. Implementation requires:

1. **Frontend UI:** Add discount/tax input controls to POS screen
2. **Invoice Display:** Update templates to clearly show discount/tax breakdown
3. **Checkout:** Apply discount/tax values to cart items before sending

---

## FILES TO MODIFY

### 1. templates/pos.html (MAIN WORK)
- Add discount input section
- Add tax input section
- Add totals breakdown display
- Update checkout payload
- Update hold payload

**Lines Changed:** ~80-100 (adding new sections)

### 2. templates/invoice_sale.html (MINOR)
- Add clear "Subtotal" row
- Format discount/tax rows better
- Add clear "Grand Total" row

**Lines Changed:** ~15-20 (rearranging existing code)

### 3. templates/pos_receipt.html (MINOR)
- Add discount/tax lines to thermal receipt
- Clear grand total marking

**Lines Changed:** ~10-15 (adding new rows)

### 4. salpurflask/sales/routes.py (NO CHANGE NEEDED)
- Backend already handles discount/tax
- No modifications required

---

## DETAILED CHANGES

### Change 1: POS UI - Add Discount/Tax Input Section

**Location:** templates/pos.html, after cart display (after line 69)

**Current code:**
```html
<div id="cartEmpty" class="pos-empty">Cart is empty — scan or search an item.</div>

<hr>
<div class="d-flex justify-content-between align-items-center mb-2">
  <span class="text-muted">Total</span>
  <span class="pos-total"><span id="curr">{{ currency }}</span> <span id="total">0.00</span></span>
</div>
```

**New code to ADD (before `<hr>`):**
```html
<!-- Discount/Tax Section -->
<div class="mt-3 p-2 border rounded" style="background: var(--surface);">
  <h6 class="text-muted small mb-2">Discount & Tax</h6>
  
  <!-- Discount Input -->
  <div class="row g-2 mb-2">
    <div class="col-8">
      <label class="form-label small mb-1">Discount</label>
      <div class="input-group input-group-sm">
        <input id="discountValue" type="number" step="0.01" min="0" 
               class="form-control" placeholder="0.00">
        <select id="discountType" class="form-select form-select-sm" style="flex: 0 1 auto; max-width: 80px;">
          <option value="percent">%</option>
          <option value="fixed">Rs</option>
        </select>
      </div>
    </div>
    <div class="col-4 text-end">
      <label class="form-label small mb-1">&nbsp;</label>
      <div class="text-muted small">= <span id="discountAmt">0.00</span></div>
    </div>
  </div>
  
  <!-- Tax Input -->
  <div class="row g-2">
    <div class="col-8">
      <label class="form-label small mb-1">Tax %</label>
      <input id="taxPercent" type="number" step="0.01" min="0" max="100" 
             class="form-control form-control-sm" placeholder="0.00">
    </div>
    <div class="col-4 text-end">
      <label class="form-label small mb-1">&nbsp;</label>
      <div class="text-muted small">= <span id="taxAmt">0.00</span></div>
    </div>
  </div>
</div>

<!-- Totals Breakdown -->
<div class="mt-3 p-2 border rounded" style="background: var(--surface);">
  <table class="w-100 small" style="line-height: 1.6;">
    <tr>
      <td class="text-muted">Subtotal</td>
      <td class="text-end">{{ currency }} <span id="subtotal">0.00</span></td>
    </tr>
    <tr id="discountRow" style="display: none;">
      <td class="text-muted">Discount</td>
      <td class="text-end text-danger">- <span id="discountDisplay">0.00</span></td>
    </tr>
    <tr id="taxRow" style="display: none;">
      <td class="text-muted">Tax</td>
      <td class="text-end text-info">+ <span id="taxDisplay">0.00</span></td>
    </tr>
    <tr style="border-top: 2px solid var(--border); font-weight: 700;">
      <td>Grand Total</td>
      <td class="text-end">{{ currency }} <span id="grandTotal">0.00</span></td>
    </tr>
  </table>
</div>
```

### Change 2: POS JavaScript - Add Discount/Tax Logic

**Location:** templates/pos.html, in JavaScript section after cartTotal function

**Add new function after line 195:**
```javascript
function applyDiscountTax() {
    const discountValue = parseFloat(document.getElementById('discountValue').value || 0);
    const discountType = document.getElementById('discountType').value;
    const taxPercent = parseFloat(document.getElementById('taxPercent').value || 0);
    
    // Apply to all items in cart
    cart.forEach(item => {
        item.discount_type = discountType;
        item.discount_value = discountValue;
        item.tax_percent = taxPercent;
    });
    
    updateTotalsDisplay();
    render();
}

function updateTotalsDisplay() {
    const discountValue = parseFloat(document.getElementById('discountValue').value || 0);
    const discountType = document.getElementById('discountType').value;
    const taxPercent = parseFloat(document.getElementById('taxPercent').value || 0);
    
    // Calculate totals
    let grossTotal = 0;
    cart.forEach(item => {
        grossTotal += (item.price * item.qty);
    });
    
    // Apply discount
    let discountAmt = 0;
    if (discountType === 'fixed') {
        discountAmt = Math.min(discountValue, grossTotal);
    } else {
        discountAmt = grossTotal * (discountValue / 100);
    }
    
    // Apply tax
    const taxable = grossTotal - discountAmt;
    const taxAmt = taxable * (taxPercent / 100);
    
    // Update display
    document.getElementById('subtotal').textContent = money(grossTotal);
    document.getElementById('discountDisplay').textContent = money(discountAmt);
    document.getElementById('taxDisplay').textContent = money(taxAmt);
    document.getElementById('grandTotal').textContent = money(grossTotal - discountAmt + taxAmt);
    document.getElementById('discountAmt').textContent = money(discountAmt);
    document.getElementById('taxAmt').textContent = money(taxAmt);
    
    // Show/hide rows
    document.getElementById('discountRow').style.display = discountAmt > 0 ? 'table-row' : 'none';
    document.getElementById('taxRow').style.display = taxAmt > 0 ? 'table-row' : 'none';
    
    updateChange();
}
```

### Change 3: POS JavaScript - Event Listeners

**Location:** After render() function call around line 391

**Add before `render();`:**
```javascript
// Discount/Tax input change listeners
document.getElementById('discountValue').addEventListener('input', updateTotalsDisplay);
document.getElementById('discountType').addEventListener('change', updateTotalsDisplay);
document.getElementById('taxPercent').addEventListener('input', updateTotalsDisplay);
```

### Change 4: Checkout Payload - Include Discount/Tax

**Location:** templates/pos.html, checkoutBtn click handler around line 326

**Change from:**
```javascript
items: cart.map(l => ({
  item_id: l.id,
  qty: l.qty,
  price: l.price,
  unit_id: l.unit_id || '',
  discount_type: l.discount_type || 'percent',
  discount_value: l.discount_value || 0,
  tax_percent: l.tax_percent || 0,
}))
```

**To (same - already correct, but ensure it's applied from UI inputs):**

The cart items now have discount/tax values from UI inputs, so this payload will automatically include them.

### Change 5: Hold Payload - Include Discount/Tax

**Location:** templates/pos.html, holdBtn click handler around line 356

**Same as checkout - already correct since cart items have the values:**
```javascript
items: cart.map(l => ({
  item_id: l.id,
  qty: l.qty,
  price: l.price,
  unit_id: l.unit_id || '',
  discount_type: l.discount_type || 'percent',
  discount_value: l.discount_value || 0,
  tax_percent: l.tax_percent || 0,
}))
```

---

## CHANGE 6: Invoice Display (invoice_sale.html)

**Location:** templates/invoice_sale.html, totals section around line 195-213

**Current code:**
```jinja2
{% if ns.total_disc > 0 %}
<tr class="text-danger">
  <td>Total Discount</td>
  <td class="text-end">− {{ ns.total_disc | fmt_num }}</td>
</tr>
{% endif %}
{% if ns.total_tax > 0 %}
<tr class="text-info">
  <td>Total Tax</td>
  <td class="text-end">+ {{ ns.total_tax | fmt_num }}</td>
</tr>
{% endif %}
```

**New code (REPLACE above):**
```jinja2
<!-- Gross Subtotal (before discount/tax) -->
<tr style="border-top: 2px solid #dee2e6; font-weight: 600;">
  <td>Subtotal</td>
  <td class="text-end">{{ ns.gross | fmt_num }}</td>
</tr>

<!-- Discount Row -->
{% if ns.total_disc > 0 %}
<tr class="text-danger">
  <td>Discount</td>
  <td class="text-end">− {{ ns.total_disc | fmt_num }}</td>
</tr>
{% endif %}

<!-- Tax Row -->
{% if ns.total_tax > 0 %}
<tr class="text-info">
  <td>Tax</td>
  <td class="text-end">+ {{ ns.total_tax | fmt_num }}</td>
</tr>
{% endif %}

<!-- Grand Total -->
<tr style="border-top: 2px solid #dee2e6; font-weight: 700; font-size: 1.1rem;">
  <td>GRAND TOTAL</td>
  <td class="text-end">{{ (ns.gross - ns.total_disc + ns.total_tax) | fmt_num }}</td>
</tr>
```

---

## CHANGE 7: POS Receipt (pos_receipt.html)

**Location:** templates/pos_receipt.html, totals section around line 72-83

**Current code:**
```jinja2
<div class="rule"></div>
<table>
  <tr class="tot"><td>TOTAL</td><td></td><td class="r">{{ currency }} {{ total | fmt_num }}</td></tr>
  <tr><td>Paid</td><td></td><td class="r">{{ received | fmt_num }}</td></tr>
```

**New code (REPLACE and ADD):**
```jinja2
<div class="rule"></div>
<table>
  {% set subtotal = 0 %}
  {% set total_disc = 0 %}
  {% set total_tax = 0 %}
  {% for si in sale.line_items %}
    {% set line_gross = si.quantity * si.sale_price %}
    {% set line_disc = si.discount_amount or 0 %}
    {% set line_tax = si.tax_amount or 0 %}
    {% set subtotal = subtotal + line_gross %}
    {% set total_disc = total_disc + line_disc %}
    {% set total_tax = total_tax + line_tax %}
  {% endfor %}
  
  <tr><td>Subtotal</td><td></td><td class="r">{{ currency }} {{ subtotal | fmt_num }}</td></tr>
  {% if total_disc > 0 %}
  <tr><td>Discount</td><td></td><td class="r">- {{ total_disc | fmt_num }}</td></tr>
  {% endif %}
  {% if total_tax > 0 %}
  <tr><td>Tax</td><td></td><td class="r">+ {{ total_tax | fmt_num }}</td></tr>
  {% endif %}
  <tr class="tot"><td>GRAND TOTAL</td><td></td><td class="r">{{ currency }} {{ total | fmt_num }}</td></tr>
  <tr><td>Paid</td><td></td><td class="r">{{ received | fmt_num }}</td></tr>
```

---

## TESTING PLAN

### Test 1: Add item, apply 5% discount
1. Scan item (price 1000)
2. Enter discount: 5, select "%"
3. Should show: Subtotal 1000, Discount -50, Grand Total 950
4. Checkout and verify database

### Test 2: Add item, apply 10% tax
1. Scan item (price 1000)
2. Enter tax: 10
3. Should show: Subtotal 1000, Tax +100, Grand Total 1100
4. Checkout and verify database

### Test 3: Discount + Tax combination
1. Scan item (price 1000)
2. Enter discount: 5%, tax: 10%
3. Should show: Subtotal 1000, Discount -50, Tax +95, Grand Total 1045
4. Checkout and verify database

### Test 4: Hold bill preserves discount/tax
1. Add item with discount 5%, tax 10%
2. Hold bill
3. Resume bill
4. Discount and tax should still be set
5. Checkout should work

### Test 5: Invoice displays correctly
1. Create POS sale with discount + tax
2. View invoice
3. Should show: Subtotal, Discount, Tax, Grand Total clearly

### Test 6: POS Receipt prints correctly
1. Checkout with discount + tax
2. Print receipt
3. Should show all lines on thermal receipt

---

## APPROVAL REQUIRED

Please confirm before proceeding:

- [ ] **Approach approved:** Global discount/tax for entire transaction (not per-item)?
- [ ] **UI location approved:** Discount/tax section below cart (as shown in mock)?
- [ ] **Invoice changes approved:** Both invoice_sale.html and pos_receipt.html?
- [ ] **Hold bill:** No changes needed (already working)?
- [ ] **Proceed with implementation?**

---

**Ready to implement upon approval.**

---
