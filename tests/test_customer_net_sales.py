"""Customer List "Sales" figure — Net Sales = gross sales (get_customer_receivable)
minus eligible Sale Returns, via the new get_customer_net_sale() helper.

Covers: a customer with no returns matches gross exactly; a customer with one
or more active returns is reduced by exactly their amount; a reversed return
is never deducted; a return against a Sale that has itself been reversed is
also never deducted (that Sale is already excluded from the gross figure, so
subtracting its return too would double-count the reversal); the TOTAL row
and each row use the identical computed value (no drift); pagination/search
still include every matching customer exactly once; and get_customer_receivable()
itself is provably unchanged — it keeps returning the gross, pre-return total.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context, Customer, Category, Item, Sale, SaleItem,
    SaleReturn, calc_discount_tax,
    sync_customer_opening, sync_customer_sale, sync_customer_sale_return,
    get_customer_receivable, get_customer_net_sale,
)


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


def make_customer(name="Cust", opening=0):
    c = Customer(name=name, contact="03000000000", address="X", opening_balance=opening)
    db.session.add(c)
    db.session.flush()
    sync_customer_opening(c)
    db.session.commit()
    return c


def make_item(name="Widget"):
    cat = Category(name="Cat-" + name)
    db.session.add(cat)
    db.session.flush()
    it = Item(name=name, category_id=cat.id, stock=0, purchase_price=10, sale_price=20)
    db.session.add(it)
    db.session.flush()
    return it


def add_sale(cust, item, qty=10, price=100):
    sale = Sale(customer_id=cust.id, item_id=item.id, quantity=qty, sale_price=price,
                cost_price=item.purchase_price or 0)
    db.session.add(sale)
    db.session.flush()
    d, t, net = calc_discount_tax(qty * price, "percent", 0, 0)
    si = SaleItem(sale_id=sale.id, item_id=item.id, quantity=qty, sale_price=price,
                  cost_price=item.purchase_price or 0, discount_type="percent", discount_value=0,
                  discount_amount=d, tax_percent=0, tax_amount=t, amount=net)
    db.session.add(si)
    db.session.flush()
    db.session.refresh(sale)
    db.session.refresh(si)
    sync_customer_sale(sale)
    db.session.commit()
    return sale, si


def add_sale_return(sale, si, cust, item, qty, return_price):
    sr = SaleReturn(sale_id=sale.id, customer_id=cust.id, item_id=item.id, quantity=qty,
                     return_price=return_price, sale_item_id=si.id)
    db.session.add(sr)
    db.session.flush()
    sync_customer_sale_return(sr)
    db.session.commit()
    return sr


# ── 1. Gross sales without returns ──────────────────────────────────────────

def test_net_sale_equals_gross_when_no_returns(appctx):
    cust = make_customer()
    item = make_item()
    add_sale(cust, item, qty=10, price=100)  # gross = 1000
    gross = get_customer_receivable(cust.id)
    net = get_customer_net_sale(cust.id)
    assert gross == 1000.0
    assert net == gross


# ── 2. A customer with one or more valid returns ────────────────────────────

def test_net_sale_subtracts_one_active_return(appctx):
    cust = make_customer()
    item = make_item()
    sale, si = add_sale(cust, item, qty=10, price=100)  # gross = 1000
    add_sale_return(sale, si, cust, item, qty=2, return_price=100)  # -200
    assert get_customer_receivable(cust.id) == 1000.0  # gross unaffected
    assert get_customer_net_sale(cust.id) == 800.0


def test_net_sale_subtracts_multiple_active_returns(appctx):
    cust = make_customer()
    item = make_item()
    sale, si = add_sale(cust, item, qty=10, price=100)  # gross = 1000
    add_sale_return(sale, si, cust, item, qty=2, return_price=100)   # -200
    add_sale_return(sale, si, cust, item, qty=1, return_price=100)   # -100
    assert get_customer_net_sale(cust.id) == 700.0


def test_net_sale_across_multiple_sales_and_returns(appctx):
    cust = make_customer()
    item = make_item()
    sale1, si1 = add_sale(cust, item, qty=10, price=100)  # 1000
    sale2, si2 = add_sale(cust, item, qty=5, price=50)    # 250
    add_sale_return(sale1, si1, cust, item, qty=1, return_price=100)  # -100
    add_sale_return(sale2, si2, cust, item, qty=1, return_price=50)   # -50
    assert get_customer_receivable(cust.id) == 1250.0
    assert get_customer_net_sale(cust.id) == 1100.0


# ── 3. Reversed sales/returns ───────────────────────────────────────────────

def test_reversed_return_is_not_deducted(appctx):
    """A return whose own is_reversed flag is True must not reduce Net Sales
    — matches get_customer_receivable()'s own is_reversed=False filter on Sale."""
    cust = make_customer()
    item = make_item()
    sale, si = add_sale(cust, item, qty=10, price=100)  # gross = 1000
    sr = add_sale_return(sale, si, cust, item, qty=2, return_price=100)  # would be -200
    sr.is_reversed = True
    db.session.commit()
    assert get_customer_net_sale(cust.id) == 1000.0  # full gross, return ignored


def test_return_against_a_reversed_sale_is_not_double_deducted(appctx):
    """If the parent Sale itself has been reversed, it is already excluded
    from get_customer_receivable()'s sum. An active return still attached to
    it must not reduce Net Sales a second time — that would make Net Sales
    negative relative to a Sale that no longer counts as revenue at all."""
    cust = make_customer()
    item = make_item()
    sale, si = add_sale(cust, item, qty=10, price=100)  # gross = 1000
    add_sale_return(sale, si, cust, item, qty=2, return_price=100)  # active return
    sale.is_reversed = True  # parent Sale reversed after the return was recorded
    db.session.commit()
    assert get_customer_receivable(cust.id) == 0.0   # Sale excluded from gross
    assert get_customer_net_sale(cust.id) == 0.0      # return not subtracted again


def test_reversed_sale_without_any_return_is_simply_excluded(appctx):
    cust = make_customer()
    item = make_item()
    sale, si = add_sale(cust, item, qty=10, price=100)
    sale.is_reversed = True
    db.session.commit()
    assert get_customer_receivable(cust.id) == 0.0
    assert get_customer_net_sale(cust.id) == 0.0


# ── 4. Per-customer and TOTAL-row Net Sales (route-level) ───────────────────

def test_customer_list_row_shows_net_sales(appctx):
    cust = make_customer(name="Return Customer")
    item = make_item()
    sale, si = add_sale(cust, item, qty=10, price=100)  # gross 1000
    add_sale_return(sale, si, cust, item, qty=3, return_price=100)  # -300 -> net 700
    client = _login()
    resp = client.get("/customer")
    html = resp.data.decode()
    assert "700.00" in html
    assert "1,000.00" not in html  # gross figure must not appear in the row


def test_customer_list_total_row_sums_net_sales(appctx):
    cust_a = make_customer(name="Alpha Co")
    cust_b = make_customer(name="Beta Co")
    item = make_item()
    sale_a, si_a = add_sale(cust_a, item, qty=10, price=100)  # gross 1000
    add_sale_return(sale_a, si_a, cust_a, item, qty=2, return_price=100)  # net 800
    add_sale(cust_b, item, qty=5, price=50)  # gross 250, no return -> net 250
    # Expected TOTAL = 800 + 250 = 1050
    client = _login()
    resp = client.get("/customer")
    html = resp.data.decode()
    assert "1,050.00" in html


# ── 5. Pagination / filters: no missing or duplicate totals ─────────────────

def test_total_net_sales_correct_across_pagination(appctx):
    """15 customers (page size is 10) — the TOTAL row must sum Net Sales for
    all 15, not just the 10 on the last page, and each customer's return must
    be counted exactly once."""
    item = make_item()
    expected_total = 0.0
    for i in range(15):
        cust = make_customer(name=f"Cust {i:02d}")
        sale, si = add_sale(cust, item, qty=10, price=100)  # gross 1000 each
        expected_total += 1000.0
        if i % 3 == 0:
            add_sale_return(sale, si, cust, item, qty=1, return_price=100)  # -100
            expected_total -= 100.0
    client = _login()
    resp_page2 = client.get("/customer?sort=name&dir=asc&page=2")
    html2 = resp_page2.data.decode()
    assert f"{expected_total:,.2f}" in html2


def test_total_net_sales_respects_search_filter(appctx):
    item = make_item()
    cust_in = make_customer(name="Zeta Traders")
    sale, si = add_sale(cust_in, item, qty=10, price=100)
    add_sale_return(sale, si, cust_in, item, qty=1, return_price=100)  # net 900
    cust_out = make_customer(name="Omitted Co")
    add_sale(cust_out, item, qty=10, price=100)  # gross 1000, must be excluded by search
    client = _login()
    resp = client.get("/customer?search=Traders")
    html = resp.data.decode()
    assert "Omitted Co" not in html
    assert "900.00" in html


# ── 6. get_customer_receivable() is provably unchanged ──────────────────────

def test_get_customer_receivable_still_returns_gross_with_returns_present(appctx):
    """The existing gross-sales helper must keep returning the raw sum of
    active Sales, completely unaffected by any Sale Returns — other modules
    that import get_customer_receivable() must see no behavior change."""
    cust = make_customer()
    item = make_item()
    sale, si = add_sale(cust, item, qty=10, price=100)
    add_sale_return(sale, si, cust, item, qty=5, return_price=100)
    assert get_customer_receivable(cust.id) == 1000.0
