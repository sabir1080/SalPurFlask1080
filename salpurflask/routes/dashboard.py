"""Dashboard and navigation routes."""

from flask import Blueprint, render_template, redirect, url_for, flash, jsonify, request
from flask_login import current_user, login_required

from salpurflask.models import (
    Item, Purchase, Sale, PurchaseItem, SaleItem, PurchaseReturn, SaleReturn, STATUS_POSTED,
    BusinessCategory
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
        'top_items': [],
        'top_items_categories': [],
        'purchases': [],
        'sales': [],
        'total_purchase_cost': 0.0,
        'total_sale_revenue': 0.0,
        'total_gross_profit': 0.0,
        'total_purchase_returns': 0.0,
        'total_sale_returns': 0.0,
        'low_stock_count': 0,
        'expired_items_count': 0,
        'near_expiry_items_count': 0,
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
        'sales_by_category': [],
        'top_suppliers': [],
        'top_customers': [],
        'payment_method_breakdown': [],
        'expense_breakdown': [],
    }

    # Load items (simple query)
    try:
        context['items'] = Item.query.all()
    except Exception:
        pass

    # Top items by stock, for the "Top Items" chart. Item.query.all()[:10]
    # (used directly in the template previously) has no ORDER BY, so it
    # returned whatever 10 rows Postgres happened to return first — on this
    # database that was 10 batch-tracked medicines that had sold out to 0,
    # so every bar was empty. This is a separate list so the low-stock table
    # below, which needs the full unordered `items`, is unaffected.
    try:
        context['top_items'] = Item.query.order_by(Item.stock.desc()).limit(10).all()
    except Exception:
        context['top_items'] = []

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

    # Gross profit = revenue - COGS on active sales (same is_reversed/
    # STATUS_POSTED exclusion as monthly_sales below, same formula app.py's
    # P&L report uses). This was previously left at the hardcoded 0.0
    # default above and never actually computed.
    try:
        val = db.session.query(
            db.func.sum(SaleItem.amount - (SaleItem.quantity * SaleItem.cost_price))
        ).join(Sale, Sale.id == SaleItem.sale_id).filter(
            Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED
        ).scalar()
        context['total_gross_profit'] = float(val) if val else 0.0
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

    # Expired items -- distinct Items whose expiry (or warranty, stored under
    # the same "expiry_date" ProductField name — see ProductField query in
    # BusinessCategory seeding, there is no separate warranty_date field)
    # has passed, from either of the two places a date can live:
    #   1. Batch.expiry_date, for batch-tracked items (BatchStock.quantity >
    #      0, same predicate expiring_batches()'s "expired" status uses).
    #   2. ProductCategoryData.field_value, for non-batch items that carry a
    #      per-category custom date field. field_value is stored as a plain
    #      'YYYY-MM-DD' JSON string (see ConfigurationService.
    #      save_product_category_data/validate_product_data), which sorts
    #      correctly as text, so it's cast to text and compared as one --
    #      there is no native date column here to compare against directly.
    # This is a per-item count (a batch-tracked item with two expired
    # batches counts once), unlike expiring_batches()'s own per-batch rows.
    try:
        from salpurflask.models import Batch
        from salpurflask.models.inventory_location import BatchStock
        from salpurflask.models.business_config import ProductField, ProductCategoryData
        from datetime import date
        today = date.today()
        today_str = today.isoformat()

        expired_batch_item_ids = (
            db.session.query(Batch.item_id)
            .join(BatchStock, BatchStock.batch_id == Batch.id)
            .filter(BatchStock.quantity > 0,
                    Batch.expiry_date.isnot(None),
                    Batch.expiry_date < today)
            .distinct()
        )

        expired_field_item_ids = (
            db.session.query(ProductCategoryData.product_id)
            .join(ProductField, db.and_(
                ProductField.category_id == ProductCategoryData.category_id,
                ProductField.field_name == ProductCategoryData.field_name))
            .filter(ProductField.field_type == 'date',
                    ProductField.field_name.ilike('%expiry%') |
                    ProductField.field_name.ilike('%warranty%') |
                    ProductField.field_label.ilike('%expiry%') |
                    ProductField.field_label.ilike('%warranty%'),
                    db.cast(ProductCategoryData.field_value, db.Text) < f'"{today_str}"')
            .distinct()
        )

        expired_item_ids = {row[0] for row in expired_batch_item_ids.all()} | \
                            {row[0] for row in expired_field_item_ids.all()}
        context['expired_items_count'] = len(expired_item_ids)
    except Exception:
        context['expired_items_count'] = 0

    # Near-expiry items -- same two sources as expired_items_count above,
    # but for items whose expiry/warranty is still in the future and falls
    # within NEAR_EXPIRY_WARNING_DAYS (the same default threshold
    # expiring_batches() uses for its own "near_expiry" status bucket).
    # Deliberately excludes anything already expired -- this is a
    # forward-looking "about to expire" count, not a superset of
    # expired_items_count.
    try:
        from salpurflask.models import Batch, NEAR_EXPIRY_WARNING_DAYS
        from salpurflask.models.inventory_location import BatchStock
        from salpurflask.models.business_config import ProductField, ProductCategoryData
        from datetime import date, timedelta
        today = date.today()
        cutoff = today + timedelta(days=NEAR_EXPIRY_WARNING_DAYS)
        today_str = today.isoformat()
        cutoff_str = cutoff.isoformat()

        near_expiry_batch_item_ids = (
            db.session.query(Batch.item_id)
            .join(BatchStock, BatchStock.batch_id == Batch.id)
            .filter(BatchStock.quantity > 0,
                    Batch.expiry_date.isnot(None),
                    Batch.expiry_date > today,
                    Batch.expiry_date <= cutoff)
            .distinct()
        )

        near_expiry_field_item_ids = (
            db.session.query(ProductCategoryData.product_id)
            .join(ProductField, db.and_(
                ProductField.category_id == ProductCategoryData.category_id,
                ProductField.field_name == ProductCategoryData.field_name))
            .filter(ProductField.field_type == 'date',
                    ProductField.field_name.ilike('%expiry%') |
                    ProductField.field_name.ilike('%warranty%') |
                    ProductField.field_label.ilike('%expiry%') |
                    ProductField.field_label.ilike('%warranty%'),
                    db.cast(ProductCategoryData.field_value, db.Text) > f'"{today_str}"',
                    db.cast(ProductCategoryData.field_value, db.Text) <= f'"{cutoff_str}"')
            .distinct()
        )

        near_expiry_item_ids = {row[0] for row in near_expiry_batch_item_ids.all()} | \
                                {row[0] for row in near_expiry_field_item_ids.all()}
        context['near_expiry_items_count'] = len(near_expiry_item_ids)
    except Exception:
        context['near_expiry_items_count'] = 0

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

    # Sales by Category — active (non-reversed, posted) SaleItem.amount
    # grouped by the item's business category, all-time (not the dashboard's
    # own date range — those KPI cards are lifetime totals too, e.g.
    # total_sale_revenue above).
    try:
        category_sales = (
            db.session.query(
                BusinessCategory.name,
                db.func.sum(SaleItem.amount).label('total')
            )
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Item, Item.id == SaleItem.item_id)
            .join(BusinessCategory, BusinessCategory.id == Item.business_category_id)
            .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED)
            .group_by(BusinessCategory.name)
            .order_by(db.func.sum(SaleItem.amount).desc())
            .all()
        )
        context['sales_by_category'] = [
            {'label': row.name, 'value': float(row.total or 0)} for row in category_sales
        ]
    except Exception:
        context['sales_by_category'] = []

    # Top 10 Suppliers by total active Purchase amount.
    try:
        from salpurflask.models import Supplier
        _pur_net = (PurchaseItem.quantity * PurchaseItem.purchase_price
                    - PurchaseItem.discount_amount + PurchaseItem.tax_amount)
        top_suppliers = (
            db.session.query(Supplier.name, db.func.sum(_pur_net).label('total'))
            .select_from(PurchaseItem)
            .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
            .join(Supplier, Supplier.id == Purchase.supplier_id)
            .filter(Purchase.is_reversed.is_(False), Purchase.status == STATUS_POSTED)
            .group_by(Supplier.name)
            .order_by(db.func.sum(_pur_net).desc())
            .limit(10)
            .all()
        )
        context['top_suppliers'] = [
            {'label': row.name, 'value': float(row.total or 0)} for row in top_suppliers
        ]
    except Exception:
        context['top_suppliers'] = []

    # Top 10 Customers by total active Sale amount.
    try:
        from salpurflask.models import Customer
        top_customers = (
            db.session.query(Customer.name, db.func.sum(SaleItem.amount).label('total'))
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Customer, Customer.id == Sale.customer_id)
            .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED)
            .group_by(Customer.name)
            .order_by(db.func.sum(SaleItem.amount).desc())
            .limit(10)
            .all()
        )
        context['top_customers'] = [
            {'label': row.name, 'value': float(row.total or 0)} for row in top_customers
        ]
    except Exception:
        context['top_customers'] = []

    # Payment Method Breakdown — SupplierPayment + CustomerPayment amounts,
    # combined and grouped by payment_method. Both share the same
    # PAYMENT_METHODS vocabulary (see CLAUDE.md), so grouping by the raw
    # string is safe without an extra lookup.
    try:
        from salpurflask.models import SupplierPayment, CustomerPayment
        supplier_pm = (
            db.session.query(SupplierPayment.payment_method, db.func.sum(SupplierPayment.amount))
            .filter(SupplierPayment.is_reversed.is_(False))
            .group_by(SupplierPayment.payment_method)
            .all()
        )
        customer_pm = (
            db.session.query(CustomerPayment.payment_method, db.func.sum(CustomerPayment.amount))
            .filter(CustomerPayment.is_reversed.is_(False))
            .group_by(CustomerPayment.payment_method)
            .all()
        )
        combined = {}
        for method, total in list(supplier_pm) + list(customer_pm):
            combined[method] = combined.get(method, 0.0) + float(total or 0)
        context['payment_method_breakdown'] = [
            {'label': method, 'value': total} for method, total in combined.items()
        ]
    except Exception:
        context['payment_method_breakdown'] = []

    # Expense Breakdown — by GL account, not the Expense table. The Expense
    # table only holds expenses entered through the Record Expense form;
    # Salaries & Wages, Allowances, Overtime (payroll postings) and any
    # expense entered as a direct journal entry debit the same "Operating
    # Expenses" GL accounts without ever creating an Expense row — Chart of
    # Accounts already proves this (Rent/Utilities/Salaries all carry real
    # balances there while the Expense table itself was near-empty). Using
    # gl_balances() (the same function chart_of_accounts() itself uses)
    # against Expense-type leaf accounts is the only way this chart reflects
    # everything a user would recognize as "an expense," not just the subset
    # that happened to go through one specific form.
    # Scoped to children of the "Operating Expenses" group account (code
    # ACC_EXPENSES = "6000") specifically, not every Expense-typed account —
    # Cost of Goods Sold (5000) and Inventory Adjustment (5100) are also
    # type='Expense' in this chart of accounts, but they already have their
    # own place in Gross Profit above and would otherwise dwarf every real
    # operating-expense slice (COGS alone is ~8.9M vs ~55k Rent) and
    # Inventory Adjustment can legitimately be negative, which a bar/pie
    # chart can't represent sensibly. Scoping by parent_id (not a code
    # prefix match) tracks the chart of accounts' actual grouping even if
    # account codes are ever renumbered.
    try:
        from app import Account, gl_balances, natural_balance, ACC_EXPENSES
        opex_group = Account.query.filter_by(code=ACC_EXPENSES, is_group=True).first()
        balances = gl_balances()
        expense_breakdown = []
        if opex_group:
            expense_accounts = (
                Account.query
                .filter(Account.parent_id == opex_group.id, Account.is_group.is_(False),
                        Account.is_active.is_(True))
                .order_by(Account.code)
                .all()
            )
            for acct in expense_accounts:
                amt = natural_balance(acct, balances.get(acct.id, 0))
                if amt:
                    expense_breakdown.append({'label': acct.name, 'value': float(amt)})
            expense_breakdown.sort(key=lambda r: r['value'], reverse=True)
        context['expense_breakdown'] = expense_breakdown
    except Exception:
        context['expense_breakdown'] = []

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

    # Categories for the "Top Items" chart's filter dropdown. Only ones that
    # actually have items are offered — an enabled-but-empty category would
    # just show a blank chart.
    try:
        # BusinessCategory.config_data is a plain `json` column (not jsonb),
        # which Postgres cannot compare for equality — DISTINCT or a JOIN
        # that forces one on the full BusinessCategory row both fail with
        # "could not identify an equality operator for type json". Filtering
        # by a subquery of category ids that have items avoids ever
        # comparing that column.
        category_ids_with_items = db.session.query(Item.business_category_id).filter(
            Item.business_category_id.isnot(None)
        ).distinct().subquery()
        context['top_items_categories'] = (
            BusinessCategory.query
            .filter(BusinessCategory.is_enabled.is_(True))
            .filter(BusinessCategory.id.in_(db.session.query(category_ids_with_items)))
            .order_by(BusinessCategory.priority)
            .all()
        )
    except Exception:
        context['top_items_categories'] = []

    return render_template('dashboard.html', **context)


@dashboard_bp.route('/dashboard/top-items')
@login_required
def dashboard_top_items():
    """JSON feed for the "Top Items" chart's category filter. Same
    ordering/shape as dashboard()'s own top_items query — kept as a
    separate endpoint so the dropdown can re-fetch without a full page
    reload, rather than duplicating the chart-building logic client-side
    from a full item dump."""
    if not current_user.verified:
        return jsonify({'error': 'not verified'}), 403
    if current_user.role not in ("admin", "manager"):
        return jsonify({'error': 'forbidden'}), 403

    category_id = request.args.get('category_id', type=int)
    query = Item.query
    if category_id:
        query = query.filter(Item.business_category_id == category_id)
    top_items = query.order_by(Item.stock.desc()).limit(10).all()
    return jsonify({
        'labels': [it.name[:15] for it in top_items],
        'stock': [float(it.stock or 0) for it in top_items],
        'reorder_level': [float(it.reorder_level or 0) for it in top_items],
    })


@dashboard_bp.route('/business-insights')
@login_required
def business_insights():
    """A second, deeper analytics page — the main Dashboard has no room left
    for these without crowding it, so they live here instead, one click away
    via the dashboard's own button row. Every section is independently
    wrapped in try/except, same defensive pattern as dashboard() above: one
    query failing must never blank out the rest of the page."""
    if not current_user.verified:
        flash(f"Please verify {current_user.email} to access this page.", "danger")
        return redirect(url_for("auth.signin"))
    if current_user.role not in ("admin", "manager"):
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("purchase"))

    from datetime import date, datetime, timedelta
    from salpurflask.models import Supplier, Customer, FinancialAccount

    context = {
        'cash_flow': [], 'total_cash_bank': 0.0,
        'overdue_receivables': [],
        'today_sales': 0.0, 'yesterday_sales': 0.0, 'last_week_same_day_sales': 0.0,
        'fast_moving_items': [], 'slow_moving_items': [],
        'category_margins': [],
        'top_margin_items': [],
        'supplier_payments_due': [],
        'selling_days': [],
        'new_customers_count': 0, 'repeat_customers_count': 0,
        'monthly_sales_trend': [],
    }

    # 10. Monthly Sales Trend — last 12 months of active sale revenue, this
    # page's own version of the same monthly grouping dashboard()'s
    # monthly_sales already does (same sql_date_fmt dialect helper), kept
    # separate since this page's date range/style is independent of the
    # main dashboard's.
    try:
        from app import sql_date_fmt
        monthly_rows = (
            db.session.query(
                sql_date_fmt(Sale.date).label('month'),
                db.func.sum(SaleItem.amount).label('total')
            )
            .join(SaleItem, Sale.id == SaleItem.sale_id)
            .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED)
            .group_by(sql_date_fmt(Sale.date))
            .order_by(sql_date_fmt(Sale.date))
            .all()
        )
        # sql_date_fmt gives "YYYY-MM" (needed as the GROUP BY/ORDER BY key
        # so months sort chronologically, not alphabetically) — reformatted
        # to "Jan-2026" here for display, in Python rather than SQL so it
        # doesn't need a second dialect-specific format string.
        def _month_label(ym):
            try:
                return datetime.strptime(ym, '%Y-%m').strftime('%b-%Y')
            except (TypeError, ValueError):
                return ym or 'Unknown'

        context['monthly_sales_trend'] = [
            {'label': _month_label(row.month), 'value': float(row.total or 0)} for row in monthly_rows
        ][-12:]
    except Exception:
        pass

    # 1. Cash Flow Snapshot — each active cash/bank account's live GL
    # balance (get_account_balance already reads the ledger, not a
    # receipts/payments/expenses sum — see its own docstring on why that
    # stopped being reliable).
    try:
        from app import get_account_balance
        accounts = FinancialAccount.query.filter_by(is_active=True, is_control=False).order_by(FinancialAccount.name).all()
        context['cash_flow'] = [
            {'label': a.name, 'type': a.account_type, 'value': get_account_balance(a)}
            for a in accounts
        ]
        context['total_cash_bank'] = sum(row['value'] for row in context['cash_flow'])
    except Exception:
        pass

    # 2. Overdue Receivables — top 10 customers by current ledger balance,
    # each paired with their oldest still-outstanding sale's age in days
    # (a customer with a big balance from one old sale is a different
    # follow-up priority than one from several recent ones, so both the
    # total and the age are shown).
    try:
        from salpurflask.models import CustomerLedgerEntry
        latest_balance_rows = db.session.execute(db.text(
            "SELECT customer_id, balance_after FROM ("
            "  SELECT customer_id, balance_after, ROW_NUMBER() OVER "
            "  (PARTITION BY customer_id ORDER BY entry_date DESC, id DESC) AS rn "
            "  FROM customer_ledger_entry) t WHERE rn = 1 AND balance_after > 0 "
            "ORDER BY balance_after DESC LIMIT 10"
        )).all()
        overdue = []
        today = date.today()
        for customer_id, balance in latest_balance_rows:
            customer = db.session.get(Customer, customer_id)
            if not customer:
                continue
            oldest_unpaid = (
                Sale.query.filter(Sale.customer_id == customer_id, Sale.is_reversed.is_(False),
                                   Sale.status == STATUS_POSTED)
                .order_by(Sale.date.asc()).first()
            )
            days_old = (today - oldest_unpaid.date.date()).days if oldest_unpaid else None
            overdue.append({'name': customer.name, 'balance': float(balance), 'days_old': days_old})
        context['overdue_receivables'] = overdue
    except Exception:
        pass

    # 3. Today vs Yesterday vs Last Week Same Day — active sale revenue only.
    try:
        today = date.today()
        yesterday = today - timedelta(days=1)
        last_week = today - timedelta(days=7)

        def _day_sales(d):
            val = (
                db.session.query(db.func.sum(SaleItem.amount))
                .join(Sale, Sale.id == SaleItem.sale_id)
                .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED,
                        db.func.date(Sale.date) == d.isoformat())
                .scalar()
            )
            return float(val or 0)

        context['today_sales'] = _day_sales(today)
        context['yesterday_sales'] = _day_sales(yesterday)
        context['last_week_same_day_sales'] = _day_sales(last_week)
    except Exception:
        pass

    # 4. Fast vs Slow Moving Items — quantity sold in the last 30 days.
    # Fast: top 10 by quantity. Slow: active items with zero sales in that
    # window (dead-stock candidates), capped at 10 for the same reason the
    # dashboard's own low-stock table doesn't dump the whole catalog.
    try:
        cutoff = date.today() - timedelta(days=30)
        moved = (
            db.session.query(Item.id, Item.name, db.func.sum(SaleItem.quantity).label('qty'))
            .join(SaleItem, SaleItem.item_id == Item.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED,
                    Sale.date >= cutoff)
            .group_by(Item.id, Item.name)
            .order_by(db.func.sum(SaleItem.quantity).desc())
            .all()
        )
        context['fast_moving_items'] = [
            {'label': row.name, 'value': float(row.qty or 0)} for row in moved[:10]
        ]
        moved_ids = {row.id for row in moved}
        slow = (
            Item.query.filter(~Item.id.in_(moved_ids))
            .order_by(Item.name).limit(10).all()
        ) if moved_ids else Item.query.order_by(Item.name).limit(10).all()
        context['slow_moving_items'] = [{'label': it.name, 'stock': float(it.stock or 0)} for it in slow]
    except Exception:
        pass

    # 5 & 6. Profit Margin — by category and item-wise top 10. Same
    # revenue-minus-COGS formula as total_gross_profit on the main
    # dashboard, expressed as a % of revenue per group instead of one
    # lump total.
    try:
        margin_expr = SaleItem.amount - (SaleItem.quantity * SaleItem.cost_price)
        category_rows = (
            db.session.query(
                BusinessCategory.name,
                db.func.sum(SaleItem.amount).label('revenue'),
                db.func.sum(margin_expr).label('margin')
            )
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Item, Item.id == SaleItem.item_id)
            .join(BusinessCategory, BusinessCategory.id == Item.business_category_id)
            .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED)
            .group_by(BusinessCategory.name)
            .all()
        )
        context['category_margins'] = sorted([
            {'label': row.name, 'value': round((float(row.margin) / float(row.revenue)) * 100, 1)}
            for row in category_rows if row.revenue
        ], key=lambda r: r['value'], reverse=True)
    except Exception:
        pass

    try:
        margin_expr = SaleItem.amount - (SaleItem.quantity * SaleItem.cost_price)
        item_rows = (
            db.session.query(
                Item.name,
                db.func.sum(SaleItem.amount).label('revenue'),
                db.func.sum(margin_expr).label('margin')
            )
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Item, Item.id == SaleItem.item_id)
            .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED)
            .group_by(Item.id, Item.name)
            .having(db.func.sum(SaleItem.amount) > 0)
            .all()
        )
        ranked = sorted([
            {'label': row.name, 'value': round((float(row.margin) / float(row.revenue)) * 100, 1)}
            for row in item_rows
        ], key=lambda r: r['value'], reverse=True)
        context['top_margin_items'] = ranked[:10]
    except Exception:
        pass

    # 7. Supplier Payments Due — top 10 by current outstanding balance,
    # same latest-ledger-row technique as Overdue Receivables above.
    try:
        from salpurflask.models import SupplierLedgerEntry
        rows = db.session.execute(db.text(
            "SELECT supplier_id, balance_after FROM ("
            "  SELECT supplier_id, balance_after, ROW_NUMBER() OVER "
            "  (PARTITION BY supplier_id ORDER BY entry_date DESC, id DESC) AS rn "
            "  FROM supplier_ledger_entry) t WHERE rn = 1 AND balance_after > 0 "
            "ORDER BY balance_after DESC LIMIT 10"
        )).all()
        due = []
        for supplier_id, balance in rows:
            supplier = db.session.get(Supplier, supplier_id)
            if supplier:
                due.append({'label': supplier.name, 'value': float(balance)})
        context['supplier_payments_due'] = due
    except Exception:
        pass

    # 8. Top Selling Days Pattern — total active sale revenue grouped by
    # day-of-week. Computed in Python rather than SQL (to_char vs strftime
    # give incompatible day-name formats across Postgres/SQLite — see
    # sql_date_fmt's own dialect branch above) — there are at most a few
    # thousand sales, so pulling (date, amount) pairs and bucketing here is
    # cheap and dialect-agnostic.
    try:
        day_totals = {i: 0.0 for i in range(7)}  # 0=Monday .. 6=Sunday
        rows = (
            db.session.query(Sale.date, SaleItem.amount)
            .join(SaleItem, SaleItem.sale_id == Sale.id)
            .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED)
            .all()
        )
        for sale_date, amount in rows:
            day_totals[sale_date.weekday()] += float(amount or 0)
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        context['selling_days'] = [{'label': day_names[i], 'value': day_totals[i]} for i in range(7)]
    except Exception:
        pass

    # 9. Customer Retention — "new" = their first-ever active sale falls in
    # the current calendar month; "repeat" = every other customer who has
    # at least one active sale at all.
    try:
        today = date.today()
        month_start = today.replace(day=1)
        first_sale_dates = (
            db.session.query(Sale.customer_id, db.func.min(Sale.date).label('first_date'))
            .filter(Sale.is_reversed.is_(False), Sale.status == STATUS_POSTED)
            .group_by(Sale.customer_id)
            .all()
        )
        new_count = sum(1 for _, first_date in first_sale_dates if first_date.date() >= month_start)
        context['new_customers_count'] = new_count
        context['repeat_customers_count'] = len(first_sale_dates) - new_count
    except Exception:
        pass

    return render_template('business_insights.html', **context)


@dashboard_bp.route('/dashboard/purchase-list')
@login_required
def purchase_list():
    """A working worksheet for restocking, opened from the dashboard's Low
    Stock Items card. Deliberately not tied to Purchase/PurchaseOrder — per
    request this stays a print-only tool: the item list is server-rendered
    once here, quantity entry and per-row removal both happen client-side
    (nothing is saved), and printing reuses the same clone-the-table
    technique dashboard.html's own Low Stock print button already uses.
    Same predicate as low_stock_count elsewhere: Item.stock <= Item.reorder_level."""
    if not current_user.verified:
        flash(f"Please verify {current_user.email} to access this page.", "danger")
        return redirect(url_for("auth.signin"))
    if current_user.role not in ("admin", "manager"):
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("purchase"))

    low_stock_items = (
        Item.query.filter(Item.stock <= Item.reorder_level)
        .order_by(Item.name)
        .all()
    )
    return render_template('purchase_list.html', items=low_stock_items)
