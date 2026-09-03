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
