"""Notification list, mark-read, mark-all-read.

Every route derives the recipient from current_user and filters by it — the
same rule self_service.py uses for payslips: a record is reached by id, but
the id alone is never enough, its recipient_id must equal the signed-in
user's id or the route 404s. Nobody can read or mark another user's
notification by changing the number in the address bar.
"""

from flask import Blueprint, render_template, redirect, url_for, abort
from flask_login import current_user, login_required

from salpurflask.extensions import db
from salpurflask.models.notification import Notification
from salpurflask.services.notifications import source_url
from datetime import datetime

notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


def _own_or_404(row_id):
    row = db.session.get(Notification, row_id)
    if row is None or row.recipient_id != current_user.id:
        abort(404)
    return row


@notifications_bp.route("/")
@login_required
def list_notifications():
    rows = (Notification.query
            .filter_by(recipient_id=current_user.id)
            .order_by(Notification.created_at.desc())
            .limit(200).all())
    for r in rows:
        r.url = source_url(r.source_type, r.source_id)
    return render_template("notifications/list.html", rows=rows)


@notifications_bp.route("/<int:row_id>/read", methods=["POST"])
@login_required
def mark_read(row_id):
    row = _own_or_404(row_id)
    if not row.is_read:
        row.is_read = True
        row.read_at = datetime.utcnow()
        db.session.commit()
    return redirect(url_for("notifications.list_notifications"))


@notifications_bp.route("/read-all", methods=["POST"])
@login_required
def mark_all_read():
    now = datetime.utcnow()
    (Notification.query
     .filter_by(recipient_id=current_user.id, is_read=False)
     .update({"is_read": True, "read_at": now}))
    db.session.commit()
    return redirect(url_for("notifications.list_notifications"))
