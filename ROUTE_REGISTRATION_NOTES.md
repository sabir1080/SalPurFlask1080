# Route Registration Pattern Notes

## Current Route Registration Methods

### 1. Traditional @app.route Decorators (132 routes)
**Location:** app.py
**Pattern:**
```python
@app.route("/purchase", methods=["GET", "POST"])
@verified_required
def purchase():
    ...
```
**Status:** ESTABLISHED - working, will remain until routes are extracted to blueprints

---

### 2. Native Blueprint.route() Decorators (58 routes)
**Location:** salpurflask/routes/auth.py, salpurflask/routes/dashboard.py
**Pattern:**
```python
auth_bp = Blueprint('auth', __name__)

@auth_bp.route("/signin", methods=["GET", "POST"])
def signin():
    ...
```
**Status:** ESTABLISHED - proper pattern, should be replicated for other modules
**Advantage:** Endpoint names automatically prefixed (e.g., `auth.signin`)

---

### 3. app.add_url_rule() - Temporary Workaround (9 routes)
**Location:** app.py, lines 222-230
**Routes Registered:**
```python
app.add_url_rule("/item/<int:id>/ledger", "item_ledger", item_ledger)
app.add_url_rule("/api/item/<int:id>", "get_item", get_item)
app.add_url_rule("/reports/stock", "report_stock", report_stock)
app.add_url_rule("/item", "item", item, methods=["GET", "POST"])
app.add_url_rule("/item/edit/<int:id>", "edit_item", edit_item, methods=["GET", "POST"])
app.add_url_rule("/item/delete/<int:id>", "delete_item", delete_item, methods=["POST"])
app.add_url_rule("/category", "category", category, methods=["GET", "POST"])
app.add_url_rule("/category/edit/<int:id>", "edit_category", edit_category, methods=["GET", "POST"])
app.add_url_rule("/category/delete/<int:id>", "delete_category", delete_category, methods=["POST"])
```

**Why This Workaround Exists:**
1. Routes are defined in salpurflask/inventory/routes.py (separate module)
2. Need to preserve exact endpoint names (user requirement from Phase 7.3.3)
3. Blueprint.route() auto-prefixes endpoint names with blueprint name (e.g., `inventory.item_ledger`)
4. app.add_url_rule() allows explicit endpoint naming without automatic prefix

**Example of the Problem Blueprint Creates:**
```python
# With Blueprint:
inventory_bp = Blueprint('inventory', __name__)

@inventory_bp.route("/item", endpoint="item")
def item():
    ...

# Results in endpoint: "inventory.item" (not "item")
# Templates expecting url_for("item") would break
```

**Status:** TEMPORARY - needs migration to proper Blueprint pattern

---

## Why These Are Temporary

### Problem with add_url_rule()
- Not scalable: adding 9+ routes one-by-one is tedious and error-prone
- Mixes registration patterns in app.py
- Harder to trace route organization
- Violates the separation-of-concerns principle

### Solution Strategy

#### Option A: Custom Blueprint Subclass (Recommended)
```python
class NoNamePrefixBlueprint(Blueprint):
    """Blueprint that doesn't prefix endpoint names."""
    
    def make_setup_state(self, app, first_registration=False):
        """Override to prevent automatic endpoint prefixing."""
        # Implement custom logic to skip blueprint prefix
        ...
```

**Pros:**
- Still uses Blueprint pattern
- Endpoint names unchanged
- Scales to any number of routes
- Clear module organization

**Cons:**
- Requires custom Blueprint implementation
- Slightly more complex

#### Option B: Accept Endpoint Prefix
```python
# Accept that inventory routes have 'inventory.' prefix
# Update templates: url_for("item") → url_for("inventory.item")

inventory_bp = Blueprint('inventory', __name__)

@inventory_bp.route("/item", endpoint="item")
def item():
    ...

# Results in endpoint: "inventory.item"
```

**Pros:**
- Standard Flask pattern
- No custom implementation needed
- Clear module namespacing in endpoints

**Cons:**
- Requires updating all template references
- Adds migration work to future phases

---

## Migration Timeline

### Phase 7.3.5 (Exports, Imports, Stock Adjustment)
- Continue using add_url_rule() temporarily
- No changes to registration pattern
- Add new routes via add_url_rule() if necessary

### Phase 8 (Purchase, Sales, etc.)
**DECISION REQUIRED:** Choose Option A or Option B above
- Implement chosen solution
- Migrate all add_url_rule() routes to chosen pattern
- Remove temporary workarounds from app.py

---

## Related Issues Resolved (Stability Cleanup)

### ✓ Helper Function Issue - FIXED
**Problem:** line_base_qty() was used in app.py export routes but only defined in inventory/routes.py
**Solution:** Extracted to salpurflask/utils/inventory_helpers.py, exported via salpurflask/utils/__init__.py

### ✓ Helper Function Naming Collision - RESOLVED
**Problem:** purchase_item_total() and sale_item_total() had same names but different purposes:
- app.py versions: Include discount/tax calculations (for orders)
- inventory/routes.py versions: Simple unit-based calculations (for ledger display)

**Solution:** 
- Renamed inventory/routes.py versions to _purchase_line_value() and _sale_line_value() (private, ledger-only)
- Kept app.py versions unchanged (they include discount/tax logic)
- Exported only line_base_qty() from utils

### ✓ Import Consistency - VERIFIED
- All required functions now properly imported
- No NameError at runtime for export routes
- No duplicate function definitions with same behavior

---

## Checklist for Future Phase

When implementing Phase 8 Blueprint migration:

- [ ] Decide between Option A (custom Blueprint) vs Option B (accept prefix)
- [ ] If Option A: Implement NoNamePrefixBlueprint subclass
- [ ] If Option B: Update all template url_for() references
- [ ] Create purchase_bp, sales_bp, supplier_bp, customer_bp, accounting_bp, etc.
- [ ] Move routes from app.py to respective blueprints
- [ ] Remove app.add_url_rule() calls
- [ ] Update app.register_blueprint() calls
- [ ] Verify all tests pass
- [ ] Update this document with final pattern chosen

---

## Testing Notes

All 138 tests pass with current add_url_rule() pattern. No regressions introduced.

Export routes verified working correctly with line_base_qty() properly accessible.

Pattern can be safely extended to additional inventory routes without changes to mechanism.
