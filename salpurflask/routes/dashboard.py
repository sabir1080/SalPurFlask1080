"""Dashboard and navigation routes."""

from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import current_user

from salpurflask.models import (
    Item, Purchase, Sale, PurchaseItem, SaleItem, PurchaseReturn, SaleReturn, STATUS_POSTED
)
from salpurflask.extensions import db

dashboard_bp = Blueprint('dashboard', __name__)


def _home_overview():
    """Real numbers for the homepage's "Dashboard Overview" panel --
    replaces what used to be hardcoded demo figures (Rs 2.4M revenue,
    fixed "Purchase #1042" text, ...). Every block is independently
    wrapped, the same defensive pattern dashboard() below already uses:
    one query failing (e.g. on a fresh install with no data yet) must
    never blank out the rest of the panel, and a metric with no reliable
    answer is left out of the dict entirely rather than shown as 0 or
    guessed -- the template only renders a stat it actually finds in
    context, never a fabricated placeholder.

    Reuses the exact same helpers/queries dashboard() already trusts
    (get_total_payable/get_total_receivable for Payables/Receivables,
    Item.stock <= Item.reorder_level for Low Stock, sql_date() for a
    dialect-safe day filter) rather than a second, competing definition
    of any of these numbers."""
    from decimal import Decimal
    from salpurflask.utils.helpers import now_local

    overview = {}

    today = now_local().date()

    # Today's Sales -- posted, non-reversed lines dated today, the same
    # is_reversed/STATUS_POSTED filter dashboard()'s own monthly_sales
    # query already applies to Sale.
    try:
        from app import sql_date
        val = (db.session.query(db.func.sum(SaleItem.amount))
               .join(Sale, Sale.id == SaleItem.sale_id)
               .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED,
                       sql_date(Sale.date) == today)
               .scalar())
        overview["today_sales"] = float(val) if val else 0.0
    except Exception:
        pass

    # Today's Purchases -- same shape, mirrored onto Purchase/PurchaseItem.
    try:
        from app import sql_date
        val = (db.session.query(db.func.sum(PurchaseItem.amount))
               .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
               .filter(Purchase.is_reversed.is_(False), Purchase.status == STATUS_POSTED,
                       sql_date(Purchase.date) == today)
               .scalar())
        overview["today_purchases"] = float(val) if val else 0.0
    except Exception:
        pass

    # Receivables / Payables -- the same ledger-balance totals the real
    # /dashboard page shows (total_receivable_balance/total_payable_balance),
    # not a re-derivation from Sale/Purchase rows, which is a materially
    # different (and less authoritative) number -- see total_supplier_
    # ledger_balance()/total_customer_ledger_balance()'s own module.
    try:
        from app import total_customer_ledger_balance
        overview["receivables"] = float(total_customer_ledger_balance())
    except Exception:
        pass

    try:
        from app import total_supplier_ledger_balance
        overview["payables"] = float(total_supplier_ledger_balance())
    except Exception:
        pass

    # Low Stock Items -- identical predicate to dashboard()'s low_stock_count.
    try:
        overview["low_stock_count"] = Item.query.filter(Item.stock <= Item.reorder_level).count()
    except Exception:
        pass

    # Near-Expiry Batches -- same window/definition expiring_batches() uses
    # (NEAR_EXPIRY_WARNING_DAYS, BatchStock.quantity > 0, excludes already-
    # expired and undated batches -- this is a forward-looking count, not
    # "already expired").
    try:
        from salpurflask.models import Batch, NEAR_EXPIRY_WARNING_DAYS
        from salpurflask.models.inventory_location import BatchStock
        from datetime import timedelta
        cutoff = today + timedelta(days=NEAR_EXPIRY_WARNING_DAYS)
        overview["near_expiry_count"] = (
            db.session.query(Batch.id)
            .join(BatchStock, BatchStock.batch_id == Batch.id)
            .filter(BatchStock.quantity > 0,
                    Batch.expiry_date.isnot(None),
                    Batch.expiry_date >= today,
                    Batch.expiry_date <= cutoff)
            .distinct().count())
    except Exception:
        pass

    # Recent Activity -- one latest row per kind, each independently
    # optional. Every entry is a real record's own fields (invoice_no,
    # party name, amount, item name) -- never synthesised.
    activity = []

    try:
        sale = (Sale.query.filter_by(is_reversed=False, status=STATUS_POSTED)
                .order_by(Sale.date.desc(), Sale.id.desc()).first())
        if sale:
            total = float(db.session.query(db.func.sum(SaleItem.amount))
                          .filter(SaleItem.sale_id == sale.id).scalar() or 0)
            label = sale.invoice_no or f"Sale #{sale.id}"
            customer = sale.customer.name if sale.customer else "Walk-in"
            activity.append({
                "icon": "bag-check", "color": "success",
                "text": f"{label} — {customer}", "amount": total,
                "url": url_for("sale_invoice", id=sale.id),
            })
    except Exception:
        pass

    try:
        pur = (Purchase.query.filter_by(is_reversed=False, status=STATUS_POSTED)
               .order_by(Purchase.date.desc(), Purchase.id.desc()).first())
        if pur:
            total = float(db.session.query(db.func.sum(PurchaseItem.amount))
                          .filter(PurchaseItem.purchase_id == pur.id).scalar() or 0)
            label = pur.invoice_no or f"Purchase #{pur.id}"
            supplier = pur.supplier.name if pur.supplier else "Unknown supplier"
            activity.append({
                "icon": "cart-check", "color": "primary",
                "text": f"{label} — {supplier}", "amount": total,
                "url": url_for("purchase_invoice", id=pur.id),
            })
    except Exception:
        pass

    try:
        from salpurflask.models import CustomerPayment
        pay = (CustomerPayment.query.filter_by(is_reversed=False)
               .order_by(CustomerPayment.payment_date.desc(), CustomerPayment.id.desc()).first())
        if pay:
            customer = pay.customer.name if pay.customer else "Customer"
            entry = {
                "icon": "cash-coin", "color": "success",
                "text": f"Payment received — {customer}", "amount": float(pay.amount),
            }
            if pay.customer_id:
                entry["url"] = url_for("customer_ledger", id=pay.customer_id)
            activity.append(entry)
    except Exception:
        pass

    try:
        from salpurflask.models.inventory_location import StockMovement
        transfer = (StockMovement.query.filter_by(movement_type="transfer")
                    .order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).first())
        if transfer:
            item_name = transfer.item.name if transfer.item else "Item"
            entry = {
                "icon": "arrow-left-right", "color": "info",
                "text": f"Stock transfer — {item_name}", "amount": None,
            }
            # source_id is the Transfer this movement came from (see
            # StockMovement's own docstring) -- transfer_detail() is
            # manager_required, so only link it for a manager, same
            # gating the Low Stock/Near-Expiry stat tiles already use.
            if transfer.source_type == "transfer" and transfer.source_id and current_user.is_manager:
                entry["url"] = url_for("transfer_detail", id=transfer.source_id)
            activity.append(entry)
    except Exception:
        pass

    if overview.get("low_stock_count"):
        activity.append({
            "icon": "exclamation-triangle-fill", "color": "warning",
            "text": f"{overview['low_stock_count']} item(s) below reorder level", "amount": None,
            "url": url_for("report_stock") + "#reorder-report" if current_user.is_manager else None,
        })

    if overview.get("near_expiry_count"):
        activity.append({
            "icon": "hourglass-split", "color": "danger",
            "text": f"{overview['near_expiry_count']} batch(es) nearing expiry", "amount": None,
            "url": url_for("expiring_batches") if current_user.is_manager else None,
        })

    overview["activity"] = activity
    return overview


@dashboard_bp.route("/")
def index():
    if current_user.is_authenticated:
        try:
            overview = _home_overview()
        except Exception:
            overview = {"activity": []}
        return render_template("index.html", overview=overview)
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
        'recent_stock_movements': [],
        'location_count': 1,
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
    #
    # Reused from app.py rather than querying PurchaseItem/SaleItem directly:
    # a reversed Purchase/Sale keeps its row (audit trail — see
    # reverse_document()) but must stop counting as active cost/revenue.
    # get_total_payable()/get_total_receivable() already join back to
    # Purchase.is_reversed/Sale.is_reversed; a second, uncoupled copy of the
    # same sum here previously did not, so it kept including reversed sales.
    try:
        from app import get_total_payable
        context['total_purchase_cost'] = float(get_total_payable())
    except Exception:
        pass

    try:
        from app import get_total_receivable
        context['total_sale_revenue'] = float(get_total_receivable())
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
        from app import sql_date_fmt

        # Get last 12 months of sales data — reversed sales excluded, same
        # reason as total_sale_revenue above: the row stays for audit but
        # must stop counting as active revenue. A Draft Sale (Phase 2) is
        # excluded the same way: it has no revenue effect until it is Posted.
        monthly_data = db.session.query(
            sql_date_fmt(Sale.date).label('month'),
            func.sum(SaleItem.amount).label('sale_amt'),
            func.sum(SaleItem.amount - (SaleItem.quantity * SaleItem.cost_price)).label('profit_amt')
        ).join(SaleItem, Sale.id == SaleItem.sale_id).filter(
            Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED
        ).group_by(
            sql_date_fmt(Sale.date)
        ).order_by(
            sql_date_fmt(Sale.date)
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

    # Recent warehouse activity — additive panel, Phase 4. Wrapped the same
    # way as every other query above: a failure here must never break the
    # rest of the dashboard. Location-scoped as of Phase 5: a restricted
    # user's panel shows only their own locations' activity, never every
    # warehouse's — this was the one confirmed leak point from the Phase 5
    # audit (this query previously carried no location filter at all).
    try:
        from salpurflask.models import StockMovement, Location
        from salpurflask.services.location_permissions import accessible_location_ids
        movement_query = StockMovement.query
        accessible_ids = accessible_location_ids()
        if accessible_ids is not None:
            movement_query = movement_query.filter(StockMovement.location_id.in_(accessible_ids))
        context['recent_stock_movements'] = (
            movement_query
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .limit(8).all())
    except Exception:
        pass

    try:
        from salpurflask.models import Location
        context['location_count'] = Location.query.count()
    except Exception:
        pass

    return render_template('dashboard.html', **context)
