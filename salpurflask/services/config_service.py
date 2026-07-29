"""Business Configuration Service"""

from salpurflask.extensions import db
from salpurflask.models.business_config import (
    BusinessCategory, ProductField, ProductCategoryData,
    CategoryMenuItem, CategoryValidation, ConfigurationSnapshot
)
from flask_login import current_user
import re


class ConfigurationService:
    """Manages business configuration"""

    @staticmethod
    def get_enabled_categories():
        """Get all enabled categories"""
        return BusinessCategory.query.filter_by(is_enabled=True).order_by(BusinessCategory.priority).all()

    @staticmethod
    def get_all_categories():
        """Get all categories"""
        return BusinessCategory.query.order_by(BusinessCategory.priority).all()

    @staticmethod
    def enable_category(category_slug):
        """Enable a category"""
        cat = BusinessCategory.query.filter_by(slug=category_slug).first()
        if cat:
            cat.is_enabled = True
            db.session.commit()
            ConfigurationService._log_change(f"Enabled category: {cat.name}")
            return cat
        return None

    @staticmethod
    def disable_category(category_slug):
        """Disable a category (keeps data intact)"""
        cat = BusinessCategory.query.filter_by(slug=category_slug).first()
        if cat:
            cat.is_enabled = False
            db.session.commit()
            ConfigurationService._log_change(f"Disabled category: {cat.name}")
            return cat
        return None

    @staticmethod
    def get_category_by_slug(slug):
        """Get category by slug"""
        return BusinessCategory.query.filter_by(slug=slug).first()

    @staticmethod
    def get_category_fields(category_slug):
        """Get all fields for a category, sorted by position"""
        cat = BusinessCategory.query.filter_by(slug=category_slug).first()
        if cat:
            return sorted(cat.fields, key=lambda x: x.position)
        return []

    @staticmethod
    def get_category_fields_by_id(category_id):
        """Get fields by category ID"""
        return ProductField.query.filter_by(category_id=category_id).order_by(ProductField.position).all()

    @staticmethod
    def add_product_field(category_id, field_data):
        """Add dynamic field to category"""
        field = ProductField(
            category_id=category_id,
            field_name=field_data.get('field_name'),
            field_label=field_data.get('field_label'),
            field_type=field_data.get('field_type', 'text'),
            is_required=field_data.get('is_required', False),
            is_searchable=field_data.get('is_searchable', False),
            placeholder=field_data.get('placeholder', ''),
            help_text=field_data.get('help_text', ''),
            options=field_data.get('options'),
            tab_name=field_data.get('tab_name', 'General'),
            position=field_data.get('position', 0),
        )
        db.session.add(field)
        db.session.commit()
        return field

    @staticmethod
    def update_product_field(field_id, field_data):
        """Update product field"""
        field = ProductField.query.get(field_id)
        if field:
            field.field_label = field_data.get('field_label', field.field_label)
            field.is_required = field_data.get('is_required', field.is_required)
            field.is_searchable = field_data.get('is_searchable', field.is_searchable)
            field.options = field_data.get('options', field.options)
            field.help_text = field_data.get('help_text', field.help_text)
            field.position = field_data.get('position', field.position)
            db.session.commit()
        return field

    @staticmethod
    def delete_product_field(field_id):
        """Delete product field"""
        field = ProductField.query.get(field_id)
        if field:
            db.session.delete(field)
            db.session.commit()
            return True
        return False

    @staticmethod
    def save_product_category_data(product_id, category_id, field_data):
        """Save category-specific product data"""
        for field_name, field_value in field_data.items():
            if field_value is None or field_value == '':
                continue

            data = ProductCategoryData.query.filter_by(
                product_id=product_id,
                category_id=category_id,
                field_name=field_name
            ).first()

            if data:
                data.field_value = field_value
            else:
                data = ProductCategoryData(
                    product_id=product_id,
                    category_id=category_id,
                    field_name=field_name,
                    field_value=field_value
                )
                db.session.add(data)

        db.session.commit()

    @staticmethod
    def get_product_category_data(product_id, category_id):
        """Get category-specific data for a product"""
        data = ProductCategoryData.query.filter_by(
            product_id=product_id,
            category_id=category_id
        ).all()

        result = {}
        for d in data:
            result[d.field_name] = d.field_value
        return result

    @staticmethod
    def validate_product_data(category_slug, form_data):
        """Validate product data against category rules"""
        errors = {}
        fields = ConfigurationService.get_category_fields(category_slug)

        for field in fields:
            value = form_data.get(field.field_name)

            # Check required
            if field.is_required and (value is None or value == ''):
                errors[field.field_name] = f"{field.field_label} is required"
                continue

            if not value:
                continue

            # Check type
            if field.field_type == 'number':
                try:
                    float(value)
                except (ValueError, TypeError):
                    errors[field.field_name] = "Must be a valid number"

            elif field.field_type == 'date':
                try:
                    from datetime import datetime
                    datetime.strptime(str(value), '%Y-%m-%d')
                except ValueError:
                    errors[field.field_name] = "Invalid date format (YYYY-MM-DD)"

            # Check pattern
            if field.validation_pattern and value:
                if not re.match(field.validation_pattern, str(value)):
                    errors[field.field_name] = "Invalid format"

            # Check min/max
            if field.field_type == 'number' and value:
                try:
                    num = float(value)
                    if field.min_value and num < float(field.min_value):
                        errors[field.field_name] = f"Must be at least {field.min_value}"
                    if field.max_value and num > float(field.max_value):
                        errors[field.field_name] = f"Must be at most {field.max_value}"
                except (ValueError, TypeError):
                    pass

        return errors

    @staticmethod
    def _log_change(description):
        """Log configuration change"""
        try:
            enabled = [c.slug for c in ConfigurationService.get_enabled_categories()]
            snapshot = ConfigurationSnapshot(
                enabled_categories=enabled,
                changed_by=current_user.id if current_user else None,
                description=description
            )
            db.session.add(snapshot)
            db.session.commit()
        except:
            pass

    @staticmethod
    def get_category_stats():
        """Get statistics about categories"""
        from salpurflask.models import Item

        total_categories = BusinessCategory.query.count()
        enabled_categories = BusinessCategory.query.filter_by(is_enabled=True).count()
        total_products = Item.query.count()

        return {
            'total_categories': total_categories,
            'enabled_categories': enabled_categories,
            'total_products': total_products,
        }
