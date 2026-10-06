"""Give a form back to the user, filled in, after a POST that failed.

Most forms re-render after a validation / business-rule error (the route flashes
the message and falls through to render_template), but the template only ever
knows how to fill itself from the database or from GET -- so the user comes back
to an empty form and has to retype every field and every line item.

Simple forms already solve this: the context processor in app.py exposes the
POST as ``form_data`` (a flat dict) and templates write
``value="{{ form_data.get(...) }}"``. That does not stretch to the transaction
forms: a flat dict keeps one value per name, so it cannot carry line-item rows, and
the rows themselves are built by JavaScript (item pickers, unit dropdowns fetched
per item, batch columns) -- there is nothing server-side to put a value into.

This module is the equivalent for those forms. On a POST response that is being
rendered (not redirected) and that carries an error/warning flash, it describes
what was submitted -- every field, every row, plus the names the pickers show for
the hidden ids -- and base.html hands it to static/js/form_restore.js, which puts
it back into the form. Nothing is stored anywhere: it is request.form, echoed.

A form that already fills itself from ``form_data`` just receives the same values
again, so this is safe to run on every failed-POST page.
"""
import re

from flask import get_flashed_messages, has_request_context, request

from salpurflask.extensions import db

_ERROR_CATEGORIES = {"danger", "warning", "error"}
_NEVER_ECHO = {"csrf_token"}
_MAX_VALUES = 500          # a runaway/hostile form must not bloat the page


_SECRET_PARTS = ("password", "passwd", "secret", "token", "api_key", "apikey", "credential")
_SECRET_NAMES = {"key", "value", "pin", "otp", "dsn", "salt"}   # exact: "discount_value[]" is fine


def _is_secret(name):
    low = name.lower()
    return low in _SECRET_NAMES or any(part in low for part in _SECRET_PARTS)


def _num(value):
    return float(value) if value is not None else None


def _item_meta(item):
    from salpurflask.models.models import item_unit_choices

    return {
        "name": item.name,
        "stock": f"{item.stock} {item.unit or 'Pcs'}",
        "batch_tracked": bool(item.batch_tracked),
        "units": [{"key": c["key"], "name": c["name"], "factor": c["factor"],
                   "sale_price": _num(c["sale_price"]),
                   "purchase_price": _num(c["purchase_price"])}
                  for c in item_unit_choices(item)],
    }


def _bad_qty(value):
    return not (value.isdigit() and int(value) > 0)


def _bad_price(value):
    try:
        return float(value) < 0
    except ValueError:
        return True


def _invalid_positions(fields):
    """Which submitted quantity / price values fail the same rules the routes
    apply (validate_line_rows: a positive whole number, a non-negative number),
    as {field name: [row index, ...]} -- so the page can mark the offending
    input, since the flashed message gives the value but not which row it is."""
    bad = {}
    for name, values in fields.items():
        base = name[:-2] if name.endswith("[]") else name
        if base == "quantity":
            check = _bad_qty
        elif base.endswith("price"):
            check = _bad_price
        else:
            continue
        positions = [i for i, v in enumerate(values) if v.strip() and check(v.strip())]
        if positions:
            bad[name] = positions
    return bad


def _named_in(messages, name):
    pattern = re.compile(r"(?<!\w)" + re.escape(name) + r":")
    return any(pattern.search(m) for m in messages)


def _digits(values):
    return [int(v) for v in values if isinstance(v, str) and v.strip().isdigit()]


def _by_id(model, ids):
    ids = set(ids)
    if not ids:
        return {}
    return {row.id: row for row in db.session.query(model).filter(model.id.in_(ids)).all()}


def build_form_restore():
    """The payload base.html embeds, or None when there is nothing to restore."""
    if not has_request_context() or request.method != "POST":
        return None
    if request.blueprint == "developer":      # configuration / credentials: never echoed
        return None
    errors = [str(msg) for cat, msg in get_flashed_messages(with_categories=True)
              if cat in _ERROR_CATEGORIES]
    if not errors:
        return None

    fields = {}
    for name in request.form.keys():
        if name in _NEVER_ECHO or _is_secret(name):
            continue
        fields[name] = request.form.getlist(name)[:_MAX_VALUES]
    if not fields:
        return None

    from salpurflask.models import Customer, Supplier, Item, SaleItem, PurchaseItem

    payload = {"fields": fields, "labels": {}, "items": [], "invalid": _invalid_positions(fields)}

    for name, model in (("customer_id", Customer), ("supplier_id", Supplier)):
        found = _by_id(model, _digits(fields.get(name, [])[:1]))
        for row in found.values():
            payload["labels"][name] = row.name

    # One meta entry per submitted item id, in submitted order (index-aligned with
    # the rows), so the page can rebuild each row's unit list, stock hint and
    # batch columns exactly as picking the item would have.
    item_field = "item_id[]" if "item_id[]" in fields else ("item_id" if "item_id" in fields else None)
    if item_field:
        values = fields[item_field]
        items = _by_id(Item, _digits(values))
        for i, v in enumerate(values):
            item = items.get(int(v)) if v.strip().isdigit() else None
            payload["items"].append(_item_meta(item) if item else None)
            if item and _named_in(errors, item.name):
                # Stock / quantity / expiry errors are phrased "<item name>: ...":
                # mark that row's input (the message alone doesn't say which row).
                field = "expiry_date[]" if any("expiry" in e for e in errors) else "quantity[]"
                payload["invalid"].setdefault(field, [])
                if i not in payload["invalid"][field]:
                    payload["invalid"][field].append(i)

    # Return forms pick a document line, not an item: show which one it was.
    for name, model, party in (("sale_item_id[]", SaleItem, "sale_header"),
                               ("purchase_item_id[]", PurchaseItem, "purchase_header")):
        if name in fields:
            lines = _by_id(model, _digits(fields[name]))
            texts = []
            for v in fields[name]:
                line = lines.get(int(v)) if v.strip().isdigit() else None
                if line is None:
                    texts.append("")
                    continue
                header = getattr(line, party)
                partner = getattr(header, "customer", None) or getattr(header, "supplier", None)
                texts.append(" — ".join(p for p in (
                    header.invoice_no or f"#{header.id}",
                    partner.name if partner else "",
                    line.item.name if line.item else "") if p))
            payload["labels"][name] = texts

    return payload
