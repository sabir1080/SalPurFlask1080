"""Location authorization — Phase 5.

Built beside the existing role system, not instead of it, the same relationship
hr_permissions.py already has to auth.py: role decides *what kind* of action a
user may take (view vs. adjust vs. post); this module decides *where*. Neither
replaces the other, and this phase introduces no new role.

The rule the whole module rests on:

    admin is unrestricted, always.
    A non-admin user with zero UserLocationAccess rows is unrestricted too —
    every existing manager and staff account predates locations entirely, so
    "no rows yet" must mean "keeps the access they already have," not "loses
    it the moment this table appears."
    A non-admin user with one or more rows is restricted to exactly those
    locations, no more.

Do not flip the zero-rows default without going back to the Phase 5 proposal
that specified it — flipping it silently locks every existing manager and
staff user out of every warehouse the moment this ships.
"""

from functools import wraps

from flask import abort, current_app
from flask_login import current_user


def accessible_location_ids(user=None):
    """The set of location ids `user` (default: current_user) may act on, or
    None if they are unrestricted — admin, or a non-admin with zero
    UserLocationAccess rows. None is the "no filter" signal every caller
    below and every route uses: `if ids is not None: query.filter(...)`,
    never a giant list of every location id standing in for "all."
    """
    from salpurflask.models import UserLocationAccess

    user = user if user is not None else current_user
    try:
        if not user or not user.is_authenticated:
            return set()
    except Exception:
        return set()

    if getattr(user, "is_admin", False):
        return None

    rows = UserLocationAccess.query.filter_by(user_id=user.id).all()
    if not rows:
        return None   # zero assignments = unrestricted, the approved default
    return {r.location_id for r in rows}


def can_access_location(location_id, user=None):
    """True if `user` may act on this one location. location_id may be None
    (resolve_location_id's own default-location convention) — treated as the
    default location, so a caller checking "the location a form is about to
    resolve to" gets the same answer resolve_location_id() would give."""
    if location_id is None:
        from salpurflask.models import get_or_create_default_location
        location_id = get_or_create_default_location().id

    ids = accessible_location_ids(user)
    return ids is None or location_id in ids


def can_access_transfer(source_location_id, destination_location_id, user=None):
    """True only if `user` may act on BOTH locations — never authorized
    because they hold just one side. Checked as a single decision, not two
    independent ones, so a caller can never accidentally authorize a
    transfer on a partial check."""
    return (can_access_location(source_location_id, user)
            and can_access_location(destination_location_id, user))


def require_location_access(location_id, user=None):
    """Abort(403) unless `user` may access `location_id`. The single place
    every route-level check in this phase calls through, so a route that
    forgets the reason for a 403 still gets a consistent one.

    404, not 403, would hide *whether* the location exists — but a location
    id always came from a real dropdown or a real document already visible
    to whoever built the request, so there is nothing to hide by name here
    the way an id-guessing attack on someone else's private record would
    need to hide (see self_service.py's own 404-over-403 reasoning, which
    does not apply to a shared operational resource like a warehouse)."""
    if not can_access_location(location_id, user):
        abort(403)


def require_transfer_access(source_location_id, destination_location_id, user=None):
    """Abort(403) unless `user` may access both transfer endpoints."""
    if not can_access_transfer(source_location_id, destination_location_id, user):
        abort(403)


def location_access_required(get_location_id):
    """Decorator factory: refuse a route unless current_user may access the
    location `get_location_id(*args, **kwargs)` resolves to, given the
    route's own arguments. A function, not a fixed field name, because the
    location a route cares about arrives differently everywhere it's
    checked — a query string, a form field, an existing row's column."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            location_id = get_location_id(*args, **kwargs)
            require_location_access(location_id)
            return f(*args, **kwargs)
        return decorated
    return decorator


__all__ = [
    "accessible_location_ids", "can_access_location", "can_access_transfer",
    "require_location_access", "require_transfer_access",
    "location_access_required",
]
