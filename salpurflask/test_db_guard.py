"""Hard guard: the test suite may only ever run against the dedicated test database.

Why this exists
---------------
tests/conftest.py's `appctx` fixture deletes every row of every table. app.py used to
load `.env` with override=True, which silently replaced the test database URL that
conftest had set with whatever `DATABASE_URL` `.env` named -- once a live Neon database.
Running pytest then emptied it.

The ONE approved test database is the local PostgreSQL database ``tradeflow_test``:

    host     127.0.0.1 or localhost
    port     5432
    database tradeflow_test            (exactly -- nothing else on that server)

Its URL comes ONLY from the TRADEFLOW_TEST_DATABASE_URL environment variable, never
from `.env`, never from a default, and never from this repository. Everything else is
rejected, by name where it matters: the development database ``tradeflow_dev``, the
original ``tradeflow``, every ``*.neon.tech`` host (production and development alike),
and any other PostgreSQL.

One narrow exception, for a single test that needs a database it can damage on purpose
(tests/test_sale_purchase_status_column.py rebuilds a pre-migration schema in a separate
subprocess): a throwaway SQLite file in the OS temp directory named
``tradeflow_test_*.db`` -- and ONLY when that subprocess is started with
TRADEFLOW_ISOLATED_SQLITE=1. The normal pytest process never sets it, and conftest
validates the normal test database with the strict PostgreSQL-only check below, so the
exception cannot apply to it.

app.py calls require_approved_test_database() BEFORE it reads the database URL into
Flask's config or touches a database; tests/conftest.py validates the URL before the app
is imported, and verifies the LIVE connection (current_database(), server address, port)
before the fixture that deletes rows.

Standard library only -- importing this module never imports the app or opens a
database connection.
"""
import os
import sys
import tempfile
from urllib.parse import unquote, urlsplit

TEST_MODE_ENV = "TRADEFLOW_TEST_MODE"
TEST_URL_ENV = "TRADEFLOW_TEST_DATABASE_URL"
ISOLATED_SQLITE_ENV = "TRADEFLOW_ISOLATED_SQLITE"

TEST_DB_NAME = "tradeflow_test"
TEST_DB_PORT = 5432
LOCAL_HOSTS = ("127.0.0.1", "localhost")
LOOPBACK_ADDRESSES = ("127.0.0.1", "::1")      # what the server reports for a local TCP connection

# The isolated-SQLite exception (see module docstring).
SQLITE_PREFIX = "tradeflow_test_"
SQLITE_SUFFIX = ".db"

_NAMED_DATABASES = {
    "tradeflow_dev": "the local DEVELOPMENT database",
    "tradeflow": "the original local database",
}


class UnsafeTestDatabase(RuntimeError):
    """Raised when a test run is about to use a database that is not the approved one."""


def under_test():
    """True when this process is a test run: conftest set TRADEFLOW_TEST_MODE=1, or
    pytest is loaded at all (so a run that bypasses conftest is still guarded)."""
    return os.environ.get(TEST_MODE_ENV) == "1" or "pytest" in sys.modules


def _is_neon(url, host):
    return "neon.tech" in (host or "") or "neon.tech" in (url or "").lower()


def _postgres_problem(url, parts):
    """None if `url` is exactly local PostgreSQL tradeflow_test, else why not.
    Never repeats the credentials: only host, port and database name are quoted."""
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        return "its port is not a number"
    database = unquote(parts.path or "").lstrip("/")
    if parts.scheme.split("+")[0] not in ("postgresql", "postgres"):
        return f"it is a {parts.scheme or 'non-PostgreSQL'} database, not local PostgreSQL {TEST_DB_NAME}"
    if host not in LOCAL_HOSTS:
        return (f"the host is '{host or '(none)'}', not the local machine "
                f"({' or '.join(LOCAL_HOSTS)})")
    if (port or TEST_DB_PORT) != TEST_DB_PORT:
        return f"the port is {port}, not {TEST_DB_PORT}"
    if database != TEST_DB_NAME:
        what = _NAMED_DATABASES.get(database)
        return (f"the database is '{database or '(none)'}'"
                f"{' (' + what + ')' if what else ''}, not '{TEST_DB_NAME}'")
    return None


def _sqlite_path(parts):
    path = unquote(parts.path or "")
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":      # '/C:/dir/x.db' on Windows
        path = path[1:]
    return path


def _real(path):
    return os.path.normcase(os.path.realpath(path))


def _isolated_sqlite_problem(parts):
    """The one exception: a throwaway tradeflow_test_*.db in the OS temp dir, and only
    in a subprocess that was explicitly started with TRADEFLOW_ISOLATED_SQLITE=1."""
    if os.environ.get(ISOLATED_SQLITE_ENV) != "1":
        return (f"it is a SQLite database and {ISOLATED_SQLITE_ENV}=1 is not set; tests use "
                f"local PostgreSQL {TEST_DB_NAME}")
    path = _sqlite_path(parts)
    if not path or path == ":memory:":
        return "it is an in-memory / unnamed SQLite database"
    real = _real(path)
    if os.path.dirname(real) != _real(tempfile.gettempdir()):
        return f"the SQLite file is not in the OS temp directory ({path})"
    name = os.path.basename(real)
    if not (name.startswith(SQLITE_PREFIX) and name.endswith(SQLITE_SUFFIX)):
        return f"the SQLite file name does not match '{SQLITE_PREFIX}*{SQLITE_SUFFIX}'"
    return None


def approval_problem(url):
    """None if `url` may be used by a test run (local PostgreSQL tradeflow_test, or the
    isolated-SQLite exception), else a sentence saying why not."""
    if not url:
        return ("no database URL is configured, so the app would fall back to the "
                "repository's own instance/database.db")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if _is_neon(url, host):
        return (f"it points at a Neon database ({host or 'neon.tech'}) -- Neon production and "
                "Neon development are both off limits to tests")
    if parts.scheme.split("+")[0] == "sqlite":
        return _isolated_sqlite_problem(parts)
    return _postgres_problem(url, parts)


def postgres_test_problem(url):
    """Strict form for the NORMAL test database: local PostgreSQL tradeflow_test only.
    The isolated-SQLite exception never passes this check."""
    if not url:
        return f"{TEST_URL_ENV} is not set"
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if _is_neon(url, host):
        return (f"it points at a Neon database ({host or 'neon.tech'}) -- Neon production and "
                "Neon development are both off limits to tests")
    return _postgres_problem(url, parts)


def _refuse(problem):
    raise UnsafeTestDatabase(
        "REFUSING TO RUN TESTS: " + problem + ". The test suite deletes every row of every "
        f"table, so it may only run against local PostgreSQL '{TEST_DB_NAME}' "
        f"(set {TEST_URL_ENV}). Nothing was changed.")


def require_approved_test_database(url):
    """app.py's check: tradeflow_test, or the isolated-SQLite exception. Raises otherwise."""
    problem = approval_problem(url)
    if problem:
        _refuse(problem)


def require_postgres_test_database(url):
    """conftest's check for the normal test database: tradeflow_test only."""
    problem = postgres_test_problem(url)
    if problem:
        _refuse(problem)


def live_connection_problem(database, address, port):
    """Judge what the server itself reports for the open connection:
    SELECT current_database(), host(inet_server_addr()), inet_server_port().
    None if it is the local tradeflow_test server connection."""
    if database != TEST_DB_NAME:
        return f"the connected database is '{database}', not '{TEST_DB_NAME}'"
    if address not in LOOPBACK_ADDRESSES:
        return (f"the server address is '{address}', not this machine "
                f"({' or '.join(LOOPBACK_ADDRESSES)})")
    if port != TEST_DB_PORT:
        return f"the server port is {port}, not {TEST_DB_PORT}"
    return None


def require_live_test_connection(database, address, port):
    problem = live_connection_problem(database, address, port)
    if problem:
        _refuse(problem)
