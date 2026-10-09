"""Business Configuration Models - Metadata-driven configurable ERP"""

from salpurflask.extensions import db
from datetime import datetime
import json


class BusinessCategory(db.Model):
    """Business categories (Medical Store, Grocery, Garments, etc)"""
    __tablename__ = 'business_category'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.Text)
    icon = db.Column(db.String(50), default='bi-box')
    is_enabled = db.Column(db.Boolean, default=False, index=True)
    priority = db.Column(db.Integer, default=0)
    color = db.Column(db.String(20), default='primary')
    config_data = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    fields = db.relationship('ProductField', backref='category', cascade='all, delete-orphan', lazy='joined')
    menu_items = db.relationship('CategoryMenuItem', backref='category', cascade='all, delete-orphan')
    reports = db.relationship('CategoryReport', backref='category', cascade='all, delete-orphan')
    validations = db.relationship('CategoryValidation', backref='category', cascade='all, delete-orphan')

    # Stock tracking settings. Stored in config_data (an existing JSON column), so no schema change and
    # no data is rewritten: a category that has never been configured has no key and falls back to what
    # its own field set already says -- a category with a Batch No. field tracks batches, one with a
    # (stock) Expiry Date field tracks expiry. Warranty Expiry Date is a different field name and is
    # deliberately not matched. An explicit True/False saved by an admin always wins.
    def _has_active_field(self, name):
        return any(f.field_name == name and f.is_active for f in self.fields)

    def _tracking_setting(self, key, field_name, in_use):
        value = (self.config_data or {}).get(key)
        if value is not None:
            return bool(value)
        # Not configured: on if the category's own fields say so, or if it is already being used that way
        # (so a category whose items are batch-tracked today keeps working after this setting existed).
        return self._has_active_field(field_name) or in_use()

    @property
    def batch_tracking_enabled(self):
        """May items of this category be batch-tracked (Batch No / opening batches)?"""
        from salpurflask.models.models import Item
        return self._tracking_setting(
            "batch_tracking", "batch_no",
            lambda: self.id is not None and db.session.query(Item.id).filter(
                Item.business_category_id == self.id, Item.batch_tracked.is_(True)).first() is not None)

    @property
    def stock_expiry_enabled(self):
        """Do stock batches of this category carry an expiry date?"""
        from salpurflask.models.models import Item, Batch
        # A category whose items are already batch-tracked keeps expiry available too (expiry lives on the
        # batch), so existing batch users see no change; only a category with neither is "off" by default.
        return self._tracking_setting(
            "stock_expiry", "expiry_date",
            lambda: self.id is not None and (
                db.session.query(Batch.id).join(Item, Item.id == Batch.item_id).filter(
                    Item.business_category_id == self.id, Batch.expiry_date.isnot(None)).first() is not None
                or self.batch_tracking_enabled_in_use()))

    def batch_tracking_enabled_in_use(self):
        """True when at least one item of this category is already batch-tracked."""
        from salpurflask.models.models import Item
        return self.id is not None and db.session.query(Item.id).filter(
            Item.business_category_id == self.id, Item.batch_tracked.is_(True)).first() is not None

    @property
    def disabled_stock_field_names(self):
        """Category field names that are switched off: neither validated nor saved for this category."""
        names = []
        if not self.batch_tracking_enabled:
            names.append("batch_no")
        if not self.stock_expiry_enabled:
            names.append("expiry_date")
        return tuple(names)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'is_enabled': self.is_enabled,
            'icon': self.icon,
            'color': self.color,
        }

    def __repr__(self):
        return f"<BusinessCategory {self.name}>"


class ProductField(db.Model):
    """Dynamic product fields per category"""
    __tablename__ = 'product_field'

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('business_category.id'), nullable=False)
    field_name = db.Column(db.String(100), nullable=False)
    field_label = db.Column(db.String(100), nullable=False)
    field_type = db.Column(db.String(20), nullable=False)  # text, date, number, select, boolean
    is_required = db.Column(db.Boolean, default=False)
    is_searchable = db.Column(db.Boolean, default=False)
    is_filterable = db.Column(db.Boolean, default=False)
    placeholder = db.Column(db.String(200))
    help_text = db.Column(db.String(300))
    validation_pattern = db.Column(db.String(500))
    min_value = db.Column(db.Numeric)
    max_value = db.Column(db.Numeric)
    options = db.Column(db.JSON)
    default_value = db.Column(db.String(200))
    position = db.Column(db.Integer, default=0)
    tab_name = db.Column(db.String(50), default='General')
    # Disables the field without deleting it — deleting would cascade and
    # destroy any ProductCategoryData history already recorded against it
    # (see the model's cascade='all, delete-orphan' on BusinessCategory.fields
    # and this table's own relationship). A disabled field is simply omitted
    # from the item form/API going forward; existing Items and their stored
    # values are never touched by toggling this.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # True only for a field seeded by ensure_default_product_fields() (system
    # master data, the same tier as the 26 default BusinessCategories). Never
    # inferred from the parent category — a user-added custom field on a
    # default category is explicitly False, so "is this field mine or the
    # system's" is always a direct, stored fact rather than a derived guess.
    is_system_default = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('category_id', 'field_name'),)

    def to_dict(self):
        return {
            'id': self.id,
            'field_name': self.field_name,
            'field_label': self.field_label,
            'field_type': self.field_type,
            'is_required': self.is_required,
            'is_searchable': self.is_searchable,
            'is_filterable': self.is_filterable,
            'tab_name': self.tab_name,
            'placeholder': self.placeholder,
            'options': self.options,
            'help_text': self.help_text,
            'is_active': self.is_active,
            'is_system_default': self.is_system_default,
        }


class ProductCategoryData(db.Model):
    """Store category-specific product data"""
    __tablename__ = 'product_category_data'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('item.id'), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey('business_category.id'), nullable=False)
    field_name = db.Column(db.String(100), nullable=False)
    field_value = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('product_id', 'category_id', 'field_name'),)


class CategoryMenuItem(db.Model):
    """Dynamic menu items per category"""
    __tablename__ = 'category_menu_item'

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('business_category.id'), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    route = db.Column(db.String(100))
    icon = db.Column(db.String(50), default='bi-box')
    parent_id = db.Column(db.Integer, db.ForeignKey('category_menu_item.id'))
    position = db.Column(db.Integer, default=0)
    permissions = db.Column(db.JSON)  # ["admin", "manager"]
    children = db.relationship('CategoryMenuItem', remote_side=[parent_id])


class CategoryReport(db.Model):
    """Dynamic reports per category"""
    __tablename__ = 'category_report'

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('business_category.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    report_type = db.Column(db.String(20))  # inventory, sales, purchase
    query_config = db.Column(db.JSON)
    columns = db.Column(db.JSON)
    filters = db.Column(db.JSON)

    __table_args__ = (db.UniqueConstraint('category_id', 'slug'),)


class CategoryValidation(db.Model):
    """Business rules per category"""
    __tablename__ = 'category_validation'

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('business_category.id'), nullable=False)
    rule_name = db.Column(db.String(100), nullable=False)
    rule_type = db.Column(db.String(50))  # field_required, expiry_check, batch_check
    field_name = db.Column(db.String(100))
    condition = db.Column(db.JSON)
    error_message = db.Column(db.String(300), nullable=False)
    is_active = db.Column(db.Boolean, default=True)


class ConfigurationSnapshot(db.Model):
    """Track configuration changes"""
    __tablename__ = 'configuration_snapshot'

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    enabled_categories = db.Column(db.JSON)
    configuration_hash = db.Column(db.String(64))
    changed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    description = db.Column(db.Text)
