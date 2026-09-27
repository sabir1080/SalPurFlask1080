"""Business Insights showed every metric as 0 on the Render (PostgreSQL) demo
while the main Dashboard worked fine. Root cause: business_insights()'s
"Monthly Sales Trend" block (dashboard.py) called sql_date_fmt(Sale.date)
separately for SELECT, GROUP BY, and ORDER BY -- each call builds its own
to_char(...) expression with its own bound parameter, so Postgres saw three
non-identical expressions and rejected the query with GroupingError
("column sale.date must appear in the GROUP BY clause or be used in an
aggregate function"), on every request, regardless of data. That failure,
being the first query the view runs, poisoned the SQLAlchemy session's
transaction for the rest of the request; every later section's own query
then failed too (each swallowed by its own `except Exception: pass`),
leaving every metric at its zero default. The exact same file's dashboard()
had -- and already fixed -- the identical bug in its own monthly_sales
chart query.

A second, independent bug in the same view's "Today vs Yesterday vs Last
Week Same Day" block compared a Postgres DATE expression to a plain Python
string (`db.func.date(Sale.date) == d.isoformat()`), which Postgres refuses
outright ("operator does not exist: date = character varying") since it
does not implicitly cast varchar to date.

Both fixes reuse patterns already proven correct elsewhere in this exact
file (dashboard()'s own monthly_sales query; _home_overview()'s "Today's
Sales" block, which already compares sql_date(Sale.date) to a real date
object).

These tests don't need a live PostgreSQL server: they dry-compile the exact
query shape against the postgresql dialect (SQLAlchemy's compiler produces
real Postgres SQL text and bind parameters without connecting to anything),
so the suite's SQLite-only test database (see conftest.py) doesn't hide a
dialect-specific bug the way it did when this shipped. Confirmed against a
real local PostgreSQL 16: business_insights() returns 200 with the buggy
pattern (silently all zeros) and populates real data with the fix; the
GroupingError / UndefinedFunction are reproduced verbatim in the
investigation this test encodes.
"""
import inspect

from sqlalchemy.dialects import postgresql

from app import app as flask_app, db, Sale, SaleItem, sql_date, sql_date_fmt
from datetime import date

from salpurflask.routes.dashboard import business_insights


def _as_postgres(monkeypatch):
    """sql_date()/sql_date_fmt() branch on db.engine.dialect.name -- the test
    suite's engine is always SQLite (conftest.py), so this flips just the
    dialect name their `if` checks read, for the duration of one test, to
    exercise the real Postgres code path and get real Postgres SQL text out
    of the compiler."""
    monkeypatch.setattr(db.engine.dialect, "name", "postgresql")


def test_monthly_trend_date_expression_is_reused_for_group_by_and_order_by(appctx, monkeypatch):
    """The fix: build sql_date_fmt(Sale.date) once and reuse it, so SELECT,
    GROUP BY, and ORDER BY share one bind parameter. Before the fix, this
    exact shape (three separate sql_date_fmt() calls) compiled to three
    distinct parameters (to_char_1/_2/_3) -- structurally different
    expressions to Postgres even though every one holds 'YYYY-MM' -- which
    is exactly what GroupingError was rejecting.
    """
    _as_postgres(monkeypatch)

    month_expr = sql_date_fmt(Sale.date)
    stmt = (
        db.session.query(
            month_expr.label("month"),
            db.func.sum(SaleItem.amount).label("total"),
        )
        .join(SaleItem, Sale.id == SaleItem.sale_id)
        .group_by(month_expr)
        .order_by(month_expr)
    ).statement

    compiled = stmt.compile(dialect=postgresql.dialect())
    sql_text = str(compiled)

    # One bind parameter for the format string, not three -- the tell-tale
    # sign the SELECT/GROUP BY/ORDER BY expressions are structurally
    # identical to Postgres.
    assert len(compiled.params) == 1, (
        f"expected sql_date_fmt(Sale.date) to be built once and reused, "
        f"got {len(compiled.params)} separate bind parameters: {compiled.params!r}"
    )
    to_char_calls = sql_text.count("to_char(sale.date")
    assert to_char_calls == 3, "sanity check: expected to_char() in SELECT, GROUP BY, and ORDER BY"


def test_day_sales_filter_binds_a_date_not_a_string(appctx, monkeypatch):
    """The fix: compare sql_date(Sale.date) to a real date object, not a
    string. Before the fix, `db.func.date(Sale.date) == d.isoformat()`
    bound the right-hand side as a plain VARCHAR, which is exactly what
    Postgres's "operator does not exist: date = character varying" was
    refusing (SQLite has no such type check, which is why this only ever
    broke on Render).
    """
    _as_postgres(monkeypatch)

    d = date(2026, 9, 27)
    fixed_expr = sql_date(Sale.date) == d
    bind_param = fixed_expr.right

    assert type(bind_param.type).__name__ == "Date", (
        f"expected the comparison value to bind as a Date, got "
        f"{type(bind_param.type).__name__} -- comparing to d.isoformat() "
        f"instead of d would reintroduce the Postgres type mismatch"
    )


def test_business_insights_source_does_not_reintroduce_either_bug():
    """The two tests above prove the *pattern* is correct in principle; this
    one ties that back to the actual, deployed business_insights() function
    (salpurflask/routes/dashboard.py) itself, since neither bug can be
    exercised end-to-end without a live PostgreSQL connection (the fixture
    in conftest.py pins the whole test session to SQLite, which never
    raised either error -- exactly how both bugs shipped in the first
    place).
    """
    # Strip comment-only lines first: this fix's own inline comments
    # document the old buggy snippets by name (e.g. "not ... d.isoformat()")
    # as an explanation, which would otherwise trip these checks up on the
    # very lines explaining why they exist.
    code_lines = [
        line for line in inspect.getsource(business_insights).splitlines()
        if not line.strip().startswith("#")
    ]
    source = "\n".join(code_lines)

    # Bug #1: sql_date_fmt(Sale.date) must be built once (e.g. `month_expr =
    # sql_date_fmt(Sale.date)`) and reused for SELECT/GROUP BY/ORDER BY, not
    # called separately for each -- three separate calls is exactly what
    # produced three non-identical to_char(...) expressions and Postgres's
    # GroupingError.
    assert source.count("sql_date_fmt(Sale.date)") == 1, (
        "sql_date_fmt(Sale.date) should be called exactly once and its "
        "result reused for SELECT/GROUP BY/ORDER BY -- calling it "
        "separately for each reintroduces the Postgres GroupingError"
    )

    # Bug #2: the day-filter must never compare a date expression to a
    # plain string -- Postgres has no `date = character varying` operator.
    assert ".isoformat()" not in source, (
        "found .isoformat() in business_insights() code -- comparing a "
        "date expression to a string reintroduces Postgres's "
        "\"operator does not exist: date = character varying\" error"
    )
