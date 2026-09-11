"""Batch/Lot + Expiry tracking — Phase A (schema foundation only).

Nothing in this file exercises purchase posting, POS/FEFO, returns, transfers,
stock adjustment, correction/reversal, reports, or any medical-store UI — all
of those are later phases per the approved architecture. This file proves only
the foundation itself: get_or_create_batch()/get_or_create_unknown_batch()'s
locking/idempotency/reject-on-cost-mismatch behavior, item_add_stock_batched()/
item_remove_stock_batched()'s BatchStock<->ItemStock synchronization and their
use of the EXISTING item_add_stock()/item_remove_stock() choke points, and —
the single most important compatibility guarantee — that a plain,
non-batch-tracked item's item_add_stock()/item_remove_stock() calls behave
exactly as they did before this phase existed.
"""
from decimal import Decimal

import pytest

from app import app as flask_app, db, Item, Category, PostingError, User, pwd_context
from salpurflask.models.inventory_location import (
    Branch, Location, ItemStock, BatchStock, StockMovement,
    get_or_create_default_location,
)
from salpurflask.models.models import (
    Batch, item_add_stock, item_remove_stock,
    item_add_stock_batched, item_remove_stock_batched,
    get_or_create_batch, get_or_create_unknown_batch,
)


def _item(stock=0, value=0, batch_tracked=False):
    cat = Category.query.first()
    if cat is None:
        cat = Category(name="Cat")
        db.session.add(cat)
        db.session.flush()
    it = Item(name=f"Item-{Item.query.count()}", category_id=cat.id, stock=stock,
             item_type="STOCK", inventory_value=Decimal(str(value)),
             batch_tracked=batch_tracked)
    db.session.add(it)
    db.session.commit()
    return it


def _second_location(name="Second WH"):
    default = get_or_create_default_location()
    branch = Branch.query.filter_by(is_default=True).first()
    loc = Location(name=name, kind="warehouse", branch_id=branch.id)
    db.session.add(loc)
    db.session.commit()
    return loc


# ══════════════════════════════════════════════════════════════════════════
# 1. Batch creation
# ══════════════════════════════════════════════════════════════════════════


def test_get_or_create_batch_creates_a_new_batch(appctx):
    it = _item(batch_tracked=True)
    batch = get_or_create_batch(it.id, "B001", None, Decimal("45.00"),
                                source_type="purchase", source_id=1)
    db.session.commit()
    assert batch.id is not None
    assert batch.item_id == it.id
    assert batch.batch_no == "B001"
    assert batch.unit_cost == Decimal("45.0000")
    assert batch.source_type == "purchase"
    assert batch.source_id == 1
    assert Batch.query.count() == 1


def test_get_or_create_batch_strips_and_treats_blank_as_none(appctx):
    it = _item(batch_tracked=True)
    batch = get_or_create_batch(it.id, "   ", None, Decimal("10"))
    db.session.commit()
    assert batch.batch_no is None  # routed to the Unknown Batch, not a blank-string batch


# ══════════════════════════════════════════════════════════════════════════
# 2. Unknown Batch idempotency
# ══════════════════════════════════════════════════════════════════════════


def test_get_or_create_unknown_batch_is_idempotent(appctx):
    it = _item(batch_tracked=True)
    b1 = get_or_create_unknown_batch(it.id)
    db.session.commit()
    b2 = get_or_create_unknown_batch(it.id)
    db.session.commit()
    assert b1.id == b2.id
    assert Batch.query.filter_by(item_id=it.id, batch_no=None).count() == 1


def test_get_or_create_batch_with_none_batch_no_routes_to_unknown_batch(appctx):
    it = _item(batch_tracked=True)
    b1 = get_or_create_unknown_batch(it.id)
    db.session.commit()
    b2 = get_or_create_batch(it.id, None, None, Decimal("5"))
    db.session.commit()
    assert b1.id == b2.id


def test_unknown_batch_is_per_item_not_shared(appctx):
    it1 = _item(batch_tracked=True)
    it2 = _item(batch_tracked=True)
    b1 = get_or_create_unknown_batch(it1.id)
    b2 = get_or_create_unknown_batch(it2.id)
    db.session.commit()
    assert b1.id != b2.id
    assert b1.item_id == it1.id
    assert b2.item_id == it2.id


# ══════════════════════════════════════════════════════════════════════════
# 3. Same batch + same cost reuses Batch
# ══════════════════════════════════════════════════════════════════════════


def test_same_batch_no_same_cost_reuses_existing_batch(appctx):
    it = _item(batch_tracked=True)
    b1 = get_or_create_batch(it.id, "B001", None, Decimal("45.00"))
    db.session.commit()
    b2 = get_or_create_batch(it.id, "B001", None, Decimal("45.00"))
    db.session.commit()
    assert b1.id == b2.id
    assert Batch.query.filter_by(item_id=it.id, batch_no="B001").count() == 1


# ══════════════════════════════════════════════════════════════════════════
# 4. Same batch + different cost is rejected
# ══════════════════════════════════════════════════════════════════════════


def test_same_batch_no_different_cost_is_rejected(appctx):
    it = _item(batch_tracked=True)
    get_or_create_batch(it.id, "B001", None, Decimal("45.00"))
    db.session.commit()
    with pytest.raises(PostingError, match="already exists at cost"):
        get_or_create_batch(it.id, "B001", None, Decimal("48.00"))
    db.session.rollback()
    # The original batch's cost must be untouched -- no silent overwrite/blend.
    batch = Batch.query.filter_by(item_id=it.id, batch_no="B001").first()
    assert batch.unit_cost == Decimal("45.0000")
    assert Batch.query.filter_by(item_id=it.id, batch_no="B001").count() == 1


# ══════════════════════════════════════════════════════════════════════════
# 5. BatchStock creation/update
# ══════════════════════════════════════════════════════════════════════════


def test_batch_stock_created_on_first_add(appctx):
    it = _item(batch_tracked=True)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch)
    db.session.commit()
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).first()
    assert bstock is not None
    assert bstock.quantity == 20


def test_batch_stock_updated_on_second_add_same_batch(appctx):
    it = _item(batch_tracked=True)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch)
    db.session.commit()
    item_add_stock_batched(it, 5, Decimal("50"), loc.id, batch)
    db.session.commit()
    bstock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).first()
    assert bstock.quantity == 25
    assert BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).count() == 1


# ══════════════════════════════════════════════════════════════════════════
# 6. StockMovement receives batch_id
# ══════════════════════════════════════════════════════════════════════════


def test_stock_movement_receives_batch_id_on_add(appctx):
    it = _item(batch_tracked=True)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch,
                           movement_type="purchase", source_type="purchase", source_id=99)
    db.session.commit()
    movement = StockMovement.query.filter_by(source_type="purchase", source_id=99).first()
    assert movement is not None
    assert movement.batch_id == batch.id


def test_stock_movement_receives_batch_id_on_remove(appctx):
    it = _item(batch_tracked=True)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch)
    db.session.commit()
    item_remove_stock_batched(it, 5, loc.id, batch,
                              movement_type="sale", source_type="sale", source_id=7)
    db.session.commit()
    movement = StockMovement.query.filter_by(source_type="sale", source_id=7).first()
    assert movement is not None
    assert movement.batch_id == batch.id


# ══════════════════════════════════════════════════════════════════════════
# 7. No duplicate StockMovement is created
# ══════════════════════════════════════════════════════════════════════════


def test_no_duplicate_stock_movement_created_by_batched_add(appctx):
    it = _item(batch_tracked=True)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    before = StockMovement.query.count()
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch,
                           movement_type="purchase", source_type="purchase", source_id=123)
    db.session.commit()
    after = StockMovement.query.count()
    assert after - before == 1  # exactly one row, not two


def test_no_duplicate_stock_movement_created_by_batched_remove(appctx):
    it = _item(batch_tracked=True)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch)
    db.session.commit()
    before = StockMovement.query.count()
    item_remove_stock_batched(it, 5, loc.id, batch,
                              movement_type="sale", source_type="sale", source_id=321)
    db.session.commit()
    after = StockMovement.query.count()
    assert after - before == 1


# ══════════════════════════════════════════════════════════════════════════
# 8/9. item_add_stock_batched()/item_remove_stock_batched() keep ItemStock
# and BatchStock synchronized
# ══════════════════════════════════════════════════════════════════════════


def test_batched_add_keeps_itemstock_and_batchstock_synchronized(appctx):
    it = _item(batch_tracked=True)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch)
    db.session.commit()
    db.session.refresh(it)
    item_stock = ItemStock.query.filter_by(item_id=it.id, location_id=loc.id).first()
    batch_stock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).first()
    assert it.stock == 20
    assert item_stock.quantity == 20
    assert batch_stock.quantity == 20
    assert item_stock.quantity == batch_stock.quantity  # the core invariant


def test_batched_remove_keeps_itemstock_and_batchstock_synchronized(appctx):
    it = _item(batch_tracked=True)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch)
    db.session.commit()
    item_remove_stock_batched(it, 8, loc.id, batch)
    db.session.commit()
    db.session.refresh(it)
    item_stock = ItemStock.query.filter_by(item_id=it.id, location_id=loc.id).first()
    batch_stock = BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).first()
    assert it.stock == 12
    assert item_stock.quantity == 12
    assert batch_stock.quantity == 12


def test_batched_remove_refuses_more_than_batch_has_even_if_item_has_more(appctx):
    """The exact scenario the architecture review called out: a location's
    ItemStock total can be sufficient while ONE batch alone is not."""
    it = _item(batch_tracked=True)
    loc = get_or_create_default_location()
    batch_a = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    batch_b = get_or_create_batch(it.id, "B002", None, Decimal("12"))
    item_add_stock_batched(it, 5, Decimal("50"), loc.id, batch_a)
    db.session.commit()
    item_add_stock_batched(it, 50, Decimal("600"), loc.id, batch_b)
    db.session.commit()
    db.session.refresh(it)
    assert it.stock == 55  # plenty company-wide and at this location
    with pytest.raises(PostingError, match="only 5 available"):
        item_remove_stock_batched(it, 10, loc.id, batch_a)  # batch_a only has 5
    db.session.rollback()


def test_batched_add_at_multiple_locations_keeps_each_location_synced(appctx):
    it = _item(batch_tracked=True)
    loc_a = get_or_create_default_location()
    loc_b = _second_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    item_add_stock_batched(it, 20, Decimal("200"), loc_a.id, batch)
    db.session.commit()
    item_add_stock_batched(it, 15, Decimal("150"), loc_b.id, batch)
    db.session.commit()
    db.session.refresh(it)
    assert it.stock == 35
    assert ItemStock.query.filter_by(item_id=it.id, location_id=loc_a.id).first().quantity == 20
    assert ItemStock.query.filter_by(item_id=it.id, location_id=loc_b.id).first().quantity == 15
    assert BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_a.id).first().quantity == 20
    assert BatchStock.query.filter_by(batch_id=batch.id, location_id=loc_b.id).first().quantity == 15


# ══════════════════════════════════════════════════════════════════════════
# 10/11. Non-batch item_add_stock()/item_remove_stock() behavior unchanged
# ══════════════════════════════════════════════════════════════════════════


def test_plain_item_add_stock_unaffected_by_batch_tracking_existing(appctx):
    """A non-batch-tracked item's item_add_stock() call must behave exactly
    as before Phase A -- same Item.stock/ItemStock effect, no Batch/BatchStock
    row created anywhere, no new query, no new error path."""
    it = _item(batch_tracked=False)
    loc = get_or_create_default_location()
    item_add_stock(it, 10, Decimal("100"), location_id=loc.id,
                   movement_type="purchase", source_type="purchase", source_id=55)
    db.session.commit()
    db.session.refresh(it)
    assert it.stock == 10
    assert ItemStock.query.filter_by(item_id=it.id, location_id=loc.id).first().quantity == 10
    assert Batch.query.filter_by(item_id=it.id).count() == 0
    assert BatchStock.query.count() == 0
    movement = StockMovement.query.filter_by(source_type="purchase", source_id=55).first()
    assert movement is not None
    assert movement.batch_id is None


def test_plain_item_remove_stock_unaffected_by_batch_tracking_existing(appctx):
    it = _item(stock=0, batch_tracked=False)
    loc = get_or_create_default_location()
    item_add_stock(it, 20, Decimal("200"), location_id=loc.id)
    db.session.commit()
    cost = item_remove_stock(it, 8, location_id=loc.id,
                             movement_type="sale", source_type="sale", source_id=77)
    db.session.commit()
    db.session.refresh(it)
    assert it.stock == 12
    assert isinstance(cost, Decimal)
    assert cost == Decimal("80.0000")  # 8 units @ avg cost 10 -- scalar Decimal, unchanged shape
    movement = StockMovement.query.filter_by(source_type="sale", source_id=77).first()
    assert movement.batch_id is None


def test_plain_item_remove_stock_return_value_is_still_a_bare_decimal(appctx):
    """The exact compatibility guarantee this phase depends on: existing
    callers that do `x = item_remove_stock(...)` and use x as a scalar
    (arithmetic, comparison, direct column assignment) must keep working.
    This mirrors test_inventory.py's own test_value_cannot_be_taken_below_zero,
    which does `assert item_remove_stock(item, 5) == Decimal(...)` directly."""
    it = _item(stock=0, batch_tracked=False)
    item_add_stock(it, 10, Decimal("1000"))
    db.session.flush()
    assert item_remove_stock(it, 5) == Decimal("500.0000")


def test_existing_callers_still_pass_no_movement_out_and_get_none_back_from_add(appctx):
    it = _item(batch_tracked=False)
    result = item_add_stock(it, 5, Decimal("50"))
    db.session.commit()
    assert result is None  # unchanged: item_add_stock() never returned anything meaningful


# ══════════════════════════════════════════════════════════════════════════
# 12. Transaction rollback removes newly created Batch/BatchStock changes
# ══════════════════════════════════════════════════════════════════════════


def test_rollback_after_get_or_create_batch_removes_the_new_batch(appctx):
    it = _item(batch_tracked=True)
    get_or_create_batch(it.id, "B001", None, Decimal("10"))
    db.session.rollback()
    assert Batch.query.filter_by(item_id=it.id, batch_no="B001").count() == 0


def test_rollback_after_batched_add_reverts_item_batch_and_location_state(appctx):
    it = _item(batch_tracked=True, stock=0)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    db.session.commit()  # the batch itself is real; only the stock mutation below rolls back
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch)
    db.session.rollback()
    db.session.refresh(it)
    assert it.stock == 0
    assert ItemStock.query.filter_by(item_id=it.id, location_id=loc.id).count() == 0
    assert BatchStock.query.filter_by(batch_id=batch.id, location_id=loc.id).count() == 0


def test_rollback_mid_transaction_never_leaves_partial_batch_state(appctx):
    """Mirrors test_multi_location.py's own
    test_rollback_mid_transaction_never_leaves_partial_stock_state, one layer
    deeper: a batch created AND stocked AND then rolled back leaves nothing --
    not the Batch, not the BatchStock, not the ItemStock."""
    it = _item(batch_tracked=True, stock=0)
    loc = get_or_create_default_location()
    batch = get_or_create_batch(it.id, "B001", None, Decimal("10"))
    item_add_stock_batched(it, 20, Decimal("200"), loc.id, batch)
    # simulate the rest of the caller's transaction failing before commit
    db.session.rollback()
    db.session.refresh(it)
    assert it.stock == 0
    assert Batch.query.filter_by(item_id=it.id, batch_no="B001").count() == 0
    assert BatchStock.query.count() == 0


# ══════════════════════════════════════════════════════════════════════════
# 13. Existing item_remove_stock() callers continue to work exactly as before
# ══════════════════════════════════════════════════════════════════════════


def test_existing_purchase_return_style_caller_still_assigns_scalar_cost(appctx):
    """Mirrors the real shape used at salpurflask/purchase/routes.py and
    app.py: `pr.cost_removed = item_remove_stock(...)` — a direct assignment
    of the return value into a Numeric-shaped attribute. This would break
    immediately if item_remove_stock() ever returned a tuple."""
    it = _item(stock=0, batch_tracked=False)
    item_add_stock(it, 10, Decimal("100"))
    db.session.flush()

    class _FakeReturnRow:
        cost_removed = None

    fake = _FakeReturnRow()
    fake.cost_removed = item_remove_stock(it, 2)
    assert fake.cost_removed == Decimal("20.0000")
    assert isinstance(fake.cost_removed, Decimal)


def test_existing_insufficient_stock_error_unchanged_for_non_batch_item(appctx):
    it = _item(stock=0, batch_tracked=False)
    item_add_stock(it, 10, Decimal("100"))
    db.session.flush()
    with pytest.raises(PostingError, match="Insufficient stock at this location"):
        item_remove_stock(it, 11)


def test_existing_value_guard_unchanged_for_non_batch_item(appctx):
    it = _item(stock=0, batch_tracked=False)
    item_add_stock(it, 10, Decimal("1000"))
    item_remove_stock(it, 5)
    db.session.flush()
    with pytest.raises(PostingError, match="cannot be taken out of it"):
        item_remove_stock(it, 4, cost_total=Decimal("900"))


# ══════════════════════════════════════════════════════════════════════════
# 14. Concurrency guard: named-batch and Unknown-Batch creation race
#
# SQLite serializes all writes at the file level, so it is structurally
# incapable of demonstrating two PostgreSQL transactions genuinely racing —
# any test that tried to simulate that with two sequential calls in one
# session would not be testing concurrency at all, it would just be testing
# sequential idempotency a second time under a misleading name. Per this
# project's own established precedent for exactly this situation (see
# test_sale_correction.py::test_concurrent_correction_is_protected_by_row_locking,
# written for Phase 6A) the honest test is a static check that the actual
# concurrency guard — locking the parent Item row via get_item_locked(),
# BEFORE the Batch lookup — is really in the source, not simulated.
#
# LIMITATION, stated explicitly: this does not exercise real PostgreSQL
# concurrent-transaction behavior. Verifying the fix under genuine
# concurrent load requires either a live PostgreSQL instance with two real
# connections/transactions, or a database-specific integration test outside
# this project's current SQLite-only test harness — neither is available
# here. The tests below prove the code takes the lock in the right place
# and in the right order, and that everything the lock is meant to protect
# (reuse, cost-mismatch rejection, Unknown Batch singularity) still holds.
# ══════════════════════════════════════════════════════════════════════════


def test_get_or_create_batch_locks_the_item_row_before_the_batch_lookup(appctx):
    """Static source check, not a simulated race (see the section docstring
    above for why). Confirms get_item_locked() -- the same choke point every
    purchase/sale/adjustment route already uses to serialize concurrent stock
    changes -- is called, and that it happens textually before the Batch
    query, matching the documented "lock Item first" contract."""
    import inspect
    from salpurflask.models.models import get_or_create_batch
    src = inspect.getsource(get_or_create_batch)
    assert "get_item_locked(item_id)" in src
    item_lock_pos = src.index("get_item_locked(item_id)")
    batch_query_pos = src.index("Batch.query")
    assert item_lock_pos < batch_query_pos, (
        "get_or_create_batch() must lock the Item row before querying Batch, "
        "or the fix for the first-time-creation race does not actually apply.")


def test_get_or_create_unknown_batch_locks_the_item_row_before_the_batch_lookup(appctx):
    import inspect
    from salpurflask.models.models import get_or_create_unknown_batch
    src = inspect.getsource(get_or_create_unknown_batch)
    assert "get_item_locked(item_id)" in src
    item_lock_pos = src.index("get_item_locked(item_id)")
    batch_query_pos = src.index("Batch.query")
    assert item_lock_pos < batch_query_pos


def test_get_or_create_batch_raises_for_a_nonexistent_item(appctx):
    """The new Item lock means a bad item_id is now caught at the very start
    of the function (get_item_locked() returns None), not deep inside a
    Batch query that would have quietly matched zero rows either way."""
    with pytest.raises(PostingError, match="does not exist"):
        get_or_create_batch(999999, "B001", None, Decimal("10"))


def test_get_or_create_unknown_batch_raises_for_a_nonexistent_item(appctx):
    with pytest.raises(PostingError, match="does not exist"):
        get_or_create_unknown_batch(999999)


def test_locking_change_preserves_same_cost_reuse(appctx):
    """Re-proves item 3 from the original Phase A test list still holds after
    the Item-lock fix — same batch, same cost, sequential calls, single row."""
    it = _item(batch_tracked=True)
    b1 = get_or_create_batch(it.id, "B001", None, Decimal("45.00"))
    db.session.commit()
    b2 = get_or_create_batch(it.id, "B001", None, Decimal("45.00"))
    db.session.commit()
    assert b1.id == b2.id
    assert Batch.query.filter_by(item_id=it.id, batch_no="B001").count() == 1


def test_locking_change_preserves_different_cost_rejection(appctx):
    """Re-proves item 4 from the original Phase A test list still holds."""
    it = _item(batch_tracked=True)
    get_or_create_batch(it.id, "B001", None, Decimal("45.00"))
    db.session.commit()
    with pytest.raises(PostingError, match="already exists at cost"):
        get_or_create_batch(it.id, "B001", None, Decimal("48.00"))
    db.session.rollback()
    batch = Batch.query.filter_by(item_id=it.id, batch_no="B001").first()
    assert batch.unit_cost == Decimal("45.0000")


def test_locking_change_preserves_unknown_batch_singularity(appctx):
    """Re-proves the Unknown Batch stays one row per item after the fix --
    the actual invariant the concurrency bug threatened."""
    it = _item(batch_tracked=True)
    b1 = get_or_create_unknown_batch(it.id)
    db.session.commit()
    b2 = get_or_create_unknown_batch(it.id)
    db.session.commit()
    b3 = get_or_create_batch(it.id, None, None, Decimal("5"))
    db.session.commit()
    assert b1.id == b2.id == b3.id
    assert Batch.query.filter_by(item_id=it.id, batch_no=None).count() == 1


def test_get_or_create_batch_delegating_to_unknown_batch_does_not_deadlock(appctx):
    """get_or_create_batch(batch_no=None) locks the Item row itself, then
    calls get_or_create_unknown_batch(), which locks the SAME Item row again
    in the same transaction. This must succeed (PostgreSQL row locks are
    held per transaction, re-acquiring the same lock in the same transaction
    is a no-op, not a wait-for-self deadlock) -- this test just proves the
    call completes normally under SQLite (where the lock is a no-op anyway)
    as a smoke check that the code path itself is well-formed; the real
    same-transaction-reentrancy guarantee is a PostgreSQL locking property
    documented in get_or_create_batch()'s own docstring, not something
    SQLite can independently verify."""
    it = _item(batch_tracked=True)
    batch = get_or_create_batch(it.id, None, None, Decimal("0"))
    db.session.commit()
    assert batch.batch_no is None
    assert batch.item_id == it.id
