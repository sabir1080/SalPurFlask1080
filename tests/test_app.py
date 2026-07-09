"""Smoke tests for the health endpoint, stock-locking helper, and timezone."""
from datetime import datetime
from app import app, db, Category, Item, get_item_locked, to_local, localdt_filter


def test_health_ok():
    client = app.test_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_get_item_locked_returns_row(appctx):
    cat = Category(name="C"); db.session.add(cat); db.session.flush()
    it = Item(name="Locked", category_id=cat.id, stock=5)
    db.session.add(it); db.session.commit()
    locked = get_item_locked(it.id)
    assert locked is not None and locked.id == it.id and locked.stock == 5
    assert get_item_locked(999999) is None


def test_timezone_conversion():
    # tests run with APP_TIMEZONE=Asia/Karachi (UTC+5) unless overridden
    tz = app.config["APP_TIMEZONE"]
    utc = datetime(2026, 1, 1, 20, 30)          # 20:30 UTC
    local = to_local(utc)
    if tz == "Asia/Karachi":
        assert local.strftime("%Y-%m-%d %H:%M") == "2026-01-02 01:30"   # +5h, next day
    # naive input is treated as UTC and always yields an aware local datetime
    assert local.tzinfo is not None
    assert localdt_filter(None) == ""
