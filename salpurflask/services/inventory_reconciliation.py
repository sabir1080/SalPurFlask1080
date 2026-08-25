"""Physical count vs. system stock reconciliation — Phase 6.

Every rule lives here, not in the route — the same discipline
services/transfers.py already holds itself to, for the same reason: a route
that skips validation on a crafted POST reaches this module either way.

A reconciliation moves quantity, not value, exactly like a Transfer — it
never calls post_document()/post_entry()/post_stock_adjustment() and never
imports PostingError's GL-flavoured siblings (posted_entry, assert_not_posted).
This is a deliberate Phase 6 scope boundary, not an oversight: the resulting
Item/ItemStock quantity and inventory_value changes are an intrinsic,
unavoidable consequence of calling item_add_stock()/item_remove_stock() (see
their own docstrings — cost tracking is not optional), but no matching
JournalEntry is ever created for a reconciliation variance in this phase.

The only door into ItemStock is item_add_stock()/item_remove_stock() in
models.py — this module never touches ItemStock.quantity directly, so the
Item.stock == SUM(ItemStock.quantity) invariant is inherited for free, the
same way Transfer already inherits it.

status is the idempotency guard, not a separate flag or constraint — the
same pattern confirm_transfer() already uses: post_reconciliation() raises
PostingError unless status == "Approved", so a second POST to the same
posting route is refused, not silently re-applied.
"""

from datetime import datetime

from salpurflask.extensions import db
from salpurflask.models.inventory_location import (
    Location, InventoryReconciliation, InventoryReconciliationLine,
    stock_at_location,
)


def create_reconciliation(*, location_id, item_ids, date=None, notes=None,
                          created_by_id=None):
    """Build a Draft reconciliation with one line per item, no counts yet.

    `item_ids` may be a manually chosen subset (a partial count) or every
    active stock item at the location (a full count) — the route decides
    which; this function treats both identically, one line per id, no
    system_quantity snapshot until finalize_count() (see that function's
    docstring for why the snapshot waits).

    Raises PostingError and writes nothing on bad input, so a half-built
    reconciliation never lands in the database — the same all-or-nothing
    shape create_transfer() already uses."""
    from app import PostingError

    location = db.session.get(Location, location_id)
    if location is None:
        raise PostingError("Warehouse does not exist.")

    if not item_ids:
        raise PostingError("A reconciliation needs at least one item.")

    from salpurflask.models.models import Item

    clean_items = []
    seen = set()
    for item_id in item_ids:
        if item_id in seen:
            continue
        seen.add(item_id)
        item = db.session.get(Item, item_id)
        if item is None:
            raise PostingError(f"Item #{item_id} does not exist.")
        clean_items.append(item)

    reconciliation = InventoryReconciliation(
        location_id=location.id,
        status="Draft",
        date=date or datetime.utcnow(),
        notes=notes or None,
        created_by_id=created_by_id,
    )
    db.session.add(reconciliation)
    db.session.flush()

    for item in clean_items:
        db.session.add(InventoryReconciliationLine(
            reconciliation_id=reconciliation.id, item_id=item.id))
    db.session.flush()
    return reconciliation


def save_counts(reconciliation, counts, *, notes_by_item=None):
    """Save physical counts on a Draft reconciliation's existing lines.
    Repeatable — counting is just editing these lines, no separate
    "Counting" state, matching the approved proposal's collapse of that
    state (nothing distinguishes "still counting" from "Draft with some
    lines filled in").

    `counts` is {item_id: physical_quantity}; unmentioned lines are left
    untouched, so a partial save (count half the warehouse today, the rest
    tomorrow) is a normal use of this function, not a special case.

    Does NOT commit."""
    from app import PostingError

    if reconciliation.status != "Draft":
        raise PostingError(
            f"Only a Draft reconciliation accepts counts (this one is {reconciliation.status}).")

    lines_by_item = {line.item_id: line for line in reconciliation.lines}
    notes_by_item = notes_by_item or {}
    for item_id, qty in counts.items():
        line = lines_by_item.get(item_id)
        if line is None:
            raise PostingError(f"Item #{item_id} is not on this reconciliation.")
        if qty is None:
            continue
        if qty < 0:
            raise PostingError(f"{line.item.name}: physical count cannot be negative.")
        line.physical_quantity = qty
        if item_id in notes_by_item:
            line.notes = notes_by_item[item_id] or None
    return reconciliation


def finalize_count(reconciliation, *, counted_by_id=None):
    """Draft -> Counted. Snapshots system_quantity on every line via
    stock_at_location() at this exact moment — not at create time (stock
    legitimately moves while counters are still walking the warehouse) and
    not deferred to post time (that would make the variance ambiguous about
    what "system stock" even meant). Computes variance the same instant, so
    the two numbers being compared are read at the same moment, never one
    live and one stale.

    Refuses a line with no physical count yet — a reconciliation cannot be
    finalized half-counted, the same "check everything, then act" shape
    confirm_transfer() already uses for its own availability check.

    Does NOT commit."""
    from app import PostingError

    if reconciliation.status != "Draft":
        raise PostingError(
            f"Only a Draft reconciliation can be finalized (this one is {reconciliation.status}).")
    if not reconciliation.lines:
        raise PostingError("A reconciliation needs at least one item.")

    uncounted = [line.item.name for line in reconciliation.lines
                if line.physical_quantity is None]
    if uncounted:
        raise PostingError("Every item needs a physical count before finalizing — "
                           "missing: " + ", ".join(uncounted))

    for line in reconciliation.lines:
        system_qty = stock_at_location(line.item_id, reconciliation.location_id)
        line.system_quantity = system_qty
        line.variance = line.physical_quantity - system_qty

    reconciliation.status = "Counted"
    reconciliation.counted_at = datetime.utcnow()
    reconciliation.counted_by_id = counted_by_id
    return reconciliation


def reopen_count(reconciliation):
    """Counted -> Draft, for a recount. Clears the stale snapshot/variance
    on every line so a subsequent finalize_count() re-snapshots cleanly
    rather than trusting numbers read at the old moment.

    Does NOT commit."""
    from app import PostingError

    if reconciliation.status != "Counted":
        raise PostingError(
            f"Only a Counted reconciliation can be reopened (this one is {reconciliation.status}).")

    for line in reconciliation.lines:
        line.system_quantity = None
        line.variance = None

    reconciliation.status = "Draft"
    reconciliation.counted_at = None
    reconciliation.counted_by_id = None
    return reconciliation


def approve_reconciliation(reconciliation, *, approved_by_id=None):
    """Counted -> Approved. Segregation of duties: the counter and the
    approver must be different people — this is Phase 6's one control, and
    it is enforced here, server-side, not just hidden in the UI. Comparing
    counted_by_id (a fact already on the row) rather than trusting anything
    the request claims about who counted it.

    Does NOT commit."""
    from app import PostingError

    if reconciliation.status != "Counted":
        raise PostingError(
            f"Only a Counted reconciliation can be approved (this one is {reconciliation.status}).")
    if reconciliation.counted_by_id is not None and reconciliation.counted_by_id == approved_by_id:
        raise PostingError(
            "The person who counted this reconciliation cannot also approve it. "
            "Ask another admin to review it.")

    reconciliation.status = "Approved"
    reconciliation.approved_at = datetime.utcnow()
    reconciliation.approved_by_id = approved_by_id
    return reconciliation


def post_reconciliation(reconciliation, *, posted_by_id=None):
    """Approved -> Posted. Applies every line's variance through
    item_add_stock()/item_remove_stock() — the only door into ItemStock —
    and nothing else. No JournalEntry, no post_document() call: a
    reconciliation moves quantity, never value in the GL sense, the same
    boundary Transfer already draws (see this module's own docstring).

    Re-verifies every line's snapshot against current stock BEFORE moving
    anything: if a sale, adjustment or transfer touched this item at this
    location since finalize_count() ran, the approved variance no longer
    describes reality, and posting must refuse rather than silently apply a
    now-stale number — the same "check everything, then act" shape
    confirm_transfer() already uses for its own pre-move validation.

    Assigns reference (RCN-...) only now — a Draft/Counted/Approved
    reconciliation that never gets posted never consumes a document number,
    the same rule Transfer.transfer_no already follows.

    Does NOT commit. The caller commits once, so the status change and
    every line's stock movement land in one transaction or none of them do."""
    from app import PostingError

    if reconciliation.status != "Approved":
        raise PostingError(
            f"Only an Approved reconciliation can be posted (this one is {reconciliation.status}).")

    stale = []
    for line in reconciliation.lines:
        current = stock_at_location(line.item_id, reconciliation.location_id)
        if current != line.system_quantity:
            stale.append(
                f"{line.item.name}: system stock changed since counting "
                f"({line.system_quantity} -> {current}) — reopen and recount.")
    if stale:
        raise PostingError("Cannot post — " + "; ".join(stale))

    from salpurflask.models.models import item_add_stock, item_remove_stock, allocate_document_number

    for line in reconciliation.lines:
        if not line.variance:
            continue
        item = line.item
        if line.variance > 0:
            cost_total = line.variance * item.avg_cost
            item_add_stock(item, line.variance, cost_total,
                           location_id=reconciliation.location_id,
                           movement_type="adjustment",
                           source_type="inventory_reconciliation", source_id=reconciliation.id)
        else:
            item_remove_stock(item, abs(line.variance),
                              location_id=reconciliation.location_id,
                              movement_type="adjustment",
                              source_type="inventory_reconciliation", source_id=reconciliation.id)

    if not reconciliation.reference:
        reconciliation.reference = allocate_document_number(
            "inventory_reconciliation", reconciliation.date)
    reconciliation.status = "Posted"
    reconciliation.posted_at = datetime.utcnow()
    reconciliation.posted_by_id = posted_by_id
    return reconciliation


def cancel_reconciliation(reconciliation):
    """Abandon a Draft or Counted reconciliation before any stock moved.
    Never allowed once Posted — stock has already moved, and Phase 6
    deliberately does not build a reversal path for that (see the approved
    proposal's scope boundary), the same way cancel_transfer() refuses a
    Confirmed transfer and points at reverse_transfer() instead — except
    Phase 6 has no reverse_reconciliation() at all, by design.

    Does NOT commit."""
    from app import PostingError

    if reconciliation.status not in ("Draft", "Counted"):
        raise PostingError(
            f"Only a Draft or Counted reconciliation can be cancelled "
            f"(this one is {reconciliation.status}).")
    reconciliation.status = "Cancelled"
    return reconciliation
