"""Item pickers must show the SELECTED warehouse's stock, not the company total.

Item.stock is the sum over every warehouse. A picker that showed it next to a form
about to move stock at one warehouse said "Stock: 63" for a warehouse holding 0 --
and the save then failed the per-warehouse check (stock_at_location, unchanged).

One server helper (services/location_stock.py) now feeds every picker: the lookup
modal's API (/api/items/lookup), the refresh API (/api/items/stock) and POS search.
The display half (static/js/item_lookup.js) is exercised in jsdom, see the change notes.

Backend validation is deliberately NOT touched: the last tests prove a sale / transfer
from the empty warehouse is still refused, and one from the stocked warehouse still works.
"""
from decimal import Decimal

from app import app as flask_app, db, Item, Sale, User, pwd_context, Category
from salpurflask.models.inventory_location import (
    Branch, ItemStock, Location, get_or_create_default_location, stock_at_location,
)
from salpurflask.models.models import item_add_stock
from salpurflask.services.location_stock import stock_by_location


def _login(role, email):
    from flask import g
    try:
        del g._login_user
    except AttributeError:
        pass
    u = User(name=role, email=email, password=pwd_context.hash("secret123"),
             verified=True, role=role)
    db.session.add(u); db.session.commit()
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True
    return c, u


def _location(name):
    get_or_create_default_location()
    branch = Branch.query.filter_by(is_default=True).first()
    loc = Location(name=name, kind="warehouse", branch_id=branch.id)
    db.session.add(loc); db.session.commit()
    return loc


def _item(name="Cola"):
    cat = Category.query.first() or Category(name="Cat")
    db.session.add(cat); db.session.flush()
    it = Item(name=name, category_id=cat.id, stock=0, purchase_price=10, sale_price=20,
              item_type="STOCK")
    db.session.add(it); db.session.commit()
    return it


def _stock(item, location, qty):
    item_add_stock(item, qty, Decimal(str(qty * 10)), location_id=location.id)
    db.session.commit()


def _world():
    """One item: Main 0, North 18, South 63 (company total 81)."""
    main = get_or_create_default_location()
    north, south = _location("North"), _location("South")
    item = _item()
    _stock(item, north, 18)
    _stock(item, south, 63)
    return item, main, north, south


def _lookup(client, q="Cola", **params):
    qs = "&".join([f"q={q}"] + [f"{k}={v}" for k, v in params.items()])
    return client.get(f"/api/items/lookup?{qs}").get_json()["results"][0]


# ── The lookup API ───────────────────────────────────────────────────────────


def test_each_warehouse_shows_its_own_stock_with_the_full_breakdown(appctx):
    item, main, north, south = _world()
    c, _ = _login("manager", "m@t.com")
    by_name = {main.name: 0, "North": 18, "South": 63}

    for loc, expected in ((main, 0), (north, 18), (south, 63)):
        row = _lookup(c, location_id=loc.id)
        assert row["stock"] == expected                      # the SELECTED warehouse
        assert row["stock_total"] == 81                      # company total still available
        assert row["location_id"] == loc.id and row["location_name"] == loc.name
        assert {b["name"]: b["qty"] for b in row["stock_by_location"]} == by_name
    # the breakdown is the same in every case and ordered by warehouse name
    names = [b["name"] for b in row["stock_by_location"]]
    assert names == sorted(names)


def test_without_a_warehouse_stock_is_still_the_company_total(appctx):
    item, *_ = _world()
    c, _ = _login("manager", "m@t.com")

    row = _lookup(c)

    assert row["stock"] == 81 and row["location_id"] is None
    assert len(row["stock_by_location"]) == 3                # breakdown is there regardless


def test_breakdown_agrees_with_the_number_the_save_validates_against(appctx):
    """stock_at_location() is what a sale / transfer / adjustment checks. Including
    the legacy rule: an item with no ItemStock row anywhere lives at the default
    warehouse."""
    item, main, north, south = _world()
    legacy = _item("Legacy")
    legacy.stock = 40                                        # Item.stock only, no ItemStock rows
    db.session.commit()
    assert ItemStock.query.filter_by(item_id=legacy.id).count() == 0

    result = stock_by_location([item, legacy])

    for it in (item, legacy):
        for entry in result[it.id]:
            assert entry["qty"] == stock_at_location(it.id, entry["id"])
    assert {b["name"]: b["qty"] for b in result[legacy.id]}[main.name] == 40


def test_a_restricted_user_sees_only_their_warehouses_and_gets_nothing_for_others(appctx):
    from salpurflask.models import UserLocationAccess
    item, main, north, south = _world()
    c, user = _login("manager", "restricted@t.com")
    db.session.add(UserLocationAccess(user_id=user.id, location_id=south.id))
    db.session.commit()

    row = _lookup(c, location_id=south.id)
    assert row["stock"] == 63
    assert [b["name"] for b in row["stock_by_location"]] == ["South"]

    # Asking for a warehouse they have no access to: it is ignored (not an error --
    # the picker still has to search); no North figure is returned in any form.
    other = _lookup(c, location_id=north.id)
    assert other["location_id"] is None and other["location_name"] is None
    assert [b["name"] for b in other["stock_by_location"]] == ["South"]
    refresh = c.get(f"/api/items/stock?ids={item.id}&location_id={north.id}")
    assert refresh.status_code == 200
    assert refresh.get_json()["items"][str(item.id)]["location_id"] is None


def test_refresh_endpoint_returns_the_same_fields_for_picked_items(appctx):
    item, main, north, south = _world()
    other = _item("Fanta"); _stock(other, main, 7)
    c, _ = _login("manager", "m@t.com")

    data = c.get(f"/api/items/stock?ids={item.id},{other.id}&location_id={main.id}").get_json()["items"]

    assert data[str(item.id)]["stock"] == 0 and data[str(item.id)]["stock_total"] == 81
    assert data[str(other.id)]["stock"] == 7
    assert c.get("/api/items/stock").get_json() == {"items": {}}        # no ids -> nothing


# ── POS ──────────────────────────────────────────────────────────────────────


def test_pos_search_shows_the_warehouse_the_sale_is_rung_from(appctx):
    """POS checkout resolves a blank location to the default warehouse; the search
    must show that warehouse's stock, not the company total checkout then rejects."""
    item, main, north, south = _world()
    c, _ = _login("manager", "m@t.com")
    from salpurflask.models import BusinessCategory
    cat = BusinessCategory(name="Drinks", slug="drinks", is_enabled=True)
    db.session.add(cat); db.session.flush()
    item.business_category_id = cat.id
    db.session.commit()

    row = c.get("/pos/lookup?q=Cola").get_json()["items"][0]

    assert row["stock"] == 0 and row["location_name"] == main.name
    assert {b["name"]: b["qty"] for b in row["stock_by_location"]} == {main.name: 0, "North": 18, "South": 63}
    south_row = c.get(f"/pos/lookup?q=Cola&location_id={south.id}").get_json()["items"][0]
    assert south_row["stock"] == 63


# ── Backend validation is unchanged ──────────────────────────────────────────


def _sale_form(client, customer, item, location, qty):
    return client.post("/sale", data={
        "customer_id": str(customer.id), "date": "2026-01-01", "notes": "",
        "location_id": str(location.id),
        "item_id[]": str(item.id), "quantity[]": str(qty), "sale_price[]": "20",
        "discount_type[]": "percent", "discount_value[]": "0", "tax_percent[]": "0"})


def test_sale_from_the_empty_warehouse_is_still_refused_and_from_the_stocked_one_saves(appctx):
    from tests.test_sale_insufficient_stock_preserve import _books, _customer
    _books()
    item, main, north, south = _world()
    cust = _customer()
    c, _ = _login("manager", "m@t.com")

    refused = _sale_form(c, cust, item, main, 5)             # picker said 0 here; company total is 81
    assert refused.status_code == 200
    assert b"Insufficient stock" in refused.data and b"only 0" in refused.data
    assert Sale.query.count() == 0

    saved = _sale_form(c, cust, item, south, 5)
    assert saved.status_code == 302
    assert Sale.query.count() == 1


def test_transfer_from_the_empty_warehouse_is_still_refused_and_from_the_stocked_one_works(appctx):
    from salpurflask.models.inventory_location import Transfer
    item, main, north, south = _world()
    c, _ = _login("admin", "a@t.com")

    def make(source, dest):
        c.post("/transfers/new", data={
            "source_location_id": str(source.id), "destination_location_id": str(dest.id),
            "date": "2026-03-01", "notes": "", "item_id[]": str(item.id), "quantity[]": "10",
            "batch_id[]": ""})
        return Transfer.query.order_by(Transfer.id.desc()).first()

    from_main = make(main, north)                            # draft is allowed ...
    r = c.post(f"/transfers/{from_main.id}/confirm", follow_redirects=True)
    assert b"Insufficient stock" in r.data                   # ... but confirming it is refused
    db.session.expire_all()
    assert stock_at_location(item.id, main.id) == 0

    from_south = make(south, north)
    c.post(f"/transfers/{from_south.id}/confirm", follow_redirects=True)
    db.session.expire_all()
    assert stock_at_location(item.id, south.id) == 53 and stock_at_location(item.id, north.id) == 28
