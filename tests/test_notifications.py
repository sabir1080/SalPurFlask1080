"""Notifications & alerts — Phase 1.

Covers the reusable Notification model/service, the three producers wired to
existing transaction points (low stock, leave, payroll), and the access rule
that a notification belongs to exactly one recipient.
"""
from datetime import date
from decimal import Decimal

from app import (app as flask_app, db, User, pwd_context, Account, Category,
                 Item, seed_chart_of_accounts, seed_fiscal_year, get_account,
                 ACC_CASH_IN_HAND, FinancialAccount)
from salpurflask.models.hr import Employee
from salpurflask.models.leave import LeaveType, LeaveRequest, seed_leave_types
from salpurflask.models.payroll import (SalaryComponent, SalaryStructure,
                                        SalaryStructureLine, PayrollPeriod,
                                        seed_default_components)
from salpurflask.models.models import item_remove_stock
from salpurflask.models.notification import Notification
from salpurflask.services import payroll_engine as engine
from salpurflask.services import payroll_accounting as acct
from salpurflask.services.feature_flags import set_module
from salpurflask.services.notifications import notify, notify_roles, unread_count, source_url


# ── helpers ───────────────────────────────────────────────────────────────────

def _user(role="admin", email=None, verified=True):
    email = email or f"{role}{User.query.count()}@notif.com"
    u = User(name=role.title(), email=email, password=pwd_context.hash("secret123"),
            verified=verified, role=role)
    db.session.add(u)
    db.session.commit()
    return u


def _client(user):
    c = flask_app.test_client()
    c.post("/signin", data={"email": user.email, "password": "secret123"})
    return c


def _enable(hr=True, leave=True, payroll=False):
    set_module("module_hr", hr, updated_by="test")
    set_module("module_leave", leave, updated_by="test")
    set_module("module_payroll", payroll, updated_by="test")


def _chart():
    if Account.query.count() == 0:
        seed_chart_of_accounts()
        db.session.commit()
    acct.seed_payroll_accounts()
    seed_fiscal_year(date(2026, 6, 15))
    seed_fiscal_year(date.today())
    db.session.commit()


def _cash():
    existing = FinancialAccount.query.filter_by(name="Cash").first()
    if existing:
        return existing
    gl = get_account(ACC_CASH_IN_HAND)
    fa_acc = FinancialAccount(name="Cash", method="Cash", account_type="Cash",
                              opening_balance=0, gl_account_id=gl.id)
    db.session.add(fa_acc)
    db.session.commit()
    return fa_acc


def _item(stock=10, reorder=5):
    cat = Category(name="Cat")
    db.session.add(cat)
    db.session.flush()
    it = Item(name="Widget", category_id=cat.id, stock=stock, reorder_level=reorder,
             purchase_price=10, sale_price=20, inventory_value=Decimal(str(stock * 10)))
    db.session.add(it)
    db.session.commit()
    return it


def _finalized_period(name="June 2026", start=date(2026, 6, 1), end=date(2026, 6, 30)):
    _chart()
    _cash()
    emp = Employee(code=f"E-{name}", name="Worker", joining_date=date(2025, 1, 1))
    db.session.add(emp)
    db.session.commit()
    seed_default_components()
    s = SalaryStructure(employee_id=emp.id, active=True)
    db.session.add(s)
    db.session.flush()
    db.session.add(SalaryStructureLine(
        structure_id=s.id, component_id=SalaryComponent.query.filter_by(code="BASIC").one().id,
        amount=Decimal("30000")))
    db.session.commit()
    p = PayrollPeriod(name=name, start_date=start, end_date=end, status="Draft")
    db.session.add(p)
    db.session.commit()
    engine.process_period(p)
    db.session.commit()
    return emp, p


# ── notification service basics ─────────────────────────────────────────────

def test_notify_creates_a_row(appctx):
    u = _user()
    row = notify(u.id, "test_type", "Title", "Message", severity="warning")
    assert row is not None
    assert Notification.query.count() == 1
    assert row.recipient_id == u.id
    assert row.severity == "warning"
    assert row.is_read is False


def test_notify_with_no_recipient_is_a_safe_noop(appctx):
    assert notify(None, "test_type", "T", "M") is None
    assert Notification.query.count() == 0


def test_unread_count(appctx):
    u = _user()
    notify(u.id, "a", "T1", "M1")
    notify(u.id, "b", "T2", "M2")
    assert unread_count(u.id) == 2
    row = Notification.query.first()
    row.is_read = True
    db.session.commit()
    assert unread_count(u.id) == 1


def test_unread_count_missing_user_returns_zero(appctx):
    assert unread_count(None) == 0
    assert unread_count(999999) == 0


def test_duplicate_protection_same_event_does_not_repeat(appctx):
    u = _user()
    notify(u.id, "low_stock", "T", "M", source_type="item", source_id=7)
    notify(u.id, "low_stock", "T", "M again", source_type="item", source_id=7)
    assert Notification.query.count() == 1


def test_duplicate_protection_lifts_once_read(appctx):
    u = _user()
    notify(u.id, "low_stock", "T", "M", source_type="item", source_id=7)
    Notification.query.first().is_read = True
    db.session.commit()
    notify(u.id, "low_stock", "T", "M again", source_type="item", source_id=7)
    assert Notification.query.count() == 2


def test_notify_roles_targets_only_matching_verified_users(appctx):
    admin = _user("admin")
    manager = _user("manager")
    _user("staff")
    _user("admin", email="unverified@notif.com", verified=False)
    created = notify_roles(("admin", "manager"), "t", "T", "M")
    recipients = {n.recipient_id for n in created}
    assert recipients == {admin.id, manager.id}


def test_missing_source_id_handled_safely(appctx):
    u = _user()
    row = notify(u.id, "t", "T", "M", source_type="item", source_id=None)
    assert row is not None
    assert source_url("item", None) is None
    assert source_url("nonexistent_type", 1) is None


# ── authorization ────────────────────────────────────────────────────────────

def test_mark_read_refuses_another_users_notification(appctx):
    owner = _user("staff", email="owner2@notif.com")
    intruder = _user("staff", email="intruder2@notif.com")
    n = notify(owner.id, "t", "T", "M")
    db.session.commit()
    c = _client(intruder)
    r = c.post(f"/notifications/{n.id}/read", follow_redirects=False)
    assert r.status_code == 404
    assert Notification.query.get(n.id).is_read is False


def test_owner_can_mark_their_own_notification_read(appctx):
    owner = _user("staff", email="owner3@notif.com")
    n = notify(owner.id, "t", "T", "M")
    db.session.commit()
    c = _client(owner)
    c.post(f"/notifications/{n.id}/read", follow_redirects=True)
    assert Notification.query.get(n.id).is_read is True


def test_list_page_only_shows_own_notifications(appctx):
    a = _user("staff", email="a@notif.com")
    b = _user("staff", email="b@notif.com")
    notify(a.id, "t", "Mine", "M")
    notify(b.id, "t", "NotMine", "M")
    db.session.commit()
    c = _client(a)
    body = c.get("/notifications/").get_data(as_text=True)
    assert "Mine" in body
    assert "NotMine" not in body


def test_mark_all_read_only_touches_own_rows(appctx):
    a = _user("staff", email="c@notif.com")
    b = _user("staff", email="d@notif.com")
    notify(a.id, "t", "T1", "M")
    notify(a.id, "t2", "T2", "M")
    other = notify(b.id, "t", "T3", "M")
    db.session.commit()
    c = _client(a)
    c.post("/notifications/read-all", follow_redirects=True)
    assert unread_count(a.id) == 0
    assert Notification.query.get(other.id).is_read is False


# ── producer: low stock ─────────────────────────────────────────────────────

def test_low_stock_notifies_on_crossing_the_reorder_level(appctx):
    admin = _user("admin")
    it = _item(stock=10, reorder=5)
    item_remove_stock(it, 6)  # 10 -> 4, crosses the reorder level of 5
    db.session.commit()
    rows = Notification.query.filter_by(notif_type="low_stock").all()
    assert len(rows) == 1
    assert rows[0].recipient_id == admin.id
    assert rows[0].source_type == "item"
    assert rows[0].source_id == it.id


def test_low_stock_does_not_repeat_while_already_below_reorder(appctx):
    _user("admin")
    it = _item(stock=10, reorder=5)
    item_remove_stock(it, 6)  # crosses to 4
    db.session.commit()
    item_remove_stock(it, 1)  # still below reorder, already alerted and unread
    db.session.commit()
    assert Notification.query.filter_by(notif_type="low_stock").count() == 1


def test_no_low_stock_notification_while_still_above_reorder(appctx):
    _user("admin")
    it = _item(stock=10, reorder=2)
    item_remove_stock(it, 3)  # 10 -> 7, still above reorder of 2
    db.session.commit()
    assert Notification.query.filter_by(notif_type="low_stock").count() == 0


def test_stock_removal_failure_does_not_touch_notifications(appctx):
    from app import PostingError
    _user("admin")
    it = _item(stock=3, reorder=5)
    try:
        item_remove_stock(it, 100)  # more than available -> PostingError
    except PostingError:
        pass
    db.session.rollback()
    assert Notification.query.count() == 0


# ── producer: leave ──────────────────────────────────────────────────────────

def test_pending_leave_notifies_approvers(appctx):
    _enable()
    admin = _user("admin", email="approver@notif.com")
    emp = Employee(code="E-1", name="Worker", joining_date=date(2025, 1, 1))
    db.session.add(emp)
    db.session.commit()
    seed_leave_types()
    lt = LeaveType.query.filter_by(code="ANNUAL").one()
    c = _client(admin)
    c.post("/leave/requests/new", data={
        "employee_id": str(emp.id), "leave_type_id": str(lt.id),
        "start_date": "2026-09-01", "end_date": "2026-09-02",
        "day_portion": "full", "reason": "test", "submit_now": "1",
    }, follow_redirects=True)
    rows = Notification.query.filter_by(notif_type="leave_pending").all()
    assert len(rows) == 1
    assert rows[0].recipient_id == admin.id


def test_leave_approval_notifies_the_employee(appctx):
    _enable()
    admin = _user("admin", email="approver2@notif.com")
    emp_user = _user("staff", email="empuser@notif.com")
    emp = Employee(code="E-2", name="Worker", joining_date=date(2025, 1, 1),
                   user_id=emp_user.id)
    db.session.add(emp)
    db.session.commit()
    seed_leave_types()
    lt = LeaveType.query.filter_by(code="UNPAID").one()  # no allocation required
    r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                     start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
                     day_portion="full", status="Pending")
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()

    c = _client(admin)
    c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    rows = Notification.query.filter_by(notif_type="leave_decided",
                                        recipient_id=emp_user.id).all()
    assert len(rows) == 1
    assert "approved" in rows[0].title.lower()


def test_leave_rejection_notifies_the_employee(appctx):
    _enable()
    admin = _user("admin", email="approver3@notif.com")
    emp_user = _user("staff", email="empuser2@notif.com")
    emp = Employee(code="E-3", name="Worker", joining_date=date(2025, 1, 1),
                   user_id=emp_user.id)
    db.session.add(emp)
    db.session.commit()
    seed_leave_types()
    lt = LeaveType.query.filter_by(code="UNPAID").one()  # no allocation required
    r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                     start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
                     day_portion="full", status="Pending")
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()

    c = _client(admin)
    c.post(f"/leave/requests/{r.id}/reject", follow_redirects=True)
    rows = Notification.query.filter_by(notif_type="leave_decided",
                                        recipient_id=emp_user.id).all()
    assert len(rows) == 1
    assert "rejected" in rows[0].title.lower()


def test_leave_request_without_a_linked_user_does_not_crash_on_decision(appctx):
    _enable()
    admin = _user("admin", email="approver4@notif.com")
    emp = Employee(code="E-4", name="Worker", joining_date=date(2025, 1, 1))
    db.session.add(emp)
    db.session.commit()
    seed_leave_types()
    lt = LeaveType.query.filter_by(code="UNPAID").one()  # no allocation required
    r = LeaveRequest(employee_id=emp.id, leave_type_id=lt.id,
                     start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
                     day_portion="full", status="Pending")
    r.recalculate_days()
    db.session.add(r)
    db.session.commit()

    c = _client(admin)
    resp = c.post(f"/leave/requests/{r.id}/approve", follow_redirects=True)
    assert resp.status_code == 200
    assert LeaveRequest.query.get(r.id).status == "Approved"


# ── producer: payroll ────────────────────────────────────────────────────────

def test_payroll_finalize_notifies_admin(appctx):
    _enable(payroll=True)
    admin = _user("admin", email="payadmin@notif.com")
    emp, p = _finalized_period()
    c = _client(admin)
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    rows = Notification.query.filter_by(notif_type="payroll_finalized").all()
    assert len(rows) == 1
    assert rows[0].recipient_id == admin.id
    assert rows[0].source_id == p.id


def test_payroll_cancel_notifies_admin(appctx):
    _enable(payroll=True)
    admin = _user("admin", email="payadmin2@notif.com")
    emp, p = _finalized_period()
    c = _client(admin)
    c.post(f"/payroll/periods/{p.id}/finalize", follow_redirects=True)
    c.post(f"/payroll/periods/{p.id}/cancel", follow_redirects=True)
    rows = Notification.query.filter_by(notif_type="payroll_cancelled").all()
    assert len(rows) == 1
    assert rows[0].recipient_id == admin.id
