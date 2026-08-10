# Azure Deployment - Database Migration Fix

## Problem
Discount and Tax fields not showing in Azure Purchase Order form.

**Reason:** Migration nahi chal raha database par.

---

## Solution - Option 1: Azure Portal se Restart (BEST)

### Step 1: Azure Portal kholo
```
https://portal.azure.com
```

### Step 2: App Service kholo
```
App Services → tradeflow-fvbwbnbhe3axc7h8
```

### Step 3: Restart the app
```
Restart button click karo (top mein)
```

### Step 4: Wait 30-60 seconds
```
App restart hote hi Flask boot hoga
migrate_database() auto-chalega
Columns create ho jayenge
```

### Step 5: Test
```
Go to: https://tradeflow-fvbwbnbhe3axc7h8.azurewebsites.net/purchase_orders
Discount aur Tax fields dikhni chahaiye
```

---

## Solution - Option 2: Azure Portal se Direct SQL Execute

### Step 1: Azure Database Portal kholo
```
SQL Database → TradeFlow DB
```

### Step 2: Query Editor kholo
```
Left sidebar: Query editor
```

### Step 3: Run migration SQL
```sql
ALTER TABLE purchase_order_item ADD COLUMN discount_type VARCHAR(10) DEFAULT 'percent';
ALTER TABLE purchase_order_item ADD COLUMN discount_value NUMERIC(14,4) DEFAULT 0;
ALTER TABLE purchase_order_item ADD COLUMN discount_amount NUMERIC(14,4) DEFAULT 0;
ALTER TABLE purchase_order_item ADD COLUMN tax_percent NUMERIC(14,4) DEFAULT 0;
ALTER TABLE purchase_order_item ADD COLUMN tax_amount NUMERIC(14,4) DEFAULT 0;
```

### Step 4: Verify columns exist
```sql
SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_NAME = 'purchase_order_item';
```

---

## Solution - Option 3: GitHub Sync

Azure ke Deployment Center mein:
1. Click "Disconnect" (if connected)
2. Click "GitHub"
3. Select: sabir1080/SalPurFlask1080
4. Select branch: main
5. Click "Save"
6. Azure auto-deploy karega latest code

---

## What Changed in Code

```python
# app.py line 1616-1633

# Add discount and tax fields to PurchaseOrderItem
if "purchase_order_item" in inspector.get_table_names():
    cols = {c["name"] for c in inspector.get_columns("purchase_order_item")}
    with db.engine.begin() as conn:
        if "discount_type" not in cols:
            conn.execute(text("ALTER TABLE purchase_order_item ADD COLUMN discount_type VARCHAR(10) NOT NULL DEFAULT 'percent'"))
        if "discount_value" not in cols:
            conn.execute(text("ALTER TABLE purchase_order_item ADD COLUMN discount_value NUMERIC(14,4) NOT NULL DEFAULT 0"))
        if "discount_amount" not in cols:
            conn.execute(text("ALTER TABLE purchase_order_item ADD COLUMN discount_amount NUMERIC(14,4) NOT NULL DEFAULT 0"))
        if "tax_percent" not in cols:
            conn.execute(text("ALTER TABLE purchase_order_item ADD COLUMN tax_percent NUMERIC(14,4) NOT NULL DEFAULT 0"))
        if "tax_amount" not in cols:
            conn.execute(text("ALTER TABLE purchase_order_item ADD COLUMN tax_amount NUMERIC(14,4) NOT NULL DEFAULT 0"))
```

---

## Expected Result After Migration

**Purchase Orders page should show:**
```
New PO Line Item:
├─ Item
├─ Unit
├─ Qty
├─ Purchase Price
├─ Discount (% or Fixed)    ← NEW
├─ Tax (%)                   ← NEW
└─ Total                      ← Updated calc
```

---

## Troubleshooting

**Abhi bhi fields nahi dikh rahe?**

1. Hard refresh: `Ctrl + Shift + R`
2. Check browser console: `F12 → Console` (koi error?)
3. Check Azure app logs: App Service → Log Stream

**Database columns exist hain but fields nahi dikh rahe?**

1. Template nahi update hua
2. Flask restart nahi hua properly
3. Browser cache issue

**Solution:**
```bash
# Local mein test karo
python app.py
# http://127.0.0.1:5172/purchase_orders
# Agar local mein dikhe, to Azure ke liye restart karo
```

---

## Quick Checklist

- [ ] Code pushed to GitHub (SalPurFlask1080)
- [ ] Azure Deployment Center configured
- [ ] App restarted in Azure
- [ ] Wait 60 seconds for boot
- [ ] Check Purchase Orders page
- [ ] Discount fields visible?
- [ ] Tax fields visible?
- [ ] Create test PO with discount
- [ ] Convert to invoice
- [ ] Check financial reports

---

**Ready to deploy!** 🚀
