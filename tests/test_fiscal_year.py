"""A tax year is not January to December everywhere, and a business cannot pretend it is.

Pakistan runs July–June, the UK April–March, the UAE and the US January–December. The
fiscal year decides which periods exist, and nothing can be posted on a date no period
covers — so a business whose year the system cannot express cannot use the system at all.
"""
from datetime import date, datetime

import pytest

import app as app_module
from app import (
    db, fiscal_year_bounds, seed_fiscal_year, FiscalYear, AccountingPeriod,
    money_filter, app as flask_app,
)


def _start_month(monkeypatch, m):
    monkeypatch.setattr(app_module, "FISCAL_YEAR_START_MONTH", m)


def test_january_year_is_the_calendar_year(monkeypatch):
    _start_month(monkeypatch, 1)
    assert fiscal_year_bounds(date(2026, 3, 9)) == ("2026", date(2026, 1, 1), date(2026, 12, 31))


def test_pakistan_runs_july_to_june(monkeypatch):
    _start_month(monkeypatch, 7)
    # September 2026 is in the year that began in July 2026
    assert fiscal_year_bounds(date(2026, 9, 1)) == ("2026-27", date(2026, 7, 1), date(2027, 6, 30))
    # March 2026 is still in the year that began in July 2025 — the trap a naive
    # `datetime.now().year` falls straight into
    assert fiscal_year_bounds(date(2026, 3, 1)) == ("2025-26", date(2025, 7, 1), date(2026, 6, 30))


def test_the_uk_runs_april_to_march(monkeypatch):
    _start_month(monkeypatch, 4)
    assert fiscal_year_bounds(date(2026, 5, 1)) == ("2026-27", date(2026, 4, 1), date(2027, 3, 31))
    assert fiscal_year_bounds(date(2026, 2, 1)) == ("2025-26", date(2025, 4, 1), date(2026, 3, 31))


def test_a_july_year_seeds_twelve_months_starting_in_july(appctx, monkeypatch):
    _start_month(monkeypatch, 7)
    assert seed_fiscal_year(datetime(2026, 9, 15)) == 12

    fy = FiscalYear.query.one()
    assert (fy.name, fy.start_date, fy.end_date) == ("2026-27", date(2026, 7, 1), date(2027, 6, 30))

    periods = AccountingPeriod.query.order_by(AccountingPeriod.start_date).all()
    assert len(periods) == 12
    assert periods[0].start_date == date(2026, 7, 1)      # first month is July
    assert periods[-1].end_date == date(2027, 6, 30)      # last is the following June
    assert periods[6].start_date == date(2027, 1, 1)      # and it rolls over the new year


def test_seeding_the_same_year_twice_does_nothing(appctx, monkeypatch):
    _start_month(monkeypatch, 7)
    assert seed_fiscal_year(datetime(2026, 9, 1)) == 12
    assert seed_fiscal_year(datetime(2027, 2, 1)) == 0    # same fiscal year, already there
    assert FiscalYear.query.count() == 1


def test_changing_the_setting_on_a_live_database_is_reported(appctx, monkeypatch):
    """The trap. Changing FISCAL_YEAR_START_MONTH does not move the years already there —
    it leaves them, overlapping the new ones, and a document dated inside the overlap lands
    in whichever period is found first. Nothing can fix that automatically: deleting a
    fiscal year would take its postings with it. So it has to be said out loud."""
    _start_month(monkeypatch, 1)
    seed_fiscal_year(datetime(2026, 3, 1))               # a calendar year, "2026"
    assert app_module.fiscal_years_that_disagree_with_the_setting() == []

    _start_month(monkeypatch, 7)                          # the business moves to July–June
    assert app_module.fiscal_years_that_disagree_with_the_setting() == ["2026"]


def test_money_carries_the_currency(monkeypatch):
    monkeypatch.setitem(flask_app.config, "CURRENCY", "AED")
    assert money_filter(1234567.891) == "AED 1,234,567.89"

    monkeypatch.setitem(flask_app.config, "CURRENCY", "")
    assert money_filter(1234.5) == "1,234.50"            # blank means no symbol, not " 1,234.50"
