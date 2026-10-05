"""A new Sale that fails on insufficient stock must not lose what the user typed.

/sale validates EVERY row's stock before writing anything (items summed per item,
so the same item on two rows is checked against its combined quantity). On any
failure it re-renders the form with the customer, date, notes, warehouse and every
row -- in order -- filled back in, so the user only has to fix the quantity.

These tests read the rendered HTML form (not just the response data), so they pin
what the user would actually see and re-submit.
"""
from html.parser import HTMLParser
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Customer, Sale, SaleItem,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, post_item_opening, sync_customer_opening,
)
from salpurflask.models.business_config import BusinessCategory
from salpurflask.models.inventory_location import Branch, Location


def _books():
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    seed_fiscal_year(2026)
    for m, t in (("Cash", "Cash"), ("Bank", "Bank"), ("Cheque", "Bank"), ("Online", "Bank")):
        db.session.add(FinancialAccount(name=m, method=m, account_type=t, opening_balance=0))
    db.session.commit()
    seed_financial_account_links()


def _item(name, stock):
    bcat = BusinessCategory(name="Cat-" + name, slug="cat-" + name.lower(), is_enabled=True)
    db.session.add(bcat); db.session.flush()
    it = Item(name=name, business_category_id=bcat.id, unit="Pcs",
             purchase_price=Decimal("10"), sale_price=Decimal("20"),
             opening_stock=stock, stock=stock, inventory_value=Decimal(str(stock * 10)))
    db.session.add(it); db.session.flush()
    post_item_opening(it)
    db.session.commit()
    return it


def _customer(name="Customer A"):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=0)
    db.session.add(c); db.session.flush()
    sync_customer_opening(c); db.session.commit()
    return c


def _manager():
    from flask import g
    try:
        del g._login_user
    except AttributeError:
        pass
    u = User(name="M", email="m@t.com", password=pwd_context.hash("secret123"),
             verified=True, role="manager")
    db.session.add(u); db.session.commit()
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True
    return c


def _second_location(name="Second WH"):
    from salpurflask.models.inventory_location import get_or_create_default_location
    get_or_create_default_location()     # the wiped test DB has no default branch until this
    branch = Branch.query.filter_by(is_default=True).first()
    loc = Location(name=name, kind="warehouse", branch_id=branch.id)
    db.session.add(loc); db.session.commit()
    return loc


class _FormReader(HTMLParser):
    """Collects what a browser would submit from #saleForm: every named field in
    document order, with the selected option for <select>s, plus the visible
    customer / item labels."""

    def __init__(self):
        super().__init__()
        self.in_form = False
        self.fields = []            # (name, value) in order
        self._select = None         # [name, selected_value, first_value]
        self._option_value = None
        self.labels = []            # text of #customer_id_label and .item-picker-label
        self._label_depth = False
        self._label_text = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "saleForm":
            self.in_form = True
            return
        if not self.in_form:
            return
        if tag == "input" and a.get("name"):
            self.fields.append((a["name"], a.get("value", "")))
        elif tag == "select":
            self._select = [a.get("name"), None, None]
        elif tag == "option" and self._select is not None:
            self._option_value = a.get("value", "")
            if self._select[2] is None:
                self._select[2] = self._option_value
            if "selected" in a:
                self._select[1] = self._option_value
        elif tag == "span" and (a.get("id") == "customer_id_label"
                                or "item-picker-label" in (a.get("class") or "")):
            self._label_depth = True
            self._label_text = ""

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.in_form = False
        elif tag == "select" and self._select is not None:
            name, selected, first = self._select
            if name:
                self.fields.append((name, selected if selected is not None else (first or "")))
            self._select = None
        elif tag == "span" and self._label_depth:
            self.labels.append(self._label_text.strip())
            self._label_depth = False

    def handle_data(self, data):
        if self._label_depth:
            self._label_text += data

    def values(self, name):
        return [v for n, v in self.fields if n == name]


def _read_form(resp):
    r = _FormReader()
    r.feed(resp.get_data(as_text=True))
    return r


def _post(client, cust, rows, location_id=None, notes="", date="2026-01-01"):
    """rows: list of dicts item/qty/price/unit/d_type/d_val/tax."""
    data = {
        "customer_id": str(cust.id), "date": date, "notes": notes,
        "item_id[]": [str(r["item"].id) for r in rows],
        "unit_id[]": [r.get("unit", "") for r in rows],
        "quantity[]": [str(r["qty"]) for r in rows],
        "sale_price[]": [str(r["price"]) for r in rows],
        "discount_type[]": [r.get("d_type", "percent") for r in rows],
        "discount_value[]": [str(r.get("d_val", "0")) for r in rows],
        "tax_percent[]": [str(r.get("tax", "0")) for r in rows],
    }
    if location_id is not None:
        data["location_id"] = str(location_id)
    return client.post("/sale", data=data)   # no redirect: a failed save re-renders


# ── Failure keeps the whole form ────────────────────────────────────────────


def test_failed_save_restores_every_field_in_order(appctx):
    _books()
    cust = _customer("Zed Traders")
    a, b, c = _item("Alpha", 100), _item("Bravo", 3), _item("Charlie", 50)
    client = _manager()
    second = _second_location()          # >1 warehouse -> the Warehouse select is rendered
    from salpurflask.models.inventory_location import get_or_create_default_location
    default_loc = get_or_create_default_location()

    resp = _post(client, cust, [
        {"item": a, "qty": 7, "price": "21.50", "d_type": "percent", "d_val": "5", "tax": "10"},
        {"item": b, "qty": 9, "price": "33", "d_type": "fixed", "d_val": "2", "tax": "0"},   # short: only 3
        {"item": c, "qty": 4, "price": "44.25", "d_type": "percent", "d_val": "0", "tax": "17"},
    ], location_id=default_loc.id, notes="deliver friday", date="2026-02-03")

    assert resp.status_code == 200
    assert Sale.query.count() == 0 and SaleItem.query.count() == 0

    page = resp.get_data(as_text=True)
    assert "Insufficient stock" in page and "Bravo" in page
    assert "Alpha" not in page.split("Insufficient stock", 1)[1].split("</div>", 1)[0]

    form = _read_form(resp)
    # header
    assert form.values("customer_id") == [str(cust.id)]
    assert form.labels[0] == "Zed Traders"
    assert form.values("date") == ["2026-02-03"]
    assert form.values("notes") == ["deliver friday"]
    assert form.values("location_id") == [str(default_loc.id)]
    # rows: same order, every value back
    assert form.values("item_id[]") == [str(a.id), str(b.id), str(c.id)]
    assert form.labels[1:] == ["Alpha", "Bravo", "Charlie"]
    assert form.values("quantity[]") == ["7", "9", "4"]
    assert form.values("sale_price[]") == ["21.50", "33", "44.25"]
    assert form.values("discount_type[]") == ["percent", "fixed", "percent"]
    assert form.values("discount_value[]") == ["5", "2", "0"]
    assert form.values("tax_percent[]") == ["10", "0", "17"]
    assert form.values("unit_id[]") == ["", "", ""]


def test_failed_save_preserves_second_warehouse_choice(appctx):
    _books()
    cust = _customer()
    item = _item("Alpha", 100)
    client = _manager()
    second = _second_location()    # holds no stock -> the save fails there

    resp = _post(client, cust, [{"item": item, "qty": 5, "price": "20"}], location_id=second.id)

    assert Sale.query.count() == 0
    assert _read_form(resp).values("location_id") == [str(second.id)]


def test_every_short_item_is_named(appctx):
    _books()
    cust = _customer()
    a, b, c = _item("Alpha", 1), _item("Bravo", 100), _item("Charlie", 2)
    client = _manager()

    resp = _post(client, cust, [{"item": a, "qty": 5, "price": "20"},
                                {"item": b, "qty": 5, "price": "20"},
                                {"item": c, "qty": 5, "price": "20"}])

    msg = resp.get_data(as_text=True).split("Insufficient stock", 1)[1]
    assert "Alpha" in msg and "Charlie" in msg
    assert Sale.query.count() == 0
    assert _read_form(resp).values("quantity[]") == ["5", "5", "5"]


def test_same_item_on_two_rows_is_checked_against_the_combined_quantity(appctx):
    _books()
    cust = _customer()
    item = _item("Alpha", 8)
    client = _manager()

    # 5 + 5 = 10 > 8, though each row alone would pass.
    resp = _post(client, cust, [{"item": item, "qty": 5, "price": "20"},
                                {"item": item, "qty": 5, "price": "20"}])

    assert Sale.query.count() == 0
    assert "need 10" in resp.get_data(as_text=True)
    assert _read_form(resp).values("quantity[]") == ["5", "5"]


def test_other_validation_failure_also_keeps_the_form(appctx):
    _books()
    cust = _customer("Zed Traders")
    a, b = _item("Alpha", 100), _item("Bravo", 100)
    client = _manager()

    # Missing date -> "Customer and date are required"; rows must survive it too.
    resp = _post(client, cust, [{"item": a, "qty": 2, "price": "20"},
                                {"item": b, "qty": 3, "price": "25"}], date="")

    form = _read_form(resp)
    assert Sale.query.count() == 0
    assert form.values("item_id[]") == [str(a.id), str(b.id)]
    assert form.values("quantity[]") == ["2", "3"]


# ── ...and the corrected form saves ─────────────────────────────────────────


def test_corrected_form_can_be_resubmitted_and_saves(appctx):
    _books()
    cust = _customer()
    a, b = _item("Alpha", 100), _item("Bravo", 3)
    client = _manager()

    first = _post(client, cust, [{"item": a, "qty": 7, "price": "21.50", "tax": "10"},
                                 {"item": b, "qty": 9, "price": "33"}])
    assert Sale.query.count() == 0

    # Re-submit exactly what the restored form contains, with only Bravo's qty fixed.
    form = _read_form(first)
    qty = form.values("quantity[]")
    qty[1] = "3"
    resp = client.post("/sale", data={
        "customer_id": form.values("customer_id")[0], "date": form.values("date")[0],
        "notes": form.values("notes")[0], "location_id": form.values("location_id")[0],
        "item_id[]": form.values("item_id[]"), "unit_id[]": form.values("unit_id[]"),
        "quantity[]": qty, "sale_price[]": form.values("sale_price[]"),
        "discount_type[]": form.values("discount_type[]"),
        "discount_value[]": form.values("discount_value[]"),
        "tax_percent[]": form.values("tax_percent[]"),
    }, follow_redirects=True)

    assert resp.status_code == 200
    sal = Sale.query.one()
    lines = sorted((li.item_id, li.quantity, float(li.sale_price)) for li in sal.line_items)
    assert lines == sorted([(a.id, 7, 21.5), (b.id, 3, 33.0)])


def test_sufficient_stock_still_saves_a_draft(appctx):
    _books()
    cust = _customer()
    item = _item("Alpha", 10)
    client = _manager()

    resp = _post(client, cust, [{"item": item, "qty": 10, "price": "20"}])

    assert resp.status_code == 302
    assert Sale.query.count() == 1
