"""SQLAlchemy database models."""

from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import uuid
from flask_login import UserMixin
from flask import current_app, request
from sqlalchemy import text
from sqlalchemy.sql import func
from salpurflask.extensions import db

# All SQLAlchemy models - 38 models total

class User(db.Model, UserMixin):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    email               = db.Column(db.String(120), unique=True, nullable=False)
    password            = db.Column(db.String(255), nullable=False)
    verified            = db.Column(db.Boolean, default=False, nullable=False)
    role                = db.Column(db.String(20), nullable=False, default="staff")
    reset_token         = db.Column(db.String(120), nullable=True)
    reset_token_expiry  = db.Column(db.DateTime, nullable=True)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_manager(self):
        return self.role in ("admin", "manager")

    def __repr__(self):
        return f"User('{self.name}', '{self.email}')"

class Supplier(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False, index=True)
    contact             = db.Column(db.String(15), nullable=False)
    address             = db.Column(db.String(200), nullable=False)
    opening_balance     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)

class Customer(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False, index=True)
    contact             = db.Column(db.String(15), nullable=False)
    address             = db.Column(db.String(200), nullable=False)
    opening_balance     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)

class Category(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), unique=True, nullable=False)
    items               = db.relationship("Item", backref="id_category", lazy=True)

class Item(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), nullable=False)
    category_id         = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    business_category_id = db.Column(db.Integer, db.ForeignKey("business_category.id"), nullable=True)
    unit                = db.Column(db.String(20), nullable=False, default="Pcs")
    # Item type: STOCK (normal inventory item) or SERVICE (no inventory tracking)
    item_type           = db.Column(db.String(20), nullable=False, default="STOCK")
    # A barcode or QR value, whichever the item is labelled with. One field serves both:
    # a scanner types the code and presses Enter, and the POS looks the item up by it —
    # it does not care whether the label was a barcode or a QR. Nullable, because an item
    # can always be found by name; a code just makes it instant at the counter.
    barcode             = db.Column(db.String(64), nullable=True, index=True)
    opening_stock       = db.Column(db.Integer, nullable=False, default=0)
    stock               = db.Column(db.Integer, nullable=False, default=0)
    reorder_level       = db.Column(db.Integer, nullable=False, default=50)
    # The price the item is *currently bought at* — a catalogue figure, used to
    # prefill forms. It is NOT what the stock on hand cost; see inventory_value.
    purchase_price      = db.Column(db.Numeric(14, 4), nullable=True)
    sale_price          = db.Column(db.Numeric(14, 4), nullable=True)
    # What the stock on hand actually cost, under weighted-average costing. Every
    # inbound movement adds its cost here, every outbound removes qty × avg_cost.
    # Holding the value (not the average) is what makes a reversal exact: undoing
    # a document subtracts precisely the amount it added. It also keeps this in
    # step with the Inventory control account, which is posted the same amounts.
    inventory_value     = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    # Default tax % applied to this item when sold — auto-populated in POS/forms, can be overridden.
    default_tax_percent = db.Column(db.Numeric(5, 2), nullable=False, default=0.0)
    is_taxable          = db.Column(db.Boolean, nullable=False, default=True)
    purchases           = db.relationship("Purchase", backref="id_item", lazy=True)
    sales               = db.relationship("Sale", backref="id_item", lazy=True)
    business_category   = db.relationship("BusinessCategory", backref="items", lazy=True, foreign_keys=[business_category_id])

    @property
    def avg_cost(self):
        """Weighted-average unit cost. Falls back to the catalogue price when there
        is no stock, so the first purchase of an item still values correctly."""
        if self.stock and self.inventory_value:
            return (Decimal(str(self.inventory_value)) / Decimal(str(self.stock))).quantize(MONEY)
        return Decimal(str(self.purchase_price or 0)).quantize(MONEY)

class ItemUnit(db.Model):
    """An alternate unit an item can be bought or sold in, besides its base unit
    (Item.unit, where Item.stock is always tracked — a Box of something is not a
    different item, it is the same stock counted differently at the counter).

    Example: a medicine's base unit is Tablet; it might also be bought in Box
    (factor 100) and sold in Strip (factor 10). `factor` is how many base units
    make one of this unit. Whole numbers only — Item.stock is an integer count,
    so a fractional factor would leave fractional pieces on the shelf.

    Purchase/sale/quotation/PO lines snapshot the name and factor they used
    (see PurchaseItem.unit_name/unit_factor) rather than pointing at this row,
    so renaming or deleting a unit here never rewrites a past invoice."""
    __tablename__ = "item_unit"
    id              = db.Column(db.Integer, primary_key=True)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    name            = db.Column(db.String(20), nullable=False)
    factor          = db.Column(db.Integer, nullable=False)   # base units per one of this unit
    purchase_price  = db.Column(db.Numeric(14, 4), nullable=True)
    sale_price      = db.Column(db.Numeric(14, 4), nullable=True)
    item            = db.relationship("Item", backref=db.backref(
        "alt_units", cascade="all,delete-orphan", order_by="ItemUnit.id"))

def item_unit_choices(item):
    """Every unit this item can be transacted in: the base unit first (key ""),
    then each alternate unit (key = its ItemUnit id, as a string)."""
    choices = [{"key": "", "name": item.unit or "Pcs", "factor": 1,
                "purchase_price": item.purchase_price, "sale_price": item.sale_price}]
    for u in item.alt_units:
        choices.append({"key": str(u.id), "name": u.name, "factor": u.factor,
                         "purchase_price": u.purchase_price, "sale_price": u.sale_price})
    return choices

def item_units_for_js(items):
    """{item_id: [{key, name, factor, purchase_price, sale_price}, ...]} with plain
    floats, ready to embed in a template via |tojson — Decimal renders as a JSON
    string, not a number, which is worth avoiding in hand-written cart/form JS."""
    return {
        it.id: [{"key": c["key"], "name": c["name"], "factor": c["factor"],
                 "purchase_price": float(c["purchase_price"]) if c["purchase_price"] is not None else None,
                 "sale_price": float(c["sale_price"]) if c["sale_price"] is not None else None}
                for c in item_unit_choices(it)]
        for it in items
    }

def purchase_item_options_for_js(items):
    """Item-picker rows for purchase forms' dynamically-added lines, as plain JSON.

    The item name must never be interpolated straight into a JS template literal
    (see purchase.html/edit_purchase.html) — a backtick or ${...} in the name would
    break out of the string and run as script. Feeding it through |tojson and letting
    JS set it via textContent, like ITEM_UNITS already does, closes that off."""
    return [
        {"id": it.id,
         "label": it.name + (f" ({it.id_category.name})" if it.id_category else ""),
         "price": float(it.purchase_price) if it.purchase_price is not None else None}
        for it in items
    ]

def sale_item_options_for_js(items):
    """Item-picker rows for sale forms' dynamically-added lines. See purchase_item_options_for_js."""
    return [
        {"id": it.id,
         "label": it.name + (f" ({it.id_category.name})" if it.id_category else ""),
         "price": float(it.sale_price) if it.sale_price is not None else None,
         "stock": it.stock,
         "unit": it.unit or "Pcs"}
        for it in items
    ]

def purchase_return_options_for_js(rows):
    """Line-picker rows for the purchase return form. See purchase_item_options_for_js."""
    return [
        {"id": row["pi"].id,
         "label": f"#PUR-{row['pi'].purchase_header.id} — {row['pi'].purchase_header.supplier.name} — "
                  f"{row['pi'].item.name} (Rem: {row['remaining']} {row['pi'].display_unit})",
         "price": float(row["pi"].purchase_price),
         "remaining": row["remaining"]}
        for row in rows
    ]

def sale_return_options_for_js(rows):
    """Line-picker rows for the sale return form. See purchase_item_options_for_js."""
    return [
        {"id": row["si"].id,
         "label": f"#SAL-{row['si'].sale_header.id} — {row['si'].sale_header.customer.name} — "
                  f"{row['si'].item.name} (Rem: {row['remaining']} {row['si'].display_unit})",
         "price": float(row["si"].sale_price),
         "remaining": row["remaining"]}
        for row in rows
    ]

def resolve_item_unit(item, unit_key):
    """Resolve a submitted unit key to (unit_name, factor).

    unit_name is None for the base unit — deliberately not item.unit's current
    value, so that if the item's base unit is ever renamed, old lines still read
    back as 'whatever the base unit was called then' via the fallback in display
    code, not silently relabelled. factor is always a positive int."""
    unit_key = (unit_key or "").strip()
    if not unit_key:
        return None, 1
    u = ItemUnit.query.filter_by(id=int(unit_key), item_id=item.id).first() if unit_key.isdigit() else None
    if not u:
        return None, 1
    return u.name, u.factor

def line_base_qty(line_item):
    """A transaction line's quantity converted to the item's base unit — the only
    unit Item.stock is ever tracked in. unit_factor is 1 for base-unit lines and
    for every line written before multi-unit existed."""
    return int(line_item.quantity) * int(line_item.unit_factor or 1)

def save_item_units(item):
    """Replace an item's alternate units from the submitted alt_unit_* form arrays.
    Deletes and recreates rather than diffing — safe because purchase/sale/etc. lines
    snapshot the unit name and factor they used, not a reference to this row, so
    renaming or removing a unit here never touches past transactions.

    Returns an error string for the first invalid row, or None."""
    names   = request.form.getlist("alt_unit_name[]")
    factors = request.form.getlist("alt_unit_factor[]")
    pprices = request.form.getlist("alt_unit_purchase_price[]")
    sprices = request.form.getlist("alt_unit_sale_price[]")
    rows = []
    for i, name in enumerate(names):
        name = name.strip()
        factor_s = factors[i].strip() if i < len(factors) else ""
        if not name and not factor_s:
            continue
        if not name or not factor_s:
            return "Each alternate unit needs both a name and a factor."
        if not factor_s.isdigit() or int(factor_s) < 1:
            return f"'{name}': the factor must be a whole number of at least 1."
        pp = pprices[i].strip() if i < len(pprices) else ""
        sp = sprices[i].strip() if i < len(sprices) else ""
        rows.append((name, int(factor_s),
                     float(pp) if pp else None, float(sp) if sp else None))
    ItemUnit.query.filter_by(item_id=item.id).delete()
    for name, factor, pp, sp in rows:
        db.session.add(ItemUnit(item_id=item.id, name=name, factor=factor,
                                purchase_price=pp, sale_price=sp))
    return None

class Purchase(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_id         = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    item_id             = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity            = db.Column(db.Integer, nullable=False)
    purchase_price      = db.Column(db.Numeric(14, 4), nullable=False)
    discount_type       = db.Column(db.String(10), nullable=False, default="percent")
    discount_value      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent         = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_amount     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_amount          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    date                = db.Column(db.DateTime, default=lambda: now_local(), nullable=False, index=True)
    notes               = db.Column(db.String(300), nullable=True)
    supplier            = db.relationship("Supplier", uselist=False)
    line_items          = db.relationship("PurchaseItem", backref="purchase_header", lazy=True, cascade="all,delete-orphan")
    is_reversed         = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at         = db.Column(db.DateTime, nullable=True)
    # Gapless, issued once and never reused. See allocate_document_number().
    invoice_no          = db.Column(db.String(30), nullable=True, unique=True, index=True)

class Sale(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    item_id             = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=True)
    quantity            = db.Column(db.Integer, nullable=True)
    sale_price          = db.Column(db.Numeric(14, 4), nullable=True)
    cost_price          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_type       = db.Column(db.String(10), nullable=False, default="percent")
    discount_value      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent         = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_amount     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_amount          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    date                = db.Column(db.DateTime, default=lambda: now_local(), nullable=False, index=True)
    notes               = db.Column(db.String(300), nullable=True)
    customer            = db.relationship("Customer", uselist=False)
    line_items          = db.relationship("SaleItem", backref="sale_header", lazy=True, cascade="all,delete-orphan")
    is_reversed         = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at         = db.Column(db.DateTime, nullable=True)
    # Gapless, issued once and never reused. See allocate_document_number().
    invoice_no          = db.Column(db.String(30), nullable=True, unique=True, index=True)

class PurchaseItem(db.Model):
    __tablename__   = "purchase_item"
    id              = db.Column(db.Integer, primary_key=True)
    purchase_id     = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=False)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    purchase_price  = db.Column(db.Numeric(14, 4), nullable=False)
    discount_type   = db.Column(db.String(10), nullable=False, default="percent")
    discount_value  = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_amount = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_amount      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    amount          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    # The unit this line was bought in (e.g. "Box"), and how many of the item's
    # base unit that equals. NULL/1 means the base unit itself — every line
    # written before multi-unit existed reads back that way automatically.
    unit_name       = db.Column(db.String(20), nullable=True)
    unit_factor     = db.Column(db.Integer, nullable=False, default=1)
    item            = db.relationship("Item", foreign_keys=[item_id])

    @property
    def display_unit(self):
        return self.unit_name or (self.item.unit if self.item else "Pcs")

    @property
    def base_quantity(self):
        """This line's quantity converted to the item's base unit — the only
        quantity that is ever safe to sum across lines transacted in different
        units. See line_base_qty()."""
        return self.quantity * (self.unit_factor or 1)

class SaleItem(db.Model):
    __tablename__   = "sale_item"
    id              = db.Column(db.Integer, primary_key=True)
    sale_id         = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    sale_price      = db.Column(db.Numeric(14, 4), nullable=False)
    cost_price      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_type   = db.Column(db.String(10), nullable=False, default="percent")
    discount_value  = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_amount = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_amount      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    amount          = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    # See PurchaseItem.unit_name/unit_factor.
    unit_name       = db.Column(db.String(20), nullable=True)
    unit_factor     = db.Column(db.Integer, nullable=False, default=1)
    item            = db.relationship("Item", foreign_keys=[item_id])

    @property
    def display_unit(self):
        return self.unit_name or (self.item.unit if self.item else "Pcs")

    @property
    def base_quantity(self):
        """See PurchaseItem.base_quantity."""
        return self.quantity * (self.unit_factor or 1)

class PosHold(db.Model):
    __tablename__ = "pos_hold"
    id                  = db.Column(db.Integer, primary_key=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    user_id             = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    cart_data           = db.Column(db.Text, nullable=False)
    hold_time           = db.Column(db.DateTime, default=lambda: now_local(), nullable=False, index=True)
    last_modified       = db.Column(db.DateTime, default=lambda: now_local(), onupdate=lambda: now_local(), nullable=False)
    version             = db.Column(db.Integer, default=1, nullable=False)  # Optimistic locking
    notes               = db.Column(db.String(300), nullable=True)
    account_id          = db.Column(db.Integer, db.ForeignKey("financial_account.id"), nullable=True)
    amount_paid_memo    = db.Column(db.Numeric(14, 4), nullable=True)
    status              = db.Column(db.String(20), default="held", nullable=False)
    customer            = db.relationship("Customer", foreign_keys=[customer_id])
    user                = db.relationship("User", foreign_keys=[user_id])
    account             = db.relationship("FinancialAccount", foreign_keys=[account_id])

    @property
    def total(self):
        import json
        try:
            cart = json.loads(self.cart_data)
            return sum(line.get('price', 0) * line.get('qty', 0) for line in cart)
        except Exception:
            return 0

    @property
    def item_count(self):
        import json
        try:
            return len(json.loads(self.cart_data))
        except Exception:
            return 0

PAYMENT_METHODS = ("Cash", "Bank", "Cheque", "Online")
ITEM_UNITS = ("Pcs", "Dozen", "Meter", "Kg", "Gram", "Liter", "Box", "Carton", "Bag", "Yard", "Foot", "Set", "Pair", "Roll", "Sheet", "Pack")

class SupplierPayment(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_id         = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    purchase_id         = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=True)
    amount              = db.Column(db.Numeric(14, 4), nullable=False)
    payment_date        = db.Column(db.DateTime, default=lambda: now_local(), nullable=False)
    payment_method      = db.Column(db.String(20), nullable=False, default="Cash")
    account_id          = db.Column(db.Integer, db.ForeignKey("financial_account.id"), nullable=True)
    reference_no        = db.Column(db.String(100), nullable=True)
    notes               = db.Column(db.String(300), nullable=True)
    supplier            = db.relationship("Supplier", backref="payments", lazy=True)
    is_reversed         = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at         = db.Column(db.DateTime, nullable=True)
    purchase            = db.relationship("Purchase", backref="supplier_payments", lazy=True)
    account             = db.relationship("FinancialAccount", lazy=True)

class CustomerPayment(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    sale_id             = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=True)
    amount              = db.Column(db.Numeric(14, 4), nullable=False)
    payment_date        = db.Column(db.DateTime, default=lambda: now_local(), nullable=False)
    payment_method      = db.Column(db.String(20), nullable=False, default="Cash")
    account_id          = db.Column(db.Integer, db.ForeignKey("financial_account.id"), nullable=True)
    reference_no        = db.Column(db.String(100), nullable=True)
    notes               = db.Column(db.String(300), nullable=True)
    customer            = db.relationship("Customer", backref="receipts", lazy=True)
    is_reversed         = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at         = db.Column(db.DateTime, nullable=True)
    sale                = db.relationship("Sale", backref="customer_payments", lazy=True)
    account             = db.relationship("FinancialAccount", lazy=True)

class PurchaseReturn(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    purchase_id  = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=False)
    supplier_id  = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    item_id      = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity     = db.Column(db.Integer, nullable=False)
    return_price = db.Column(db.Numeric(14, 4), nullable=False)
    date         = db.Column(db.DateTime, default=lambda: now_local(), nullable=False)
    reason       = db.Column(db.String(300), nullable=True)
    purchase     = db.relationship("Purchase", backref="returns", lazy=True)
    # What the returned goods actually cost us (weighted average at return time).
    # Stored so the journal entry and its later reversal use the same figure.
    cost_removed = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    is_reversed  = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at  = db.Column(db.DateTime, nullable=True)
    # Copied from the PurchaseItem line being returned against, at return time —
    # a return is always in that line's own unit, never chosen separately. See
    # PurchaseItem.unit_name/unit_factor.
    unit_name    = db.Column(db.String(20), nullable=True)
    unit_factor  = db.Column(db.Integer, nullable=False, default=1)
    # Which specific PurchaseItem line this return was made against. Needed because
    # the same item can appear on a purchase more than once in different units (e.g.
    # 5 loose Pcs and 3 Box) — without this, "how much of THIS line is still
    # returnable" could only be tracked per (purchase, item), which mixes unrelated
    # units together. Nullable for rows written before this column existed; those
    # fall back to the old, coarser (purchase_id, item_id) grouping.
    purchase_item_id = db.Column(db.Integer, db.ForeignKey("purchase_item.id"), nullable=True)
    supplier     = db.relationship("Supplier", backref="purchase_returns", lazy=True)
    item         = db.relationship("Item", backref="purchase_returns", lazy=True)

class SaleReturn(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    sale_id      = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    customer_id  = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    item_id      = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity     = db.Column(db.Integer, nullable=False)
    return_price = db.Column(db.Numeric(14, 4), nullable=False)
    date         = db.Column(db.DateTime, default=lambda: now_local(), nullable=False)
    reason       = db.Column(db.String(300), nullable=True)
    sale         = db.relationship("Sale", backref="returns", lazy=True)
    # What the goods cost when they were sold — the cost they come back in at.
    cost_restored = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    is_reversed  = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at  = db.Column(db.DateTime, nullable=True)
    # Copied from the SaleItem line being returned against. See PurchaseReturn.unit_name.
    unit_name    = db.Column(db.String(20), nullable=True)
    unit_factor  = db.Column(db.Integer, nullable=False, default=1)
    # See PurchaseReturn.purchase_item_id.
    sale_item_id = db.Column(db.Integer, db.ForeignKey("sale_item.id"), nullable=True)
    customer     = db.relationship("Customer", backref="sale_returns", lazy=True)
    item         = db.relationship("Item", backref="sale_returns", lazy=True)

# ── Stock Adjustment ──────────────────────────────────────────────────────────
# Each adjustment type carries its own direction. It used to be worked out by matching
# the label against a list of the outbound ones, so anything not on that list — a typo,
# a type added here and forgotten there — silently became an *inbound* adjustment and
# quietly increased stock. And "Count Correction" was inbound-only, so a stocktake that
# found goods missing could not be entered honestly at all: the only way to reduce stock
# was to call it damage, which it was not.
ADJUSTMENT_DIRECTIONS = {
    "Stock In":                    "in",
    "Count Correction (Increase)": "in",
    "Stock Out":                   "out",
    "Damage Write-off":            "out",
    "Sample / Free Issue":         "out",
    "Count Correction (Decrease)": "out",
    # Written by an older version, which only ever meant "in". Kept so its rows still
    # read back; not offered on the form any more.
    "Count Correction":            "in",
}
ADJUSTMENT_TYPES = tuple(t for t in ADJUSTMENT_DIRECTIONS if t != "Count Correction")

class StockAdjustment(db.Model):
    __tablename__ = "stock_adjustment"
    id              = db.Column(db.Integer, primary_key=True)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    adj_type        = db.Column(db.String(30), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    direction       = db.Column(db.String(4), nullable=False, default="in")   # "in" or "out"
    date            = db.Column(db.DateTime, default=lambda: now_local(), nullable=False)
    reason          = db.Column(db.String(300), nullable=True)
    # The value moved in or out, costed at the average when the adjustment was made.
    cost_value      = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    is_reversed     = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at     = db.Column(db.DateTime, nullable=True)
    item            = db.relationship("Item", backref="adjustments", lazy=True)

# ── Expense Tracking ──────────────────────────────────────────────────────────
class ExpenseCategory(db.Model):
    __tablename__ = "expense_category"
    id      = db.Column(db.Integer, primary_key=True)
    name    = db.Column(db.String(100), unique=True, nullable=False)
    # Which GL expense account this category debits. Unset falls back to 6090.
    gl_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True)
    gl_account    = db.relationship("Account", lazy=True)
    expenses = db.relationship("Expense", backref="category", lazy=True)

class Expense(db.Model):
    __tablename__ = "expense"
    id              = db.Column(db.Integer, primary_key=True)
    category_id     = db.Column(db.Integer, db.ForeignKey("expense_category.id"), nullable=True)
    description     = db.Column(db.String(300), nullable=False)
    amount          = db.Column(db.Numeric(14, 4), nullable=False)
    date            = db.Column(db.DateTime, nullable=False,
                                default=lambda: now_local())
    payment_method  = db.Column(db.String(20), nullable=False, default="Cash")
    account_id      = db.Column(db.Integer, db.ForeignKey("financial_account.id"), nullable=True)
    reference_no    = db.Column(db.String(100), nullable=True)
    is_reversed     = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at     = db.Column(db.DateTime, nullable=True)
    notes           = db.Column(db.String(300), nullable=True)
    account         = db.relationship("FinancialAccount", lazy=True)

class FinancialAccount(db.Model):
    """A cash or bank account. Balance = opening_balance + receipts in − (payments
    + expenses) out.

    A movement belongs to an account in one of two ways:

      1. Explicitly, via its `account_id` FK. This is how every new movement is
         tagged, and it is what makes more than one bank account possible.
      2. Implicitly (legacy), when `account_id` is NULL: the movement is matched
         by `payment_method == account.method`. This is how records created before
         `account_id` existed keep counting, with no backfill needed.

    Only the four seeded accounts carry a real `method` (one of PAYMENT_METHODS),
    so only they absorb untagged legacy rows. Accounts created afterwards get an
    unused synthetic token instead — `method` is NOT NULL + UNIQUE in the existing
    schema, and relaxing that would mean a table rebuild on SQLite and a locking
    DDL on Postgres during deploy. The token satisfies the constraint and matches
    no payment_method, so a new account sees only its explicitly tagged rows."""
    __tablename__ = "financial_account"
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(80), nullable=False)          # display name
    method          = db.Column(db.String(20), nullable=False, unique=True)  # legacy payment_method, or synthetic token
    account_type    = db.Column(db.String(10), nullable=False, default="Cash")  # Cash / Bank
    opening_balance = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    is_active       = db.Column(db.Boolean, nullable=False, default=True)
    is_control      = db.Column(db.Boolean, nullable=False, default=False)  # True = control account (header), False = selectable
    parent_id       = db.Column(db.Integer, db.ForeignKey("financial_account.id"), nullable=True)  # parent control account
    # Every cash/bank account is a GL account too — that is what a payment credits.
    gl_account_id   = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True)
    gl_account      = db.relationship("Account", lazy="joined")
    parent          = db.relationship("FinancialAccount", remote_side=[id], backref="children", lazy=True)  # hierarchical relationship

def new_account_method_token():
    """A `method` value for a user-created account: unique, ≤20 chars, and
    guaranteed never to equal a payment_method (so it absorbs no legacy rows)."""
    return f"acct-{uuid.uuid4().hex[:10]}"

# ═══ General Ledger foundation ═══════════════════════════════════════════════
# The GL is the single source of truth: every report sums journal lines. Nothing
# is derived from Purchase/Sale/payment tables any more.

ACCOUNT_TYPES = ("Asset", "Liability", "Equity", "Income", "Expense")

# Which side increases an account of this type. Debit-natured accounts (assets,
# expenses) grow with debits; the rest grow with credits. Used everywhere a
# balance is turned into a report figure.
DEBIT_NATURED = ("Asset", "Expense")

class Account(db.Model):
    """A chart-of-accounts node. Groups (`is_group`) are headers only and can
    never be posted to; leaves carry the balance.

    `is_control` marks an account whose balance is owned by a subledger —
    Accounts Receivable by customer ledgers, Accounts Payable by supplier
    ledgers, Inventory by stock movements. Manual journal entries are refused
    against control accounts, otherwise the GL and its subledger silently drift
    apart and no report can be trusted.

    `code` is what accountants navigate by, so it is the natural sort key."""
    __tablename__ = "account"
    id          = db.Column(db.Integer, primary_key=True)
    code        = db.Column(db.String(20), nullable=False, unique=True)
    name        = db.Column(db.String(100), nullable=False)
    type        = db.Column(db.String(20), nullable=False)   # one of ACCOUNT_TYPES
    parent_id   = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True)
    is_group    = db.Column(db.Boolean, nullable=False, default=False)
    is_control  = db.Column(db.Boolean, nullable=False, default=False)
    is_active   = db.Column(db.Boolean, nullable=False, default=True)
    # Which cash-flow-statement section this account's movements belong to. NULL
    # means "use the default for this type" (see account_cf_section) — so the
    # seeded chart needs no backfill, and a Fixed Assets or Loan account added
    # later can be tagged Investing / Financing explicitly.
    cash_flow_section = db.Column(db.String(12), nullable=True)
    # What the posting layer uses this account FOR ("depreciation", "accum_dep", …).
    # Accounts added to an existing chart cannot claim a fixed code — the user may
    # already have used it — so they are seeded on whatever code is free and found
    # again by role. See account_for_role(). NULL for ordinary accounts.
    role        = db.Column(db.String(30), nullable=True, index=True)
    children    = db.relationship("Account", backref=db.backref("parent", remote_side=[id]), lazy=True)

    @property
    def is_debit_natured(self):
        return self.type in DEBIT_NATURED

    def __repr__(self):
        return f"<Account {self.code} {self.name}>"

class TaxCode(db.Model):
    """A named tax treatment picked on a document line. Its components produce
    the actual GL postings, which is what makes one model serve every country:

      Pakistan  "Standard 17%"   → 1 component  @17%
      UK        "VAT 20%"        → 1 component  @20%
      India     "GST 18% intra"  → 2 components @9% (CGST) + @9% (SGST)
      India     "IGST 18%"       → 1 component  @18%
      anywhere  "Zero-rated"     → 1 component  @0%

    Rates live on the component, never on the code, so a split tax is not a
    special case in any calling code."""
    __tablename__ = "tax_code"
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(60), nullable=False, unique=True)
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    components = db.relationship("TaxComponent", backref="tax_code", lazy=True,
                                 cascade="all,delete-orphan")

    @property
    def total_rate(self):
        return sum(float(c.rate) for c in self.components)

class TaxComponent(db.Model):
    """One leg of a tax code. Input tax is recoverable (an asset); output tax is
    owed to the authority (a liability) — hence two different accounts."""
    __tablename__ = "tax_component"
    id                = db.Column(db.Integer, primary_key=True)
    tax_code_id       = db.Column(db.Integer, db.ForeignKey("tax_code.id"), nullable=False)
    name              = db.Column(db.String(40), nullable=False)          # "CGST", "VAT", "Sales Tax"
    rate              = db.Column(db.Numeric(7, 4), nullable=False, default=0)   # percent
    input_account_id  = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    output_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    input_account     = db.relationship("Account", foreign_keys=[input_account_id], lazy=True)
    output_account    = db.relationship("Account", foreign_keys=[output_account_id], lazy=True)

class FiscalYear(db.Model):
    """Closing a year moves income and expense balances into Retained Earnings.
    Nothing may be posted into a closed year."""
    __tablename__ = "fiscal_year"
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(40), nullable=False, unique=True)     # "2026" or "FY 2025-26"
    start_date = db.Column(db.Date, nullable=False)
    end_date   = db.Column(db.Date, nullable=False)
    is_closed  = db.Column(db.Boolean, nullable=False, default=False)
    closed_at  = db.Column(db.DateTime, nullable=True)
    periods    = db.relationship("AccountingPeriod", backref="fiscal_year", lazy=True,
                                 cascade="all,delete-orphan")

class AccountingPeriod(db.Model):
    """Usually a month. Closing a period stops backdated postings into it, which
    is what stops last month's already-filed numbers from moving."""
    __tablename__ = "accounting_period"
    id             = db.Column(db.Integer, primary_key=True)
    fiscal_year_id = db.Column(db.Integer, db.ForeignKey("fiscal_year.id"), nullable=False)
    name           = db.Column(db.String(40), nullable=False)              # "Jan 2026"
    start_date     = db.Column(db.Date, nullable=False)
    end_date       = db.Column(db.Date, nullable=False)
    is_closed      = db.Column(db.Boolean, nullable=False, default=False)

# ── Gapless document numbering ────────────────────────────────────────────────
DOCUMENT_PREFIXES = {"purchase": "PUR", "sale": "INV"}

class DocumentSequence(db.Model):
    """The next invoice number for a document type in a fiscal year.

    This is a table row on purpose, not a database SEQUENCE. A row update is part
    of the transaction, so if the document is never committed the number goes back
    and the next one reuses it. A database sequence does not roll back, and every
    abandoned save would leave a hole in the numbering — which is exactly what a
    gapless requirement forbids."""
    __tablename__ = "document_sequence"
    __table_args__ = (db.UniqueConstraint("doc_type", "year", name="uq_docseq_type_year"),)
    id          = db.Column(db.Integer, primary_key=True)
    doc_type    = db.Column(db.String(20), nullable=False)     # purchase / sale
    year        = db.Column(db.String(40), nullable=False)     # fiscal year name, e.g. "2026"
    prefix      = db.Column(db.String(10), nullable=False)     # PUR / INV
    next_number = db.Column(db.Integer, nullable=False, default=1)

# ── Chart of accounts ─────────────────────────────────────────────────────────
# The posting layer looks accounts up by code, never by name — names are the
# user's to rename, codes are the contract. Keep these constants in step with
# the seed below.
ACC_CASH_IN_HAND   = "1010"
ACC_AR             = "1100"
ACC_INVENTORY      = "1200"
ACC_TAX_INPUT      = "1300"
ACC_AP             = "2100"
ACC_TAX_OUTPUT     = "2300"
ACC_CAPITAL        = "3100"
ACC_DRAWINGS       = "3200"
ACC_OPENING_EQUITY = "3300"
ACC_RETAINED       = "3900"
ACC_SALES          = "4000"
ACC_SALES_RETURNS  = "4100"
ACC_COGS           = "5000"
ACC_STOCK_ADJ      = "5100"
ACC_EXPENSES       = "6000"

# (code, name, type, parent_code, is_group, is_control)
CHART_OF_ACCOUNTS = [
    ("1000", "Current Assets",            "Asset",     None,   True,  False),
    (ACC_CASH_IN_HAND, "Cash in Hand",    "Asset",     "1000", False, False),
    ("1020", "Bank Accounts",             "Asset",     "1000", True,  False),
    (ACC_AR, "Accounts Receivable",       "Asset",     "1000", False, True),
    (ACC_INVENTORY, "Inventory",          "Asset",     "1000", False, True),
    (ACC_TAX_INPUT, "Tax Input (Recoverable)", "Asset", "1000", False, False),

    ("2000", "Current Liabilities",       "Liability", None,   True,  False),
    (ACC_AP, "Accounts Payable",          "Liability", "2000", False, True),
    (ACC_TAX_OUTPUT, "Tax Output (Payable)", "Liability", "2000", False, False),

    ("3000", "Equity",                    "Equity",    None,   True,  False),
    (ACC_CAPITAL, "Owner's Capital",      "Equity",    "3000", False, False),
    (ACC_DRAWINGS, "Owner's Drawings",    "Equity",    "3000", False, False),
    # The other side of every opening balance: what the business already owned and
    # owed on the day it started using the system.
    (ACC_OPENING_EQUITY, "Opening Balance Equity", "Equity", "3000", False, False),
    (ACC_RETAINED, "Retained Earnings",   "Equity",    "3000", False, False),

    (ACC_SALES, "Sales Revenue",          "Income",    None,   False, False),
    (ACC_SALES_RETURNS, "Sales Returns",  "Income",    None,   False, False),

    (ACC_COGS, "Cost of Goods Sold",      "Expense",   None,   False, False),
    (ACC_STOCK_ADJ, "Inventory Adjustment", "Expense",  None,   False, False),
    (ACC_EXPENSES, "Operating Expenses",  "Expense",   None,   True,  False),
    ("6010", "Rent",                      "Expense",   ACC_EXPENSES, False, False),
    ("6020", "Salaries & Wages",          "Expense",   ACC_EXPENSES, False, False),
    ("6030", "Utilities",                 "Expense",   ACC_EXPENSES, False, False),
    ("6040", "Freight & Carriage",        "Expense",   ACC_EXPENSES, False, False),
    ("6090", "Other Expenses",            "Expense",   ACC_EXPENSES, False, False),
]

# ── Accounts the fixed-asset module needs ─────────────────────────────────────
# These are added to a chart that is already in use, so they cannot claim a fixed
# code: the user may already have created an account on it (a real "6050 Internet
# & Broadband" is what caught this). Each is seeded on its preferred code when that
# is free and on the next free one under its parent otherwise, then found again by
# `role` — never by code.
#   role, preferred code, name, type, parent code, cash-flow section
FIXED_ASSET_ACCOUNTS = [
    ("fixed_group",   "1500", "Fixed Assets",                     "Asset",   None,          None),
    ("fixed_cost",    "1510", "Fixed Assets at Cost",             "Asset",   "fixed_group", "Investing"),
    # Contra-asset: a credit balance that nets against cost to give net book value.
    ("accum_dep",     "1590", "Accumulated Depreciation",         "Asset",   "fixed_group", "Investing"),
    ("disposal_gain", "4200", "Gain on Disposal of Fixed Assets", "Income",  None,          "Investing"),
    ("disposal_loss", "5200", "Loss on Disposal of Fixed Assets", "Expense", None,          "Investing"),
    # Depreciation never touches cash, so it can never reach the cash flow statement
    # — its section is left to the default.
    ("depreciation",  "6050", "Depreciation",                     "Expense", ACC_EXPENSES,  None),
]
FIXED_ASSET_ROLES = tuple(r for r, *_ in FIXED_ASSET_ACCOUNTS)

# Codes the posting layer looks up by name. Renaming one is fine; changing its
# code or deleting it would break a posting path, so the UI refuses both.
# (Role-bearing accounts are protected the same way — see account_is_system.)
SYSTEM_ACCOUNT_CODES = frozenset({
    ACC_CASH_IN_HAND, ACC_AR, ACC_INVENTORY, ACC_TAX_INPUT, ACC_AP, ACC_TAX_OUTPUT,
    ACC_CAPITAL, ACC_DRAWINGS, ACC_OPENING_EQUITY, ACC_RETAINED,
    ACC_SALES, ACC_SALES_RETURNS, ACC_COGS, ACC_STOCK_ADJ, ACC_EXPENSES,
    "1000", "1020", "2000", "3000", "6090",
})

def get_account(code):
    """Posting-layer lookup. Raises rather than returning None: a missing account
    is a seeding bug, and a silently skipped journal line is far worse than a
    loud failure."""
    acct = Account.query.filter_by(code=code).first()
    if acct is None:
        raise LookupError(f"Account {code} is missing from the chart of accounts")
    return acct

def account_has_activity(acct):
    return db.session.query(
        JournalLine.query.filter_by(account_id=acct.id).exists()).scalar()

def expense_gl_accounts():
    """Postable expense accounts an expense category may be pointed at.

    Cost of Goods Sold and Inventory Adjustment are excluded: the posting layer
    owns them, and an expense landing in COGS would silently distort gross
    profit."""
    return (Account.query
            .filter(Account.type == "Expense", Account.is_group.is_(False),
                    Account.is_control.is_(False), Account.is_active.is_(True),
                    Account.code.notin_((ACC_COGS, ACC_STOCK_ADJ)))
            .order_by(Account.code).all())

def parse_expense_gl_account(raw):
    """Form value → (account_id or None, error or None). Blank means 'use 6090'."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if not raw.isdigit():
        return None, "Invalid account."
    acct = db.session.get(Account, int(raw))
    if acct is None or acct.type != "Expense" or acct.is_group or not acct.is_active:
        return None, "Pick an active expense account that is not a heading."
    if acct.code in (ACC_COGS, ACC_STOCK_ADJ):
        return None, f"{acct.code} {acct.name} is maintained by the system and cannot take expenses."
    return acct.id, None

def seed_chart_of_accounts():
    """Idempotent: inserts only accounts whose code is absent, so it is safe to
    re-run after adding a row to CHART_OF_ACCOUNTS."""
    existing = {a.code: a for a in Account.query.all()}
    created = 0
    for code, name, type_, parent_code, is_group, is_control in CHART_OF_ACCOUNTS:
        if code in existing:
            continue
        acct = Account(code=code, name=name, type=type_, is_group=is_group,
                       is_control=is_control)
        db.session.add(acct)
        db.session.flush()          # need the id before a child references it
        existing[code] = acct
        created += 1
    # Second pass: parents are resolved once every code exists, so CHART_OF_ACCOUNTS
    # does not have to be topologically ordered.
    for code, _, _, parent_code, _, _ in CHART_OF_ACCOUNTS:
        if parent_code:
            existing[code].parent_id = existing[parent_code].id
    db.session.commit()
    return created

def account_for_role(role):
    """The account the posting layer uses for `role`. Raises rather than returning
    None — a missing one is a seeding bug, and a silently skipped journal line is
    far worse than a loud failure."""
    acct = Account.query.filter_by(role=role).first()
    if acct is None:
        raise LookupError(f"No account is set up for '{role}'")
    return acct

def account_is_system(acct):
    """System accounts cannot be renumbered or deleted: a posting path resolves
    them, by code for the original chart and by role for anything added later."""
    return acct.code in SYSTEM_ACCOUNT_CODES or bool(acct.role)

def _free_code(preferred):
    """`preferred` if nothing has taken it, else the next free code beside it. A
    chart that is already in use may well have an account on the code we wanted."""
    taken = {c for (c,) in db.session.query(Account.code).all()}
    if preferred not in taken:
        return preferred
    base = int(preferred) if preferred.isdigit() else None
    if base is None:
        raise LookupError(f"Cannot find a free code near {preferred}")
    for n in range(base + 1, base + 400):
        if str(n) not in taken:
            return str(n)
    raise LookupError(f"No free account code near {preferred}")

def backfill_account_openings():
    """Post cash/bank opening balances that were entered before they were posted at
    all. The money sat on the FinancialAccount row while every report read the GL,
    so it was invisible. Idempotent — an account that already has its opening entry
    is skipped. Never allowed to break boot: a failure is logged and the app starts."""
    posted = 0
    for fa in FinancialAccount.query.all():
        if not fa.opening_balance or posted_entry("account_opening", fa.id):
            continue
        try:
            post_account_opening(fa)
            db.session.commit()
            posted += 1
        except Exception as e:
            db.session.rollback()
            app.logger.warning("Could not post opening balance for %s: %s", fa.name, e)
    return posted

def realign_backdated_reversals():
    """Pull any reversal dated before the entry it cancels onto that entry's date.

    reverse_entry used to date every reversal today. Reverse an entry dated ahead of
    today and the cancelling credit landed before the debit it undoes, so a report cut
    between the two showed the credit alone — an expense reading negative, and a profit
    inflated by the very charge that had just been taken off. reverse_entry now refuses
    to backdate, but rows written before that fix are still split. Move them; the pair
    then nets to zero in any period containing either.

    Idempotent — a reversal already on or after its original is left alone. Never
    allowed to break boot."""
    moved = 0
    try:
        reversals = JournalEntry.query.filter(JournalEntry.reversal_of_id.isnot(None)).all()
        for rev in reversals:
            original = db.session.get(JournalEntry, rev.reversal_of_id)
            if original is None or rev.entry_date >= original.entry_date:
                continue
            rev.entry_date = original.entry_date
            moved += 1
        if moved:
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.warning("Could not realign backdated reversals: %s", e)
    return moved

def backfill_document_numbers():
    """Number the purchases and sales that predate numbering, oldest first, so the
    order they were raised in is the order they are numbered in. Idempotent: only
    documents with no number are touched, so a numbered invoice never changes."""
    numbered = 0
    for doc_type, model in (("purchase", Purchase), ("sale", Sale)):
        rows = (model.query.filter(model.invoice_no.is_(None))
                .order_by(model.date.asc(), model.id.asc()).all())
        for doc in rows:
            doc.invoice_no = allocate_document_number(doc_type, doc.date)
            numbered += 1
    if numbered:
        db.session.commit()
    return numbered

def seed_fixed_asset_accounts():
    """Create the accounts the fixed-asset module posts to, once. Idempotent, and
    safe on a chart already in use: an account is found by its role, and if its
    preferred code is taken it is seeded on the next free one instead."""
    created = 0
    by_role = {}
    for role, preferred, name, type_, parent_ref, cf_section in FIXED_ASSET_ACCOUNTS:
        acct = Account.query.filter_by(role=role).first()
        if acct is None:
            parent = None
            if parent_ref in by_role:                       # a role we just seeded
                parent = by_role[parent_ref]
            elif parent_ref:                                # a code from the base chart
                parent = Account.query.filter_by(code=parent_ref).first()
            acct = Account(code=_free_code(preferred), name=name, type=type_,
                           parent_id=parent.id if parent else None,
                           is_group=(role == "fixed_group"), is_control=False,
                           role=role, cash_flow_section=cf_section)
            db.session.add(acct)
            db.session.flush()
            created += 1
        by_role[role] = acct
    db.session.commit()
    return created

ACC_ASSETS_GROUP = "1000"
ACC_BANK_GROUP   = "1020"

def next_child_code(parent_code):
    """Next free code under a group, e.g. 1020 → 1021, 1022 … Keeps user-created
    bank accounts inside the Bank Accounts heading where an accountant expects them."""
    parent = get_account(parent_code)
    used = {a.code for a in Account.query.filter_by(parent_id=parent.id).all()}
    base = int(parent_code)
    for n in range(base + 1, base + 100):
        if str(n) not in used:
            return str(n)
    raise PostingError(f"No free account code left under {parent_code}.")

def ensure_gl_account_for_financial(fin_acct):
    """Give a cash/bank account its own GL leaf. Cash accounts land under Current
    Assets, bank ones under Bank Accounts. Idempotent."""
    if fin_acct.gl_account_id:
        return fin_acct.gl_account
    if fin_acct.method == "Cash":
        gl = get_account(ACC_CASH_IN_HAND)     # the seeded 1010, shared by the Cash account
    else:
        parent_code = ACC_BANK_GROUP if fin_acct.account_type == "Bank" else ACC_ASSETS_GROUP
        parent = get_account(parent_code)
        gl = Account(code=next_child_code(parent_code), name=fin_acct.name,
                     type="Asset", parent_id=parent.id, is_group=False, is_control=False)
        db.session.add(gl)
        db.session.flush()
    fin_acct.gl_account_id = gl.id
    return gl

def seed_financial_account_links():
    """Back-fill gl_account_id for the seeded cash/bank accounts."""
    for fa in FinancialAccount.query.filter_by(gl_account_id=None).all():
        ensure_gl_account_for_financial(fa)
    db.session.commit()

def fiscal_year_bounds(when):
    """The fiscal year that `when` falls in: (name, first day, last day).

    A tax year is not January to December everywhere, and a business cannot simply pretend
    it is. Pakistan runs July–June, the UK April–March, the UAE and the US January–December.
    Set FISCAL_YEAR_START_MONTH once and every year, period, opening balance and invoice
    sequence follows it.

    A year that does not start in January spans two calendar years, so it is named for both
    — "2026-27" — which is how such a year is written and, incidentally, how it has to be
    written for an invoice number to be unambiguous.
    """
    m = _get_fiscal_year_start_month()
    start_year = when.year if when.month >= m else when.year - 1
    start = date(start_year, m, 1)
    end = date(start_year + 1, m, 1) - timedelta(days=1)
    name = str(start_year) if m == 1 else f"{start_year}-{(start_year + 1) % 100:02d}"
    return name, start, end

def fiscal_years_that_disagree_with_the_setting():
    """Fiscal years already in the database that do not start in FISCAL_YEAR_START_MONTH.

    Changing the setting on a database that already has years does not move them. The old
    ones stay exactly where they are, and their periods now *overlap* the new ones — so a
    document dated inside the overlap lands in whichever period is found first, which is to
    say arbitrarily. It will be numbered for that year, closed with that year, and reported
    in it.

    Nothing here can fix that automatically: deleting a fiscal year would take its periods,
    and its postings, with it. So this reports, loudly, and leaves the decision to a human.
    """
    fiscal_year_start = _get_fiscal_year_start_month()
    return [fy.name for fy in FiscalYear.query.all()
            if fy.start_date.month != fiscal_year_start]

def seed_fiscal_year(when=None):
    """One fiscal year with twelve monthly periods, for the year `when` falls in.

    `when` may be a date, a datetime, or an int — an int being the calendar year the fiscal
    year *starts* in, which is what a caller who says `seed_fiscal_year(2026)` means.
    """
    from calendar import monthrange
    if when is None:
        when = now_local()
    if isinstance(when, int):
        m = _get_fiscal_year_start_month()
        when = date(when, m, 1)

    name, start, end = fiscal_year_bounds(when)
    if FiscalYear.query.filter_by(name=name).first():
        return 0
    fy = FiscalYear(name=name, start_date=start, end_date=end)
    db.session.add(fy)
    db.session.flush()

    y, m = start.year, start.month
    for _ in range(12):
        last = monthrange(y, m)[1]
        db.session.add(AccountingPeriod(
            fiscal_year_id=fy.id,
            name=date(y, m, 1).strftime("%b %Y"),
            start_date=date(y, m, 1), end_date=date(y, m, last)))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    db.session.commit()
    return 12

# ── Posting service ───────────────────────────────────────────────────────────
# The only way a journal entry may be created. Every rule that protects the GL
# lives here, so no caller can bypass one by forgetting to check it.

MONEY = Decimal("0.0001")            # Numeric(14,4) — the smallest storable unit

class PostingError(Exception):
    """A posting was refused. The message is shown to the user verbatim."""

def current_user_id():
    """The signed-in user's id, or None. Safe outside a request — the CLI seeder
    and any background task post entries with no user attached."""
    try:
        return current_user.id if current_user and current_user.is_authenticated else None
    except Exception:
        return None

def _money(value):
    """Parse to Decimal at storage precision. Never float: 0.1 + 0.2 must not
    decide whether an entry balances."""
    try:
        return Decimal(str(value or 0)).quantize(MONEY)
    except (InvalidOperation, ValueError):
        raise PostingError(f"'{value}' is not a valid amount.")

def find_period(when):
    """The accounting period containing this date, or None if no fiscal year
    covers it."""
    d = when.date() if isinstance(when, datetime) else when
    return (AccountingPeriod.query
            .filter(AccountingPeriod.start_date <= d, AccountingPeriod.end_date >= d)
            .first())

def document_year(when):
    """The fiscal year a document belongs to — numbering restarts each year. Falls
    back to the calendar year for a date no fiscal year covers (old data)."""
    period = find_period(when)
    return period.fiscal_year.name if period else str(when.year)

def allocate_document_number(doc_type, when):
    """Take the next number for `doc_type` in `when`'s fiscal year, e.g. INV-2026-000123.

    SELECT ... FOR UPDATE holds the counter row for the rest of the transaction, so
    two people saving an invoice at the same moment queue up and get consecutive
    numbers instead of the same one. Because the counter is a row and not a database
    sequence, a save that fails hands its number straight back — no hole.

    Does NOT commit: the number, the document and its journal entry all land in one
    transaction, or none of them do."""
    year   = document_year(when)
    prefix = DOCUMENT_PREFIXES[doc_type]

    seq = (DocumentSequence.query
           .filter_by(doc_type=doc_type, year=year)
           .with_for_update()
           .first())
    if seq is None:
        seq = DocumentSequence(doc_type=doc_type, year=year, prefix=prefix, next_number=1)
        db.session.add(seq)
        db.session.flush()

    number = seq.next_number
    seq.next_number = number + 1
    return f"{prefix}-{year}-{number:06d}"

def post_entry(*, entry_date, description, lines, reference=None,
               source_type="manual", source_id=None, allow_control=False,
               created_by_id=None):
    """Validate and write one balanced journal entry. Returns the JournalEntry.

    `lines` is a list of dicts: {"account_id" or "code", "debit", "credit", "memo"}.

    Raises PostingError — never writes a partial entry — if any of:
      · fewer than two lines
      · a line has both or neither of debit/credit
      · debits ≠ credits
      · the account is missing, inactive, or a group header
      · the account is a control account and allow_control is False
      · the date falls in no fiscal year, or in a closed period/year

    `allow_control=True` is for the posting layer itself (a sale legitimately
    debits Accounts Receivable). Manual entries never get it: a hand-written
    line against AR would silently desynchronise the customer subledger.

    Does NOT commit — the caller commits, so a document and its entry land in
    one transaction."""
    if not description or not str(description).strip():
        raise PostingError("A journal entry needs a description.")

    period = find_period(entry_date)
    if period is None:
        raise PostingError(
            f"No fiscal year covers {entry_date:%Y-%m-%d}. Create the fiscal year first.")
    if period.is_closed:
        raise PostingError(f"{period.name} is closed. Post to an open period.")
    if period.fiscal_year.is_closed:
        raise PostingError(f"Fiscal year {period.fiscal_year.name} is closed.")

    prepared, total_dr, total_cr = [], Decimal("0"), Decimal("0")
    for raw in lines:
        debit, credit = _money(raw.get("debit")), _money(raw.get("credit"))
        if debit < 0 or credit < 0:
            raise PostingError("Amounts cannot be negative.")
        if debit > 0 and credit > 0:
            raise PostingError("A line cannot be both a debit and a credit.")
        if debit == 0 and credit == 0:
            continue                                   # blank row — skip silently

        acct = (db.session.get(Account, raw["account_id"]) if raw.get("account_id")
                else get_account(raw["code"]))
        if acct is None:
            raise PostingError("Unknown account on one of the lines.")
        if not acct.is_active:
            raise PostingError(f"Account {acct.code} {acct.name} is inactive.")
        if acct.is_group:
            raise PostingError(
                f"{acct.code} {acct.name} is a heading, not a postable account.")
        if acct.is_control and not allow_control:
            raise PostingError(
                f"{acct.code} {acct.name} is a control account — it is maintained by "
                f"its subledger and cannot be posted to by hand.")

        prepared.append((acct, debit, credit, (raw.get("memo") or None)))
        total_dr += debit
        total_cr += credit

    if len(prepared) < 2:
        raise PostingError("A journal entry needs at least two lines with amounts.")
    if total_dr != total_cr:
        raise PostingError(
            f"Entry is out of balance: debits {total_dr:,.2f} ≠ credits {total_cr:,.2f} "
            f"(difference {abs(total_dr - total_cr):,.2f}).")
    if total_dr == 0:
        raise PostingError("An entry of zero has no effect.")

    entry = JournalEntry(
        entry_date=entry_date, description=str(description).strip(),
        reference=(reference or None), source_type=source_type, source_id=source_id,
        period_id=period.id, created_by_id=created_by_id)
    db.session.add(entry)
    db.session.flush()
    for acct, debit, credit, memo in prepared:
        db.session.add(JournalLine(entry_id=entry.id, account_id=acct.id,
                                   debit=debit, credit=credit, memo=memo))
    return entry

def reverse_entry(entry, on_date=None, created_by_id=None):
    """Post the mirror image of `entry`. The original is never touched beyond
    being flagged, which is the whole point: both rows stay in the ledger.

    The reversal is dated today by default, not on the original's date — a
    correction made in July belongs in July, not backdated into a period whose
    numbers may already have been reported.

    Never earlier than the original, though. An entry dated ahead of today (a
    month-end accrual, say) would otherwise be cancelled by a reversal that lands
    before it, and every report cut between the two dates would show the credit
    without the debit it undoes."""
    if entry.is_reversed:
        raise PostingError(f"Entry #{entry.id} has already been reversed.")
    if entry.is_reversal:
        raise PostingError("A reversal cannot itself be reversed.")

    reversal = post_entry(
        entry_date=on_date or max(now_local(), entry.entry_date),
        description=f"Reversal of #{entry.id}: {entry.description}",
        reference=entry.reference,
        source_type=entry.source_type, source_id=entry.source_id,
        allow_control=True,          # mirrors whatever the original touched
        created_by_id=created_by_id,
        lines=[{"account_id": l.account_id, "debit": l.credit, "credit": l.debit,
                "memo": l.memo} for l in entry.lines],
    )
    reversal.reversal_of_id = entry.id
    entry.is_reversed = True
    return reversal

def postable_accounts():
    """Accounts a human may choose in the manual journal form: active leaves that
    are not control accounts."""
    return (Account.query
            .filter_by(is_active=True, is_group=False, is_control=False)
            .order_by(Account.code).all())

# ── Document posting ──────────────────────────────────────────────────────────
# Every business document becomes a journal entry. These functions are the only
# place the debit/credit shape of a document is written down.

def posted_entry(source_type, source_id):
    """The live (non-reversed) entry for a document, or None. Reversals carry the
    same source_type/source_id, so they are excluded explicitly."""
    return (JournalEntry.query
            .filter_by(source_type=source_type, source_id=source_id,
                       reversal_of_id=None, is_reversed=False)
            .first())

def assert_not_posted(source_type, source_id, what):
    """Guard for edit/delete routes. A posted document is history: changing the
    row underneath a journal entry would make the ledger describe something that
    no longer exists.

    A *reversed* document is refused too. Its entry is no longer live, so
    posted_entry() returns None — but its journal entries are still in the ledger
    and deleting the document would orphan them."""
    if posted_entry(source_type, source_id):
        raise PostingError(
            f"{what} is posted to the general ledger and can no longer be changed. "
            f"Reverse it instead.")
    if JournalEntry.query.filter_by(source_type=source_type, source_id=source_id).first():
        raise PostingError(
            f"{what} has already been reversed. It stays in the ledger as a record "
            f"of what happened and cannot be deleted.")

def assert_not_numbered(doc, what):
    """Guard for delete routes. An issued invoice number can never be withdrawn —
    deleting the document would take its number out of the sequence and leave a gap,
    which is the one thing gapless numbering exists to prevent. Reverse it instead:
    the number stays, marked reversed."""
    if getattr(doc, "invoice_no", None):
        raise PostingError(
            f"{what} {doc.invoice_no} has an issued invoice number and cannot be "
            f"deleted — that would leave a gap in the numbering. Reverse it instead.")

def _cash_gl(fin_acct):
    if fin_acct is None:
        raise PostingError("This movement has no cash/bank account, so it cannot be posted. "
                           "Pick an account on the form.")
    gl = ensure_gl_account_for_financial(fin_acct)
    return gl.id

def _resolve_financial_account(movement):
    """A movement points at its account explicitly, or (legacy) by payment_method."""
    if movement.account_id:
        return db.session.get(FinancialAccount, movement.account_id)
    return FinancialAccount.query.filter_by(method=movement.payment_method).first()

def _doc_lines(doc, price_attr):
    """Normalise a purchase/sale to (taxable, tax) totals whether it uses
    line_items or the older single-item columns."""
    taxable = tax = Decimal("0")
    rows = doc.line_items or []
    if rows:
        for r in rows:
            gross = Decimal(str(r.quantity)) * Decimal(str(getattr(r, price_attr)))
            disc, t, _ = calc_discount_tax(gross, r.discount_type, r.discount_value, r.tax_percent)
            taxable += Decimal(str(gross)) - Decimal(str(disc))
            tax     += Decimal(str(t))
    elif doc.quantity and getattr(doc, price_attr):
        gross = Decimal(str(doc.quantity)) * Decimal(str(getattr(doc, price_attr)))
        disc, t, _ = calc_discount_tax(gross, doc.discount_type, doc.discount_value, doc.tax_percent)
        taxable = Decimal(str(gross)) - Decimal(str(disc))
        tax     = Decimal(str(t))
    return taxable.quantize(MONEY), tax.quantize(MONEY)

def _cogs_of(sale):
    rows = sale.line_items or []
    if rows:
        # cost_price is per base unit, so the total needs the base qty.
        return sum(Decimal(str(line_base_qty(r))) * Decimal(str(r.cost_price or 0)) for r in rows).quantize(MONEY)
    return (Decimal(str(sale.quantity or 0)) * Decimal(str(sale.cost_price or 0))).quantize(MONEY)

def post_purchase(pur, created_by_id=None):
    """Dr Inventory (goods) + Dr Tax Input (tax) / Cr Accounts Payable (total)."""
    if posted_entry("purchase", pur.id):
        return None                                    # idempotent
    goods, tax = _doc_lines(pur, "purchase_price")
    lines = [{"code": ACC_INVENTORY, "debit": goods, "credit": 0, "memo": "Goods received"}]
    if tax:
        lines.append({"code": ACC_TAX_INPUT, "debit": tax, "credit": 0, "memo": "Recoverable tax"})
    lines.append({"code": ACC_AP, "debit": 0, "credit": goods + tax,
                  "memo": pur.supplier.name if pur.supplier else None})
    return post_entry(entry_date=pur.date, description=f"Purchase #{pur.id}",
                      reference=f"PUR-{pur.id}", source_type="purchase", source_id=pur.id,
                      allow_control=True, created_by_id=created_by_id, lines=lines)

def post_sale(sale, created_by_id=None):
    """Two economic events in one entry: the revenue side, and the cost of the
    goods that left. Perpetual inventory means both happen at the moment of sale."""
    if posted_entry("sale", sale.id):
        return None
    revenue, tax = _doc_lines(sale, "sale_price")
    cogs = _cogs_of(sale)
    lines = [{"code": ACC_AR, "debit": revenue + tax, "credit": 0,
              "memo": sale.customer.name if sale.customer else None},
             {"code": ACC_SALES, "debit": 0, "credit": revenue}]
    if tax:
        lines.append({"code": ACC_TAX_OUTPUT, "debit": 0, "credit": tax, "memo": "Tax payable"})
    if cogs:
        lines.append({"code": ACC_COGS, "debit": cogs, "credit": 0, "memo": "Cost of goods sold"})
        lines.append({"code": ACC_INVENTORY, "debit": 0, "credit": cogs})
    return post_entry(entry_date=sale.date, description=f"Sale #{sale.id}",
                      reference=f"SAL-{sale.id}", source_type="sale", source_id=sale.id,
                      allow_control=True, created_by_id=created_by_id, lines=lines)

def post_supplier_payment(pmt, created_by_id=None):
    """Dr Accounts Payable / Cr Cash or Bank."""
    if posted_entry("payment", pmt.id):
        return None
    amount = Decimal(str(pmt.amount)).quantize(MONEY)
    return post_entry(entry_date=pmt.payment_date, description=f"Payment to {pmt.supplier.name}",
                      reference=pmt.reference_no or f"PAY-{pmt.id}",
                      source_type="payment", source_id=pmt.id,
                      allow_control=True, created_by_id=created_by_id,
                      lines=[{"code": ACC_AP, "debit": amount, "credit": 0},
                             {"account_id": _cash_gl(_resolve_financial_account(pmt)),
                              "debit": 0, "credit": amount, "memo": pmt.payment_method}])

def post_customer_receipt(rcpt, created_by_id=None):
    """Dr Cash or Bank / Cr Accounts Receivable."""
    if posted_entry("receipt", rcpt.id):
        return None
    amount = Decimal(str(rcpt.amount)).quantize(MONEY)
    return post_entry(entry_date=rcpt.payment_date, description=f"Receipt from {rcpt.customer.name}",
                      reference=rcpt.reference_no or f"RCT-{rcpt.id}",
                      source_type="receipt", source_id=rcpt.id,
                      allow_control=True, created_by_id=created_by_id,
                      lines=[{"account_id": _cash_gl(_resolve_financial_account(rcpt)),
                              "debit": amount, "credit": 0, "memo": rcpt.payment_method},
                             {"code": ACC_AR, "debit": 0, "credit": amount}])

def post_expense(exp, created_by_id=None):
    """Dr the category's expense account / Cr Cash or Bank."""
    if posted_entry("expense", exp.id):
        return None
    amount = Decimal(str(exp.amount)).quantize(MONEY)
    cat = exp.category
    expense_gl = (cat.gl_account_id if cat and cat.gl_account_id else get_account("6090").id)
    return post_entry(entry_date=exp.date, description=f"Expense: {exp.description}",
                      reference=exp.reference_no or f"EXP-{exp.id}",
                      source_type="expense", source_id=exp.id,
                      allow_control=True, created_by_id=created_by_id,
                      lines=[{"account_id": expense_gl, "debit": amount, "credit": 0},
                             {"account_id": _cash_gl(_resolve_financial_account(exp)),
                              "debit": 0, "credit": amount, "memo": exp.payment_method}])

def post_purchase_return(pr, created_by_id=None):
    """Goods go back to the supplier. Inventory is credited with what the goods
    actually cost us (their average), while the supplier's account is debited
    with what we agreed to get back. The two rarely match exactly — the gap is a
    real gain or loss and goes to Inventory Adjustment."""
    if posted_entry("purchase_return", pr.id):
        return None
    credit_note = (Decimal(str(pr.quantity)) * Decimal(str(pr.return_price))).quantize(MONEY)
    cost = Decimal(str(pr.cost_removed or 0)).quantize(MONEY)
    lines = [{"code": ACC_AP, "debit": credit_note, "credit": 0,
              "memo": pr.supplier.name if pr.supplier else None},
             {"code": ACC_INVENTORY, "debit": 0, "credit": cost}]
    variance = credit_note - cost
    if variance > 0:                       # got back more than it cost — a gain
        lines.append({"code": ACC_STOCK_ADJ, "debit": 0, "credit": variance, "memo": "Return price variance"})
    elif variance < 0:
        lines.append({"code": ACC_STOCK_ADJ, "debit": -variance, "credit": 0, "memo": "Return price variance"})
    return post_entry(entry_date=pr.date, description=f"Purchase return #{pr.id}",
                      reference=f"PRT-{pr.id}", source_type="purchase_return", source_id=pr.id,
                      allow_control=True, created_by_id=created_by_id, lines=lines)

def post_sale_return(sr, created_by_id=None):
    """Dr Sales Returns / Cr Accounts Receivable, and the goods come back into
    stock at what they cost when they left: Dr Inventory / Cr Cost of Goods Sold."""
    if posted_entry("sale_return", sr.id):
        return None
    amount = (Decimal(str(sr.quantity)) * Decimal(str(sr.return_price))).quantize(MONEY)
    cost = Decimal(str(sr.cost_restored or 0)).quantize(MONEY)
    lines = [{"code": ACC_SALES_RETURNS, "debit": amount, "credit": 0},
             {"code": ACC_AR, "debit": 0, "credit": amount,
              "memo": sr.customer.name if sr.customer else None}]
    if cost:
        lines.append({"code": ACC_INVENTORY, "debit": cost, "credit": 0, "memo": "Goods returned to stock"})
        lines.append({"code": ACC_COGS, "debit": 0, "credit": cost})
    return post_entry(entry_date=sr.date, description=f"Sale return #{sr.id}",
                      reference=f"SRT-{sr.id}", source_type="sale_return", source_id=sr.id,
                      allow_control=True, created_by_id=created_by_id, lines=lines)

# ── Reading the ledger ────────────────────────────────────────────────────────
# Every report below sums journal lines. Nothing is derived from the Purchase /
# Sale / payment tables any more, so a reversal, a manual adjustment and a sale
# all reach the reports by exactly the same path.

def gl_balances(as_of=None, start=None):
    """Net movement per account as {account_id: Decimal(debit - credit)}.

    `start` and `as_of` bound the entry_date. Leaving `start` open gives a
    cumulative balance (what a balance sheet needs); passing both gives the
    movement in a period (what a P&L needs)."""
    q = (db.session.query(JournalLine.account_id,
                          func.sum(JournalLine.debit).label("dr"),
                          func.sum(JournalLine.credit).label("cr"))
         .join(JournalEntry, JournalLine.entry_id == JournalEntry.id))
    if start is not None:
        q = q.filter(JournalEntry.entry_date >= start)
    if as_of is not None:
        q = q.filter(JournalEntry.entry_date <= as_of)
    return {aid: Decimal(str(dr or 0)) - Decimal(str(cr or 0))
            for aid, dr, cr in q.group_by(JournalLine.account_id).all()}

def natural_balance(account, signed_balance):
    """`signed_balance` is debit-minus-credit. Flip it for credit-natured accounts
    so a liability with a credit balance reads as a positive number, the way it is
    printed on a balance sheet."""
    return signed_balance if account.is_debit_natured else -signed_balance

def accounts_by_type(balances, *types):
    """(account, natural_balance) for leaf accounts of the given types, skipping
    the ones that never moved. Groups are excluded — their children carry the money."""
    out = []
    for acct in Account.query.filter(Account.type.in_(types), Account.is_group.is_(False)
                                     ).order_by(Account.code).all():
        raw = balances.get(acct.id, Decimal("0"))
        if raw:
            out.append((acct, natural_balance(acct, raw)))
    return out

def gl_profit(start, end):
    """Net profit for a period, straight from the income and expense accounts."""
    b = gl_balances(as_of=end, start=start)
    income  = sum(bal for _, bal in accounts_by_type(b, "Income"))
    expense = sum(bal for _, bal in accounts_by_type(b, "Expense"))
    return Decimal(str(income or 0)), Decimal(str(expense or 0))

def retained_earnings_to_date(as_of):
    """Profit earned up to `as_of` that has not been closed into equity yet.

    Until a fiscal year is closed, income and expense accounts still carry their
    balances. The balance sheet must show that profit inside equity, or it will
    not balance. Once year-end closing exists (Phase 6) this becomes the profit
    of the *current* year only; the rest will already sit in 3900."""
    income, expense = gl_profit(None, as_of)
    return income - expense

# ── Cash flow statement ───────────────────────────────────────────────────────
CASH_FLOW_SECTIONS = ("Operating", "Investing", "Financing")

def account_cf_section(acct):
    """Which cash-flow section this account's movements belong to. An explicit
    setting on the account wins. Otherwise Equity is Financing and everything else
    Operating — the right default for the seeded chart, which carries only working
    capital. A Fixed Assets or Loan account added later should be tagged."""
    if acct.cash_flow_section in CASH_FLOW_SECTIONS:
        return acct.cash_flow_section
    return "Financing" if acct.type == "Equity" else "Operating"

def parse_cf_section(raw):
    """A blank/unknown choice means 'use the default for this type'."""
    raw = (raw or "").strip()
    return raw if raw in CASH_FLOW_SECTIONS else None

def cash_gl_account_ids():
    """The GL leaves that *are* cash — one per cash/bank account."""
    return {fa.gl_account_id for fa in FinancialAccount.query.all() if fa.gl_account_id}

def cash_balance_as_of(as_of, cash_ids=None):
    ids = cash_ids if cash_ids is not None else cash_gl_account_ids()
    b = gl_balances(as_of=as_of)
    # cash is debit-natured, so the raw debit-minus-credit already reads positive
    return sum((b.get(i, Decimal("0")) for i in ids), Decimal("0"))

def cash_flow_statement(start, end):
    """Cash movements in [start, end], split into Operating/Investing/Financing.

    Every entry balances, so an entry's cash lines move exactly minus what its
    non-cash lines move. Each non-cash line therefore contributes -(debit-credit)
    to cash, and is classified by its own account. Summing those gives the sections
    *and* ties to the real change in cash — the reconciliation is a genuine check,
    not a balancing plug.
    """
    cash_ids = cash_gl_account_ids()
    opening = cash_balance_as_of(start - timedelta(seconds=1), cash_ids)
    closing = cash_balance_as_of(end, cash_ids)

    sections = {s: {} for s in CASH_FLOW_SECTIONS}   # section -> {account: amount}
    entries = (JournalEntry.query
               .filter(JournalEntry.entry_date >= start, JournalEntry.entry_date <= end)
               .all())
    for e in entries:
        cash_delta = sum(
            (Decimal(str(l.debit or 0)) - Decimal(str(l.credit or 0)))
            for l in e.lines if l.account_id in cash_ids
        )
        if not cash_delta:
            continue          # never moved cash (or was a cash-to-cash transfer)
        for l in e.lines:
            if l.account_id in cash_ids:
                continue
            contrib = -(Decimal(str(l.debit or 0)) - Decimal(str(l.credit or 0)))
            if not contrib:
                continue
            bucket = sections[account_cf_section(l.account)]
            bucket[l.account] = bucket.get(l.account, Decimal("0")) + contrib

    totals = {s: sum(sections[s].values(), Decimal("0")) for s in CASH_FLOW_SECTIONS}
    net_change = sum(totals.values(), Decimal("0"))
    return {
        "sections": {s: sorted(sections[s].items(), key=lambda kv: kv[0].code)
                     for s in CASH_FLOW_SECTIONS},
        "totals": totals,
        "net_change": net_change,
        "opening": opening,
        "closing": closing,
        # both sides come from the same journal lines; if they ever disagree an
        # entry was written incompletely, and the report says so instead of hiding it
        "reconciles": abs((opening + net_change) - closing) < Decimal("0.01"),
    }

# ── Period and year-end closing ───────────────────────────────────────────────
def close_fiscal_year(fy, created_by_id=None):
    """Post the closing entry and lock the year.

    Income and expense accounts measure one year only. Closing zeroes each of
    them against Retained Earnings, so that on the first day of the next year the
    P&L starts from nothing while the balance sheet carries forward untouched.

    The entry is dated the last day of the year and posted before the year is
    marked closed — otherwise post_entry() would refuse its own closing entry."""
    if fy.is_closed:
        raise PostingError(f"Fiscal year {fy.name} is already closed.")
    if FiscalYear.query.filter(FiscalYear.end_date < fy.start_date,
                               FiscalYear.is_closed.is_(False)).first():
        raise PostingError("An earlier fiscal year is still open. Close years in order.")

    end = datetime.combine(fy.end_date, datetime.max.time().replace(microsecond=0))
    start = datetime.combine(fy.start_date, datetime.min.time())
    b = gl_balances(as_of=end, start=start)

    lines, profit = [], Decimal("0")
    for acct in Account.query.filter(Account.type.in_(("Income", "Expense")),
                                     Account.is_group.is_(False)).order_by(Account.code).all():
        raw = b.get(acct.id, Decimal("0"))                    # debit-minus-credit
        if not raw:
            continue
        # Post the opposite of whatever the account carries, to bring it to zero.
        if raw > 0:
            lines.append({"account_id": acct.id, "debit": 0, "credit": raw})
        else:
            lines.append({"account_id": acct.id, "debit": -raw, "credit": 0})
        profit -= raw          # income is credit-natured (raw < 0) and adds to profit

    if lines:
        if profit > 0:
            lines.append({"code": ACC_RETAINED, "debit": 0, "credit": profit,
                          "memo": f"Profit for {fy.name}"})
        else:
            lines.append({"code": ACC_RETAINED, "debit": -profit, "credit": 0,
                          "memo": f"Loss for {fy.name}"})
        post_entry(entry_date=end, description=f"Year-end closing — {fy.name}",
                   reference=f"CLOSE-{fy.name}", source_type="closing", source_id=fy.id,
                   allow_control=True, created_by_id=created_by_id, lines=lines)

    for p in fy.periods:
        p.is_closed = True
    fy.is_closed = True
    fy.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return profit

def _unwind_stock_and_subledger(kind, doc):
    """Undo a document's operational effects — the physical stock it moved and the
    supplier/customer subledger row it created. The GL is NOT touched here; its
    correction is a reversing entry, because the GL is a record of what happened,
    not of what is currently true."""
    if kind == "purchase":
        for pi in doc.line_items:
            item = db.session.get(Item, pi.item_id)
            if item:
                # Remove exactly the cost this line added — its taxable amount —
                # not today's average, which later purchases may have moved.
                # Stock moves in the item's base unit, however the line was priced.
                item_remove_stock(item, line_base_qty(pi),
                                  cost_total=Decimal(str(pi.amount)) - Decimal(str(pi.tax_amount or 0)))
        sup_id = remove_supplier_ledger_entry("purchase", doc.id)
        return ("supplier", sup_id)

    if kind == "sale":
        for si in doc.line_items:
            item = db.session.get(Item, si.item_id)
            if item:
                # Goods come back in at the cost they left at. cost_price is per base
                # unit (it is a snapshot of avg_cost), so the total needs the base qty.
                base_qty = line_base_qty(si)
                item_add_stock(item, base_qty,
                               Decimal(str(si.cost_price or 0)) * Decimal(str(base_qty)))
        cust_id = remove_customer_ledger_entry("sale", doc.id)
        return ("customer", cust_id)

    if kind == "payment":
        return ("supplier", remove_supplier_ledger_entry("payment", doc.id))

    if kind == "receipt":
        return ("customer", remove_customer_ledger_entry("receipt", doc.id))

    if kind == "expense":
        return (None, None)                       # touches no stock, no subledger

    if kind == "purchase_return":
        item = db.session.get(Item, doc.item_id)
        if item:                                   # goods had left; bring them back
            item_add_stock(item, line_base_qty(doc), Decimal(str(doc.cost_removed or 0)))
        return ("supplier", remove_supplier_ledger_entry("purchase_return", doc.id))

    if kind == "sale_return":
        item = db.session.get(Item, doc.item_id)
        if item:                                   # goods had come back; send them out
            item_remove_stock(item, line_base_qty(doc), cost_total=Decimal(str(doc.cost_restored or 0)))
        return ("customer", remove_customer_ledger_entry("sale_return", doc.id))

    if kind == "stock_adjustment":
        item = db.session.get(Item, doc.item_id)
        if item:
            value = Decimal(str(doc.cost_value or 0))
            if doc.direction == "out":
                item_add_stock(item, doc.quantity, value)
            else:
                item_remove_stock(item, doc.quantity, cost_total=value)
        return (None, None)

    raise PostingError(f"Don't know how to reverse a {kind}.")

DOCUMENT_LABELS = {
    "purchase": "Purchase", "sale": "Sale", "payment": "Supplier payment",
    "receipt": "Customer receipt", "expense": "Expense",
    "purchase_return": "Purchase return", "sale_return": "Sale return",
    "stock_adjustment": "Stock adjustment",
}

def reverse_document(kind, doc):
    """Cancel a posted document without erasing it.

    Three things happen, in this order: a mirror journal entry is posted, the
    stock and subledger effects are undone, and the document is flagged. The
    document row and both journal entries stay — that is the audit trail.

    Does not commit; the caller does."""
    label = DOCUMENT_LABELS.get(kind, kind)
    if getattr(doc, "is_reversed", False):
        raise PostingError(f"{label} #{doc.id} has already been reversed.")

    entry = posted_entry(kind, doc.id)
    if entry is None:
        raise PostingError(
            f"{label} #{doc.id} has no live journal entry, so there is nothing to reverse.")

    uid = current_user_id()
    reversal = reverse_entry(entry, created_by_id=uid)

    owner_kind, owner_id = _unwind_stock_and_subledger(kind, doc)
    db.session.flush()
    if owner_kind == "supplier" and owner_id:
        recalculate_supplier_ledger(owner_id)
    elif owner_kind == "customer" and owner_id:
        recalculate_customer_ledger(owner_id)

    doc.is_reversed = True
    doc.reversed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return reversal

# ── Weighted-average inventory costing ────────────────────────────────────────
# Goods in add their cost to Item.inventory_value; goods out remove qty × the
# average at that moment. The average is never stored, only derived, so it can
# never drift out of step with the value it came from.
#
# Every amount here is also what the Inventory control account is posted, which
# is why the reconciliation report can compare the two.

def item_add_stock(item, qty, cost_total):
    """Goods in, at a known total cost."""
    item.stock += qty
    item.inventory_value = Decimal(str(item.inventory_value or 0)) + Decimal(str(cost_total)).quantize(MONEY)

def item_remove_stock(item, qty, cost_total=None):
    """Goods out. Costed at the current average unless a cost is given (a sale
    return puts goods back at what they left at, not at today's average).

    Refuses to take out more than is there. Every route that sells or issues stock
    checks first, but the *reversal* paths did not: reversing a purchase whose goods
    have since been sold took the goods back anyway, and the warehouse ended up holding
    minus eighty widgets while the Inventory account went negative and parted company
    with the item it was meant to mirror. The check belongs here, at the one place all
    of them pass through, rather than in each caller — a new caller gets it for free,
    which is exactly what the reversal paths never did.

    Returns the cost removed, which is what the caller posts as COGS."""
    value = Decimal(str(item.inventory_value or 0))
    cost = Decimal(str(cost_total)) if cost_total is not None else (item.avg_cost * Decimal(str(qty)))
    cost = cost.quantize(MONEY)

    if qty > (item.stock or 0):
        raise PostingError(
            f"Only {item.stock or 0} × {item.name} in stock, so {qty} cannot be taken out. "
            f"If you are reversing a document, the goods it brought in have already been "
            f"sold or issued — reverse those first.")
    if cost > value and qty < (item.stock or 0):
        # Taking the whole stock out is allowed to take the whole value with it (that is
        # what a final sale does). Taking out *part* of it must not cost more than the
        # stock is carrying, or the item would hold goods worth less than nothing.
        raise PostingError(
            f"{item.name} carries {value} of value, so {cost} cannot be taken out of it. "
            f"The cost of these goods has already been absorbed into stock that was sold. "
            f"Raise a return instead of reversing.")

    item.stock -= qty
    item.inventory_value = value - cost
    if item.stock <= 0:
        # Last unit gone: any rounding residue would otherwise linger as value
        # against zero stock, which the reconciliation would (rightly) flag.
        item.inventory_value = Decimal("0")
    return cost

def sale_line_cost(sale_return):
    """What the returned goods cost when they were sold. Recorded on the original
    sale line, so a return never re-values stock at today's average."""
    si = (db.session.get(SaleItem, sale_return.sale_item_id) if sale_return.sale_item_id
          else SaleItem.query.filter_by(sale_id=sale_return.sale_id, item_id=sale_return.item_id).first())
    unit = Decimal(str(si.cost_price)) if si else Decimal(str(sale_return.item.avg_cost if sale_return.item else 0))
    return (unit * Decimal(str(line_base_qty(sale_return)))).quantize(MONEY)

# ── Opening balances ──────────────────────────────────────────────────────────
# What the business already owned and owed before it started using the system.
# Without these the GL's control accounts can never agree with their subledgers,
# because a customer's opening balance is real receivable that no sale created.

def _repost_opening(source_type, source_id, entry_date, description, lines):
    """Opening balances are the one thing a user legitimately edits after the fact.
    Rather than let the GL drift, reverse the old entry and post a fresh one — the
    correction stays visible in the ledger."""
    existing = posted_entry(source_type, source_id)
    uid = current_user_id()
    if existing:
        reverse_entry(existing, created_by_id=uid)
    if not lines:
        return None
    return post_entry(entry_date=entry_date, description=description,
                      reference=f"OB-{source_type}-{source_id}",
                      source_type=source_type, source_id=source_id,
                      allow_control=True, created_by_id=uid, lines=lines)

def _opening_date():
    """Dated to the start of the open fiscal year, so an opening balance lands in
    the period it describes rather than on whatever day it was typed."""
    fy = FiscalYear.query.filter_by(is_closed=False).order_by(FiscalYear.start_date).first()
    return datetime.combine(fy.start_date, datetime.min.time()) if fy else now_local()

def post_supplier_opening(supplier):
    """A positive opening balance is money we already owed: Cr Accounts Payable."""
    ob = Decimal(str(supplier.opening_balance or 0)).quantize(MONEY)
    lines = []
    if ob > 0:
        lines = [{"code": ACC_OPENING_EQUITY, "debit": ob, "credit": 0},
                 {"code": ACC_AP, "debit": 0, "credit": ob, "memo": supplier.name}]
    elif ob < 0:                       # we had already paid them in advance
        lines = [{"code": ACC_AP, "debit": -ob, "credit": 0, "memo": supplier.name},
                 {"code": ACC_OPENING_EQUITY, "debit": 0, "credit": -ob}]
    return _repost_opening("supplier_opening", supplier.id, _opening_date(),
                           f"Opening balance — {supplier.name}", lines)

def post_customer_opening(customer):
    """A positive opening balance is money already owed to us: Dr Accounts Receivable."""
    ob = Decimal(str(customer.opening_balance or 0)).quantize(MONEY)
    lines = []
    if ob > 0:
        lines = [{"code": ACC_AR, "debit": ob, "credit": 0, "memo": customer.name},
                 {"code": ACC_OPENING_EQUITY, "debit": 0, "credit": ob}]
    elif ob < 0:                       # they had already paid us in advance
        lines = [{"code": ACC_OPENING_EQUITY, "debit": -ob, "credit": 0},
                 {"code": ACC_AR, "debit": 0, "credit": -ob, "memo": customer.name}]
    return _repost_opening("customer_opening", customer.id, _opening_date(),
                           f"Opening balance — {customer.name}", lines)

def post_account_opening(fin_acct):
    """Cash or bank already in hand on the day the system started: Dr the account,
    Cr Opening Balance Equity.

    Without this the money exists on the FinancialAccount row but not in the GL, and
    since every report is summed from the GL, the balance sheet simply would not see
    it — which is exactly what happened."""
    ob = Decimal(str(fin_acct.opening_balance or 0)).quantize(MONEY)
    gl = ensure_gl_account_for_financial(fin_acct)
    lines = []
    if ob > 0:
        lines = [{"account_id": gl.id, "debit": ob, "credit": 0, "memo": fin_acct.name},
                 {"code": ACC_OPENING_EQUITY, "debit": 0, "credit": ob}]
    elif ob < 0:                        # started overdrawn
        lines = [{"code": ACC_OPENING_EQUITY, "debit": -ob, "credit": 0},
                 {"account_id": gl.id, "debit": 0, "credit": -ob, "memo": fin_acct.name}]
    return _repost_opening("account_opening", fin_acct.id, _opening_date(),
                           f"Opening balance — {fin_acct.name}", lines)

def post_item_opening(item):
    """Stock on hand before the system existed, valued at the item's cost. This is
    the one place `purchase_price` legitimately values inventory: there is no
    purchase history to average over yet."""
    qty = Decimal(str(item.opening_stock or 0))
    cost = Decimal(str(item.purchase_price or 0))
    value = (qty * cost).quantize(MONEY)
    lines = []
    if value > 0:
        lines = [{"code": ACC_INVENTORY, "debit": value, "credit": 0, "memo": item.name},
                 {"code": ACC_OPENING_EQUITY, "debit": 0, "credit": value}]
    return _repost_opening("item_opening", item.id, _opening_date(),
                           f"Opening stock — {item.name}", lines)

def post_document(kind, doc):
    """Post `doc` to the GL. Called from route handlers after db.session.flush()
    and before their commit, so the document and its entry share one transaction:
    if posting is refused, the document is never written either.

    Raises PostingError, which routes turn into a flash message."""
    poster = {
        "purchase":         post_purchase,
        "sale":             post_sale,
        "payment":          post_supplier_payment,
        "receipt":          post_customer_receipt,
        "expense":          post_expense,
        "purchase_return":  post_purchase_return,
        "sale_return":      post_sale_return,
        "stock_adjustment": post_stock_adjustment,
    }[kind]
    uid = current_user_id()
    return poster(doc, created_by_id=uid)

def post_stock_adjustment(adj, created_by_id=None):
    """Stock found or lost, valued at the weighted-average cost recorded on the
    adjustment. The other side is an expense account, so a write-off lands in the
    P&L where it belongs."""
    if posted_entry("stock_adjustment", adj.id):
        return None
    value = Decimal(str(adj.cost_value or 0)).quantize(MONEY)
    if not value:
        return None                        # a zero-cost item has no accounting effect
    if adj.direction == "in":
        lines = [{"code": ACC_INVENTORY, "debit": value, "credit": 0},
                 {"code": ACC_STOCK_ADJ, "debit": 0, "credit": value, "memo": adj.adj_type}]
    else:
        lines = [{"code": ACC_STOCK_ADJ, "debit": value, "credit": 0, "memo": adj.adj_type},
                 {"code": ACC_INVENTORY, "debit": 0, "credit": value}]
    return post_entry(entry_date=adj.date, description=f"Stock adjustment #{adj.id}: {adj.adj_type}",
                      reference=f"ADJ-{adj.id}", source_type="stock_adjustment", source_id=adj.id,
                      allow_control=True, created_by_id=created_by_id, lines=lines)

def seed_tax_codes():
    """A single zero-rated code plus one standard rate. Everything else is the
    user's to define — that is the point of the TaxCode/TaxComponent split."""
    if TaxCode.query.count():
        return 0
    tax_in  = get_account(ACC_TAX_INPUT).id
    tax_out = get_account(ACC_TAX_OUTPUT).id
    for name, comps in (
        ("Zero-rated", [("Tax", 0)]),
        ("Standard",   [("Tax", 0)]),   # rate left at 0 — the user sets their country's rate
    ):
        code = TaxCode(name=name)
        db.session.add(code)
        db.session.flush()
        for cname, rate in comps:
            db.session.add(TaxComponent(tax_code_id=code.id, name=cname, rate=rate,
                                        input_account_id=tax_in, output_account_id=tax_out))
    db.session.commit()
    return TaxCode.query.count()

class JournalEntry(db.Model):
    """A balanced double-entry posting. Every financial event in the system —
    manual adjustment, purchase, sale, payment, expense — becomes one of these.

    Entries are immutable once written. A mistake is corrected by posting a
    reversing entry (`reverse_entry`), never by editing or deleting, so the
    audit trail always shows what was recorded and what corrected it.

    `source_type` + `source_id` link the entry back to the document that caused
    it ("purchase", 12). Together they also make posting idempotent: a document
    that already has an entry is never posted twice."""
    __tablename__ = "journal_entry"
    id             = db.Column(db.Integer, primary_key=True)
    entry_date     = db.Column(db.DateTime, nullable=False,
                               default=lambda: now_local(), index=True)
    reference      = db.Column(db.String(50), nullable=True)
    description    = db.Column(db.String(300), nullable=False)
    source_type    = db.Column(db.String(20), nullable=False, default="manual")
    source_id      = db.Column(db.Integer, nullable=True)
    period_id      = db.Column(db.Integer, db.ForeignKey("accounting_period.id"), nullable=True)
    reversal_of_id = db.Column(db.Integer, db.ForeignKey("journal_entry.id"), nullable=True)
    is_reversed    = db.Column(db.Boolean, nullable=False, default=False)
    created_by_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at     = db.Column(db.DateTime, nullable=False,
                               default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    lines          = db.relationship("JournalLine", backref="entry", lazy=True,
                                     cascade="all,delete-orphan")
    period         = db.relationship("AccountingPeriod", lazy=True)
    created_by     = db.relationship("User", lazy=True)
    reversal_of    = db.relationship("JournalEntry", remote_side=[id], lazy=True)

    @property
    def total_debit(self):
        return sum(Decimal(str(l.debit or 0)) for l in self.lines)

    @property
    def total_credit(self):
        return sum(Decimal(str(l.credit or 0)) for l in self.lines)

    @property
    def is_reversal(self):
        return self.reversal_of_id is not None

class JournalLine(db.Model):
    """One side of an entry. Exactly one of debit/credit is positive."""
    __tablename__ = "journal_line"
    id         = db.Column(db.Integer, primary_key=True)
    entry_id   = db.Column(db.Integer, db.ForeignKey("journal_entry.id"), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    debit      = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    credit     = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    memo       = db.Column(db.String(200), nullable=True)
    account    = db.relationship("Account", lazy="joined")

# ── Fixed assets ──────────────────────────────────────────────────────────────
DEPRECIATION_METHODS = ("Straight Line", "Reducing Balance")
ASSET_STATUSES = ("Active", "Fully Depreciated", "Disposed")

class FixedAsset(db.Model):
    """An item of property, plant or equipment. Its cost sits in the GL at
    ACC_FIXED_COST and is written down over its life into ACC_ACCUM_DEP; the
    register below is what tells you *which* asset each of those figures belongs
    to, which the GL alone cannot.

    Straight Line spreads (cost − salvage) evenly over the life. Reducing Balance
    charges `rate_percent` a year on the written-down value, so the charge falls
    each month — the method Pakistani tax depreciation uses."""
    __tablename__ = "fixed_asset"
    id                 = db.Column(db.Integer, primary_key=True)
    name               = db.Column(db.String(120), nullable=False)
    tag                = db.Column(db.String(40), nullable=True)      # asset tag / serial
    acquisition_date   = db.Column(db.DateTime, nullable=False)
    cost               = db.Column(db.Numeric(14, 4), nullable=False)
    salvage_value      = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    method             = db.Column(db.String(20), nullable=False, default="Straight Line")
    useful_life_months = db.Column(db.Integer, nullable=True)         # Straight Line
    rate_percent       = db.Column(db.Numeric(14, 4), nullable=True)  # Reducing Balance, per year
    status             = db.Column(db.String(20), nullable=False, default="Active")
    disposal_date      = db.Column(db.DateTime, nullable=True)
    disposal_proceeds  = db.Column(db.Numeric(14, 4), nullable=True)
    notes              = db.Column(db.String(300), nullable=True)
    charges            = db.relationship("DepreciationCharge", backref="asset", lazy=True,
                                         cascade="all,delete-orphan")

    @property
    def accumulated(self):
        return sum((Decimal(str(c.amount)) for c in self.charges), Decimal("0"))

    @property
    def net_book_value(self):
        return Decimal(str(self.cost)) - self.accumulated

    @property
    def depreciable_base(self):
        """What may be written off in total — never below salvage value."""
        return Decimal(str(self.cost)) - Decimal(str(self.salvage_value or 0))

class DepreciationCharge(db.Model):
    """One month's depreciation on one asset. Unique per (asset, month), so a run
    can never charge the same month twice however often it is clicked."""
    __tablename__ = "depreciation_charge"
    __table_args__ = (db.UniqueConstraint("asset_id", "period_end",
                                          name="uq_depreciation_asset_period"),)
    id         = db.Column(db.Integer, primary_key=True)
    asset_id   = db.Column(db.Integer, db.ForeignKey("fixed_asset.id"), nullable=False)
    period_end = db.Column(db.DateTime, nullable=False)      # last day of the month charged
    amount     = db.Column(db.Numeric(14, 4), nullable=False)
    entry_id   = db.Column(db.Integer, db.ForeignKey("journal_entry.id"), nullable=True)
    entry      = db.relationship("JournalEntry", lazy=True)

def month_end(when):
    """The last moment of `when`'s month — the date a month's depreciation is dated."""
    from calendar import monthrange
    last = monthrange(when.year, when.month)[1]
    return datetime(when.year, when.month, last, 23, 59, 59)

def depreciation_for_month(asset, period_end):
    """This month's charge for one asset, or zero if none is due.

    Nothing is charged before the month it was acquired, after it is disposed, or
    once it is written down to its salvage value. The last charge is clipped to the
    remaining depreciable amount, so accumulated depreciation lands exactly on the
    base and never overshoots it — which is what stops an asset depreciating below
    salvage after enough runs."""
    if asset.status == "Disposed" or asset.acquisition_date > period_end:
        return Decimal("0")

    remaining = asset.depreciable_base - asset.accumulated
    if remaining <= 0:
        return Decimal("0")

    if asset.method == "Reducing Balance":
        yearly = Decimal(str(asset.rate_percent or 0)) / Decimal("100")
        charge = (asset.net_book_value * yearly / Decimal("12")).quantize(MONEY)
    else:                                   # Straight Line
        life = asset.useful_life_months or 0
        if life <= 0:
            return Decimal("0")
        charge = (asset.depreciable_base / Decimal(life)).quantize(MONEY)

    if charge <= 0:
        return Decimal("0")
    return min(charge, remaining).quantize(MONEY)

def post_asset_acquisition(asset, credit_account_id, created_by_id=None):
    """Dr Fixed Assets at Cost / Cr whatever paid for it."""
    if posted_entry("asset", asset.id):
        return None
    cost = Decimal(str(asset.cost)).quantize(MONEY)
    return post_entry(
        entry_date=asset.acquisition_date,
        description=f"Fixed asset acquired: {asset.name}",
        reference=asset.tag or f"FA-{asset.id}",
        source_type="asset", source_id=asset.id, created_by_id=created_by_id,
        lines=[{"account_id": account_for_role("fixed_cost").id,
                "debit": cost, "credit": 0, "memo": asset.name},
               {"account_id": credit_account_id, "debit": 0, "credit": cost}])

def run_depreciation(period_end, created_by_id=None):
    """Charge every eligible asset for the month ending `period_end`, and post the
    lot as one entry: Dr Depreciation / Cr Accumulated Depreciation.

    Idempotent twice over — the month's entry is written once (source_type +
    source_id), and the unique (asset, month) constraint is the backstop."""
    run_id = int(period_end.strftime("%Y%m"))
    if posted_entry("depreciation", run_id):
        raise PostingError(f"Depreciation for {period_end:%B %Y} has already been posted.")

    now = now_local()
    if period_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0) > now:
        raise PostingError(f"{period_end:%B %Y} has not started yet.")

    # Charge the month, but never date the entry in the future. Run mid-month and a
    # month-end date would sit ahead of today, so every report that runs "as of now"
    # — the P&L, the balance sheet — would post the depreciation and then not show it.
    entry_date = min(period_end, now)

    charges = []
    for asset in FixedAsset.query.filter(FixedAsset.status != "Disposed").all():
        if DepreciationCharge.query.filter_by(asset_id=asset.id, period_end=period_end).first():
            continue
        amount = depreciation_for_month(asset, period_end)
        if amount > 0:
            charges.append((asset, amount))

    if not charges:
        raise PostingError(f"Nothing to depreciate for {period_end:%B %Y}.")

    total = sum((amt for _, amt in charges), Decimal("0"))
    entry = post_entry(
        entry_date=entry_date,
        description=f"Depreciation for {period_end:%B %Y}",
        source_type="depreciation", source_id=run_id, created_by_id=created_by_id,
        lines=[{"account_id": account_for_role("depreciation").id, "debit": total, "credit": 0},
               {"account_id": account_for_role("accum_dep").id, "debit": 0, "credit": total}])
    db.session.flush()

    for asset, amount in charges:
        db.session.add(DepreciationCharge(asset_id=asset.id, period_end=period_end,
                                          amount=amount, entry_id=entry.id))
        if asset.accumulated + amount >= asset.depreciable_base:
            asset.status = "Fully Depreciated"
    return entry, total, len(charges)

def unwind_depreciation(entry):
    """A depreciation run's charges live in the fixed-asset register, not the GL.
    Reversing the entry without removing them would leave the register claiming
    depreciation the ledger has just cancelled — the two would drift — and the month
    could never be run again, because its charges would still be sitting there."""
    charges = DepreciationCharge.query.filter_by(entry_id=entry.id).all()
    assets = {ch.asset for ch in charges if ch.asset}
    for ch in charges:
        db.session.delete(ch)
    db.session.flush()
    for asset in assets:
        db.session.refresh(asset)
        if asset.status == "Fully Depreciated" and asset.accumulated < asset.depreciable_base:
            asset.status = "Active"          # it no longer is, now the charge is gone
    return len(charges)

def unwind_asset_disposal(entry):
    """Reversing a disposal has to put the asset back on the register, or the ledger
    would hold it again while the register still said it was sold."""
    asset = db.session.get(FixedAsset, entry.source_id)
    if asset is None:
        return
    asset.status = ("Fully Depreciated"
                    if asset.accumulated >= asset.depreciable_base else "Active")
    asset.disposal_date = None
    asset.disposal_proceeds = None

def unwind_asset_entry(entry):
    """Keep the fixed-asset register in step with a reversed journal entry."""
    if entry.source_type == "depreciation":
        unwind_depreciation(entry)
    elif entry.source_type == "asset_disposal":
        unwind_asset_disposal(entry)
    elif entry.source_type == "asset":
        raise PostingError(
            "An asset's acquisition cannot be reversed from the journal — the register "
            "would still hold an asset the ledger no longer paid for. Dispose of it "
            "instead, from the Fixed Assets page.")

def post_asset_disposal(asset, disposal_date, proceeds, cash_account_id, created_by_id=None):
    """Take the asset off the books: its cost out, the depreciation accumulated
    against it out, the money in, and whatever is left over to gain or loss."""
    if asset.status == "Disposed":
        raise PostingError(f"{asset.name} has already been disposed.")

    cost     = Decimal(str(asset.cost)).quantize(MONEY)
    accum    = asset.accumulated.quantize(MONEY)
    proceeds = Decimal(str(proceeds or 0)).quantize(MONEY)
    gain     = proceeds - (cost - accum)      # positive = gain, negative = loss

    lines = []
    if proceeds > 0:
        lines.append({"account_id": cash_account_id, "debit": proceeds, "credit": 0})
    if accum > 0:
        lines.append({"account_id": account_for_role("accum_dep").id,
                      "debit": accum, "credit": 0})
    lines.append({"account_id": account_for_role("fixed_cost").id,
                  "debit": 0, "credit": cost, "memo": asset.name})
    if gain > 0:
        lines.append({"account_id": account_for_role("disposal_gain").id,
                      "debit": 0, "credit": gain})
    elif gain < 0:
        lines.append({"account_id": account_for_role("disposal_loss").id,
                      "debit": -gain, "credit": 0})

    entry = post_entry(
        entry_date=disposal_date,
        description=f"Disposal of fixed asset: {asset.name}",
        reference=asset.tag or f"FA-{asset.id}",
        source_type="asset_disposal", source_id=asset.id,
        created_by_id=created_by_id, lines=lines)

    asset.status = "Disposed"
    asset.disposal_date = disposal_date
    asset.disposal_proceeds = proceeds
    return entry, gain

# ── Purchase Order ────────────────────────────────────────────────────────────
PO_STATUSES = ("Draft", "Confirmed", "Received", "Cancelled")

class PurchaseOrder(db.Model):
    __tablename__ = "purchase_order"
    id              = db.Column(db.Integer, primary_key=True)
    supplier_id     = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    order_date      = db.Column(db.DateTime, nullable=False,
                                default=lambda: now_local())
    expected_date   = db.Column(db.DateTime, nullable=True)
    status          = db.Column(db.String(20), nullable=False, default="Draft")
    notes           = db.Column(db.String(300), nullable=True)
    converted_purchase_id = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=True)
    supplier        = db.relationship("Supplier", backref="purchase_orders", lazy=True)
    line_items      = db.relationship("PurchaseOrderItem", backref="order", lazy=True,
                                      cascade="all,delete-orphan")

class PurchaseOrderItem(db.Model):
    __tablename__ = "purchase_order_item"
    id              = db.Column(db.Integer, primary_key=True)
    po_id           = db.Column(db.Integer, db.ForeignKey("purchase_order.id"), nullable=False)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    purchase_price  = db.Column(db.Numeric(14, 4), nullable=False)
    discount_type   = db.Column(db.String(10), nullable=False, default="percent")
    discount_value  = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    discount_amount = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_amount      = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    unit_name       = db.Column(db.String(20), nullable=True)
    unit_factor     = db.Column(db.Integer, nullable=False, default=1)
    item            = db.relationship("Item", foreign_keys=[item_id])

    @property
    def display_unit(self):
        return self.unit_name or (self.item.unit if self.item else "Pcs")

# ── Quotation ─────────────────────────────────────────────────────────────────
QUOTATION_STATUSES = ("Draft", "Sent", "Accepted", "Rejected", "Converted")

class Quotation(db.Model):
    __tablename__ = "quotation"
    id              = db.Column(db.Integer, primary_key=True)
    customer_id     = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    quote_date      = db.Column(db.DateTime, nullable=False,
                                default=lambda: now_local())
    valid_until     = db.Column(db.DateTime, nullable=True)
    status          = db.Column(db.String(20), nullable=False, default="Draft")
    notes           = db.Column(db.String(300), nullable=True)
    converted_sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=True)
    customer        = db.relationship("Customer", backref="quotations", lazy=True)
    line_items      = db.relationship("QuotationItem", backref="quotation", lazy=True,
                                      cascade="all,delete-orphan")

class QuotationItem(db.Model):
    __tablename__ = "quotation_item"
    id              = db.Column(db.Integer, primary_key=True)
    quotation_id    = db.Column(db.Integer, db.ForeignKey("quotation.id"), nullable=False)
    item_id         = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity        = db.Column(db.Integer, nullable=False)
    sale_price      = db.Column(db.Numeric(14, 4), nullable=False)
    discount_type   = db.Column(db.String(10), nullable=False, default="percent")
    discount_value  = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    tax_percent     = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    # See PurchaseItem.unit_name/unit_factor. Carried onto the Sale this quotation converts to.
    unit_name       = db.Column(db.String(20), nullable=True)
    unit_factor     = db.Column(db.Integer, nullable=False, default=1)
    item            = db.relationship("Item", foreign_keys=[item_id])

    @property
    def display_unit(self):
        return self.unit_name or (self.item.unit if self.item else "Pcs")

# ── Delivery Challan ──────────────────────────────────────────────────────────
CHALLAN_STATUSES = ("Pending", "Dispatched", "Delivered", "Cancelled")

class DeliveryChallan(db.Model):
    __tablename__ = "delivery_challan"
    id              = db.Column(db.Integer, primary_key=True)
    sale_id         = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False, unique=True)
    challan_date    = db.Column(db.DateTime, nullable=False,
                                default=lambda: now_local())
    dispatch_date   = db.Column(db.DateTime, nullable=True)
    delivery_date   = db.Column(db.DateTime, nullable=True)
    status          = db.Column(db.String(20), nullable=False, default="Pending")
    transport       = db.Column(db.String(100), nullable=True)
    notes           = db.Column(db.String(300), nullable=True)
    sale            = db.relationship("Sale", backref=db.backref("delivery_challan", uselist=False), lazy=True)

OPENING_LEDGER_DATE = datetime(1900, 1, 1)

class SupplierLedgerEntry(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_id         = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    entry_date          = db.Column(db.DateTime, nullable=False, index=True)
    entry_type          = db.Column(db.String(30), nullable=False)
    source_type         = db.Column(db.String(20), nullable=False)
    source_id           = db.Column(db.Integer, nullable=True)
    description         = db.Column(db.String(300), nullable=False)
    debit               = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    credit              = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    balance_after       = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    supplier            = db.relationship("Supplier", backref="ledger_entries", lazy=True)

class CustomerLedgerEntry(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    entry_date          = db.Column(db.DateTime, nullable=False, index=True)
    entry_type          = db.Column(db.String(30), nullable=False)
    source_type         = db.Column(db.String(20), nullable=False)
    source_id           = db.Column(db.Integer, nullable=True)
    description         = db.Column(db.String(300), nullable=False)
    debit               = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    credit              = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    balance_after       = db.Column(db.Numeric(14, 4), nullable=False, default=0.0)
    customer            = db.relationship("Customer", backref="ledger_entries", lazy=True)

class RateLimitHit(db.Model):
    """One row per throttled action attempt (login, password reset). Shared across
    workers so brute-force limits actually hold. See check_rate_limit()."""
    __tablename__ = "rate_limit_hit"
    id         = db.Column(db.Integer, primary_key=True)
    key        = db.Column(db.String(200), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, index=True,
                           default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class AuditLog(db.Model):
    """Who did what, when — an append-only activity trail for accountability.
    user_name is denormalized so the record still makes sense if the user is
    later deleted. Written via record_audit()."""
    __tablename__ = "audit_log"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, index=True,
                           default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    user_id    = db.Column(db.Integer, nullable=True)
    user_name  = db.Column(db.String(100), nullable=False, default="system")
    action     = db.Column(db.String(20), nullable=False)   # create / update / delete / login / restore
    entity     = db.Column(db.String(50), nullable=False)   # Purchase / Sale / Supplier / ...
    entity_id  = db.Column(db.Integer, nullable=True)
    summary    = db.Column(db.String(300), nullable=False, default="")

class ImportLog(db.Model):
    """Track all bulk imports - CSV, Excel, JSON files."""
    __tablename__ = "import_log"
    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, nullable=False, index=True, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    user_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    import_type     = db.Column(db.String(50), nullable=False)  # items, customers, suppliers, purchases, sales
    file_name       = db.Column(db.String(255), nullable=False)
    file_type       = db.Column(db.String(10), nullable=False)  # csv, xlsx, json
    total_records   = db.Column(db.Integer, nullable=False, default=0)
    successful      = db.Column(db.Integer, nullable=False, default=0)
    failed          = db.Column(db.Integer, nullable=False, default=0)
    status          = db.Column(db.String(20), nullable=False, default="pending")  # pending, processing, completed, failed
    errors          = db.Column(db.Text, nullable=True)  # JSON with error details
    user            = db.relationship('User', backref='imports')


# Helper functions for timezone and ledger management

def _get_app_tz():
    """Get the application timezone from Flask config, or use UTC if outside app context."""
    try:
        tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
        return ZoneInfo(tz_name)
    except (RuntimeError, Exception):
        # Outside app context
        return ZoneInfo("UTC")

def _get_fiscal_year_start_month():
    """Get FISCAL_YEAR_START_MONTH from app module (for tests), then app.config, then default to 1."""
    # First try app module (for test compatibility - tests monkeypatch this)
    try:
        import app as app_module
        return getattr(app_module, "FISCAL_YEAR_START_MONTH", None) or 1
    except (ImportError, AttributeError):
        pass

    # Then try app.config (when inside app context, e.g., during requests)
    try:
        return current_app.config.get("FISCAL_YEAR_START_MONTH", 1)
    except (RuntimeError, Exception):
        pass

    return 1

_UTC = ZoneInfo("UTC")

def to_local(dt):
    """A stored datetime (assumed UTC; naive or aware) -> aware datetime in APP_TZ."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_get_app_tz())

def now_local():
    """What time it is *for this business*, naive, in APP_TZ.

    This — not datetime.now() — is the clock the app runs on. datetime.now() reads the
    machine's clock, and the machine is a server in someone else's data centre: on Render
    it is UTC, five hours behind Karachi. Every business date derived from it is therefore
    the server's idea of today, not the user's.

    That is not a cosmetic difference. An invoice entered at 2am in Karachi lands on
    yesterday's date. A month-end entry posted on the 1st lands in the previous month. And
    a document reversed in the morning is stamped five hours in the past, so a report run
    "as of now" shows the sale and not the reversal — the cancelled invoice goes on earning
    profit that was never made.

    Timestamps are different: when a row was *saved* is a moment in time, and those stay in
    UTC and are converted for display (to_local, the localdt filter). The rule is the same
    one the bizdate filter draws: a business date is a date, not an instant.
    """
    return datetime.now(_get_app_tz()).replace(tzinfo=None)

def recalculate_supplier_ledger(supplier_id):
    """Recalculate running balance for all supplier ledger entries."""
    entries = (
        SupplierLedgerEntry.query.filter_by(supplier_id=supplier_id)
        .order_by(SupplierLedgerEntry.entry_date.asc(), SupplierLedgerEntry.id.asc())
        .all()
    )
    balance = 0.0
    for entry in entries:
        balance += float(entry.credit) - float(entry.debit)
        entry.balance_after = balance

def recalculate_customer_ledger(customer_id):
    """Recalculate running balance for all customer ledger entries."""
    entries = (
        CustomerLedgerEntry.query.filter_by(customer_id=customer_id)
        .order_by(CustomerLedgerEntry.entry_date.asc(), CustomerLedgerEntry.id.asc())
        .all()
    )
    balance = 0.0
    for entry in entries:
        balance += float(entry.debit) - float(entry.credit)
        entry.balance_after = balance

def remove_supplier_ledger_entry(source_type, source_id):
    """Remove a supplier ledger entry and return the supplier_id for ledger recalculation."""
    entry = SupplierLedgerEntry.query.filter_by(source_type=source_type, source_id=source_id).first()
    supplier_id = entry.supplier_id if entry else None
    if entry:
        db.session.delete(entry)
    return supplier_id

def remove_customer_ledger_entry(source_type, source_id):
    """Remove a customer ledger entry and return the customer_id for ledger recalculation."""
    entry = CustomerLedgerEntry.query.filter_by(source_type=source_type, source_id=source_id).first()
    customer_id = entry.customer_id if entry else None
    if entry:
        db.session.delete(entry)
    return customer_id

def record_audit(action, entity, entity_id=None, summary=""):
    """Write an audit entry in its own transaction. Called AFTER the business
    change has committed, so a failure here can never roll back or break the real
    operation — auditing is best-effort by design."""
    from flask_login import current_user
    try:
        if current_user and current_user.is_authenticated:
            uid, uname = current_user.id, current_user.name
        else:
            uid, uname = None, "system"
        db.session.add(AuditLog(action=action, entity=entity, entity_id=entity_id,
                                summary=(summary or "")[:300], user_id=uid, user_name=uname))
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Log but don't raise - auditing is best-effort

def calc_discount_tax(gross, discount_type, discount_value, tax_percent):
    """Returns (discount_amt, tax_amt, net_total). discount_type: 'percent' or 'fixed'."""
    gross = float(gross or 0)          # tolerate Decimal/str inputs
    dv = float(discount_value or 0)
    tp = float(tax_percent or 0)
    if discount_type == "fixed":
        disc = min(dv, gross)
    else:
        disc = gross * dv / 100
    taxable = gross - disc
    tax = taxable * tp / 100
    return round(disc, 4), round(tax, 4), round(taxable + tax, 4)


__all__ = [
    'to_local',
    'now_local',
    'recalculate_supplier_ledger',
    'recalculate_customer_ledger',
    'remove_supplier_ledger_entry',
    'remove_customer_ledger_entry',
    'record_audit',
    'calc_discount_tax',
    'ACCOUNT_TYPES',
    'ACC_AP',
    'ACC_AR',
    'ACC_ASSETS_GROUP',
    'ACC_BANK_GROUP',
    'ACC_CAPITAL',
    'ACC_CASH_IN_HAND',
    'ACC_COGS',
    'ACC_DRAWINGS',
    'ACC_EXPENSES',
    'ACC_INVENTORY',
    'ACC_OPENING_EQUITY',
    'ACC_RETAINED',
    'ACC_SALES',
    'ACC_SALES_RETURNS',
    'ACC_STOCK_ADJ',
    'ACC_TAX_INPUT',
    'ACC_TAX_OUTPUT',
    'ADJUSTMENT_DIRECTIONS',
    'ADJUSTMENT_TYPES',
    'ASSET_STATUSES',
    'CASH_FLOW_SECTIONS',
    'CHALLAN_STATUSES',
    'CHART_OF_ACCOUNTS',
    'DEBIT_NATURED',
    'DEPRECIATION_METHODS',
    'DOCUMENT_LABELS',
    'DOCUMENT_PREFIXES',
    'FIXED_ASSET_ACCOUNTS',
    'FIXED_ASSET_ROLES',
    'MONEY',
    'OPENING_LEDGER_DATE',
    'PO_STATUSES',
    'QUOTATION_STATUSES',
    'SYSTEM_ACCOUNT_CODES',
    
    'PAYMENT_METHODS',
    'ITEM_UNITS',
    'User',
    'Supplier',
    'Customer',
    'Category',
    'Item',
    'ItemUnit',
    'Purchase',
    'Sale',
    'PurchaseItem',
    'SaleItem',
    'PosHold',
    'SupplierPayment',
    'CustomerPayment',
    'PurchaseReturn',
    'SaleReturn',
    'StockAdjustment',
    'ExpenseCategory',
    'Expense',
    'FinancialAccount',
    'Account',
    'TaxCode',
    'TaxComponent',
    'FiscalYear',
    'AccountingPeriod',
    'DocumentSequence',
    'PostingError',
    'JournalEntry',
    'JournalLine',
    'FixedAsset',
    'DepreciationCharge',
    'PurchaseOrder',
    'PurchaseOrderItem',
    'Quotation',
    'QuotationItem',
    'DeliveryChallan',
    'SupplierLedgerEntry',
    'CustomerLedgerEntry',
    'RateLimitHit',
    'AuditLog',
    'ImportLog',
    'item_unit_choices',
    'item_units_for_js',
    'purchase_item_options_for_js',
    'sale_item_options_for_js',
    'purchase_return_options_for_js',
    'sale_return_options_for_js',
    'resolve_item_unit',
    'line_base_qty',
    'save_item_units',
    'new_account_method_token',
    'get_account',
    'account_has_activity',
    'expense_gl_accounts',
    'parse_expense_gl_account',
    'seed_chart_of_accounts',
    'account_for_role',
    'account_is_system',
    '_free_code',
    'backfill_account_openings',
    'realign_backdated_reversals',
    'backfill_document_numbers',
    'seed_fixed_asset_accounts',
    'next_child_code',
    'ensure_gl_account_for_financial',
    'seed_financial_account_links',
    'fiscal_year_bounds',
    'fiscal_years_that_disagree_with_the_setting',
    'seed_fiscal_year',
    'current_user_id',
    '_money',
    'find_period',
    'document_year',
    'allocate_document_number',
    'post_entry',
    'reverse_entry',
    'postable_accounts',
    'posted_entry',
    'assert_not_posted',
    'assert_not_numbered',
    '_cash_gl',
    '_resolve_financial_account',
    '_doc_lines',
    '_cogs_of',
    'post_purchase',
    'post_sale',
    'post_supplier_payment',
    'post_customer_receipt',
    'post_expense',
    'post_purchase_return',
    'post_sale_return',
    'gl_balances',
    'natural_balance',
    'accounts_by_type',
    'gl_profit',
    'retained_earnings_to_date',
    'account_cf_section',
    'parse_cf_section',
    'cash_gl_account_ids',
    'cash_balance_as_of',
    'cash_flow_statement',
    'close_fiscal_year',
    '_unwind_stock_and_subledger',
    'reverse_document',
    'item_add_stock',
    'item_remove_stock',
    'sale_line_cost',
    '_repost_opening',
    '_opening_date',
    'post_supplier_opening',
    'post_customer_opening',
    'post_account_opening',
    'post_item_opening',
    'post_document',
    'post_stock_adjustment',
    'seed_tax_codes',
    'month_end',
    'depreciation_for_month',
    'post_asset_acquisition',
    'run_depreciation',
    'unwind_depreciation',
    'unwind_asset_disposal',
    'unwind_asset_entry',
    'post_asset_disposal',
]
