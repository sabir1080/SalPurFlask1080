"""Dashboard and navigation routes."""

from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import current_user

from salpurflask.models import (
    Item, Purchase, Sale, PurchaseItem, SaleItem, PurchaseReturn, SaleReturn
)
from salpurflask.extensions import db

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route("/")
def index():
    if current_user.is_authenticated:
        return render_template("index.html")
    return redirect(url_for("auth.signin"))

@dashboard_bp.route("/about")
def about():
    return render_template("about2.html")

@dashboard_bp.route("/manual")
def manual():
    """The user manual. Public, like About — someone evaluating the system should be able
    to read what it does and what it refuses to do before they have an account."""
    return render_template("manual.html")

@dashboard_bp.route('/dashboard')
def dashboard():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.signin"))
    if not current_user.verified:
        flash(f"Please verify {current_user.email} to access this page.", "danger")
        return redirect(url_for("auth.signin"))
    if current_user.role not in ("admin", "manager"):
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("purchase"))

    # Safe defaults - all values start at 0
    context = {
        'items': [],
        'purchases': [],
        'sales': [],
        'total_purchase_cost': 0.0,
        'total_sale_revenue': 0.0,
        'total_gross_profit': 0.0,
        'total_purchase_returns': 0.0,
        'total_sale_returns': 0.0,
        'low_stock_count': 0,
        'total_payable': 0.0,
        'total_paid_suppliers': 0.0,
        'total_receivable': 0.0,
        'total_received_customers': 0.0,
        'total_payable_balance': 0.0,
        'total_receivable_balance': 0.0,
        'monthly_sales': [],
        'monthly_purchases': [],
    }

    # Load items (simple query)
    try:
        context['items'] = Item.query.all()
    except Exception:
        pass

    # Load recent purchases
    try:
        context['purchases'] = Purchase.query.order_by(Purchase.date.desc()).limit(5).all()
    except Exception:
        pass

    # Load recent sales
    try:
        context['sales'] = Sale.query.order_by(Sale.date.desc()).limit(5).all()
    except Exception:
        pass

    # Simple aggregations - no complex expressions
    try:
        val = db.session.query(db.func.sum(PurchaseItem.amount)).scalar()
        context['total_purchase_cost'] = float(val) if val else 0.0
    except Exception:
        pass

    try:
        val = db.session.query(db.func.sum(SaleItem.amount)).scalar()
        context['total_sale_revenue'] = float(val) if val else 0.0
    except Exception:
        pass

    try:
        val = db.session.query(db.func.sum(PurchaseReturn.quantity * PurchaseReturn.return_price)).scalar()
        context['total_purchase_returns'] = float(val) if val else 0.0
    except Exception:
        pass

    try:
        val = db.session.query(db.func.sum(SaleReturn.quantity * SaleReturn.return_price)).scalar()
        context['total_sale_returns'] = float(val) if val else 0.0
    except Exception:
        pass

    try:
        context['low_stock_count'] = Item.query.filter(Item.stock <= Item.reorder_level).count()
    except Exception:
        pass

    # Ledger balances - wrapped individually
    try:
        from app import get_total_payable
        context['total_payable'] = float(get_total_payable()) if get_total_payable else 0.0
    except Exception:
        pass

    try:
        from app import get_total_paid_suppliers
        context['total_paid_suppliers'] = float(get_total_paid_suppliers()) if get_total_paid_suppliers else 0.0
    except Exception:
        pass

    try:
        from app import total_supplier_ledger_balance
        context['total_payable_balance'] = float(total_supplier_ledger_balance()) if total_supplier_ledger_balance else 0.0
    except Exception:
        pass

    try:
        from app import get_total_receivable
        context['total_receivable'] = float(get_total_receivable()) if get_total_receivable else 0.0
    except Exception:
        pass

    try:
        from app import get_total_received_customers
        context['total_received_customers'] = float(get_total_received_customers()) if get_total_received_customers else 0.0
    except Exception:
        pass

    try:
        from app import total_customer_ledger_balance
        context['total_receivable_balance'] = float(total_customer_ledger_balance()) if total_customer_ledger_balance else 0.0
    except Exception:
        pass

    # Monthly sales and profit data for chart
    try:
        from sqlalchemy import func, text
        from datetime import datetime, timedelta

        # Get last 12 months of sales data
        monthly_data = db.session.query(
            func.strftime('%Y-%m', Sale.date).label('month'),
            func.sum(SaleItem.amount).label('sale_amt'),
            func.sum(SaleItem.amount - (SaleItem.quantity * SaleItem.cost_price)).label('profit_amt')
        ).join(SaleItem, Sale.id == SaleItem.sale_id).group_by(
            func.strftime('%Y-%m', Sale.date)
        ).order_by(
            func.strftime('%Y-%m', Sale.date)
        ).all()

        context['monthly_sales'] = [
            {
                'month': row.month or 'Unknown',
                'sale_amt': float(row.sale_amt) if row.sale_amt else 0.0,
                'profit_amt': float(row.profit_amt) if row.profit_amt else 0.0
            }
            for row in monthly_data
        ]
    except Exception:
        context['monthly_sales'] = []

    return render_template('dashboard.html', **context)
