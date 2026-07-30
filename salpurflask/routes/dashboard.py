"""Dashboard and navigation routes."""

from flask import Blueprint, render_template, redirect, url_for
from flask_login import current_user

from salpurflask.models import (
    Item, Purchase, Sale, PurchaseItem, SaleItem, PurchaseReturn, SaleReturn
)
from salpurflask.extensions import db
from sqlalchemy import func

def _get_dashboard_helpers():
    """Delayed import to avoid circular dependency."""
    from app import (
        get_total_payable, get_total_paid_suppliers,
        total_supplier_ledger_balance, sql_date_fmt,
        get_total_receivable, get_total_received_customers, total_customer_ledger_balance
    )
    return (get_total_payable, get_total_paid_suppliers, total_supplier_ledger_balance,
            sql_date_fmt, get_total_receivable, get_total_received_customers,
            total_customer_ledger_balance)

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
    from flask import flash, abort
    if not current_user.is_authenticated:
        return redirect(url_for("auth.signin"))
    if not current_user.verified:
        flash(f"Please verify {current_user.email} to access this page.", "danger")
        return redirect(url_for("auth.signin"))
    if current_user.role not in ("admin", "manager"):
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("purchase"))
    (get_total_payable, get_total_paid_suppliers, total_supplier_ledger_balance,
     sql_date_fmt, get_total_receivable, get_total_received_customers,
     total_customer_ledger_balance) = _get_dashboard_helpers()
    items = Item.query.all()
    purchases = Purchase.query.order_by(Purchase.date.desc()).limit(5).all()
    sales = Sale.query.order_by(Sale.date.desc()).limit(5).all()
    total_purchase_cost = 0.0
    total_sale_revenue = 0.0
    total_gross_profit = 0.0
    _profit_expr = None

    try:
        total_purchase_cost = db.session.query(func.sum(PurchaseItem.amount)).scalar() or 0.0
    except Exception:
        total_purchase_cost = 0.0

    try:
        total_sale_revenue = db.session.query(func.sum(SaleItem.amount)).scalar() or 0.0
    except Exception:
        total_sale_revenue = 0.0

    try:
        _profit_expr = SaleItem.quantity * SaleItem.sale_price - SaleItem.discount_amount - SaleItem.quantity * SaleItem.unit_factor * SaleItem.cost_price
        total_gross_profit = db.session.query(func.sum(_profit_expr)).scalar() or 0.0
    except Exception:
        total_gross_profit = 0.0
        _profit_expr = None

    try:
        total_purchase_returns = db.session.query(func.sum(PurchaseReturn.quantity * PurchaseReturn.return_price)).scalar() or 0.0
        total_sale_returns = db.session.query(func.sum(SaleReturn.quantity * SaleReturn.return_price)).scalar() or 0.0
    except Exception:
        total_purchase_returns = 0.0
        total_sale_returns = 0.0

    try:
        low_stock_count = Item.query.filter(Item.stock <= Item.reorder_level).count()
    except Exception:
        low_stock_count = 0

    try:
        total_payable = get_total_payable()
        total_paid_suppliers = get_total_paid_suppliers()
        total_receivable = get_total_receivable()
        total_received_customers = get_total_received_customers()
        total_payable_balance = total_supplier_ledger_balance()
        total_receivable_balance = total_customer_ledger_balance()
    except Exception:
        total_payable = total_paid_suppliers = total_receivable = total_received_customers = 0.0
        total_payable_balance = total_receivable_balance = 0.0

    try:
        if _profit_expr is not None:
            from sqlalchemy import cast, String
            month_col = sql_date_fmt(Sale.date, "%Y-%m")
            monthly_sales = (
                db.session.query(
                    month_col.label("month"),
                    db.func.sum(SaleItem.amount).label("sale_amt"),
                    db.func.sum(_profit_expr).label("profit_amt"),
                )
                .join(SaleItem, SaleItem.sale_id == Sale.id)
                .group_by(month_col)
                .order_by(month_col)
                .limit(12)
                .all()
            )
        else:
            monthly_sales = []
    except Exception:
        monthly_sales = []
    try:
        month_col_purchase = sql_date_fmt(Purchase.date, "%Y-%m")
        monthly_purchases = (
            db.session.query(
                month_col_purchase.label("month"),
                db.func.sum(PurchaseItem.amount).label("purchase_amt"),
            )
            .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
            .group_by(month_col_purchase)
            .order_by(month_col_purchase)
            .limit(12)
            .all()
        )
    except Exception:
        monthly_purchases = []
    return render_template(
        'dashboard.html',
        items=items,
        purchases=purchases,
        sales=sales,
        total_purchase_cost=total_purchase_cost,
        total_sale_revenue=total_sale_revenue,
        total_gross_profit=total_gross_profit,
        total_purchase_returns=total_purchase_returns,
        total_sale_returns=total_sale_returns,
        low_stock_count=low_stock_count,
        total_payable=total_payable,
        total_paid_suppliers=total_paid_suppliers,
        total_payable_balance=total_payable_balance,
        total_receivable=total_receivable,
        total_received_customers=total_received_customers,
        total_receivable_balance=total_receivable_balance,
        monthly_sales=monthly_sales,
        monthly_purchases=monthly_purchases,
    )

