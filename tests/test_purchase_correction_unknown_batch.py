"""Correcting a purchase that was posted BEFORE batch tracking was enabled must keep the item's Unknown
Batch consistent with its stock.

enable_batch_tracking() folds all stock that exists at that moment, the old purchase's units included,
into the item's Unknown Batch. The correction used to take the old line's units out with the plain
item_remove_stock(), which lowers Item.stock / ItemStock but never the Unknown Batch, while the corrected
line is then added to a real batch. The Unknown Batch kept those units for good and BatchStock ended up
above the item's stock (seen in dev: item 299 held 112 units but 122 in batches). The unwind now goes
through item_remove_stock_batched() against the Unknown Batch: item and warehouse stock are reduced exactly
once, and so is the Unknown Batch.

Fixtures are shared with tests/test_purchase_correction_pretracking_batch.py.
"""
from app import db, Item, StockMovement, Batch
from salpurflask.models.inventory_location import BatchStock, ItemStock, get_or_create_default_location

from tests.test_purchase_correction_batch_consumed import (
    _books, _item, _supplier, _manager, _admin,
    _purchase_via_form, _latest_purchase, _post, _correct_via_form,
)
from tests.test_purchase_correction_pretracking_batch import _enable_tracking


def _unknown_qty(item):
    return (db.session.query(db.func.coalesce(db.func.sum(BatchStock.quantity), 0))
            .join(Batch, Batch.id == BatchStock.batch_id)
            .filter(Batch.item_id == item.id, Batch.batch_no.is_(None)).scalar())


def _batch_total(item):
    return (db.session.query(db.func.coalesce(db.func.sum(BatchStock.quantity), 0))
            .join(Batch, Batch.id == BatchStock.batch_id).filter(Batch.item_id == item.id).scalar())


def _itemstock(item):
    loc = get_or_create_default_location().id
    return ItemStock.query.filter_by(item_id=item.id, location_id=loc).one().quantity


def _consistent(item, stock):
    db.session.expire_all()
    item = db.session.get(Item, item.id)
    assert item.stock == stock
    assert _itemstock(item) == stock
    assert _batch_total(item) == stock           # BatchStock never above (or below) the item's stock
    return item


def test_unknown_batch_is_reduced_when_a_historical_purchase_is_corrected(appctx):
    _books()
    sup, item = _supplier(), _item(batch_tracked=False)
    manager, admin = _manager(), _admin()
    _purchase_via_form(manager, sup, item, 2, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _enable_tracking(item)
    assert _unknown_qty(item) == 2

    resp = _correct_via_form(admin, pur, [(item, 3, 200, "B-NEW", "2027-06-30")], reason="qty")
    assert b"Cannot correct" not in resp.data

    item = _consistent(item, 3)
    assert _unknown_qty(item) == 0               # the old 2 units left the Unknown Batch
    new_batch = Batch.query.filter_by(item_id=item.id, batch_no="B-NEW").one()
    assert BatchStock.query.filter_by(batch_id=new_batch.id).one().quantity == 3


def test_other_stock_in_the_unknown_batch_is_left_alone(appctx):
    """Item 299's shape: an earlier historical purchase stays in the Unknown Batch, only the corrected
    purchase's own units come out of it."""
    _books()
    sup, item = _supplier(), _item(batch_tracked=False)
    manager, admin = _manager(), _admin()
    _purchase_via_form(manager, sup, item, 5, 200, batch_no="")
    _post(manager, _latest_purchase().id)
    _purchase_via_form(manager, sup, item, 10, 200, batch_no="")
    second = _latest_purchase()
    _post(manager, second.id)
    _enable_tracking(item)
    assert _unknown_qty(item) == 15

    _correct_via_form(admin, second, [(item, 10, 200, "B-10", "2027-06-30")], reason="add batch no")

    item = _consistent(item, 15)
    assert _unknown_qty(item) == 5               # the first purchase's units
    new_batch = Batch.query.filter_by(item_id=item.id, batch_no="B-10").one()
    assert BatchStock.query.filter_by(batch_id=new_batch.id).one().quantity == 10


def test_unwind_movement_is_recorded_against_the_unknown_batch(appctx):
    _books()
    sup, item = _supplier(), _item(batch_tracked=False)
    manager, admin = _manager(), _admin()
    _purchase_via_form(manager, sup, item, 2, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _enable_tracking(item)
    unknown = Batch.query.filter_by(item_id=item.id, batch_no=None).one()

    _correct_via_form(admin, pur, [(item, 3, 200, "B-NEW", "2027-06-30")], reason="qty")

    outs = StockMovement.query.filter_by(item_id=item.id, source_type="purchase", source_id=pur.id,
                                         direction="out").all()
    assert len(outs) == 1 and outs[0].quantity == -2 and outs[0].batch_id == unknown.id


def test_correction_is_refused_and_rolled_back_when_the_unknown_batch_cannot_cover_it(appctx):
    """Item stock is enough but the Unknown Batch no longer holds the units: the correction must be
    refused as a whole instead of leaving batch stock and item stock out of step."""
    _books()
    sup, item = _supplier(), _item(batch_tracked=False)
    manager, admin = _manager(), _admin()
    _purchase_via_form(manager, sup, item, 2, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _enable_tracking(item)
    _purchase_via_form(manager, sup, item, 3, 200, batch_no="B-X", expiry_date="2027-06-30")
    _post(manager, _latest_purchase().id)
    unknown_stock = (BatchStock.query.join(Batch, Batch.id == BatchStock.batch_id)
                     .filter(Batch.item_id == item.id, Batch.batch_no.is_(None)).one())
    unknown_stock.quantity = 1                     # arrange: fewer left in the Unknown Batch than the line's 2
    db.session.commit()
    before = (db.session.get(Item, item.id).stock, _itemstock(item), _batch_total(item))

    resp = _correct_via_form(admin, pur, [(item, 3, 200, "B-NEW", "2027-06-30")], reason="qty")

    assert b"has only 1 available" in resp.data
    db.session.expire_all()
    assert (db.session.get(Item, item.id).stock, _itemstock(item), _batch_total(item)) == before
    assert db.session.get(type(pur), pur.id).quantity == 2
