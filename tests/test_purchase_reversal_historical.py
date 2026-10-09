"""Reversing a purchase that was posted BEFORE batch tracking was enabled.

Such a line has no PurchaseItemBatch allocation (enable_batch_tracking() never rewrites old documents) and
its units were folded into the item's Unknown Batch. reverse_document() used to refuse it outright with
"batch allocations total 0 ..."; it now takes the units out of the Unknown Batch through
item_remove_stock_batched(), which lowers Item.stock / ItemStock once and the Unknown Batch once. If the
Unknown Batch cannot cover it, the PostingError rolls the whole request back (journal reversal included).
A genuine allocation, and a PARTIAL allocation, behave exactly as before.

Fixtures are shared with tests/test_purchase_correction_batch_consumed.py.
"""
from app import db, Item, Purchase, JournalEntry, StockMovement, Batch, PurchaseItem, PurchaseItemBatch
from salpurflask.models.models import JournalLine
from salpurflask.models.inventory_location import BatchStock, ItemStock, get_or_create_default_location

from tests.test_purchase_correction_batch_consumed import (
    _books, _item, _supplier, _manager, _admin, _purchase_via_form, _latest_purchase, _post,
)
from tests.test_purchase_correction_pretracking_batch import _enable_tracking


def _reverse(admin, pur):
    return admin.post(f"/document/purchase/{pur.id}/reverse", follow_redirects=True)


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


def _state(item_id, pur_id):
    """Everything a partial change could touch."""
    db.session.expire_all()
    item = db.session.get(Item, item_id)
    return {
        "stock": item.stock, "value": str(item.inventory_value), "itemstock": _itemstock(item),
        "batch_total": _batch_total(item), "unknown": _unknown_qty(item),
        "is_reversed": db.session.get(Purchase, pur_id).is_reversed,
        "entries": JournalEntry.query.count(), "lines": JournalLine.query.count(),
        "movements": StockMovement.query.count(),
    }


def _historical(qty=2):
    """A purchase posted while the item was NOT batch-tracked, tracking enabled afterwards."""
    _books()
    sup, item = _supplier(), _item(batch_tracked=False)
    manager, admin = _manager(), _admin()
    _purchase_via_form(manager, sup, item, qty, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _enable_tracking(item)
    assert PurchaseItemBatch.query.filter_by(
        purchase_item_id=PurchaseItem.query.filter_by(purchase_id=pur.id).first().id).count() == 0
    return sup, item, manager, admin, pur


def test_historical_purchase_reversal_succeeds_and_keeps_every_quantity_consistent(appctx):
    sup, item, manager, admin, pur = _historical(qty=2)
    assert _unknown_qty(item) == 2
    unknown = Batch.query.filter_by(item_id=item.id, batch_no=None).one()

    resp = _reverse(admin, pur)
    assert b"refusing to reverse" not in resp.data

    after = _state(item.id, pur.id)
    assert after["is_reversed"] is True
    assert after["stock"] == after["itemstock"] == after["batch_total"] == after["unknown"] == 0
    assert after["value"] in ("0", "0.0000")
    out = StockMovement.query.filter_by(item_id=item.id, source_type="purchase", source_id=pur.id,
                                        direction="out").one()
    assert out.quantity == -2 and out.batch_id == unknown.id      # one removal, against the Unknown Batch


def test_historical_purchase_reversal_accounting_is_the_normal_mirror_entry(appctx):
    sup, item, manager, admin, pur = _historical(qty=2)
    before_entries = JournalEntry.query.filter_by(source_type="purchase", source_id=pur.id).count()
    assert before_entries == 1

    _reverse(admin, pur)

    entries = JournalEntry.query.filter_by(source_type="purchase", source_id=pur.id).all()
    assert len(entries) == 2                                      # original (flagged) + one mirror
    original = next(e for e in entries if e.reversal_of_id is None)
    mirror = next(e for e in entries if e.reversal_of_id == original.id)
    assert original.is_reversed is True and mirror.is_reversed is False
    dr = {l.account.code: (l.debit, l.credit) for l in JournalLine.query.filter_by(entry_id=original.id)}
    mr = {l.account.code: (l.debit, l.credit) for l in JournalLine.query.filter_by(entry_id=mirror.id)}
    assert mr == {code: (c, d) for code, (d, c) in dr.items()}    # exact mirror, debit <-> credit
    assert sum(l.debit - l.credit for l in JournalLine.query.filter(
        JournalLine.entry_id.in_([original.id, mirror.id]))) == 0


def test_other_stock_in_the_unknown_batch_is_left_alone(appctx):
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

    _reverse(admin, second)

    after = _state(item.id, second.id)
    assert after["is_reversed"] is True
    assert after["stock"] == after["itemstock"] == after["batch_total"] == after["unknown"] == 5


def test_insufficient_unknown_batch_rolls_everything_back(appctx):
    sup, item, manager, admin, pur = _historical(qty=2)
    _purchase_via_form(manager, sup, item, 3, 200, batch_no="B-X", expiry_date="2027-06-30")
    _post(manager, _latest_purchase().id)
    unknown_stock = (BatchStock.query.join(Batch, Batch.id == BatchStock.batch_id)
                     .filter(Batch.item_id == item.id, Batch.batch_no.is_(None)).one())
    unknown_stock.quantity = 1                    # arrange: fewer in the Unknown Batch than the line's 2
    db.session.commit()
    before = _state(item.id, pur.id)

    resp = _reverse(admin, pur)

    assert b"has only 1 available" in resp.data
    assert _state(item.id, pur.id) == before      # journal, stock, warehouse, batches, status: nothing moved
    assert JournalEntry.query.filter_by(source_type="purchase", source_id=pur.id,
                                        reversal_of_id=None, is_reversed=False).count() == 1


def test_genuine_batch_allocation_reversal_is_unchanged(appctx):
    _books()
    sup, item = _supplier(), _item(batch_tracked=True)
    manager, admin = _manager(), _admin()
    _purchase_via_form(manager, sup, item, 4, 200, batch_no="B-1", expiry_date="2027-06-30")
    pur = _latest_purchase()
    _post(manager, pur.id)
    assert _batch_total(item) == 4

    _reverse(admin, pur)

    after = _state(item.id, pur.id)
    assert after["is_reversed"] is True
    assert after["stock"] == after["itemstock"] == after["batch_total"] == 0
    out = StockMovement.query.filter_by(item_id=item.id, source_type="purchase", source_id=pur.id,
                                        direction="out").one()
    assert out.batch_id == Batch.query.filter_by(item_id=item.id, batch_no="B-1").one().id


def test_partial_allocation_is_still_refused_and_rolled_back(appctx):
    _books()
    sup, item = _supplier(), _item(batch_tracked=True)
    manager, admin = _manager(), _admin()
    _purchase_via_form(manager, sup, item, 4, 200, batch_no="B-1", expiry_date="2027-06-30")
    pur = _latest_purchase()
    _post(manager, pur.id)
    alloc = PurchaseItemBatch.query.join(PurchaseItem, PurchaseItem.id == PurchaseItemBatch.purchase_item_id) \
        .filter(PurchaseItem.purchase_id == pur.id).one()
    alloc.quantity = 3                            # arrange: allocation (3) no longer matches the line (4)
    db.session.commit()
    before = _state(item.id, pur.id)

    resp = _reverse(admin, pur)

    assert b"allocations total 3" in resp.data
    assert _state(item.id, pur.id) == before
