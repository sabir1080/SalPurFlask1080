"""Pytest setup: point the app at a throwaway temp SQLite DB before importing it,
so tests never touch the real dev/instance database. Each test gets a clean DB.
"""
import os
import tempfile
import pytest

# Must be set BEFORE `import app` — the app reads these at import time.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = "sqlite:///" + _tmp.name.replace("\\", "/")
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

flask_app.config["WTF_CSRF_ENABLED"] = False   # let tests POST forms without a token
flask_app.config["PROPAGATE_EXCEPTIONS"] = False


@pytest.fixture()
def appctx():
    """Run inside an app context with an empty database."""
    with flask_app.app_context():
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()
        yield
        db.session.rollback()


def pytest_unconfigure(config):
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass
