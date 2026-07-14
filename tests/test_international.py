"""This app is not only going to be used in Karachi.

A trader in Karachi has suppliers in Dubai. A business in London has customers in New York.
The moment either of them tries to save a contact, the app has to accept a phone number
that was not written the way Pakistani mobile numbers are written.
"""
from datetime import datetime

import pytest

from app import app as flask_app, db, User, pwd_context, Supplier, Customer, valid_phone


def _admin(email="a@t.com"):
    db.session.add(User(name="A", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


@pytest.mark.parametrize("number", [
    "03001234567",              # Karachi, as locals write it
    "+92 300 1234567",          # Karachi, with its country code — the old check rejected this
    "+44 20 7946 0958",         # London
    "(212) 555-0143",           # New York
    "+971 50 123 4567",         # Dubai
    "+1-415-555-0132",
    "021-34567890",             # a Karachi landline
])
def test_a_phone_number_from_anywhere_is_accepted(number):
    assert valid_phone(number), number


@pytest.mark.parametrize("number", [
    "",
    "12345",                    # too few digits to be a phone number
    "not a number",
    "0300 abc 4567",
    "+" + "1" * 16,             # more digits than the ITU allows
])
def test_nonsense_is_still_rejected(number):
    assert not valid_phone(number)


def test_a_dubai_supplier_can_actually_be_saved(appctx):
    """The unit test above is the rule; this is the route that enforces it. Before, the
    form refused the number and the supplier could not be entered at all."""
    c = _admin()
    r = c.post("/supplier", data={
        "name": "Gulf Traders LLC", "contact": "+971 4 123 4567",
        "address": "Deira, Dubai", "opening_balance": "0",
    }, follow_redirects=True)
    assert r.status_code == 200

    sup = Supplier.query.filter_by(name="Gulf Traders LLC").first()
    assert sup is not None, "a Dubai supplier was rejected by the phone check"
    assert sup.contact == "+971 4 123 4567"      # stored exactly as it was written


def test_a_london_customer_can_actually_be_saved(appctx):
    c = _admin()
    c.post("/customer", data={
        "name": "Thames Retail Ltd", "contact": "+44 20 7946 0958",
        "address": "Camden, London", "opening_balance": "0",
    }, follow_redirects=True)

    cus = Customer.query.filter_by(name="Thames Retail Ltd").first()
    assert cus is not None, "a London customer was rejected by the phone check"
