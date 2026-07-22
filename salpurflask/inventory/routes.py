"""Inventory read-only routes (ledger, API, reports)."""

from datetime import datetime
from decimal import Decimal

from flask import abort, flash, render_template, request
from sqlalchemy import and_

from salpurflask.extensions import db
from salpurflask.models import (
    Item, Category, PurchaseItem, Purchase, SaleItem, Sale,
    PurchaseReturn, SaleReturn, StockAdjustment,
)
from salpurflask.auth import verified_required, manager_required
from salpurflask.utils import now_local
def line_base_qty(line):
    """Get quantity in base unit."""
    return line.quantity * (line.unit_factor or 1)


def purchase_item_total(pi):
    """Calculate purchase item total."""
    return pi.quantity * pi.purchase_price * (pi.unit_factor or 1)


def sale_item_total(si):
    """Calculate sale item total."""
    return si.quantity * si.sale_price * (si.unit_factor or 1)


@verified_required
def item_ledger(id):
    """Display item ledger with stock movements."""
    item = db.session.get(Item, id) or abort(404)
    start_date_str = request.args.get("start_date", "")
    end_date_str   = request.args.get("end_date", "")

    # A reversed document never happened, so it must not still count here — its
    # stock effect was already undone, but its row still exists for the audit trail.
    purchase_items   = (PurchaseItem.query.join(Purchase)
                        .filter(PurchaseItem.item_id == id, Purchase.is_reversed.is_(False)).all())
    sale_items       = (SaleItem.query.join(Sale)
                        .filter(SaleItem.item_id == id, Sale.is_reversed.is_(False)).all())
    purchase_returns = PurchaseReturn.query.filter_by(item_id=id, is_reversed=False).all()
    sale_returns     = SaleReturn.query.filter_by(item_id=id, is_reversed=False).all()

    # stock_in/out are in the item's base unit — the only unit item.stock (and this
    # ledger's running balance) is ever tracked in, whatever unit the line was actually
    # bought/sold in. Rate follows it down to a per-base-unit price so it still lines
    # up with stock_in/out (Rate × qty ≈ Value); Value itself is a total and needs no
    # conversion either way.
    entries = []
    for pi in purchase_items:
        factor = pi.unit_factor or 1
        entries.append({
            "date": pi.purchase_header.date, "type": "Purchase", "badge": "success",
            "ref": f"PO #{pi.purchase_header.id}", "party": pi.purchase_header.id_supplier.name,
            "stock_in": line_base_qty(pi), "stock_out": 0,
            "rate": pi.purchase_price / factor, "value": purchase_item_total(pi),
        })
    for si in sale_items:
        factor = si.unit_factor or 1
        entries.append({
            "date": si.sale_header.date, "type": "Sale", "badge": "primary",
            "ref": f"SO #{si.sale_header.id}", "party": si.sale_header.id_customer.name,
            "stock_in": 0, "stock_out": line_base_qty(si),
            "rate": si.sale_price / factor, "value": sale_item_total(si),
        })
    for pr in purchase_returns:
        factor = pr.unit_factor or 1
        entries.append({
            "date": pr.date, "type": "Purchase Return", "badge": "warning",
            "ref": f"PR #{pr.id}", "party": pr.supplier.name,
            "stock_in": 0, "stock_out": line_base_qty(pr),
            "rate": pr.return_price / factor, "value": round(pr.quantity * pr.return_price, 2),
        })
    for sr in sale_returns:
        factor = sr.unit_factor or 1
        entries.append({
            "date": sr.date, "type": "Sale Return", "badge": "secondary",
            "ref": f"SR #{sr.id}", "party": sr.customer.name,
            "stock_in": line_base_qty(sr), "stock_out": 0,
            "rate": sr.return_price / factor, "value": round(sr.quantity * sr.return_price, 2),
        })

    adjustments = StockAdjustment.query.filter_by(item_id=id).all()
    for adj in adjustments:
        stock_in = adj.quantity if adj.direction == "in" else 0
        stock_out = adj.quantity if adj.direction == "out" else 0
        entries.append({
            "date": adj.date, "type": f"Adjustment ({adj.adj_type})", "badge": "info",
            "ref": f"ADJ #{adj.id}", "party": adj.reason or "—",
            "stock_in": stock_in, "stock_out": stock_out,
            "rate": 0, "value": 0,
        })

    entries.sort(key=lambda x: (x["date"], x["ref"]))

    date_filtered = False
    if start_date_str and end_date_str:
        try:
            sd = datetime.strptime(start_date_str, "%Y-%m-%d")
            ed = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
            entries = [e for e in entries if sd <= e["date"] <= ed]
            date_filtered = True
        except ValueError:
            flash("Invalid date format!", "danger")

    # Opening stock entry — prepend when not date-filtered (or always as starting balance)
    opening = item.opening_stock or 0
    if not date_filtered:
        opening_entry = {
            "date": None, "type": "Opening Stock", "badge": "dark",
            "ref": "—", "party": "—",
            "stock_in": opening, "stock_out": 0,
            "rate": 0, "value": 0,
            "balance": opening, "is_opening": True,
        }
        entries = [opening_entry] + entries
        balance = opening
    else:
        balance = 0

    for e in entries:
        if e.get("is_opening"):
            continue
        balance += e["stock_in"] - e["stock_out"]
        e["balance"] = balance

    total_in  = sum(e["stock_in"]  for e in entries if not e.get("is_opening"))
    total_out = sum(e["stock_out"] for e in entries if not e.get("is_opening"))

    return render_template(
        "item_ledger.html",
        item=item,
        entries=entries,
        total_in=total_in,
        total_out=total_out,
        opening_stock=opening,
        current_stock=item.stock,
        start_date=start_date_str,
        end_date=end_date_str,
    )


@verified_required
def get_item(id):
    """Get item details as JSON."""
    item = db.session.get(Item, id) or abort(404)
    return {
        "purchase_price": item.purchase_price,
        "sale_price": item.sale_price,
        "unit": item.unit or "Pcs",
        "category": item.id_category.name if item.id_category else None,
    }


@manager_required
def report_stock():
    """Display stock report with valuation.

    What is in the warehouse right now, and what it cost. A snapshot, not a
    period — so unlike sales/purchase reports it takes no date range. It is an
    accounting report, not just a stock list: the total is valued at
    weighted-average cost, which is the amount the Inventory account was
    debited when the goods came in, so it is the Inventory line on the balance
    sheet and can be read straight across.
    """
    items = (Item.query.outerjoin(Category, Item.category_id == Category.id)
             .order_by(Category.name, Item.name).all())
    return render_template(
        "report_stock.html",
        stock_report=items,
        stock_value_total=sum(Decimal(str(i.inventory_value or 0)) for i in items),
        items_in_stock=sum(1 for i in items if i.stock and i.stock > 0),
        reorder_report=[i for i in items if i.stock <= i.reorder_level],
        as_of=now_local().strftime("%d %B %Y"),
    )
