"""Multi-branch / multi-warehouse foundation — Phase 1.

Kept in its own module and its own tables, the same convention as hr.py /
attendance.py / payroll.py: nothing here alters an existing table, and no
existing model gains a column or a relationship pointing this way, so the
core (inventory, POS, sales, purchase, accounting) is unchanged whether
these tables hold one location or a hundred.

Item.stock is NOT replaced. It stays the company-wide total, kept in sync by
item_add_stock()/item_remove_stock() in models.py (the same functions every
existing route already calls) so every existing report, export and dashboard
widget keeps reading it unchanged. ItemStock is the new source of truth for
"how much of this item is at this specific location" — the two numbers are
kept equal by construction, one write, one transaction, never independently.

A business with exactly one location sees no UI for any of this yet (that is
Phase 2). This phase only makes the data model ready for a second warehouse.
"""

from datetime import datetime

from salpurflask.extensions import db


class Branch(db.Model):
    """A physical or organisational branch of the business. Deliberately a
    separate table from Location/Warehouse rather than a unified
    self-referential model — see the architecture plan for why: branch-level
    access and warehouse-level stock are different-shaped questions, and a
    branch can exist with zero warehouses of its own (a sales-only branch
    fulfilled from a central warehouse elsewhere)."""
    __tablename__ = "branch"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False, unique=True)
    is_default  = db.Column(db.Boolean, nullable=False, default=False)
    active      = db.Column(db.Boolean, nullable=False, default=True)

    locations   = db.relationship("Location", backref="branch", lazy=True)

    def __repr__(self):
        return f"<Branch {self.name!r}>"


class Location(db.Model):
    """A warehouse or stock-holding point within a branch. Named Location,
    not Warehouse, so a future phase that needs to track stock at the branch
    level directly is a `kind` value away, not a schema rewrite — today
    `kind` is always "warehouse"."""
    __tablename__ = "location"
    __table_args__ = (
        db.Index("ix_location_branch", "branch_id"),
    )

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    kind        = db.Column(db.String(20), nullable=False, default="warehouse")
    branch_id   = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    is_default  = db.Column(db.Boolean, nullable=False, default=False)
    active      = db.Column(db.Boolean, nullable=False, default=True)
    address     = db.Column(db.String(200), nullable=True)

    def __repr__(self):
        return f"<Location {self.name!r}>"


class ItemStock(db.Model):
    """How much of one item is at one location. One row per (item, location)
    pair — the same "one number, one truth" shape as the rest of this app's
    balance-by-summation designs. Item.stock is the sum of these rows across
    every location for that item, kept equal by item_add_stock()/
    item_remove_stock() writing both in the same transaction."""
    __tablename__ = "item_stock"
    __table_args__ = (
        db.UniqueConstraint("item_id", "location_id", name="uq_item_stock_item_location"),
        db.Index("ix_item_stock_location", "location_id"),
    )

    id          = db.Column(db.Integer, primary_key=True)
    item_id     = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=False)
    quantity    = db.Column(db.Integer, nullable=False, default=0)

    item        = db.relationship("Item", backref=db.backref(
        "stock_by_location", cascade="all,delete-orphan", lazy=True))
    location    = db.relationship("Location")

    def __repr__(self):
        return f"<ItemStock item={self.item_id} location={self.location_id} qty={self.quantity}>"


class UserLocationAccess(db.Model):
    """One row = this user may act on this location — Phase 5.

    Absence is not denial: a non-admin user with zero rows here is
    unrestricted, not locked out. Every existing manager and staff user was
    created before locations existed at all, so "no rows yet" has to mean
    "keeps the access they already have," not "loses it the moment this
    table appears" — see salpurflask/services/location_permissions.py for
    where that rule is actually enforced. This table only ever narrows
    access for a user someone has deliberately assigned at least one row to.

    admin bypasses this table entirely (User.is_admin), by construction —
    no row is ever needed or created for an admin."""
    __tablename__ = "user_location_access"
    __table_args__ = (
        db.UniqueConstraint("user_id", "location_id", name="uq_user_location"),
        db.Index("ix_user_location_access_user", "user_id"),
    )

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    location_id   = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=False)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    granted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    user          = db.relationship("User", foreign_keys=[user_id])
    location      = db.relationship("Location")
    granted_by    = db.relationship("User", foreign_keys=[granted_by_id])

    def __repr__(self):
        return f"<UserLocationAccess user={self.user_id} location={self.location_id}>"


TRANSFER_STATUSES = ("Draft", "Confirmed", "Cancelled", "Reversed")


class Transfer(db.Model):
    """A warehouse-to-warehouse stock movement — Phase 3.

    Modeled after StockAdjustment's own document/reversal shape (is_reversed
    + reversed_at, never delete a confirmed row), not after Purchase/Sale:
    a transfer moves quantity between two of the company's own locations, so
    it touches no GL account and carries no cost/value column of its own —
    see the architecture plan's accounting section. The Transfer row itself
    is the audit trail; ItemStock is only ever mutated through it, via
    item_add_stock()/item_remove_stock(), never by a direct += / -=.

    Draft: a working list, no stock moved yet, freely editable via delete.
    Confirmed: stock has moved; the row becomes historical evidence and can
        only be reversed, never edited or deleted (mirrors assert_not_posted's
        "posted document is history" rule, without needing the GL machinery
        that rule is actually built on).
    Cancelled: a Draft abandoned before confirmation — no stock ever moved.
    Reversed: a Confirmed transfer undone — the exact inverse movement
        happened, both rows stay for the record.
    """
    __tablename__ = "inventory_transfer"
    __table_args__ = (
        db.Index("ix_transfer_source", "source_location_id"),
        db.Index("ix_transfer_destination", "destination_location_id"),
    )

    id                     = db.Column(db.Integer, primary_key=True)
    transfer_no            = db.Column(db.String(30), nullable=True, unique=True, index=True)
    source_location_id     = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=False)
    destination_location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=False)
    status                 = db.Column(db.String(20), nullable=False, default="Draft")
    date                   = db.Column(db.DateTime, nullable=False)
    notes                  = db.Column(db.String(300), nullable=True)
    created_by_id          = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at             = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at             = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                                       onupdate=datetime.utcnow)
    confirmed_at           = db.Column(db.DateTime, nullable=True)
    confirmed_by_id        = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    is_reversed            = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at            = db.Column(db.DateTime, nullable=True)
    reversed_by_id         = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    source_location        = db.relationship("Location", foreign_keys=[source_location_id])
    destination_location   = db.relationship("Location", foreign_keys=[destination_location_id])
    created_by             = db.relationship("User", foreign_keys=[created_by_id])
    confirmed_by           = db.relationship("User", foreign_keys=[confirmed_by_id])
    reversed_by            = db.relationship("User", foreign_keys=[reversed_by_id])
    lines                  = db.relationship("TransferItem", backref="transfer",
                                             lazy=True, cascade="all,delete-orphan")

    def __repr__(self):
        return f"<Transfer {self.transfer_no or self.id} {self.status}>"


class TransferItem(db.Model):
    __tablename__ = "inventory_transfer_item"

    id           = db.Column(db.Integer, primary_key=True)
    transfer_id  = db.Column(db.Integer, db.ForeignKey("inventory_transfer.id"), nullable=False)
    item_id      = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity     = db.Column(db.Integer, nullable=False)
    notes        = db.Column(db.String(200), nullable=True)

    item         = db.relationship("Item")

    def __repr__(self):
        return f"<TransferItem item={self.item_id} qty={self.quantity}>"


MOVEMENT_TYPES = ("purchase", "sale", "purchase_return", "sale_return",
                  "adjustment", "transfer_out", "transfer_in", "opening")
MOVEMENT_DIRECTIONS = ("in", "out")


class StockMovement(db.Model):
    """An audit trail, not a source of truth. Current stock is still, and
    only ever, Item.stock / ItemStock.quantity — this table exists purely to
    answer "what happened to this item, at this location, and why" after the
    fact. Written only from item_add_stock()/item_remove_stock() in
    models.py (the same transaction, so a caller's rollback removes an
    in-progress row along with everything else), plus three call sites that
    seed opening stock outside those choke points on purpose — see
    movement_type="opening".

    source_type/source_id trace back to the document that caused the row —
    "purchase", 12 — the same pattern JournalEntry and Notification already
    use, so a query never needs to guess which table to join back to."""
    __tablename__ = "stock_movement"
    __table_args__ = (
        db.Index("ix_stock_movement_item_location_created", "item_id", "location_id", "created_at"),
        db.Index("ix_stock_movement_location_created", "location_id", "created_at"),
        db.Index("ix_stock_movement_source", "source_type", "source_id"),
    )

    id            = db.Column(db.Integer, primary_key=True)
    item_id       = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    location_id   = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=False)
    movement_type = db.Column(db.String(20), nullable=False)
    direction     = db.Column(db.String(4), nullable=False)     # "in" or "out"
    quantity      = db.Column(db.Integer, nullable=False)        # signed: +in, -out
    source_type   = db.Column(db.String(30), nullable=True)
    source_id     = db.Column(db.Integer, nullable=True)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    item          = db.relationship("Item")
    location      = db.relationship("Location")
    created_by    = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return (f"<StockMovement {self.movement_type} item={self.item_id} "
                f"location={self.location_id} qty={self.quantity}>")


def record_stock_movement(item_id, location_id, direction, quantity, movement_type, *,
                          source_type=None, source_id=None, created_by_id=None):
    """The one function that writes a StockMovement row.

    `direction` ("in"/"out") comes from the caller, not guessed from
    `movement_type` — item_add_stock() always means "in", item_remove_stock()
    always means "out", regardless of which document triggered the call (an
    edit that reverts an old purchase line still adds stock back before
    removing the new amount; both calls are movement_type="purchase" but
    opposite directions). `quantity` is the unsigned amount that moved; the
    row's own signed quantity is derived from direction here, so a caller
    never repeats that arithmetic.

    Never raises — a broken ledger write must not take the stock mutation it
    describes down with it, the same posture notify()/notify_roles() already
    hold for notifications elsewhere in this app. Logs and swallows instead."""
    import logging

    if not quantity:
        return None
    if movement_type not in MOVEMENT_TYPES:
        logging.getLogger(__name__).error(
            "record_stock_movement: unknown movement_type %r", movement_type)
        return None
    if direction not in MOVEMENT_DIRECTIONS:
        logging.getLogger(__name__).error(
            "record_stock_movement: unknown direction %r", direction)
        return None

    signed_qty = abs(quantity) if direction == "in" else -abs(quantity)

    try:
        row = StockMovement(
            item_id=item_id, location_id=location_id, movement_type=movement_type,
            direction=direction, quantity=signed_qty,
            source_type=source_type, source_id=source_id, created_by_id=created_by_id)
        db.session.add(row)
        db.session.flush()
        return row
    except Exception:
        logging.getLogger(__name__).exception(
            "record_stock_movement failed for item=%s location=%s type=%s",
            item_id, location_id, movement_type)
        return None


def get_or_create_default_location():
    """The one warehouse every existing single-location item's stock lives
    at. Idempotent — safe to call from the migration, from a test, or from a
    route that needs a location and none has been chosen yet."""
    branch = Branch.query.filter_by(is_default=True).first()
    if branch is None:
        branch = Branch.query.first()
    if branch is None:
        branch = Branch(name="Main Branch", is_default=True)
        db.session.add(branch)
        db.session.flush()

    location = Location.query.filter_by(is_default=True).first()
    if location is None:
        location = Location.query.first()
    if location is None:
        location = Location(name="Main Warehouse", kind="warehouse",
                            branch_id=branch.id, is_default=True)
        db.session.add(location)
        db.session.flush()
    return location


def get_item_stock_locked(item_id, location_id):
    """Fetch an ItemStock row FOR UPDATE, so concurrent stock changes at the
    same location serialize instead of racing — the same pattern and the
    same reasoning as get_item_locked() in utils/helpers.py, now narrowed to
    one (item, location) pair instead of the whole item. A real row lock on
    PostgreSQL, a harmless no-op on SQLite."""
    return (db.session.query(ItemStock)
            .filter_by(item_id=item_id, location_id=location_id)
            .with_for_update().first())


def get_or_create_item_stock(item_id, location_id):
    """The ItemStock row for (item, location), created at quantity 0 on
    first use — a location where an item has never been stocked is "tracked,
    empty," not "no row exists yet." Locks the row once created so the
    caller's subsequent read-modify-write is safe under concurrency."""
    row = get_item_stock_locked(item_id, location_id)
    if row is None:
        row = ItemStock(item_id=item_id, location_id=location_id, quantity=0)
        db.session.add(row)
        db.session.flush()
        row = get_item_stock_locked(item_id, location_id)
    return row


def stock_at_location(item_id, location_id):
    """How much of an item is available at one specific location — the number
    a sale, an adjustment "out", or a return must validate against once a
    location is in play, never Item.stock (the company-wide total).

    An item with an ItemStock row at this location reads its quantity,
    genuinely zero included. An item with no ItemStock row ANYWHERE yet
    (created before this phase's migration ran, or built directly in a test
    that bypasses item_add_stock/item_remove_stock) is not a zero-stock
    item — Item.stock already holds its real count, and it implicitly lives
    at the default location, the same backward-compatibility rule
    item_add_stock()/item_remove_stock() already apply on write. Reading
    availability must agree with what a mutation would do, or a sale could
    be refused for stock the item provably has.

    Only in between — this item has been migrated (it has rows at other
    locations) but never at *this* one — does it read as a genuine 0."""
    row = ItemStock.query.filter_by(item_id=item_id, location_id=location_id).first()
    if row is not None:
        return row.quantity
    has_any_row = (db.session.query(ItemStock.id)
                   .filter_by(item_id=item_id).first() is not None)
    if has_any_row:
        return 0
    from salpurflask.models.models import Item
    default_location = get_or_create_default_location()
    if location_id == default_location.id:
        item = db.session.get(Item, item_id)
        return (item.stock or 0) if item else 0
    return 0


def resolve_location_id(raw_value):
    """Turn a form field's raw location_id (a string, possibly blank, from
    request.form) into a real location id — the default location when blank,
    so a single-warehouse business never has to pick anything. Raises
    ValueError for a value that doesn't correspond to any Location, so a
    tampered or stale form field fails loudly rather than silently landing
    on the default."""
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return get_or_create_default_location().id
    if not raw_value.isdigit():
        raise ValueError(f"'{raw_value}' is not a valid warehouse.")
    location = db.session.get(Location, int(raw_value))
    if location is None:
        raise ValueError(f"Warehouse #{raw_value} does not exist.")
    return location.id


def backfill_item_stock_locations():
    """One-time (but idempotent, safe to run on every boot) migration: seed
    the default branch/location, then give every existing Item's current
    stock its ItemStock row there. A straight copy, one row per item, no
    computation — the entire migration for stock data. Service-type items
    get no row (they were never stock-tracked); an item that already has an
    ItemStock row anywhere is left alone, so a second run never double-counts.

    The default location is always seeded, even on a database with no items
    yet, so it exists as soon as the app boots rather than being created
    lazily by whichever route happens to touch stock first.

    Item.stock is not touched here — it already holds the right number, and
    this only creates the location-level mirror of it.
    """
    from salpurflask.models.models import Item

    location = get_or_create_default_location()
    db.session.commit()

    stocked_item_ids = {row[0] for row in
                        db.session.query(ItemStock.item_id).distinct().all()}
    query = Item.query.filter(Item.item_type == "STOCK")
    if stocked_item_ids:
        query = query.filter(~Item.id.in_(stocked_item_ids))
    candidates = query.all()
    if not candidates:
        return

    for item in candidates:
        db.session.add(ItemStock(item_id=item.id, location_id=location.id,
                                 quantity=item.stock or 0))
    db.session.commit()
