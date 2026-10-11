"""Click-to-sort column headers on the Supplier, Customer, and Item listing
pages — server-side (sorting must apply before pagination, since these pages
paginate 10 rows at a time via flask_paginate).

Covers: allowlisted sort/dir query params (invalid values fall back safely,
never reach raw SQL), the Name-ascending default on all three pages, the
ascending/descending header-click toggle (asserted both on resulting row
order and on the rendered header links' href), numeric columns sorting
numerically (not lexicographically), NULLs always landing last regardless of
direction, sorting composing correctly with the existing search/category
filters, and sorting applying to the whole filtered dataset across
pagination.
"""
import re
from decimal import Decimal

from app import app as flask_app, db, User, pwd_context, Supplier, Customer
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory


def _login(role="manager", email=None):
    email = email or f"{role}@t.com"
    u = User(name=role.capitalize(), email=email, password=pwd_context.hash("secret123"),
             verified=True, role=role)
    db.session.add(u)
    db.session.commit()
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True
    return c


def _category(slug="grocery"):
    cat = BusinessCategory.query.filter_by(slug=slug).first()
    if cat is None:
        cat = BusinessCategory(name=slug.title(), slug=slug, is_enabled=True)
        db.session.add(cat)
        db.session.commit()
    return cat


def _item(name, cat, **kw):
    it = Item(name=name, business_category_id=cat.id, unit="Pcs",
              stock=kw.pop("stock", 10), opening_stock=0, reorder_level=5, **kw)
    db.session.add(it)
    db.session.commit()
    return it


def _names_in_order(html, names):
    """Return `names` re-ordered by first-appearance index in `html` — lets a
    test assert the order rows actually rendered in, independent of any other
    markup around them."""
    return sorted(names, key=lambda n: html.index(n))


def _sort_hrefs(html):
    """Every column-header sort link's href, in the order the columns render
    (the macro always emits them in template order, so a fixed index — e.g.
    1 for the Name column, present on all three pages — reliably picks out
    one specific header's link without depending on its label text)."""
    return re.findall(r'href="([^"]*\bsort=[^"]*)"', html)


# ── Supplier ─────────────────────────────────────────────────────────────

def test_supplier_default_sort_is_name_ascending(appctx):
    client = _login()
    db.session.add(Supplier(name="Charlie Co", contact="0300", address="A", opening_balance=0))
    db.session.add(Supplier(name="Alpha Co", contact="0301", address="B", opening_balance=0))
    db.session.commit()
    resp = client.get("/supplier")
    html = resp.data.decode()
    assert _names_in_order(html, ["Charlie Co", "Alpha Co"]) == ["Alpha Co", "Charlie Co"]


def test_supplier_invalid_sort_and_dir_fall_back_to_name_ascending(appctx):
    client = _login()
    db.session.add(Supplier(name="Charlie Co", contact="0300", address="A", opening_balance=0))
    db.session.add(Supplier(name="Alpha Co", contact="0301", address="B", opening_balance=0))
    db.session.commit()
    resp = client.get("/supplier?sort=not_a_real_column&dir=not_a_real_direction")
    html = resp.data.decode()
    assert _names_in_order(html, ["Charlie Co", "Alpha Co"]) == ["Alpha Co", "Charlie Co"]


def test_supplier_sort_by_name_ascending(appctx):
    client = _login()
    db.session.add(Supplier(name="Charlie Co", contact="0300", address="A", opening_balance=0))
    db.session.add(Supplier(name="Alpha Co", contact="0301", address="B", opening_balance=0))
    db.session.add(Supplier(name="Bravo Co", contact="0302", address="C", opening_balance=0))
    db.session.commit()
    resp = client.get("/supplier?sort=name&dir=asc")
    html = resp.data.decode()
    assert _names_in_order(html, ["Alpha Co", "Bravo Co", "Charlie Co"]) == \
        ["Alpha Co", "Bravo Co", "Charlie Co"]


def test_supplier_sort_by_name_descending(appctx):
    client = _login()
    db.session.add(Supplier(name="Charlie Co", contact="0300", address="A", opening_balance=0))
    db.session.add(Supplier(name="Alpha Co", contact="0301", address="B", opening_balance=0))
    db.session.commit()
    resp = client.get("/supplier?sort=name&dir=desc")
    html = resp.data.decode()
    assert _names_in_order(html, ["Charlie Co", "Alpha Co"]) == ["Charlie Co", "Alpha Co"]


def test_supplier_sort_by_opening_balance_is_numeric_not_lexicographic(appctx):
    """5 < 20 < 100 numerically, but '100' < '20' < '5' as plain strings —
    this would fail if sorting ever fell back to a string comparison."""
    client = _login()
    db.session.add(Supplier(name="Five", contact="1", address="A", opening_balance=5))
    db.session.add(Supplier(name="Twenty", contact="2", address="B", opening_balance=20))
    db.session.add(Supplier(name="Hundred", contact="3", address="C", opening_balance=100))
    db.session.commit()
    resp = client.get("/supplier?sort=opening_balance&dir=asc")
    html = resp.data.decode()
    assert _names_in_order(html, ["Five", "Twenty", "Hundred"]) == ["Five", "Twenty", "Hundred"]


def test_supplier_invalid_sort_key_falls_back_safely(appctx):
    client = _login()
    db.session.add(Supplier(name="A", contact="1", address="X", opening_balance=0))
    db.session.commit()
    resp = client.get("/supplier?sort=" + "'; DROP TABLE supplier; --" + "&dir=asc")
    assert resp.status_code == 200
    assert Supplier.query.count() == 1  # table is still there


def test_supplier_invalid_direction_falls_back_to_ascending(appctx):
    client = _login()
    db.session.add(Supplier(name="Bravo Traders", contact="1", address="X", opening_balance=0))
    db.session.add(Supplier(name="Alpha Traders", contact="2", address="Y", opening_balance=0))
    db.session.commit()
    resp = client.get("/supplier?sort=name&dir=sideways")
    html = resp.data.decode()
    assert _names_in_order(html, ["Alpha Traders", "Bravo Traders"]) == \
        ["Alpha Traders", "Bravo Traders"]


def test_supplier_sort_persists_through_search_filter(appctx):
    client = _login()
    db.session.add(Supplier(name="Zeta Traders", contact="1", address="X", opening_balance=0))
    db.session.add(Supplier(name="Alpha Traders", contact="2", address="Y", opening_balance=0))
    db.session.add(Supplier(name="Omitted Co", contact="3", address="Z", opening_balance=0))
    db.session.commit()
    resp = client.get("/supplier?search=Traders&sort=name&dir=asc")
    html = resp.data.decode()
    assert "Omitted Co" not in html
    assert _names_in_order(html, ["Alpha Traders", "Zeta Traders"]) == \
        ["Alpha Traders", "Zeta Traders"]


def test_supplier_sort_applies_across_full_paginated_dataset(appctx):
    """10 rows per page — sort must happen before the LIMIT/OFFSET, not after,
    or page 2 would show an unsorted remainder instead of the next 10 in order."""
    client = _login()
    for i in range(15):
        db.session.add(Supplier(name=f"Supplier {i:02d}", contact=str(i), address="A", opening_balance=0))
    db.session.commit()
    resp = client.get("/supplier?sort=name&dir=desc&page=1")
    html = resp.data.decode()
    assert "Supplier 14" in html and "Supplier 05" in html and "Supplier 04" not in html
    resp2 = client.get("/supplier?sort=name&dir=desc&page=2")
    html2 = resp2.data.decode()
    assert "Supplier 04" in html2 and "Supplier 00" in html2 and "Supplier 14" not in html2


# ── Customer ─────────────────────────────────────────────────────────────

def test_customer_default_sort_is_name_ascending(appctx):
    client = _login()
    db.session.add(Customer(name="Zed Retail", contact="1", address="A", opening_balance=0))
    db.session.add(Customer(name="Amy Retail", contact="2", address="B", opening_balance=0))
    db.session.commit()
    resp = client.get("/customer")
    html = resp.data.decode()
    assert _names_in_order(html, ["Zed Retail", "Amy Retail"]) == ["Amy Retail", "Zed Retail"]


def test_customer_invalid_sort_and_dir_fall_back_to_name_ascending(appctx):
    client = _login()
    db.session.add(Customer(name="Zed Retail", contact="1", address="A", opening_balance=0))
    db.session.add(Customer(name="Amy Retail", contact="2", address="B", opening_balance=0))
    db.session.commit()
    resp = client.get("/customer?sort=nope&dir=nope")
    html = resp.data.decode()
    assert _names_in_order(html, ["Zed Retail", "Amy Retail"]) == ["Amy Retail", "Zed Retail"]


def test_customer_sort_by_name_ascending(appctx):
    client = _login()
    db.session.add(Customer(name="Zed Retail", contact="1", address="A", opening_balance=0))
    db.session.add(Customer(name="Amy Retail", contact="2", address="B", opening_balance=0))
    db.session.commit()
    resp = client.get("/customer?sort=name&dir=asc")
    html = resp.data.decode()
    assert _names_in_order(html, ["Amy Retail", "Zed Retail"]) == ["Amy Retail", "Zed Retail"]


def test_customer_sort_by_opening_balance_numeric(appctx):
    client = _login()
    db.session.add(Customer(name="Low", contact="1", address="A", opening_balance=Decimal("9")))
    db.session.add(Customer(name="High", contact="2", address="B", opening_balance=Decimal("150")))
    db.session.commit()
    resp = client.get("/customer?sort=opening_balance&dir=asc")
    html = resp.data.decode()
    assert _names_in_order(html, ["Low", "High"]) == ["Low", "High"]


def test_customer_sort_by_opening_balance_descending(appctx):
    client = _login()
    db.session.add(Customer(name="Low", contact="1", address="A", opening_balance=Decimal("9")))
    db.session.add(Customer(name="High", contact="2", address="B", opening_balance=Decimal("150")))
    db.session.commit()
    resp = client.get("/customer?sort=opening_balance&dir=desc")
    html = resp.data.decode()
    assert _names_in_order(html, ["High", "Low"]) == ["High", "Low"]


def test_customer_invalid_sort_key_falls_back_safely(appctx):
    client = _login()
    db.session.add(Customer(name="A", contact="1", address="X", opening_balance=0))
    db.session.commit()
    resp = client.get("/customer?sort=../../etc/passwd&dir=asc")
    assert resp.status_code == 200
    assert Customer.query.count() == 1


# ── Item ─────────────────────────────────────────────────────────────────

def test_item_default_sort_is_name_ascending(appctx):
    cat = _category()
    client = _login()
    _item("Zinc Sheet", cat)
    _item("Aluminium Sheet", cat)
    resp = client.get("/item")
    html = resp.data.decode()
    assert _names_in_order(html, ["Zinc Sheet", "Aluminium Sheet"]) == \
        ["Aluminium Sheet", "Zinc Sheet"]


def test_item_invalid_sort_and_dir_fall_back_to_name_ascending(appctx):
    cat = _category()
    client = _login()
    _item("Zinc Sheet", cat)
    _item("Aluminium Sheet", cat)
    resp = client.get("/item?sort=nope&dir=nope")
    html = resp.data.decode()
    assert _names_in_order(html, ["Zinc Sheet", "Aluminium Sheet"]) == \
        ["Aluminium Sheet", "Zinc Sheet"]


def test_item_sort_by_name_ascending(appctx):
    cat = _category()
    client = _login()
    _item("Zinc Sheet", cat)
    _item("Aluminium Sheet", cat)
    resp = client.get("/item?sort=name&dir=asc")
    html = resp.data.decode()
    assert _names_in_order(html, ["Aluminium Sheet", "Zinc Sheet"]) == \
        ["Aluminium Sheet", "Zinc Sheet"]


def test_item_sort_by_category_name(appctx):
    client = _login()
    zeta_cat = _category("zeta-cat")
    alpha_cat = _category("alpha-cat")
    _item("Item In Zeta", zeta_cat)
    _item("Item In Alpha", alpha_cat)
    resp = client.get("/item?sort=category&dir=asc")
    html = resp.data.decode()
    assert _names_in_order(html, ["Item In Alpha", "Item In Zeta"]) == \
        ["Item In Alpha", "Item In Zeta"]


def test_item_sort_by_stock_is_numeric(appctx):
    """9 < 15 < 100 numerically; a lexicographic sort would put '100' before '15' before '9'."""
    cat = _category()
    client = _login()
    _item("Nine", cat, stock=9)
    _item("Fifteen", cat, stock=15)
    _item("Hundred", cat, stock=100)
    resp = client.get("/item?sort=stock&dir=asc")
    html = resp.data.decode()
    assert _names_in_order(html, ["Nine", "Fifteen", "Hundred"]) == \
        ["Nine", "Fifteen", "Hundred"]


def test_item_sort_by_purchase_price_pushes_nulls_last_both_directions(appctx):
    cat = _category()
    client = _login()
    _item("No Price", cat, purchase_price=None)
    _item("Has Price", cat, purchase_price=Decimal("10"))

    asc_html = client.get("/item?sort=purchase_price&dir=asc").data.decode()
    assert asc_html.index("Has Price") < asc_html.index("No Price")

    desc_html = client.get("/item?sort=purchase_price&dir=desc").data.decode()
    assert desc_html.index("Has Price") < desc_html.index("No Price")


def test_item_sort_combines_with_search_filter(appctx):
    cat = _category()
    client = _login()
    _item("Blue Widget", cat)
    _item("Alpha Widget", cat)
    _item("Unrelated Gadget", cat)
    resp = client.get("/item?search=Widget&sort=name&dir=asc")
    html = resp.data.decode()
    assert "Unrelated Gadget" not in html
    assert _names_in_order(html, ["Alpha Widget", "Blue Widget"]) == \
        ["Alpha Widget", "Blue Widget"]


def test_item_invalid_sort_key_falls_back_safely(appctx):
    cat = _category()
    client = _login()
    _item("Safe Item", cat)
    resp = client.get("/item?sort=business_category_id%20--&dir=asc")
    assert resp.status_code == 200
    assert Item.query.count() == 1


def test_item_sort_applies_across_full_paginated_dataset(appctx):
    cat = _category()
    client = _login()
    for i in range(15):
        _item(f"Item {i:02d}", cat)
    resp = client.get("/item?sort=name&dir=desc&page=1")
    html = resp.data.decode()
    assert "Item 14" in html and "Item 05" in html and "Item 04" not in html
    resp2 = client.get("/item?sort=name&dir=desc&page=2")
    html2 = resp2.data.decode()
    assert "Item 04" in html2 and "Item 00" in html2 and "Item 14" not in html2


# ── Header-link rendering: toggle, column-switch, filter preservation ──────

def test_supplier_name_header_toggles_asc_desc_asc(appctx):
    client = _login()
    db.session.add(Supplier(name="A", contact="1", address="X", opening_balance=0))
    db.session.commit()

    # No explicit sort yet -> default is name/asc, so the Name header's own
    # link must offer desc next.
    hrefs = _sort_hrefs(client.get("/supplier").data.decode())
    assert "sort=name" in hrefs[1] and "dir=desc" in hrefs[1]

    # Currently name/desc -> the Name header's link must offer asc next.
    hrefs = _sort_hrefs(client.get("/supplier?sort=name&dir=desc").data.decode())
    assert "sort=name" in hrefs[1] and "dir=asc" in hrefs[1]

    # Currently name/asc -> the Name header's link must offer desc next.
    hrefs = _sort_hrefs(client.get("/supplier?sort=name&dir=asc").data.decode())
    assert "sort=name" in hrefs[1] and "dir=desc" in hrefs[1]


def test_supplier_other_header_link_starts_ascending(appctx):
    client = _login()
    db.session.add(Supplier(name="A", contact="1", address="X", opening_balance=0))
    db.session.commit()
    # Currently sorted name/desc -> the Contact header's own link must still
    # start at asc (switching column always resets to ascending).
    hrefs = _sort_hrefs(client.get("/supplier?sort=name&dir=desc").data.decode())
    assert "sort=contact" in hrefs[2] and "dir=asc" in hrefs[2]


def test_customer_name_header_toggles_asc_desc_asc(appctx):
    client = _login()
    db.session.add(Customer(name="A", contact="1", address="X", opening_balance=0))
    db.session.commit()
    hrefs = _sort_hrefs(client.get("/customer").data.decode())
    assert "sort=name" in hrefs[1] and "dir=desc" in hrefs[1]
    hrefs = _sort_hrefs(client.get("/customer?sort=name&dir=desc").data.decode())
    assert "sort=name" in hrefs[1] and "dir=asc" in hrefs[1]


def test_customer_other_header_link_starts_ascending(appctx):
    client = _login()
    db.session.add(Customer(name="A", contact="1", address="X", opening_balance=0))
    db.session.commit()
    hrefs = _sort_hrefs(client.get("/customer?sort=name&dir=desc").data.decode())
    assert "sort=contact" in hrefs[2] and "dir=asc" in hrefs[2]


def test_item_name_header_toggles_asc_desc_asc(appctx):
    cat = _category()
    client = _login()
    _item("Only Item", cat)
    hrefs = _sort_hrefs(client.get("/item").data.decode())
    assert "sort=name" in hrefs[1] and "dir=desc" in hrefs[1]
    hrefs = _sort_hrefs(client.get("/item?sort=name&dir=desc").data.decode())
    assert "sort=name" in hrefs[1] and "dir=asc" in hrefs[1]


def test_item_other_header_link_starts_ascending(appctx):
    cat = _category()
    client = _login()
    _item("Only Item", cat)
    # Currently sorted name/desc -> the Category header's own link must
    # still start at asc.
    hrefs = _sort_hrefs(client.get("/item?sort=name&dir=desc").data.decode())
    assert "sort=category" in hrefs[2] and "dir=asc" in hrefs[2]


def test_supplier_header_links_preserve_search(appctx):
    client = _login()
    db.session.add(Supplier(name="Zed Traders", contact="1", address="X", opening_balance=0))
    db.session.commit()
    hrefs = _sort_hrefs(client.get("/supplier?search=Traders&sort=name&dir=asc").data.decode())
    assert "search=Traders" in hrefs[1]


def test_customer_header_links_preserve_search(appctx):
    client = _login()
    db.session.add(Customer(name="Zed Retail", contact="1", address="X", opening_balance=0))
    db.session.commit()
    hrefs = _sort_hrefs(client.get("/customer?search=Retail&sort=name&dir=asc").data.decode())
    assert "search=Retail" in hrefs[1]


def test_item_header_links_preserve_search_and_category_filter(appctx):
    cat = _category()
    client = _login()
    _item("Blue Widget", cat)
    hrefs = _sort_hrefs(
        client.get(f"/item?search=Widget&business_category_id={cat.id}&sort=name&dir=asc").data.decode())
    assert "search=Widget" in hrefs[1]
    assert f"business_category_id={cat.id}" in hrefs[1]


# ── Existing functionality preserved ────────────────────────────────────

def test_supplier_edit_and_delete_actions_still_present_when_sorted(appctx):
    client = _login("admin")
    s = Supplier(name="Still Here", contact="1", address="X", opening_balance=0)
    db.session.add(s)
    db.session.commit()
    resp = client.get("/supplier?sort=name&dir=asc")
    html = resp.data.decode()
    assert f"/supplier/edit/{s.id}" in html
    assert f"/supplier/delete/{s.id}" in html


def test_item_add_still_works_with_sort_params_on_the_page(appctx):
    """Submitting the Add Item form while the list happens to be sorted must
    not be affected by the sort/dir query params on that same GET/POST."""
    cat = _category()
    client = _login()
    resp = client.post("/item?sort=name&dir=desc", data={
        "name": "New Sorted Item", "business_category_id": str(cat.id), "item_type": "STOCK",
        "unit": "Pcs", "opening_stock": "0", "reorder_level": "5",
        "purchase_price": "10", "sale_price": "20",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert Item.query.filter_by(name="New Sorted Item").first() is not None
