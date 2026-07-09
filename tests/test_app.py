"""Smoke tests for the health endpoint and the stock-locking helper."""
from app import app, db, Category, Item, get_item_locked


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
