"""Tests for the audit trail helper."""
from app import db, AuditLog, record_audit


def test_record_audit_creates_entry(appctx):
    record_audit("create", "Supplier", 42, "Supplier 'Acme' added")
    e = AuditLog.query.one()
    assert e.action == "create"
    assert e.entity == "Supplier"
    assert e.entity_id == 42
    assert e.summary == "Supplier 'Acme' added"
    # no request/user context in a bare test -> attributed to system
    assert e.user_name == "system"
    assert e.created_at is not None


def test_record_audit_truncates_long_summary(appctx):
    record_audit("update", "Item", 1, "x" * 500)
    assert len(AuditLog.query.one().summary) == 300


def test_record_audit_never_raises(appctx):
    # Bad/None args must not blow up the caller.
    record_audit("delete", "Thing", None, None)
    assert AuditLog.query.count() == 1
