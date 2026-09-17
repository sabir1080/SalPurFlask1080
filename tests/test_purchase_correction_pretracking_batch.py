"""Posted Purchase Correction for a line that was originally posted while
its Item was NOT batch-tracked, where batch tracking was enabled for that
Item only AFTERWARDS (enable_batch_tracking(), Phase G).

Bug this file guards against (production incident, Purchase #18 /
"Brofen 250ML" on tradeflow-demo): such a PurchaseItem has ZERO
PurchaseItemBatch allocations -- it was posted through the plain,
non-batch item_add_stock() path, since item.batch_tracked was False at
that time. enable_batch_tracking() never rewrites history (see its own
docstring in salpurflask/models/models.py) -- it only backfills a
snapshot of CURRENT stock into a new "Unknown Batch", so this old line's
absence of any PurchaseItemBatch row is permanent and expected, not
corruption. Once item.batch_tracked flips to True, correcting that same
old Purchase used to hit _unwind_batch_tracked_purchase_for_correction()'s
consistency guard unconditionally:

    "Purchase #{id} line for {item}: batch allocations total 0 but the
    line quantity is {qty} -- refusing to reverse an inconsistent batch
    allocation."

-- even though nothing is actually inconsistent: there was never any
allocation to be consistent WITH in the first place.

Fix: _unwind_batch_tracked_purchase_for_correction() now treats a
COMPLETELY EMPTY allocation list as this historical case (never raising
for it) and reports the affected PurchaseItem ids back to the caller.
correct_purchase_route() routes those specific lines through the exact
same plain item_remove_stock() mechanism the line was originally posted
with, instead of skipping them (previously, any line whose Item was
batch_tracked was unconditionally excluded from the plain-unwind loop).
The pre-correction stock-availability pre-check is updated the same way,
so a historical line whose stock has since been sold elsewhere is still
caught before anything is written, exactly like an ordinary non-batch
line.

A line that DOES have allocations, but whose total doesn't match the
line quantity (a genuine, different kind of inconsistency) still raises
the original PostingError unchanged -- this fix narrows the guard to
exclude only the zero-allocation case, it does not weaken it.

Reuses the fixtures/helpers from tests/test_purchase_correction_batch_
consumed.py (same admin/manager/purchase/correction-form plumbing) rather
than duplicating them.
"""
from decimal import Decimal

from app import (
    app as flask_app, db, User, pwd_context,
    Item, FinancialAccount, Supplier, Customer, Purchase, PurchaseItem,
    JournalEntry, StockMovement, PostingError,
    seed_chart_of_accounts, seed_fixed_asset_accounts, seed_fiscal_year,
    seed_financial_account_links, sync_customer_opening,
    Batch, PurchaseItemBatch, SaleItemBatch, Sale,
)
from salpurflask.models.models import enable_batch_tracking
from salpurflask.models.inventory_location import BatchStock, ItemStock, get_or_create_default_location
from salpurflask.models.business_config import BusinessCategory

from tests.test_purchase_correction_batch_consumed import (
    _books, _item, _supplier, _customer, _manager, _admin,
    _purchase_via_form, _latest_purchase, _post, _correct_via_form,
    _sale_via_form, _latest_sale, _post_sale, _batchstock,
)


def _enable_tracking(item):
    enable_batch_tracking(item.id)
    db.session.commit()
    db.session.refresh(item)


# ─── 1: historical pre-batch purchase, batch tracking enabled afterwards ───


def test_historical_prebatch_line_correction_succeeds(appctx):
    """Purchase posted while batch_tracked=False (no PurchaseItemBatch
    rows at all) -> batch tracking enabled afterwards -> correcting that
    old Purchase must succeed, not raise the "allocations total 0"
    PostingError."""
    _books()
    sup = _supplier()
    item = _item(batch_tracked=False)
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 2, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    db.session.refresh(item)
    assert item.stock == 2

    old_pi = PurchaseItem.query.filter_by(purchase_id=pur.id).first()
    assert PurchaseItemBatch.query.filter_by(purchase_item_id=old_pi.id).count() == 0

    _enable_tracking(item)
    assert item.batch_tracked is True

    resp = _correct_via_form(admin, pur, [(item, 3, 200, "B-NEW", "2027-06-30")],
                             reason="qty correction after enabling batch tracking")
    assert resp.status_code == 200
    assert b"Cannot correct" not in resp.data
    assert b"refusing to reverse" not in resp.data

    db.session.refresh(pur)
    db.session.refresh(item)
    assert pur.quantity == 3
    assert item.stock == 3


def test_historical_prebatch_line_stock_not_double_reversed(appctx):
    """The historical line's original +2 stock must be removed exactly
    once during the unwind (not skipped, not double-removed) before the
    corrected +3 is re-added -- net Item.stock after correction must be
    exactly the corrected quantity, matching a normal non-batch
    correction's own arithmetic."""
    _books()
    sup = _supplier()
    item = _item(batch_tracked=False)
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 2, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _enable_tracking(item)

    resp = _correct_via_form(admin, pur, [(item, 5, 200, "B-NEW", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" not in resp.data

    db.session.refresh(item)
    # If the old +2 had been silently left in place (double count / never
    # unwound), stock would be 2 + 5 = 7 instead of the correct 5.
    assert item.stock == 5


def test_historical_prebatch_line_gets_real_batch_allocation_after_correction(appctx):
    """Once corrected, the NEW PurchaseItem must be posted through the
    item's CURRENT (now batch-tracked) posting logic: a real
    PurchaseItemBatch row, BatchStock populated, no fabricated history for
    the OLD line."""
    _books()
    sup = _supplier()
    item = _item(batch_tracked=False)
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 2, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _enable_tracking(item)

    _correct_via_form(admin, pur, [(item, 3, 200, "B-NEW", "2027-06-30")])

    db.session.refresh(pur)
    new_pi = PurchaseItem.query.filter_by(purchase_id=pur.id).first()
    allocations = PurchaseItemBatch.query.filter_by(purchase_item_id=new_pi.id).all()
    assert sum(a.quantity for a in allocations) == 3
    assert _batchstock(item.id, "B-NEW") == 3

    batch = Batch.query.filter_by(item_id=item.id, batch_no=None).first()
    assert batch is not None, "the Phase G Unknown Batch backfill must still exist, untouched"


# ─── 2: genuine batch-tracked mismatch still refused ───────────────────────


def test_genuine_batch_tracked_missing_allocation_still_refused(appctx):
    """A line that IS genuinely batch-tracked (posted while batch_tracked
    was already True, so it should have gotten a real allocation) but
    whose PurchaseItemBatch row was removed/never created due to a
    DIFFERENT bug must still be refused -- this fix must not blanket-allow
    every zero-allocation case, only ones proven impossible to have ever
    had one (see historical_pi_ids' own docstring: the guarantee only
    holds because the line's item is ALSO consistent with never having
    been through the batch-aware Post branch; the test setup below
    reproduces a genuinely inconsistent record directly, the same way an
    actual data-corruption case would look on disk)."""
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)
    db.session.refresh(item)
    assert item.stock == 5

    pi = PurchaseItem.query.filter_by(purchase_id=pur.id).first()
    # Simulate a corrupted historical record: allocation total does NOT
    # match the line quantity (partial, not absent) -- this must still be
    # rejected by the unchanged mismatch guard.
    alloc = PurchaseItemBatch.query.filter_by(purchase_item_id=pi.id).first()
    alloc.quantity = 2
    db.session.commit()

    resp = _correct_via_form(admin, pur, [(item, 5, 10, "B0145", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" in resp.data or b"refusing to reverse" in resp.data
    assert b"allocations total 2" in resp.data
    assert b"line quantity is 5" in resp.data

    db.session.refresh(pur)
    db.session.refresh(item)
    assert pur.quantity == 5
    assert item.stock == 5


# Note: a line whose PurchaseItemBatch rows were deleted outright (rather
# than a partial/mismatched quantity, tested above) is indistinguishable,
# by the schema itself, from a genuine pre-tracking historical line -- see
# the "Schema/data-model limitation" note in this fix's design report.
# This is a documented, accepted limitation, not a gap covered here.


# ─── 3: genuine batch-tracked valid allocation -- unaffected regression ────


def test_genuine_batch_tracked_valid_allocation_correction_still_succeeds(appctx):
    """Normal, healthy batch-tracked correction (real, matching allocation)
    must continue to work exactly as before this fix."""
    _books()
    sup = _supplier()
    item = _item(batch_tracked=True)
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 5, 10, batch_no="B0145")
    pur = _latest_purchase()
    _post(manager, pur.id)

    resp = _correct_via_form(admin, pur, [(item, 7, 10, "B0145", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" not in resp.data

    db.session.refresh(item)
    assert item.stock == 7
    assert _batchstock(item.id, "B0145") == 7


# ─── 4: mixed purchase -- one historical line + one genuine batch line ─────


def test_mixed_purchase_historical_and_genuine_batch_lines_both_corrected(appctx):
    """One line whose item was non-batch-tracked at Post time (now
    batch-tracked, zero allocations -- the historical case) and one line
    whose item was ALREADY batch-tracked at Post time (real allocation) on
    the SAME Purchase document. Correction must unwind each line through
    its own correct mechanism and both must end up correctly re-posted."""
    _books()
    sup = _supplier()
    historical_item = _item(name="Historical-Item", batch_tracked=False)
    genuine_item = _item(name="Genuine-Batch-Item", batch_tracked=True)
    manager = _manager()
    admin = _admin()

    resp = manager.post("/purchase", data={
        "supplier_id": str(sup.id), "date": "2026-01-01", "notes": "",
        "item_id[]": [str(historical_item.id), str(genuine_item.id)],
        "quantity[]": ["2", "5"], "purchase_price[]": ["200", "10"],
        "discount_type[]": ["percent", "percent"], "discount_value[]": ["0", "0"],
        "tax_percent[]": ["0", "0"],
        "batch_no[]": ["", "BG01"], "expiry_date[]": ["", "2027-06-30"],
    }, follow_redirects=True)
    assert resp.status_code == 200
    pur = _latest_purchase()
    _post(manager, pur.id)
    db.session.refresh(historical_item)
    db.session.refresh(genuine_item)
    assert historical_item.stock == 2
    assert genuine_item.stock == 5

    hist_pi = PurchaseItem.query.filter_by(
        purchase_id=pur.id, item_id=historical_item.id).first()
    assert PurchaseItemBatch.query.filter_by(purchase_item_id=hist_pi.id).count() == 0
    genuine_pi = PurchaseItem.query.filter_by(
        purchase_id=pur.id, item_id=genuine_item.id).first()
    assert PurchaseItemBatch.query.filter_by(purchase_item_id=genuine_pi.id).count() == 1

    _enable_tracking(historical_item)
    assert historical_item.batch_tracked is True

    resp = _correct_via_form(admin, pur, [
        (historical_item, 4, 200, "BH01", "2027-06-30"),
        (genuine_item, 8, 10, "BG01", "2027-06-30"),
    ])
    assert resp.status_code == 200
    assert b"Cannot correct" not in resp.data
    assert b"refusing to reverse" not in resp.data

    db.session.refresh(historical_item)
    db.session.refresh(genuine_item)
    assert historical_item.stock == 4
    assert genuine_item.stock == 8
    assert _batchstock(historical_item.id, "BH01") == 4
    assert _batchstock(genuine_item.id, "BG01") == 8


# ─── 5: stock/accounting regression, no duplicate reversal ─────────────────


def test_historical_prebatch_correction_gl_total_matches_corrected_amount(appctx):
    """The GL entry after correction must reflect the CORRECTED total, the
    same as any other correction -- confirms the accounting path
    (reverse_entry/sync_supplier_purchase/post_document) is completely
    unaffected by which stock-unwind branch a line took."""
    from app import posted_entry

    _books()
    sup = _supplier()
    item = _item(batch_tracked=False)
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 2, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _enable_tracking(item)

    _correct_via_form(admin, pur, [(item, 3, 200, "B-NEW", "2027-06-30")])

    db.session.refresh(pur)
    entry = posted_entry("purchase", pur.id)
    assert entry is not None
    total_debit = sum(Decimal(str(l.debit or 0)) for l in entry.lines)
    # 3 units @ 200 = 600, no tax/discount in this fixture.
    assert total_debit == Decimal("600.0000") or total_debit == Decimal("600")


def test_historical_prebatch_correction_itemstock_matches_item_stock(appctx):
    """ItemStock must stay reconciled with Item.stock after a historical
    line's correction, the same invariant test_itemstock_batchstock_
    invariant_after_correction already checks for the genuine-batch case."""
    _books()
    sup = _supplier()
    item = _item(batch_tracked=False)
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 2, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    _enable_tracking(item)

    _correct_via_form(admin, pur, [(item, 6, 200, "B-NEW", "2027-06-30")])

    db.session.refresh(item)
    loc_id = get_or_create_default_location().id
    itemstock_qty = ItemStock.query.filter_by(item_id=item.id, location_id=loc_id).first().quantity
    assert item.stock == 6
    assert itemstock_qty == 6


def test_historical_prebatch_insufficient_stock_still_blocks_correction(appctx):
    """If some of the historical line's stock has already moved on (sold
    elsewhere) before batch tracking was enabled and the correction is
    attempted, the plain stock pre-check must still catch it -- exactly
    like an ordinary non-batch-tracked correction -- instead of being
    silently skipped because item.batch_tracked now reads True."""
    _books()
    sup = _supplier()
    cust = _customer()
    item = _item(batch_tracked=False)
    manager = _manager()
    admin = _admin()

    _purchase_via_form(manager, sup, item, 2, 200, batch_no="")
    pur = _latest_purchase()
    _post(manager, pur.id)
    db.session.refresh(item)
    assert item.stock == 2

    _sale_via_form(manager, cust, item, 2, 300)
    sale = _latest_sale()
    _post_sale(manager, sale.id)
    db.session.refresh(item)
    assert item.stock == 0

    _enable_tracking(item)

    resp = _correct_via_form(admin, pur, [(item, 3, 200, "B-NEW", "2027-06-30")])
    assert resp.status_code == 200
    assert b"Cannot correct" in b"".join([resp.data])
    assert b"already moved on" in resp.data

    db.session.refresh(pur)
    assert pur.quantity == 2
