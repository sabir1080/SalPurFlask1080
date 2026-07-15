"""Printable barcode / QR labels for stock. The value is that the code on the sticker is
the same string the POS looks the item up by — print it, stick it, scan it, and the till
finds the item. These tests cover the code generation, the label sheet, and the one-click
assignment of codes to items that have none.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context, Category, Item,
    code_svg,
)


def _manager(email="m@t.com"):
    db.session.add(User(name="M", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="manager"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _item(name="Widget", barcode="8964000112233"):
    cat = Category.query.first() or Category(name="X")
    if cat.id is None:
        db.session.add(cat); db.session.flush()
    it = Item(name=name, category_id=cat.id, unit="Pcs", barcode=barcode,
              sale_price=Decimal("100"), opening_stock=0, stock=0, reorder_level=0,
              inventory_value=0)
    db.session.add(it); db.session.commit()
    return it


def test_a_barcode_and_a_qr_both_render_as_inline_svg():
    bc = code_svg("8964000112233", "barcode")
    qr = code_svg("8964000112233", "qr")
    assert bc.startswith("<svg") and len(bc) > 200
    assert qr.startswith("<svg") and len(qr) > 200


def test_a_blank_or_broken_code_does_not_crash_the_sheet():
    assert code_svg("", "barcode") == ""
    assert code_svg(None, "qr") == ""


def test_the_label_sheet_shows_a_label_only_when_copies_are_asked_for(appctx):
    it = _item()
    c = _manager()

    # no copies requested -> no labels rendered
    body = c.get("/labels").get_data(as_text=True)
    assert "label(s) ready" not in body

    # ask for two -> two labels, each carrying the code the POS scans
    body = c.get(f"/labels?copies_{it.id}=2&type=barcode").get_data(as_text=True)
    assert "2 label(s) ready" in body
    assert it.barcode in body
    assert body.count("<svg") >= 2


def test_qr_labels_render_when_asked_for(appctx):
    it = _item()
    c = _manager()
    body = c.get(f"/labels?copies_{it.id}=1&type=qr").get_data(as_text=True)
    assert "1 label(s) ready" in body
    assert "<svg" in body


def test_items_without_a_code_can_be_assigned_one_in_one_click(appctx):
    a = _item("Coded", "111222333")
    b = _item("No code", None)
    c = _manager()

    # the page flags the item that has no code
    assert "have no barcode yet" in c.get("/labels").get_data(as_text=True)

    c.post("/labels/assign", follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(Item, a.id).barcode == "111222333"      # untouched
    assigned = db.session.get(Item, b.id).barcode
    assert assigned and assigned.isdigit()                        # numeric, scanner-friendly
    # and it is the value the POS will match on
    r = c.get(f"/pos/lookup?q={assigned}")
    assert r.get_json()["items"][0]["id"] == b.id


def test_two_items_cannot_share_a_barcode(appctx):
    """A scanned code has to name one product. If two items carried the same barcode, the
    POS would ring up whichever it found first — silently the wrong thing."""
    _item("Cola", "8964000112233")
    c = _manager()
    cat = Category.query.first()

    # adding a second item with the same code is refused
    r = c.post("/item", data={
        "name": "Pepsi", "category_id": cat.id, "unit": "Pcs", "opening_stock": "0",
        "reorder_level": "5", "sale_price": "100", "barcode": "8964000112233",
    }, follow_redirects=True)
    assert "already used by another item" in r.get_data(as_text=True)
    assert Item.query.filter_by(name="Pepsi").count() == 0

    # a different (company) barcode is fine
    c.post("/item", data={
        "name": "Pepsi", "category_id": cat.id, "unit": "Pcs", "opening_stock": "0",
        "reorder_level": "5", "sale_price": "100", "barcode": "8964000999999",
    }, follow_redirects=True)
    assert Item.query.filter_by(name="Pepsi").one().barcode == "8964000999999"


def test_editing_an_item_keeps_its_own_barcode(appctx):
    """The duplicate check must not trip on the item's own code when it is edited."""
    it = _item("Cola", "8964000112233")
    c = _manager()
    cat = Category.query.first()
    r = c.post(f"/item/edit/{it.id}", data={
        "name": "Cola 1L", "category_id": cat.id, "unit": "Pcs",
        "opening_stock": "0", "reorder_level": "5", "sale_price": "120",
        "barcode": "8964000112233",             # unchanged — its own code
    }, follow_redirects=True)
    assert "already used" not in r.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(Item, it.id).name == "Cola 1L"


def test_staff_cannot_open_the_label_printer(appctx):
    db.session.add(User(name="S", email="s@t.com", password=pwd_context.hash("secret123"),
                        verified=True, role="staff"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": "s@t.com", "password": "secret123"})
    assert c.get("/labels").status_code in (301, 302)
