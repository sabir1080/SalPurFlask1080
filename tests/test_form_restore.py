"""A failed POST must hand the form back as it was filled in.

Routes that fall through to render_template after an error used to return a
blank form (the templates fill themselves from the database / GET only), and the
admin correction + stock-adjustment routes redirected, which loses the POST
outright. Now the re-render carries what was submitted (salpurflask/utils/
form_restore.py -> #form-restore-data -> static/js/form_restore.js).

pytest has no browser, so these tests pin the server half: the status is the
re-render (200, not 302), nothing was written, and the page embeds every
submitted field/row in order, with the names/units/batch flags the pickers need.
The script half was run against the real rendered pages in jsdom (see the
change notes), not here.
"""
import json
import re
from datetime import date
from decimal import Decimal

from app import (
    app as flask_app, db, Item, Purchase, PurchaseItem, SaleItem, StockAdjustment,
    CustomerPayment,
)
from salpurflask.models.models import Batch
from tests.test_batch_phase_c import (
    _books as _batch_books, _item as _batch_item, _customer as _batch_customer,
    _receive_batch, _sale_via_form, _post_sale, _admin as _batch_admin,
    _manager as _batch_manager,
)
from tests.test_sale_correction import (
    _admin, _books as _corr_books, _cash_account_id, _correction_payload,
    make_customer, make_item, make_posted_sale,
)
from tests.test_sale_insufficient_stock_preserve import _item as _plain_item, _customer as _plain_customer
from tests.test_sale_insufficient_stock_preserve import _books as _plain_books


def restore_payload(resp):
    """The JSON the page embeds for the restore script, or None."""
    m = re.search(r'<script id="form-restore-data" type="application/json">(.*?)</script>',
                  resp.get_data(as_text=True), re.S)
    return json.loads(m.group(1)) if m else None


# ── Purchase (new): errors that fall through ────────────────────────────────


def _purchase_post(client, rows, supplier_id="", date_str="2026-02-03", notes="", location_id=None):
    data = {
        "supplier_id": supplier_id, "date": date_str, "notes": notes,
        "item_id[]": [str(r["item"].id) for r in rows],
        "unit_id[]": [r.get("unit", "") for r in rows],
        "quantity[]": [str(r["qty"]) for r in rows],
        "purchase_price[]": [str(r["price"]) for r in rows],
        "discount_type[]": [r.get("d_type", "percent") for r in rows],
        "discount_value[]": [str(r.get("d_val", "0")) for r in rows],
        "tax_percent[]": [str(r.get("tax", "0")) for r in rows],
        "batch_no[]": [r.get("batch_no", "") for r in rows],
        "expiry_date[]": [r.get("expiry", "") for r in rows],
    }
    if location_id is not None:
        data["location_id"] = str(location_id)
    return client.post("/purchase", data=data)


def _supplier(name="Supplier A"):
    from app import Supplier, sync_supplier_opening
    s = Supplier(name=name, contact="0300", address="X", opening_balance=0)
    db.session.add(s); db.session.flush()
    sync_supplier_opening(s); db.session.commit()
    return s


def test_required_field_error_returns_every_row_in_order(appctx):
    _plain_books()
    a, b = _plain_item("Alpha", 5), _plain_item("Bravo", 5)
    client = _batch_manager()

    resp = _purchase_post(client, [
        {"item": a, "qty": 7, "price": "11.5", "d_type": "fixed", "d_val": "3", "tax": "10"},
        {"item": b, "qty": 2, "price": "40", "tax": "0"},
    ], supplier_id="", notes="call before delivery")   # supplier missing

    assert resp.status_code == 200
    assert b"Supplier and date are required" in resp.data
    assert Purchase.query.count() == 0

    p = restore_payload(resp)
    f = p["fields"]
    assert f["item_id[]"] == [str(a.id), str(b.id)]
    assert f["quantity[]"] == ["7", "2"]
    assert f["purchase_price[]"] == ["11.5", "40"]
    assert f["discount_type[]"] == ["fixed", "percent"]
    assert f["discount_value[]"] == ["3", "0"]
    assert f["tax_percent[]"] == ["10", "0"]
    assert f["notes"] == ["call before delivery"] and f["date"] == ["2026-02-03"]
    # what the pickers show for the hidden ids, index-aligned with the rows
    assert [m["name"] for m in p["items"]] == ["Alpha", "Bravo"]
    assert all(m["units"] and m["units"][0]["name"] for m in p["items"])


def test_invalid_quantity_keeps_the_bad_value_and_the_other_rows(appctx):
    _plain_books()
    a, b = _plain_item("Alpha", 5), _plain_item("Bravo", 5)
    sup = _supplier()
    client = _batch_manager()

    resp = _purchase_post(client, [{"item": a, "qty": "abc", "price": "10"},
                                   {"item": b, "qty": 3, "price": "20"}], supplier_id=sup.id)

    assert resp.status_code == 200
    assert b"Quantity must be a positive whole number" in resp.data
    assert Purchase.query.count() == 0
    p = restore_payload(resp)
    assert p["fields"]["quantity[]"] == ["abc", "3"]
    assert p["fields"]["supplier_id"] == [str(sup.id)]
    assert p["labels"]["supplier_id"] == "Supplier A"


def test_invalid_batch_expiry_keeps_batch_columns(appctx):
    _batch_books()
    med = _batch_item("Medicine", batch_tracked=True)
    sup = _supplier()
    client = _batch_manager()

    resp = _purchase_post(client, [{"item": med, "qty": 4, "price": "10",
                                    "batch_no": "LOT-9", "expiry": "31/12/2027"}],
                          supplier_id=sup.id)

    assert resp.status_code == 200
    assert b"invalid expiry date" in resp.data
    assert Purchase.query.count() == 0 and PurchaseItem.query.count() == 0     # rolled back
    p = restore_payload(resp)
    assert p["fields"]["batch_no[]"] == ["LOT-9"]
    assert p["fields"]["expiry_date[]"] == ["31/12/2027"]
    assert p["items"][0]["batch_tracked"] is True       # so the page can show the batch columns


# ── Admin correction: errors used to redirect (the POST was lost) ───────────


def _corrected_form(cust, item, **extra):
    payload = _correction_payload(item, qty=500, price=100, extra=extra or None)
    payload["customer_id"] = str(cust.id)
    return payload


def test_correct_sale_insufficient_stock_rerenders_with_entered_values(appctx):
    _corr_books()
    item = make_item(stock=100); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    c = _admin()

    payload = _corrected_form(cust, item)
    payload["notes"] = "typed note"
    payload["reason"] = "customer asked for more"
    resp = c.post(f"/sale/correct/{sale.id}", data=payload)

    assert resp.status_code == 200                       # was 302 -> fresh GET -> empty edits
    assert b"insufficient stock" in resp.data.lower()
    assert db.session.get(SaleItem, si.id).quantity == 10    # nothing was applied
    p = restore_payload(resp)
    assert p["fields"]["quantity[]"] == ["500"]
    assert p["fields"]["reason"] == ["customer asked for more"]
    assert p["fields"]["notes"] == ["typed note"]
    assert p["labels"]["customer_id"] == cust.name


def test_correct_sale_needing_confirmation_keeps_everything(appctx):
    _corr_books()
    item = make_item(stock=100); cust = make_customer()
    sale, si = make_posted_sale(cust, item, qty=10, price=100)
    db.session.add(CustomerPayment(customer_id=cust.id, sale_id=sale.id, amount=500,
                                   payment_method="Cash", account_id=_cash_account_id()))
    db.session.commit()
    c = _admin()

    payload = _correction_payload(item, qty=5, price=80)         # no confirm_correction
    payload["customer_id"] = str(cust.id)
    resp = c.post(f"/sale/correct/{sale.id}", data=payload)

    assert resp.status_code == 200
    assert b"Confirm to continue" in resp.data
    assert db.session.get(SaleItem, si.id).quantity == 10
    p = restore_payload(resp)
    assert p["fields"]["quantity[]"] == ["5"] and p["fields"]["sale_price[]"] == ["80"]
    assert p["fields"]["reason"] == ["test correction"]


def test_correct_sale_expired_batch_error_rerenders_and_rolls_back(appctx):
    _batch_books()
    med = _batch_item("Medicine", batch_tracked=True)
    cust = _batch_customer()
    batch = _receive_batch(med, "B1", 10, expiry_date=date(2027, 1, 1))
    admin = _batch_admin()
    _sale_via_form(admin, cust, med, 4, 20)
    sale = _latest(db)
    _post_sale(admin, sale.id)
    original_qty = sale.line_items[0].quantity

    batch = db.session.get(Batch, batch.id)
    batch.expiry_date = date(2020, 1, 1)               # the batch expires before the correction
    db.session.commit()

    resp = admin.post(f"/sale/correct/{sale.id}", data={
        "customer_id": str(cust.id), "notes": "n", "reason": "fix qty",
        "item_id[]": [str(med.id)], "quantity[]": ["6"], "sale_price[]": ["25"],
        "discount_type[]": ["percent"], "discount_value[]": ["0"],
        "tax_percent[]": ["0"], "unit_id[]": [""],
    })

    assert resp.status_code == 200
    assert b"alert-danger" in resp.data
    db.session.expire_all()
    assert db.session.get(type(sale), sale.id).line_items[0].quantity == original_qty   # rolled back
    p = restore_payload(resp)
    assert p["fields"]["quantity[]"] == ["6"] and p["fields"]["sale_price[]"] == ["25"]
    assert p["fields"]["reason"] == ["fix qty"]


def _latest(_db):
    from app import Sale
    return Sale.query.order_by(Sale.id.desc()).first()


# ── Stock adjustment ─────────────────────────────────────────────────────────


def test_stock_adjustment_insufficient_stock_keeps_the_entry(appctx):
    _plain_books()
    item = _plain_item("Alpha", 5)
    client = _batch_manager()

    resp = client.post("/stock_adjustment", data={
        "item_id": str(item.id), "adj_type": "Damage Write-off", "quantity": "50",
        "date": "2026-02-03", "reason": "dropped pallet"})

    assert resp.status_code == 200
    assert b"Insufficient stock" in resp.data
    assert StockAdjustment.query.count() == 0
    f = restore_payload(resp)["fields"]
    assert f["quantity"] == ["50"] and f["reason"] == ["dropped pallet"]
    assert f["adj_type"] == ["Damage Write-off"] and f["item_id"] == [str(item.id)]


def test_stock_adjustment_wrong_batch_rerenders_instead_of_redirecting(appctx):
    _batch_books()
    mine = _batch_item("Medicine", batch_tracked=True)
    other = _batch_item("Syrup", batch_tracked=True)
    _receive_batch(mine, "M1", 10, expiry_date=date(2027, 1, 1))
    foreign = _receive_batch(other, "S1", 10, expiry_date=date(2027, 1, 1))
    client = _batch_manager()

    resp = client.post("/stock_adjustment", data={
        "item_id": str(mine.id), "adj_type": "Damage Write-off", "quantity": "2",
        "date": "2026-02-03", "reason": "wrong lot picked", "batch_id": str(foreign.id)})

    assert resp.status_code == 200                      # was a 302 to a blank page
    assert b"does not exist or does not belong to this item" in resp.data
    assert StockAdjustment.query.count() == 0
    f = restore_payload(resp)["fields"]
    assert f["batch_id"] == [str(foreign.id)] and f["reason"] == ["wrong lot picked"]


# ── What must NOT change ─────────────────────────────────────────────────────


def test_no_payload_on_a_plain_page_load(appctx):
    _plain_books()
    client = _batch_manager()
    assert restore_payload(client.get("/purchase")) is None
    assert restore_payload(client.get("/stock_adjustment")) is None


def test_successful_post_is_still_a_redirect_with_no_payload(appctx):
    _plain_books()
    a = _plain_item("Alpha", 5)
    sup = _supplier()
    client = _batch_manager()

    resp = _purchase_post(client, [{"item": a, "qty": 3, "price": "10"}], supplier_id=sup.id)

    assert resp.status_code == 302
    assert Purchase.query.count() == 1


def test_a_form_that_fills_itself_still_works_and_gets_the_same_values(appctx):
    """/sale fills itself (form_data) -- the restore payload only repeats it."""
    _plain_books()
    cust = _plain_customer()
    item = _plain_item("Alpha", 2)
    client = _batch_manager()

    resp = client.post("/sale", data={
        "customer_id": str(cust.id), "date": "2026-01-01", "notes": "",
        "item_id[]": str(item.id), "quantity[]": "9", "sale_price[]": "20",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0"})

    assert b"Insufficient stock" in resp.data
    assert restore_payload(resp)["fields"]["quantity[]"] == ["9"]


def test_passwords_and_tokens_are_never_echoed(appctx):
    from flask import flash
    from salpurflask.utils.form_restore import build_form_restore

    form = {"email": "a@b.c", "password": "hunter2", "confirm_password": "hunter2",
            "csrf_token": "abc", "api_token": "t0k", "name": "N",
            "ANTHROPIC_API_KEY": "sk-x", "key": "SECRET_KEY", "value": "s3cret", "pin": "1234",
            "discount_value[]": "5"}

    with flask_app.test_request_context("/x", method="POST", data=form):
        assert build_form_restore() is None          # no error flashed -> nothing to restore

    with flask_app.test_request_context("/x", method="POST", data=form):
        flash("Something went wrong", "danger")
        fields = build_form_restore()["fields"]

    assert fields == {"email": ["a@b.c"], "name": ["N"], "discount_value[]": ["5"]}


# ── Multi-row forms: quotation, purchase order, transfer, edit sale / purchase ──
#
# Each submits three rows with one bad row. The page must come back (200, not a
# redirect), nothing may have been written, every row must be in the payload in
# order, and the bad row must be marked. A good submission is unchanged.

from tests.test_sale_insufficient_stock_preserve import (   # noqa: E402
    _manager as _plain_manager, _post as _sale_post,
)
from tests.test_transfers import (   # noqa: E402
    _second_location as _xfer_location, _item as _xfer_item, _manager as _xfer_manager,
)


def _line(item, qty, price, d_type="percent", d_val="0", tax="0"):
    return {"item": item, "qty": qty, "price": price, "d_type": d_type, "d_val": d_val, "tax": tax}


def _rows_data(rows, price_name):
    return {
        "item_id[]": [str(r["item"].id) for r in rows], "unit_id[]": [""] * len(rows),
        "quantity[]": [str(r["qty"]) for r in rows], price_name: [str(r["price"]) for r in rows],
        "discount_type[]": [r["d_type"] for r in rows], "discount_value[]": [r["d_val"] for r in rows],
        "tax_percent[]": [r["tax"] for r in rows],
    }


def _three(a, b, c, bad=None):
    rows = [_line(a, 7, "21.50", "percent", "5", "10"),
            _line(b, 2, "33", "fixed", "3", "0"),
            _line(c, 4, "44.25", "percent", "0", "17")]
    for index, changes in (bad or {}).items():
        rows[index].update(changes)
    return rows


def _three_items(stock=50):
    return _plain_item("Alpha", stock), _plain_item("Bravo", stock), _plain_item("Charlie", stock)


def _assert_rows_in_payload(payload, rows, price_name):
    f = payload["fields"]
    assert f["item_id[]"] == [str(r["item"].id) for r in rows]
    assert f["quantity[]"] == [str(r["qty"]) for r in rows]
    assert f[price_name] == [str(r["price"]) for r in rows]
    assert f["discount_type[]"] == [r["d_type"] for r in rows]
    assert f["discount_value[]"] == [r["d_val"] for r in rows]
    assert f["tax_percent[]"] == [r["tax"] for r in rows]
    assert [m["name"] for m in payload["items"]] == ["Alpha", "Bravo", "Charlie"]


def _quotation_post(client, cust, rows):
    data = {"customer_id": str(cust.id), "quote_date": "2026-03-01", "valid_until": "2026-04-01",
            "notes": "q-note"}
    data.update(_rows_data(rows, "sale_price[]"))
    return client.post("/quotations", data=data)


def test_quotation_error_keeps_all_rows_and_marks_the_bad_one(appctx):
    from app import Quotation
    _plain_books()
    cust = _plain_customer("Quote Cust")
    a, b, c = _three_items()
    client = _plain_manager()
    rows = _three(a, b, c, bad={1: {"qty": "abc"}})

    resp = _quotation_post(client, cust, rows)

    assert resp.status_code == 200
    assert b"Quantity must be a positive whole number" in resp.data
    assert Quotation.query.count() == 0
    p = restore_payload(resp)
    _assert_rows_in_payload(p, rows, "sale_price[]")
    assert p["invalid"] == {"quantity[]": [1]}
    assert p["labels"]["customer_id"] == "Quote Cust"
    assert p["fields"]["valid_until"] == ["2026-04-01"] and p["fields"]["notes"] == ["q-note"]


def test_quotation_success_is_unchanged(appctx):
    from app import Quotation
    _plain_books()
    cust = _plain_customer("Quote Cust")
    a, b, c = _three_items()

    resp = _quotation_post(_plain_manager(), cust, _three(a, b, c))

    assert resp.status_code == 302
    assert len(Quotation.query.one().line_items) == 3


def _po_post(client, sup, rows):
    data = {"supplier_id": str(sup.id), "order_date": "2026-03-01", "expected_date": "2026-03-20",
            "notes": "po-note"}
    data.update(_rows_data(rows, "purchase_price[]"))
    return client.post("/purchase_orders", data=data)


def test_purchase_order_error_keeps_all_rows_and_marks_the_bad_one(appctx):
    from app import PurchaseOrder
    _plain_books()
    sup = _supplier("PO Sup")
    a, b, c = _three_items()
    client = _plain_manager()
    rows = _three(a, b, c, bad={2: {"price": "-5"}})

    resp = _po_post(client, sup, rows)

    assert resp.status_code == 200
    assert b"Price cannot be negative" in resp.data
    assert PurchaseOrder.query.count() == 0
    p = restore_payload(resp)
    _assert_rows_in_payload(p, rows, "purchase_price[]")
    assert p["invalid"] == {"purchase_price[]": [2]}
    assert p["labels"]["supplier_id"] == "PO Sup"


def test_purchase_order_success_is_unchanged(appctx):
    from app import PurchaseOrder
    _plain_books()
    sup = _supplier("PO Sup")
    a, b, c = _three_items()

    resp = _po_post(_plain_manager(), sup, _three(a, b, c))

    assert resp.status_code == 302
    assert PurchaseOrder.query.count() == 1


def _transfer_post(client, source, dest, items, quantities):
    return client.post("/transfers/new", data={
        "source_location_id": str(source.id), "destination_location_id": str(dest.id),
        "date": "2026-03-01", "notes": "t-note",
        "item_id[]": [str(i.id) for i in items], "quantity[]": quantities,
        "batch_id[]": [""] * len(items)})


def test_transfer_error_rerenders_with_all_rows_and_marks_the_bad_one(appctx):
    from salpurflask.models.inventory_location import Transfer, get_or_create_default_location
    default = get_or_create_default_location()
    dest = _xfer_location("Second WH")
    a, b, c = (_xfer_item(stock=100, name=n) for n in ("Alpha", "Bravo", "Charlie"))
    client = _xfer_manager()

    resp = _transfer_post(client, default, dest, [a, b, c], ["10", "0", "5"])

    assert resp.status_code == 200                       # was a 302 to an empty form
    assert b"Bravo: quantity must be greater than zero" in resp.data
    assert Transfer.query.count() == 0
    p = restore_payload(resp)
    f = p["fields"]
    assert f["source_location_id"] == [str(default.id)] and f["destination_location_id"] == [str(dest.id)]
    assert f["item_id[]"] == [str(a.id), str(b.id), str(c.id)] and f["quantity[]"] == ["10", "0", "5"]
    assert f["notes"] == ["t-note"] and f["date"] == ["2026-03-01"]
    assert p["invalid"] == {"quantity[]": [1]}


def test_transfer_non_numeric_quantity_also_rerenders(appctx):
    from salpurflask.models.inventory_location import Transfer, get_or_create_default_location
    default = get_or_create_default_location()
    dest = _xfer_location("Second WH")
    a, b = _xfer_item(stock=100, name="Alpha"), _xfer_item(stock=100, name="Bravo")

    resp = _transfer_post(_xfer_manager(), default, dest, [a, b], ["10", "x"])

    assert resp.status_code == 200
    assert b"Quantity must be a whole number" in resp.data
    assert Transfer.query.count() == 0
    assert restore_payload(resp)["invalid"] == {"quantity[]": [1]}


def test_transfer_success_is_unchanged(appctx):
    from salpurflask.models.inventory_location import Transfer, get_or_create_default_location
    default = get_or_create_default_location()
    dest = _xfer_location("Second WH")
    a, b = _xfer_item(stock=100, name="Alpha"), _xfer_item(stock=100, name="Bravo")

    resp = _transfer_post(_xfer_manager(), default, dest, [a, b], ["10", "5"])

    assert resp.status_code == 302
    assert Transfer.query.one().status == "Draft"


def test_edit_sale_error_keeps_submitted_rows_and_the_saved_sale_untouched(appctx):
    from app import Sale
    _plain_books()
    cust = _plain_customer("Edit Cust")
    a, b, c = _plain_item("Alpha", 100), _plain_item("Bravo", 3), _plain_item("Charlie", 50)
    client = _plain_manager()
    _sale_post(client, cust, [_line(a, 2, "20"), _line(c, 1, "30")])
    sal = Sale.query.one()
    rows = [_line(a, 7, "21.50", "percent", "5", "10"), _line(b, 9, "33", "fixed", "3"),
            _line(c, 4, "44.25", "percent", "0", "17")]

    data = {"customer_id": str(cust.id), "date": "2026-02-03", "notes": "edit-note"}
    data.update(_rows_data(rows, "sale_price[]"))
    resp = client.post(f"/sale/edit/{sal.id}", data=data)

    assert resp.status_code == 200
    assert b"Insufficient stock" in resp.data and b"Bravo" in resp.data
    db.session.expire_all()
    assert sorted(li.quantity for li in db.session.get(Sale, sal.id).line_items) == [1, 2]   # unchanged
    p = restore_payload(resp)
    _assert_rows_in_payload(p, rows, "sale_price[]")
    assert p["invalid"] == {"quantity[]": [1]}             # the Bravo row
    assert p["fields"]["notes"] == ["edit-note"]

    # Fix the quantity and save: the edit goes through.
    rows[1]["qty"] = 3
    data.update(_rows_data(rows, "sale_price[]"))
    ok = client.post(f"/sale/edit/{sal.id}", data=data)
    assert ok.status_code == 302
    db.session.expire_all()
    assert sorted(li.quantity for li in db.session.get(Sale, sal.id).line_items) == [3, 4, 7]


def test_edit_purchase_error_keeps_submitted_rows_and_the_saved_purchase_untouched(appctx):
    _plain_books()
    sup = _supplier("Edit Sup")
    a, b, c = _three_items(stock=5)
    client = _plain_manager()
    _purchase_post(client, [_line(a, 2, "10"), _line(c, 1, "30")], supplier_id=sup.id)
    pur = Purchase.query.one()
    rows = [_line(a, 7, "21.50", "percent", "5", "10"), _line(b, "abc", "33", "fixed", "2"),
            _line(c, 4, "44.25", "percent", "0", "17")]

    data = {"supplier_id": str(sup.id), "date": "2026-02-03", "notes": "pedit-note",
            "batch_no[]": ["", "", ""], "expiry_date[]": ["", "", ""]}
    data.update(_rows_data(rows, "purchase_price[]"))
    resp = client.post(f"/purchase/edit/{pur.id}", data=data)

    assert resp.status_code == 200
    assert b"Quantity must be a positive whole number" in resp.data
    db.session.expire_all()
    assert sorted(li.quantity for li in db.session.get(Purchase, pur.id).line_items) == [1, 2]
    p = restore_payload(resp)
    _assert_rows_in_payload(p, rows, "purchase_price[]")
    assert p["invalid"] == {"quantity[]": [1]}

    rows[1]["qty"] = 3
    data.update(_rows_data(rows, "purchase_price[]"))
    ok = client.post(f"/purchase/edit/{pur.id}", data=data)
    assert ok.status_code == 302
    db.session.expire_all()
    assert sorted(li.quantity for li in db.session.get(Purchase, pur.id).line_items) == [3, 4, 7]
