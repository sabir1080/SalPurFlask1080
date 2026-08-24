"""Centralized notification creation.

One door in, so producers never hand-roll their own row-creation or dedupe
logic. A caller names an event (`source_type` + `source_id` + `notif_type`);
this module decides who gets told and whether it has already told them.

Never lets a notification failure break the business transaction it came
from — the payroll finalize, the leave approval, the stock update all
already committed (or are about to) regardless of whether anyone gets told
about it. A failed `notify()` call is logged and swallowed, not raised.
"""

import logging

from salpurflask.extensions import db
from salpurflask.models.notification import Notification, SEVERITIES

logger = logging.getLogger(__name__)


# source_type -> a url_for(endpoint, **kwargs) builder, kwargs derived from source_id.
# Kept in one place so the UI never has to guess a route shape per notif_type.
_SOURCE_ROUTES = {
    "item": ("item_ledger", "id"),
    "leave_request": ("leave.requests_list", None),
    "payroll_period": ("payroll.period_detail", "period_id"),
}


def source_url(source_type, source_id):
    """Best-effort link to the record a notification is about, or None.

    None is an ordinary result — a deleted record, an unknown source_type, or
    a route that no longer exists all fall back to "no link" rather than a
    broken href.
    """
    if not source_type or source_id is None:
        return None
    entry = _SOURCE_ROUTES.get(source_type)
    if entry is None:
        return None
    endpoint, id_kwarg = entry
    try:
        from flask import url_for
        if id_kwarg:
            return url_for(endpoint, **{id_kwarg: source_id})
        return url_for(endpoint)
    except Exception:
        return None


def notify(recipient_id, notif_type, title, message, *, source_type=None,
           source_id=None, severity="info", dedupe=True):
    """Create one notification for one recipient.

    dedupe=True (the default) skips creating a row when an unread
    notification already exists for the same recipient + notif_type +
    source_type + source_id — so a page load or a retried request can call
    this freely without piling up duplicates for one underlying event.

    Returns the Notification (existing or new), or None if creation failed
    or recipient_id is falsy. Never raises — a broken notification must not
    take the calling transaction down with it.
    """
    if not recipient_id:
        return None
    if severity not in SEVERITIES:
        severity = "info"

    try:
        if dedupe and source_type is not None and source_id is not None:
            existing = Notification.query.filter_by(
                recipient_id=recipient_id, notif_type=notif_type,
                source_type=source_type, source_id=source_id, is_read=False,
            ).first()
            if existing is not None:
                return existing

        row = Notification(
            recipient_id=recipient_id, notif_type=notif_type, title=title,
            message=message, source_type=source_type, source_id=source_id,
            severity=severity,
        )
        db.session.add(row)
        db.session.flush()
        return row
    except Exception:
        logger.exception("notify() failed for recipient=%s type=%s", recipient_id, notif_type)
        return None


def notify_roles(roles, notif_type, title, message, *, source_type=None,
                  source_id=None, severity="info", dedupe=True):
    """notify() every verified user holding one of `roles`. Best-effort per
    recipient — one failure does not stop the rest from being notified."""
    from salpurflask.models.models import User

    try:
        users = User.query.filter(User.role.in_(roles), User.verified.is_(True)).all()
    except Exception:
        logger.exception("notify_roles() failed to resolve recipients for roles=%s", roles)
        return []

    created = []
    for user in users:
        row = notify(user.id, notif_type, title, message, source_type=source_type,
                     source_id=source_id, severity=severity, dedupe=dedupe)
        if row is not None:
            created.append(row)
    return created


def unread_count(user_id):
    """Unread notification count for a user, or 0 on any failure — a broken
    count must never take a page down, it just shows nothing."""
    if not user_id:
        return 0
    try:
        return Notification.query.filter_by(recipient_id=user_id, is_read=False).count()
    except Exception:
        logger.exception("unread_count() failed for user=%s", user_id)
        return 0
