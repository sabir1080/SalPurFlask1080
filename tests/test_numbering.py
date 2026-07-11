"""Gapless invoice numbering."""
from datetime import datetime

import pytest

from app import (
    db, Sale, DocumentSequence, PostingError,
    allocate_document_number, assert_not_numbered, backfill_document_numbers,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
)


def _years(*years):
    seed_chart_of_accounts()
    seed_fixed_asset_accounts()
    for y in years:
        seed_fiscal_year(y)
    db.session.commit()


def test_numbers_are_sequential(appctx):
    _years(2026)
    numbers = []
    for _ in range(3):
        numbers.append(allocate_document_number("purchase", datetime(2026, 2, 2)))
        db.session.commit()
    assert numbers == ["PUR-2026-000001", "PUR-2026-000002", "PUR-2026-000003"]


def test_an_abandoned_save_gives_its_number_back(appctx):
    """The whole point of holding the counter in a table rather than a database
    sequence: a save that never commits must not burn a number."""
    _years(2026)

    first = allocate_document_number("sale", datetime(2026, 3, 1))
    db.session.commit()                       # this invoice was saved
    assert first == "INV-2026-000001"

    abandoned = allocate_document_number("sale", datetime(2026, 3, 5))
    db.session.rollback()                     # …and this one failed

    next_real = allocate_document_number("sale", datetime(2026, 3, 9))
    db.session.commit()

    assert abandoned == "INV-2026-000002"
    assert next_real == "INV-2026-000002"     # the abandoned number came back — no gap


def test_numbering_restarts_each_fiscal_year(appctx):
    _years(2026, 2027)

    a = allocate_document_number("sale", datetime(2026, 6, 1)); db.session.commit()
    b = allocate_document_number("sale", datetime(2026, 7, 1)); db.session.commit()
    c = allocate_document_number("sale", datetime(2027, 1, 4)); db.session.commit()

    assert (a, b) == ("INV-2026-000001", "INV-2026-000002")
    assert c == "INV-2027-000001"             # a new year starts at one again


def test_purchases_and_sales_have_their_own_sequences(appctx):
    _years(2026)
    p = allocate_document_number("purchase", datetime(2026, 5, 1)); db.session.commit()
    s = allocate_document_number("sale", datetime(2026, 5, 1)); db.session.commit()
    assert p == "PUR-2026-000001"
    assert s == "INV-2026-000001"             # one series does not consume the other
    assert DocumentSequence.query.count() == 2


def test_a_numbered_invoice_cannot_be_deleted(appctx):
    """Deleting it would take its number out of the run — reverse it instead."""
    doc = Sale(customer_id=1, item_id=1, quantity=1, sale_price=1,
               invoice_no="INV-2026-000001")
    with pytest.raises(PostingError, match="gap"):
        assert_not_numbered(doc, "Sale")


def test_backfill_numbers_old_documents_oldest_first(appctx):
    _years(2026)
    older = Sale(customer_id=1, item_id=1, quantity=1, sale_price=1,
                 date=datetime(2026, 1, 10))
    newer = Sale(customer_id=1, item_id=1, quantity=1, sale_price=1,
                 date=datetime(2026, 4, 20))
    db.session.add_all([newer, older])        # added out of order on purpose
    db.session.commit()

    assert backfill_document_numbers() == 2
    assert older.invoice_no == "INV-2026-000001"   # numbered in date order
    assert newer.invoice_no == "INV-2026-000002"

    # running it again must not renumber anything
    assert backfill_document_numbers() == 0
    assert older.invoice_no == "INV-2026-000001"
