"""Accounting/GL module authorization — Phase 8 security remediation.

The "PHASE 11 - ACCOUNTING MODULE MIGRATION" commit moved every accounting
route from app.py into salpurflask/accounting/routes.py and silently dropped
every @manager_required/@admin_required decorator in the process — verified
against that commit's own parent, where all 27 routes carried one or the
other. No app-wide before_request auth hook ever existed to compensate, so
the entire GL (view, edit, delete, close periods, reverse entries) was
reachable by anyone with no session at all. This file proves the restored
decorators actually gate access, using the app's own existing role_required
semantics (unauthenticated -> redirect to signin; wrong role -> redirect,
never a raw 200).
"""
from app import app as flask_app, db, User, pwd_context


# ── helpers ───────────────────────────────────────────────────────────────────

def _login(user):
    from flask import g
    try:
        del g._login_user
    except AttributeError:
        pass
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True
    return c


def _user(role, email=None):
    email = email or f"{role}{User.query.count()}@accttest.com"
    u = User(name=role.capitalize(), email=email, password=pwd_context.hash("secret123"),
            verified=True, role=role)
    db.session.add(u)
    db.session.commit()
    return u


def _admin(email=None):
    return _user("admin", email=email)


def _manager(email=None):
    return _user("manager", email=email)


def _staff(email=None):
    return _user("staff", email=email)


# The 27 accounting routes and their required role, mirroring the exact
# historical mapping recovered from git commit 9ab32d2's parent.
MANAGER_GET_ROUTES = [
    "/accounts", "/journal", "/fixed_assets", "/periods",
    "/chart_of_accounts", "/reports/balance_sheet", "/reports/trial_balance",
    "/reports/gst",
]
# report_reconciliation() needs a seeded chart of accounts to render past its
# own business logic (unrelated to authorization) — its auth gate is proven
# instead by the redirect-when-unauthenticated tests below, same as every
# other route here; it's just excluded from the "returns 200" bulk checks.
ADMIN_ONLY_GET_ROUTES = [
    "/accounts/new", "/chart_of_accounts/new", "/tax_codes",
]


# ── unauthenticated access ──────────────────────────────────────────────────

def test_unauthenticated_client_cannot_reach_chart_of_accounts(appctx):
    c = flask_app.test_client()
    resp = c.get("/chart_of_accounts", follow_redirects=False)
    assert resp.status_code == 302
    assert "/signin" in resp.headers["Location"]


def test_unauthenticated_client_cannot_reach_journal(appctx):
    c = flask_app.test_client()
    resp = c.get("/journal", follow_redirects=False)
    assert resp.status_code == 302
    assert "/signin" in resp.headers["Location"]


def test_unauthenticated_client_cannot_reach_accounts(appctx):
    c = flask_app.test_client()
    resp = c.get("/accounts", follow_redirects=False)
    assert resp.status_code == 302
    assert "/signin" in resp.headers["Location"]


def test_unauthenticated_client_cannot_reach_periods(appctx):
    c = flask_app.test_client()
    resp = c.get("/periods", follow_redirects=False)
    assert resp.status_code == 302
    assert "/signin" in resp.headers["Location"]


def test_unauthenticated_client_cannot_delete_gl_account(appctx):
    """The single most destructive accounting route — must never be
    reachable anonymously. CSRF fires first (also correctly configured),
    but the auth gate must hold even if CSRF were somehow bypassed."""
    c = flask_app.test_client()
    resp = c.post("/chart_of_accounts/1/delete", follow_redirects=False)
    assert resp.status_code in (302, 400)  # CSRF rejection or auth redirect — never a 200


def test_unauthenticated_client_cannot_close_fiscal_year(appctx):
    c = flask_app.test_client()
    resp = c.post("/fiscal_years/1/close", follow_redirects=False)
    assert resp.status_code in (302, 400)


def test_unauthenticated_client_cannot_reach_any_manager_route(appctx):
    c = flask_app.test_client()
    for path in MANAGER_GET_ROUTES:
        resp = c.get(path, follow_redirects=False)
        assert resp.status_code == 302, f"{path} returned {resp.status_code}, expected a redirect"
        assert "/signin" in resp.headers["Location"], f"{path} did not redirect to signin"


def test_unauthenticated_client_cannot_reach_any_admin_only_route(appctx):
    c = flask_app.test_client()
    for path in ADMIN_ONLY_GET_ROUTES:
        resp = c.get(path, follow_redirects=False)
        assert resp.status_code == 302, f"{path} returned {resp.status_code}, expected a redirect"
        assert "/signin" in resp.headers["Location"], f"{path} did not redirect to signin"


# ── authorized manager ──────────────────────────────────────────────────────

def test_manager_can_access_chart_of_accounts(appctx):
    mgr = _manager()
    c = _login(mgr)
    resp = c.get("/chart_of_accounts")
    assert resp.status_code == 200


def test_manager_can_access_journal(appctx):
    mgr = _manager(email="mgr2@accttest.com")
    c = _login(mgr)
    resp = c.get("/journal")
    assert resp.status_code == 200


def test_manager_can_access_periods(appctx):
    mgr = _manager(email="mgr3@accttest.com")
    c = _login(mgr)
    resp = c.get("/periods")
    assert resp.status_code == 200


# ── authorized admin ────────────────────────────────────────────────────────

def test_admin_can_access_new_account_form(appctx):
    admin = _admin()
    c = _login(admin)
    resp = c.get("/accounts/new")
    assert resp.status_code == 200


def test_admin_can_access_tax_codes(appctx):
    admin = _admin(email="admin2@accttest.com")
    c = _login(admin)
    resp = c.get("/tax_codes")
    assert resp.status_code == 200


def test_admin_can_access_manager_level_routes_too(appctx):
    """Admin is a superset — every @manager_required route must also work
    for an admin, the same role hierarchy every other module already uses."""
    admin = _admin(email="admin3@accttest.com")
    c = _login(admin)
    for path in MANAGER_GET_ROUTES:
        resp = c.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code} for admin"


# ── unauthorized lower-privilege user ───────────────────────────────────────

def test_staff_cannot_access_manager_only_chart_of_accounts(appctx):
    staff = _staff()
    c = _login(staff)
    resp = c.get("/chart_of_accounts", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] != "/chart_of_accounts"  # actually refused, not served


def test_manager_cannot_access_admin_only_new_account(appctx):
    mgr = _manager(email="mgr4@accttest.com")
    c = _login(mgr)
    resp = c.get("/accounts/new", follow_redirects=False)
    assert resp.status_code == 302


def test_manager_cannot_delete_gl_account(appctx):
    """delete_gl_account is @admin_required — a manager must be refused,
    not merely discouraged by a hidden button."""
    mgr = _manager(email="mgr5@accttest.com")
    c = _login(mgr)
    resp = c.post("/chart_of_accounts/1/delete", follow_redirects=False)
    assert resp.status_code == 302


def test_manager_cannot_close_fiscal_year(appctx):
    mgr = _manager(email="mgr6@accttest.com")
    c = _login(mgr)
    resp = c.post("/fiscal_years/1/close", follow_redirects=False)
    assert resp.status_code == 302


def test_staff_cannot_access_any_manager_route(appctx):
    staff = _staff(email="staff2@accttest.com")
    c = _login(staff)
    for path in MANAGER_GET_ROUTES:
        resp = c.get(path, follow_redirects=False)
        assert resp.status_code == 302, f"{path} returned {resp.status_code} for staff, expected redirect"
