"""POS warehouse selector + the warehouse a cart is pinned to (P3).

A POS sale is made from ONE warehouse, chosen when the sale starts (the company default,
read fresh) and changeable for that sale only. The page sends it with every lookup, batch
lookup, hold and checkout; the server never trusts it: it must exist, be ACTIVE, and be
one the user may use, and every refusal is JSON (the POS script parses JSON -- an HTML
403 would only ever show as "Network error").

Runs on the dedicated PostgreSQL test database (conftest). The page's own JavaScript is
checked by hand / in jsdom; here: the server half.
"""
import json
import re

from app import db, Sale
from salpurflask.models import BusinessCategory, Location, PosHold, UserLocationAccess
from salpurflask.models.inventory_location import get_or_create_default_location, stock_at_location
from salpurflask.sales.routes import get_walkin_customer
from tests.test_picker_location_stock import _item, _location, _login, _stock
from tests.test_pos import _cash_account_id, _checkout, _world


def _setup():
    """Books + one POS-searchable item: Main 5, South 20, North 0 (North starts active)."""
    _world()                                   # chart, fiscal year, cash/bank accounts
    main = get_or_create_default_location()
    south, north = _location("South"), _location("North")
    item = _item("Widget")
    cat = BusinessCategory(name="Gadgets", slug="gadgets", is_enabled=True)
    db.session.add(cat); db.session.flush()
    item.business_category_id = cat.id
    db.session.commit()
    _stock(item, main, 5)
    _stock(item, south, 20)
    return item, main, south, north


class _Client:
    """Flask-Login caches the resolved user on flask.g, and the appctx fixture keeps one app
    context open for the whole test, so two test clients (e.g. an unrestricted manager and a
    restricted one) would both resolve to whichever made the first request. Clearing the
    cache before every request keeps each client its own user -- the same wrapper
    test_draft_sale.py and test_batch_phase_c.py use."""

    def __init__(self, client):
        self._client = client

    def _clear_g(self):
        from flask import g
        try:
            del g._login_user
        except AttributeError:
            pass

    def get(self, *args, **kwargs):
        self._clear_g()
        return self._client.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        self._clear_g()
        return self._client.post(*args, **kwargs)


def _manager(email="pos@t.com"):
    client, user = _login("manager", email)
    return _Client(client), user


def _restrict(user, *locations):
    for loc in locations:
        db.session.add(UserLocationAccess(user_id=user.id, location_id=loc.id))
    db.session.commit()


def _deactivate(location):
    location.active = False
    db.session.commit()


def _make_default(location):
    """What an admin changing the company default amounts to (test database only)."""
    for loc in Location.query.all():
        loc.is_default = (loc.id == location.id)
    db.session.commit()


def _pay(item, qty, location_id=None, **extra):
    payload = {"items": [{"item_id": item.id, "qty": qty, "price": 100}],
               "account_id": _cash_account_id(), "amount_paid": qty * 100}
    if location_id is not None:
        payload["location_id"] = str(location_id)
    payload.update(extra)
    return payload


def _page_state(client):
    """The warehouse list and default the POS page was rendered with."""
    html = client.get("/pos").get_data(as_text=True)
    warehouses = json.loads(re.search(r"INITIAL_WAREHOUSES = (\[.*?\]);", html, re.S).group(1))
    default = json.loads(re.search(r"INITIAL_DEFAULT_WAREHOUSE = (.+?);", html).group(1))
    assert 'id="posWarehouse"' in html                  # the selector is always on the page
    return [w["name"] for w in warehouses], default


def _assert_refused(resp, status):
    assert resp.status_code == status
    assert resp.is_json                                  # JSON, never the HTML 403 page
    body = resp.get_json()
    assert body["ok"] is False and body["code"] == "warehouse" and body["error"]


# ── Which warehouses, and which one a new sale starts on ───────────────────────


def test_page_lists_only_active_warehouses_and_starts_on_the_company_default(appctx):
    item, main, south, north = _setup()
    _deactivate(north)
    client, _ = _manager()

    names, default = _page_state(client)

    assert names == sorted([main.name, "South"])         # inactive North is not offered
    assert default == main.id


def test_restricted_user_only_gets_their_active_warehouses(appctx):
    item, main, south, north = _setup()
    _deactivate(north)
    client, user = _manager("restricted@t.com")
    _restrict(user, south, north)                        # North is assigned but inactive

    names, default = _page_state(client)

    assert names == ["South"]
    assert default == south.id                           # company default (Main) is not theirs


def test_a_new_sale_starts_on_the_default_as_it_is_now(appctx):
    item, main, south, north = _setup()
    client, _ = _manager()
    assert client.get("/pos/warehouses").get_json()["default_id"] == main.id

    _make_default(south)                                 # an admin changes the company default

    body = client.get("/pos/warehouses").get_json()
    assert body["ok"] and body["default_id"] == south.id
    assert sorted(w["name"] for w in body["warehouses"]) == sorted([main.name, "South", "North"])
    assert _page_state(client)[1] == south.id


def test_user_with_no_usable_warehouse_gets_an_empty_selector_and_is_refused(appctx):
    item, main, south, north = _setup()
    _deactivate(north)
    client, user = _manager("nowhere@t.com")
    _restrict(user, north)                               # their only warehouse is inactive

    names, default = _page_state(client)
    assert names == [] and default is None
    assert client.get("/pos/warehouses").get_json() == {"ok": True, "warehouses": [], "default_id": None}
    _assert_refused(client.get("/pos/lookup?q=Widget"), 403)
    _assert_refused(_checkout(client, _pay(item, 1)), 403)
    assert Sale.query.count() == 0


# ── Lookup and batch lookup follow the selected warehouse ──────────────────────


def test_lookup_shows_the_selected_warehouses_stock(appctx):
    item, main, south, north = _setup()
    client, _ = _manager()

    def row(**params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return client.get(f"/pos/lookup?q=Widget&{qs}").get_json()["items"][0]

    assert row(location_id=south.id)["stock"] == 20
    assert row(location_id=south.id)["location_id"] == south.id
    assert row(location_id=main.id)["stock"] == 5
    assert row(location_id=north.id)["stock"] == 0
    assert client.get("/pos/lookup?q=Widget").get_json()["items"][0]["stock"] == 5   # blank -> default


def test_lookup_and_batches_refuse_a_bad_warehouse_with_json(appctx):
    item, main, south, north = _setup()
    _deactivate(north)
    client, user = _manager()
    restricted, ruser = _manager("r2@t.com")
    _restrict(ruser, south)

    _assert_refused(client.get(f"/pos/lookup?q=Widget&location_id={north.id}"), 400)       # inactive
    _assert_refused(client.get("/pos/lookup?q=Widget&location_id=999999"), 400)           # nonexistent
    _assert_refused(client.get("/pos/lookup?q=Widget&location_id=abc"), 400)              # not a number
    _assert_refused(restricted.get(f"/pos/lookup?q=Widget&location_id={main.id}"), 403)   # not theirs

    _assert_refused(client.get(f"/pos/item-batches/{item.id}?location_id={north.id}"), 400)
    _assert_refused(client.get(f"/pos/item-batches/{item.id}?location_id=999999"), 400)
    _assert_refused(restricted.get(f"/pos/item-batches/{item.id}?location_id={main.id}"), 403)
    ok = client.get(f"/pos/item-batches/{item.id}?location_id={south.id}").get_json()
    assert ok["ok"] is True and ok["batches"] == []


# ── Checkout is pinned to the cart's warehouse ─────────────────────────────────


def test_checkout_sells_from_the_selected_warehouse_and_records_it(appctx):
    item, main, south, north = _setup()
    client, _ = _manager()

    r = _checkout(client, _pay(item, 3, location_id=south.id))

    assert r.status_code == 200 and r.get_json()["ok"]
    db.session.expire_all()
    assert Sale.query.one().location_id == south.id
    assert stock_at_location(item.id, south.id) == 17
    assert stock_at_location(item.id, main.id) == 5      # the default warehouse was not touched


def test_checkout_stays_on_the_pinned_warehouse_after_the_default_changes(appctx):
    item, main, south, north = _setup()
    client, _ = _manager()
    _make_default(south)                                 # admin changes the default meanwhile

    r = _checkout(client, _pay(item, 2, location_id=main.id))   # the cart was pinned to Main

    assert r.get_json()["ok"]
    db.session.expire_all()
    assert Sale.query.one().location_id == main.id
    assert stock_at_location(item.id, main.id) == 3
    assert stock_at_location(item.id, south.id) == 20


def test_checkout_without_a_location_uses_the_default_for_old_clients(appctx):
    item, main, south, north = _setup()
    client, _ = _manager()

    r = _checkout(client, _pay(item, 1))                 # no location_id in the payload

    assert r.get_json()["ok"]
    assert Sale.query.one().location_id == main.id


def test_checkout_rejects_inactive_inaccessible_and_missing_warehouses(appctx):
    item, main, south, north = _setup()
    _stock(item, north, 10)
    _deactivate(north)
    client, _ = _manager()
    restricted, ruser = _manager("r3@t.com")
    _restrict(ruser, south)

    _assert_refused(_checkout(client, _pay(item, 1, location_id=north.id)), 400)          # went inactive
    _assert_refused(_checkout(client, _pay(item, 1, location_id=999999)), 400)            # gone
    _assert_refused(_checkout(restricted, _pay(item, 1, location_id=main.id)), 403)       # access revoked

    db.session.expire_all()
    assert Sale.query.count() == 0                                   # nothing was sold
    assert stock_at_location(item.id, north.id) == 10                # and no stock moved
    assert stock_at_location(item.id, main.id) == 5


def test_checkout_still_refuses_to_oversell_the_selected_warehouse(appctx):
    item, main, south, north = _setup()
    client, _ = _manager()

    r = _checkout(client, _pay(item, 10, location_id=main.id))       # Main has 5 (South has 20)

    assert r.status_code == 400 and r.get_json()["ok"] is False
    assert "stock" in r.get_json()["error"].lower()
    db.session.expire_all()
    assert Sale.query.count() == 0 and stock_at_location(item.id, main.id) == 5


# ── Held bills carry their warehouse ───────────────────────────────────────────


def _hold(client, item, **extra):
    payload = {"customer_id": get_walkin_customer().id, "amount_paid": 0,
               "items": [{"item_id": item.id, "qty": 2, "price": 100}]}
    payload.update(extra)
    return client.post("/pos/hold", json=payload)


def test_a_held_bill_saves_and_restores_its_warehouse(appctx):
    item, main, south, north = _setup()
    client, _ = _manager()

    r = _hold(client, item, location_id=str(south.id))
    assert r.get_json()["ok"]
    hold = PosHold.query.one()
    assert hold.location_id == south.id

    got = client.get(f"/pos/held-bills/{hold.id}").get_json()
    assert got["ok"] and got["location_id"] == south.id and got["location_name"] == "South"

    # re-holding the same bill under another warehouse updates it
    r2 = _hold(client, item, location_id=str(main.id), hold_id=hold.id)
    assert r2.get_json()["ok"]
    db.session.expire_all()
    assert PosHold.query.one().location_id == main.id


def test_a_hold_with_a_bad_warehouse_is_refused_and_not_saved(appctx):
    item, main, south, north = _setup()
    _deactivate(north)
    client, _ = _manager()

    _assert_refused(_hold(client, item, location_id=str(north.id)), 400)
    _assert_refused(_hold(client, item, location_id="999999"), 400)
    assert PosHold.query.count() == 0


def test_an_old_hold_without_a_warehouse_still_resumes(appctx):
    item, main, south, north = _setup()
    client, user = _manager()
    old = PosHold(customer_id=get_walkin_customer().id, user_id=user.id, location_id=None,
                  cart_data=json.dumps([{"item_id": item.id, "qty": 1, "price": 100,
                                         "name": "Widget", "stock": 25}]))
    db.session.add(old); db.session.commit()

    got = client.get(f"/pos/held-bills/{old.id}").get_json()

    assert got["ok"] is True
    assert got["location_id"] is None and got["location_name"] is None   # page falls back to the default
    assert len(got["cart"]) == 1

    # a bill held by a client that predates the selector (no location in the payload)
    # is saved on the current default
    assert _hold(client, item).get_json()["ok"]
    assert PosHold.query.order_by(PosHold.id.desc()).first().location_id == main.id


def test_a_rejected_checkout_leaves_the_resumed_hold_and_stock_alone(appctx):
    item, main, south, north = _setup()
    client, _ = _manager()
    assert _hold(client, item, location_id=str(south.id)).get_json()["ok"]
    hold = PosHold.query.one()
    _deactivate(south)                                   # the bill's warehouse goes inactive

    r = _checkout(client, _pay(item, 2, location_id=south.id, hold_id=hold.id))

    _assert_refused(r, 400)
    db.session.expire_all()
    assert PosHold.query.count() == 1                    # the bill is still there to retry
    assert Sale.query.count() == 0
    assert stock_at_location(item.id, south.id) == 20
