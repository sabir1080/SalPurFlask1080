"""Pytest setup: run the whole suite against ONE dedicated database -- local PostgreSQL
`tradeflow_test` -- and nothing else.

SAFETY: the `appctx` fixture below deletes every row of every table. These tests may only
ever run against `tradeflow_test` on 127.0.0.1/localhost:5432 -- never `tradeflow_dev`,
never `tradeflow`, never Neon (production or development), never any other database.

The database URL comes ONLY from the TRADEFLOW_TEST_DATABASE_URL environment variable
(no default, no `.env`, nothing in this repository), e.g. in PowerShell:

    $env:TRADEFLOW_TEST_DATABASE_URL = "postgresql://<user>:<password>@127.0.0.1:5432/tradeflow_test"

salpurflask/test_db_guard.py holds the rules. The URL is validated here BEFORE the app is
imported (importing the app runs migrations against the database), app.py validates it
again before reading it into Flask's config, and `appctx` verifies the LIVE connection
(current_database(), server address and port) before it deletes anything.
"""
import os
import pytest
from sqlalchemy import text

from salpurflask.test_db_guard import (
    TEST_MODE_ENV, TEST_URL_ENV, UnsafeTestDatabase,
    require_live_test_connection, require_postgres_test_database,
)

# Must be set BEFORE `import app` — the app reads these at import time.
_TEST_DB_URL = os.environ.get(TEST_URL_ENV, "").strip()
try:
    require_postgres_test_database(_TEST_DB_URL)
except UnsafeTestDatabase as exc:
    pytest.exit(str(exc), returncode=2)

os.environ[TEST_MODE_ENV] = "1"          # app.py: .env may not override; DB must be approved
os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("SECURITY_PASSWORD_SALT", "test-salt")
os.environ.setdefault("FLASK_DEBUG", "0")

# Pin the business settings the tests are written against.
#
# app.py calls load_dotenv(), and load_dotenv does not override variables already in the
# environment — so setting them here wins over whatever is in the developer's .env. Without
# this the suite reads that .env: put FISCAL_YEAR_START_MONTH=7 in it, for a Pakistani
# client, and two dozen tests that post a document in March fail with "no open period",
# because the only fiscal year seeded now runs July to June. Green on CI, where there is no
# .env, and red on the machine of the person who has to fix it.
#
# A test suite whose result depends on a file that is not in the repository is not telling
# you about your code. Tests that care about another value monkeypatch it and say so.
os.environ["FISCAL_YEAR_START_MONTH"] = "1"
os.environ["APP_TIMEZONE"] = "UTC"
os.environ["CURRENCY"] = "Rs"

from app import app as flask_app, db  # noqa: E402


def _assert_test_database_only():
    """Nothing destructive may run unless this is, on the live connection itself,
    `tradeflow_test` on the local PostgreSQL server."""
    # What the app was configured and built with ...
    require_postgres_test_database(flask_app.config["SQLALCHEMY_DATABASE_URI"])
    require_postgres_test_database(db.engine.url.render_as_string(hide_password=False))
    # ... and what the server says it actually connected us to.
    database, address, port = db.session.execute(text(
        "SELECT current_database(), host(inet_server_addr()), inet_server_port()")).one()
    require_live_test_connection(database, address, port)


# Belt and braces: app.py already refused to start on any other database.
with flask_app.app_context():
    _assert_test_database_only()
    db.session.rollback()

flask_app.config["WTF_CSRF_ENABLED"] = False   # let tests POST forms without a token
flask_app.config["PROPAGATE_EXCEPTIONS"] = False


@pytest.fixture()
def appctx():
    """Run inside an app context with an empty database."""
    with flask_app.app_context():
        _assert_test_database_only()      # never delete anything unless this is tradeflow_test
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()
        yield
        db.session.rollback()
