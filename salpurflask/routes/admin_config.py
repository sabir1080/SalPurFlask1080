"""Business Configuration Admin Routes"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from salpurflask.extensions import db
from salpurflask.models.business_config import (
    BusinessCategory, ProductField, ProductCategoryData
)
from salpurflask.services.config_service import ConfigurationService
from sqlalchemy import func

config_bp = Blueprint('admin_config', __name__, url_prefix='/admin/config')


@config_bp.route('/')
@login_required
def index():
    """Configuration dashboard"""
    if current_user.role != 'admin':
        flash("Access denied. Admin only.", "danger")
        return redirect(url_for('dashboard.index'))

    categories = BusinessCategory.query.order_by(BusinessCategory.priority).all()
    stats = ConfigurationService.get_category_stats()

    return render_template(
        'admin/business_config.html',
        categories=categories,
        stats=stats
    )


@config_bp.route('/category/create', methods=['POST'])
@login_required
def create_category():
    """Create new business category"""
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()

    if not name:
        return jsonify({'error': 'Category name is required'}), 400

    # Check if category already exists
    existing = BusinessCategory.query.filter_by(name=name).first()
    if existing:
        return jsonify({'error': 'Category already exists'}), 400

    try:
        # Generate slug from name
        import re
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

        # Get next priority
        max_priority = db.session.query(func.max(BusinessCategory.priority)).scalar() or 0

        new_category = BusinessCategory(
            name=name,
            slug=slug,
            description=description,
            priority=max_priority + 1,
            is_enabled=True
        )
        db.session.add(new_category)
        db.session.commit()

        return jsonify({
            'success': True,
            'category': {
                'id': new_category.id,
                'name': new_category.name,
                'slug': new_category.slug,
                'description': new_category.description
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400


@config_bp.route('/category/<int:category_id>', methods=['GET', 'POST'])
@login_required
def manage_category(category_id):
    """Manage fields for a category"""
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    cat = BusinessCategory.query.get(category_id)
    if not cat:
        return jsonify({'error': 'Category not found'}), 404

    if request.method == 'POST':
        data = request.json
        cat.name = data.get('name', cat.name)
        cat.description = data.get('description', cat.description)
        db.session.commit()
        return jsonify({'success': True, 'category': cat.to_dict()})

    fields = ConfigurationService.get_category_fields_by_id(category_id)
    return render_template(
        'admin/category_fields.html',
        category=cat,
        fields=fields
    )


@config_bp.route('/category/<int:category_id>/toggle', methods=['POST'])
@login_required
def toggle_category(category_id):
    """Enable/disable a category"""
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    cat = BusinessCategory.query.get(category_id)
    if cat:
        cat.is_enabled = not cat.is_enabled
        db.session.commit()

        # Log change
        from salpurflask.models.business_config import ConfigurationSnapshot
        snapshot = ConfigurationSnapshot(
            enabled_categories=[c.slug for c in ConfigurationService.get_enabled_categories()],
            changed_by=current_user.id,
            description=f"{'Enabled' if cat.is_enabled else 'Disabled'} category: {cat.name}"
        )
        db.session.add(snapshot)
        db.session.commit()

        return jsonify({'success': True, 'is_enabled': cat.is_enabled})

    return jsonify({'error': 'Category not found'}), 404


@config_bp.route('/field/add/<int:category_id>', methods=['POST'])
@login_required
def add_field(category_id):
    """Add field to category"""
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json
    try:
        field = ConfigurationService.add_product_field(category_id, data)
        return jsonify({'success': True, 'field': field.to_dict()})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@config_bp.route('/field/<int:field_id>', methods=['PUT', 'DELETE'])
@login_required
def manage_field(field_id):
    """Update or delete field"""
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    field = ProductField.query.get(field_id)
    if not field:
        return jsonify({'error': 'Field not found'}), 404

    if request.method == 'PUT':
        data = request.json
        try:
            ConfigurationService.update_product_field(field_id, data)
            field = ProductField.query.get(field_id)
            return jsonify({'success': True, 'field': field.to_dict()})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    elif request.method == 'DELETE':
        try:
            ConfigurationService.delete_product_field(field_id)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    return jsonify({'error': 'Invalid method'}), 400


@config_bp.route('/api/category/<int:category_id>/fields', methods=['GET'])
@login_required
def get_category_fields_by_id(category_id):
    """API endpoint to get category fields by ID"""
    fields = ConfigurationService.get_category_fields_by_id(category_id)
    return jsonify([f.to_dict() for f in fields])


@config_bp.route('/api/category/<slug>/fields', methods=['GET'])
@login_required
def get_category_fields(slug):
    """API endpoint to get category fields by slug"""
    fields = ConfigurationService.get_category_fields(slug)
    return jsonify([f.to_dict() for f in fields])


@config_bp.route('/api/enabled-categories', methods=['GET'])
@login_required
def get_enabled_categories():
    """API endpoint to get enabled categories"""
    categories = ConfigurationService.get_enabled_categories()
    return jsonify({
        'success': True,
        'categories': [c.to_dict() for c in categories]
    })
