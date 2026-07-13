"""Backup / restore round-trip.

The tables added with the general ledger (fiscal_year, accounting_period) use plain
`Date` columns, and a `date` is not a `datetime` — so it slipped straight past the
serializer and broke backups on production with "Not JSON serializable". These
tests pin both directions: a date must survive the JSON, and come back as a date.
"""
import json
from datetime import date, datetime
from decimal import Decimal

from app import (
    db, FiscalYear, AccountingPeriod, Supplier,
    export_database_dict, import_database_dict, _json_default,
    seed_chart_of_accounts, seed_fiscal_year,
)


def _round_trip():
    """Export, go through real JSON, and import it back."""
    blob = json.dumps(export_database_dict(), default=_json_default)
    import_database_dict(json.loads(blob))
    return blob


def test_backup_serializes_date_columns(appctx):
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    db.session.commit()

    # this is the call that used to raise TypeError on production
    blob = json.dumps(export_database_dict(), default=_json_default)

    fy = json.loads(blob)["tables"]["fiscal_year"][0]
    assert fy["start_date"] == "2026-01-01"      # a plain date, not a timestamp
    assert fy["end_date"] == "2026-12-31"


def test_dates_come_back_as_dates_not_strings(appctx):
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    db.session.commit()

    _round_trip()

    fy = FiscalYear.query.filter_by(name="2026").one()
    assert isinstance(fy.start_date, date) and not isinstance(fy.start_date, datetime)
    assert fy.start_date == date(2026, 1, 1)

    # and a period, so find_period()'s date comparison still works after a restore
    p = AccountingPeriod.query.order_by(AccountingPeriod.start_date).first()
    assert isinstance(p.start_date, date)
    assert AccountingPeriod.query.filter(
        AccountingPeriod.start_date <= date(2026, 6, 15),
        AccountingPeriod.end_date >= date(2026, 6, 15)).count() == 1


def test_round_trip_preserves_rows_and_money(appctx):
    seed_chart_of_accounts()
    seed_fiscal_year(2026)
    db.session.add(Supplier(name="ABC", contact="03000000000", address="x",
                            opening_balance=Decimal("1234.5600")))
    db.session.commit()
    before = {t.name: db.session.query(t).count() for t in db.metadata.sorted_tables}

    _round_trip()

    after = {t.name: db.session.query(t).count() for t in db.metadata.sorted_tables}
    assert after == before                                   # nothing lost, nothing doubled
    assert Supplier.query.one().opening_balance == Decimal("1234.5600")   # exact money
