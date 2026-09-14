"""Warehouse-to-warehouse stock transfers — Phase 3 of multi-warehouse.

Every rule lives here, not in the route: a route that skips validation on a
crafted POST reaches this module either way, the same discipline
payroll_accounting.py and period_pay() already hold themselves to.

A transfer moves quantity, not value — it touches no GL account (see the
architecture plan's accounting section), so this module never calls
post_entry()/reverse_entry() and never imports PostingError's GL-flavoured
siblings (posted_entry, assert_not_posted). It raises the same PostingError
class every other service does for a refused business action, because the
route-level try/except/flash pattern (see payroll/routes.py's period_cancel)
already knows how to turn that into a rollback and a message — reusing it
here means the route needs no new error-handling shape.

The only door into ItemStock is item_add_stock()/item_remove_stock() in
models.py — this module never touches ItemStock.quantity directly, so the
Item.stock == SUM(ItemStock.quantity) invariant is inherited for free,
exactly the same way every other document in this app inherits it.
"""

from datetime import datetime

from salpurflask.extensions import db
from salpurflask.models.inventory_location import (
    Location, Transfer, TransferItem, stock_at_location,
)


def create_transfer(*, source_location_id, destination_location_id, lines,
                    date=None, notes=None, created_by_id=None):
    """Build a Draft transfer. `lines` is [(item_id, quantity), ...] or, for a
    batch-tracked item, [(item_id, quantity, batch_id), ...] — a plain
    2-tuple and a 3-tuple can be mixed in the same call, so existing callers
    passing 2-tuples for non-batch items keep working unchanged.

    No stock moves yet — Draft is a working list, the same role it plays for
    PayrollPeriod. Raises PostingError and writes nothing on any bad input,
    so a half-built transfer never lands in the database.

    Batch/Lot tracking — Phase D. A batch-tracked item's quantity split
    across multiple batches is represented the same way Purchase Phase B
    represents a multi-batch Draft: multiple TransferItem rows for the same
    item_id, each with its own batch_id and quantity — see TransferItem.
    batch_id's own Phase A docstring ("wiring it in is a later phase's
    work"). No new junction table. Validation here is limited to shape
    (batch exists, belongs to the item) — availability is checked, and can
    only be safely checked, under the Item lock inside confirm_transfer(),
    since stock can change between Draft creation and Confirm.
    """
    from app import PostingError

    if source_location_id == destination_location_id:
        raise PostingError("Source and destination warehouse must be different.")

    source = db.session.get(Location, source_location_id)
    if source is None:
        raise PostingError("Source warehouse does not exist.")
    destination = db.session.get(Location, destination_location_id)
    if destination is None:
        raise PostingError("Destination warehouse does not exist.")

    if not lines:
        raise PostingError("A transfer needs at least one item.")

    from salpurflask.models.models import Item, Batch

    clean_lines = []
    for line in lines:
        item_id, quantity = line[0], line[1]
        batch_id = line[2] if len(line) > 2 else None
        item = db.session.get(Item, item_id)
        if item is None:
            raise PostingError(f"Item #{item_id} does not exist.")
        if quantity is None or quantity <= 0:
            raise PostingError(f"{item.name}: quantity must be greater than zero.")
        batch = None
        if batch_id:
            if not item.batch_tracked:
                raise PostingError(
                    f"{item.name} is not batch-tracked; a batch cannot be selected for it.")
            batch = db.session.get(Batch, batch_id)
            if batch is None or batch.item_id != item.id:
                raise PostingError(
                    f"Batch #{batch_id} does not exist or does not belong to {item.name}.")
        clean_lines.append((item, quantity, batch))

    transfer = Transfer(
        source_location_id=source.id,
        destination_location_id=destination.id,
        status="Draft",
        date=date or datetime.utcnow(),
        notes=notes or None,
        created_by_id=created_by_id,
    )
    db.session.add(transfer)
    db.session.flush()

    for item, quantity, batch in clean_lines:
        db.session.add(TransferItem(transfer_id=transfer.id, item_id=item.id,
                                    quantity=quantity,
                                    batch_id=batch.id if batch else None))
    db.session.flush()
    return transfer


def confirm_transfer(transfer, *, confirmed_by_id=None):
    """Move the stock. The single place item_remove_stock()/item_add_stock()
    (or their batch-aware wrappers) are called for a transfer — everything
    above this function only ever builds or reads the Transfer/TransferItem
    rows.

    Validates every line's source availability BEFORE moving anything, so a
    transfer with five lines where the fifth is short never leaves the first
    four partially moved — the same "check everything, then act" shape
    pos_checkout() already uses for its own multi-line validation.

    Batch/Lot tracking — Phase D. Locks every distinct item touched by this
    transfer FIRST (get_item_locked(), sorted by item_id for a deterministic
    order across items — the same defence against two transfers locking two
    shared items in opposite order that pos_checkout()'s single-item-at-a-
    time loop gets for free, but this function must do explicitly since one
    transfer can touch several items at once), each exactly once even if
    that item appears on multiple TransferItem rows (multi-batch lines) —
    re-locking an already-held row is a harmless no-op on PostgreSQL, but
    doing it once keeps the intent explicit. Established order preserved:
    Item lock -> source BatchStock -> destination BatchStock -> ItemStock
    (the last three all happen inside item_remove_stock_batched()/
    item_add_stock_batched(), unmodified from Phase A).

    Does NOT commit. The caller commits once, so the status change and every
    line's stock movement land in one transaction or none of them do.
    """
    from app import PostingError

    if transfer.status != "Draft":
        raise PostingError(
            f"Only a Draft transfer can be confirmed (this one is {transfer.status}).")
    if not transfer.lines:
        raise PostingError("A transfer needs at least one item.")

    from salpurflask.utils.helpers import get_item_locked
    for item_id in sorted({line.item_id for line in transfer.lines}):
        get_item_locked(item_id)

    from salpurflask.models.inventory_location import BatchStock

    shortfalls = []
    for line in transfer.lines:
        if line.batch_id:
            bstock = (BatchStock.query
                      .filter_by(batch_id=line.batch_id, location_id=transfer.source_location_id)
                      .first())
            available = (bstock.quantity or 0) if bstock else 0
            if available < line.quantity:
                batch_no = line.batch.batch_no if line.batch else None
                shortfalls.append(
                    f"{line.item.name} (batch {batch_no or '(unknown)'}): only {available} "
                    f"available at {transfer.source_location.name}, cannot move {line.quantity}")
        else:
            available = stock_at_location(line.item_id, transfer.source_location_id)
            if available < line.quantity:
                shortfalls.append(
                    f"{line.item.name}: only {available} available at "
                    f"{transfer.source_location.name}, cannot move {line.quantity}")
    if shortfalls:
        raise PostingError("Insufficient stock — " + "; ".join(shortfalls))

    from salpurflask.models.models import (
        item_add_stock, item_remove_stock, item_add_stock_batched,
        item_remove_stock_batched, allocate_document_number)

    for line in transfer.lines:
        if line.batch_id:
            cost = item_remove_stock_batched(
                line.item, line.quantity, location_id=transfer.source_location_id,
                batch=line.batch, movement_type="transfer_out", source_type="transfer",
                source_id=transfer.id)
            item_add_stock_batched(
                line.item, line.quantity, cost, location_id=transfer.destination_location_id,
                batch=line.batch, movement_type="transfer_in", source_type="transfer",
                source_id=transfer.id)
        else:
            cost = item_remove_stock(line.item, line.quantity,
                                     location_id=transfer.source_location_id,
                                     movement_type="transfer_out", source_type="transfer",
                                     source_id=transfer.id)
            item_add_stock(line.item, line.quantity, cost,
                           location_id=transfer.destination_location_id,
                           movement_type="transfer_in", source_type="transfer",
                           source_id=transfer.id)

    if not transfer.transfer_no:
        transfer.transfer_no = allocate_document_number("transfer", transfer.date)
    transfer.status = "Confirmed"
    transfer.confirmed_at = datetime.utcnow()
    transfer.confirmed_by_id = confirmed_by_id
    return transfer


def cancel_transfer(transfer):
    """Abandon a Draft before any stock moved. Never allowed on a Confirmed
    transfer — that is what reverse_transfer() is for, because stock has
    already moved and deleting the row would destroy the audit trail of a
    movement that genuinely happened.

    Does NOT commit."""
    from app import PostingError

    if transfer.status != "Draft":
        raise PostingError(
            f"Only a Draft transfer can be cancelled (this one is {transfer.status}). "
            f"A confirmed transfer must be reversed instead.")
    transfer.status = "Cancelled"
    return transfer


def reverse_transfer(transfer, *, reversed_by_id=None):
    """Undo a Confirmed transfer: the exact inverse movement, destination
    back to source. Refuses a transfer that was never confirmed (nothing to
    undo) and a transfer already reversed (no double reversal) — the same
    two guards period_cancel()'s "already cancelled" check and payment
    reversal's is_reversed flag both enforce elsewhere in this app.

    Validates destination availability first (the goods might have moved on
    from there since confirmation — a second transfer, a sale), so a
    reversal that cannot fully complete never partially completes either.

    Batch/Lot tracking — Phase D. Same Item-first locking as
    confirm_transfer() (see its own docstring) — locks every distinct item
    touched, in a deterministic (sorted) order, before any BatchStock/
    ItemStock read or mutation.

    Does NOT commit."""
    from app import PostingError

    if transfer.status != "Confirmed":
        raise PostingError(
            f"Only a Confirmed transfer can be reversed (this one is {transfer.status}).")
    if transfer.is_reversed:
        raise PostingError("This transfer has already been reversed.")

    from salpurflask.utils.helpers import get_item_locked
    for item_id in sorted({line.item_id for line in transfer.lines}):
        get_item_locked(item_id)

    from salpurflask.models.inventory_location import BatchStock

    shortfalls = []
    for line in transfer.lines:
        if line.batch_id:
            bstock = (BatchStock.query
                      .filter_by(batch_id=line.batch_id, location_id=transfer.destination_location_id)
                      .first())
            available = (bstock.quantity or 0) if bstock else 0
            if available < line.quantity:
                batch_no = line.batch.batch_no if line.batch else None
                shortfalls.append(
                    f"{line.item.name} (batch {batch_no or '(unknown)'}): only {available} "
                    f"available at {transfer.destination_location.name} to reverse, needs "
                    f"{line.quantity} (some may have already moved on)")
        else:
            available = stock_at_location(line.item_id, transfer.destination_location_id)
            if available < line.quantity:
                shortfalls.append(
                    f"{line.item.name}: only {available} available at "
                    f"{transfer.destination_location.name} to reverse, needs {line.quantity} "
                    f"(some may have already moved on)")
    if shortfalls:
        raise PostingError("Cannot reverse — " + "; ".join(shortfalls))

    from salpurflask.models.models import (
        item_add_stock, item_remove_stock, item_add_stock_batched, item_remove_stock_batched)

    for line in transfer.lines:
        if line.batch_id:
            cost = item_remove_stock_batched(
                line.item, line.quantity, location_id=transfer.destination_location_id,
                batch=line.batch, movement_type="transfer_out", source_type="transfer",
                source_id=transfer.id)
            item_add_stock_batched(
                line.item, line.quantity, cost, location_id=transfer.source_location_id,
                batch=line.batch, movement_type="transfer_in", source_type="transfer",
                source_id=transfer.id)
        else:
            cost = item_remove_stock(line.item, line.quantity,
                                     location_id=transfer.destination_location_id,
                                     movement_type="transfer_out", source_type="transfer",
                                     source_id=transfer.id)
            item_add_stock(line.item, line.quantity, cost,
                           location_id=transfer.source_location_id,
                           movement_type="transfer_in", source_type="transfer",
                           source_id=transfer.id)

    transfer.status = "Reversed"
    transfer.is_reversed = True
    transfer.reversed_at = datetime.utcnow()
    transfer.reversed_by_id = reversed_by_id
    return transfer
