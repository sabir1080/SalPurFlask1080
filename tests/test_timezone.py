"""The app runs on the business's clock, not the server's.

datetime.now() reads the machine's clock, and the machine is a server in someone else's
data centre — on Render it is UTC, five hours behind Karachi. Every business date derived
from it is the server's idea of today, and that is not a cosmetic difference: an invoice
entered at 2am lands on yesterday, a month-end entry posted on the 1st lands in the
previous month, and a document reversed this morning is stamped five hours in the past, so
a report run "as of now" shows the sale but not the reversal — the cancelled invoice goes
on earning profit that was never made.

That last one is not hypothetical. It is what the demo did: profit 277,517.89 against a
ledger that said 230,120.10.
"""
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import app as app_module
from app import (
    app as flask_app, db, now_local, post_entry, reverse_entry,
    get_account, ACC_CASH_IN_HAND, ACC_CAPITAL,
    seed_chart_of_accounts, seed_fiscal_year,
)

# Fifteen hours from UTC — far enough that a wrong clock cannot pass by luck, whatever
# zone the machine running the tests happens to be in.
FAR_AWAY = ZoneInfo("Pacific/Kiritimati")


def test_app_py_never_reads_the_machine_clock_for_a_business_date():
    """A guard, because this failure is silent. `datetime.now()` looks completely correct
    at the call site; it only goes wrong once the code is on a server in another time zone,
    and then it goes wrong quietly, in the ledger."""
    src = Path(flask_app.root_path, "app.py").read_text(encoding="utf-8")

    offenders = []
    for n, line in enumerate(src.splitlines(), 1):
        if not re.search(r"datetime\.now\(\)", line):
            continue
        # The docstring of now_local() is allowed to name the thing it replaces.
        if line.lstrip().startswith(("#", '"', "'")) or "not datetime.now()" in line:
            continue
        offenders.append(f"app.py:{n}: {line.strip()}")

    assert not offenders, (
        "use now_local(), not datetime.now() — the server's clock is not the business's:\n  "
        + "\n  ".join(offenders))


def test_now_local_follows_the_configured_timezone_not_the_machine(monkeypatch):
    monkeypatch.setattr(app_module, "APP_TZ", FAR_AWAY)
    drift = abs(now_local() - datetime.now())
    assert drift > timedelta(hours=2), (
        "now_local() is tracking the machine clock — the APP_TIMEZONE setting does nothing")


def test_a_reversal_is_dated_on_the_business_clock(appctx, monkeypatch):
    """The bug in the round: the reversal is stamped by the server, the report asks the
    server what 'now' is, and the two agree — until the entries were written by a machine in
    a different zone, or the report is run near midnight. Then the reversal falls outside
    'as of now' and the document it cancels is still counted."""
    seed_chart_of_accounts()
    seed_fiscal_year(datetime.now().year)
    monkeypatch.setattr(app_module, "APP_TZ", FAR_AWAY)

    entry = post_entry(
        entry_date=now_local() - timedelta(days=3), description="Cash in",
        lines=[{"code": ACC_CASH_IN_HAND, "debit": Decimal("100"), "credit": 0},
               {"code": ACC_CAPITAL, "debit": 0, "credit": Decimal("100")}],
    )
    db.session.commit()

    reversal = reverse_entry(entry)
    db.session.commit()

    # Dated on the business's clock, so a report that asks the same clock for "now" sees it.
    assert abs(reversal.entry_date - now_local()) < timedelta(minutes=5)
    assert reversal.entry_date <= now_local()
