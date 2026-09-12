"""Regression test for the Item Ledger footer's closing-balance cell.

Bug: the footer's last cell rendered `current_stock` (item.stock), a value
that can drift from the ledger table it sits under (e.g. it is read
straight from the Item row, independent of the entries actually listed).
The table above it already computes the correct final running balance on
each entry — the footer must reuse that instead of a separately-sourced
number.

Reproduces the reported sequence: Opening 18, Purchase +25, Sale -8,
Sale -10, Purchase +5 -> Stock In=30, Stock Out=18, closing balance=30
(18 + 30 - 18), regardless of what item.stock itself happens to hold.
"""
from decimal import Decimal

from app import app as flask_app, db, User, pwd_context, Category, Item, Supplier, Customer
from salpurflask.models.models import Purchase, PurchaseItem, Sale, SaleItem


def _login(user):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
    return c


def _admin():
    u = User(name="Admin", email=f"admin{User.query.count()}@footertest.com",
             password=pwd_context.hash("secret123"), verified=True, role="admin")
    db.session.add(u)
    db.session.commit()
    return u


def _item(opening_stock, stale_stock):
    cat = Category.query.first()
    if cat is None:
        cat = Category(name="Cat")
        db.session.add(cat)
        db.session.flush()
    it = Item(name="Widget", category_id=cat.id, stock=stale_stock,
              opening_stock=opening_stock, purchase_price=10, sale_price=20,
              item_type="STOCK")
    db.session.add(it)
    db.session.commit()
    return it


def test_footer_closing_balance_matches_final_running_balance_not_item_stock(appctx):
    admin = _admin()
    # item.stock is deliberately left at a stale 25 -- one purchase short of
    # the true 30 -- to prove the footer no longer parrots this field.
    item = _item(opening_stock=18, stale_stock=25)
    supplier = Supplier(name="Acme Supplier", contact="03000000000", address="x", opening_balance=0)
    customer = Customer(name="Jane Customer", contact="03000000000", address="x", opening_balance=0)
    db.session.add_all([supplier, customer])
    db.session.commit()

    def _purchase(qty, price):
        p = Purchase(supplier_id=supplier.id, item_id=item.id, quantity=qty, purchase_price=price)
        db.session.add(p)
        db.session.flush()
        db.session.add(PurchaseItem(purchase_id=p.id, item_id=item.id, quantity=qty,
                                     purchase_price=price, amount=qty * price))
        db.session.commit()

    def _sale(qty, price):
        s = Sale(customer_id=customer.id, item_id=item.id, quantity=qty, sale_price=price)
        db.session.add(s)
        db.session.flush()
        db.session.add(SaleItem(sale_id=s.id, item_id=item.id, quantity=qty,
                                 sale_price=price, amount=qty * price))
        db.session.commit()

    _purchase(25, Decimal("10"))
    _sale(8, Decimal("20"))
    _sale(10, Decimal("20"))
    _purchase(5, Decimal("10"))

    c = _login(admin)
    r = c.get(f"/item/{item.id}/ledger")
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    idx = body.find("Totals:")
    assert idx != -1
    footer = body[idx:idx + 800]

    assert "+30.00" in footer
    assert "-18.00" in footer

    # Isolate the final <td> (closing balance cell) of the totals row.
    row_end = footer.find("</tr>")
    totals_row = footer[:row_end]
    cells = totals_row.split("<td")
    closing_cell = cells[-1]
    assert "30.00" in closing_cell
    assert "25.00" not in closing_cell
