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

from app import app as flask_app, db  # noqa: E402


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
