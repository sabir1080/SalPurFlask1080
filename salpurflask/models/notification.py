"""In-app notifications.

One table, reusable by any module. Nothing here alters an existing table, so
the core (inventory, POS, sales, purchase, accounting, HR) is unchanged
whether this table holds a thousand rows or none — the same convention
already used for the HR/attendance/payroll/leave models.
"""

from datetime import datetime

from salpurflask.extensions import db


SEVERITIES = ("info", "warning", "critical")


class Notification(db.Model):
    __tablename__ = "notification"
    __table_args__ = (
        db.Index("ix_notification_recipient_unread", "recipient_id", "is_read"),
        db.Index("ix_notification_recipient_created", "recipient_id", "created_at"),
        db.Index("ix_notification_source", "source_type", "source_id"),
    )

    id            = db.Column(db.Integer, primary_key=True)
    recipient_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    notif_type    = db.Column(db.String(40), nullable=False)
    title         = db.Column(db.String(200), nullable=False)
    message       = db.Column(db.String(500), nullable=False)
    source_type   = db.Column(db.String(40), nullable=True)
    source_id     = db.Column(db.Integer, nullable=True)
    severity      = db.Column(db.String(20), nullable=False, default="info")
    is_read       = db.Column(db.Boolean, nullable=False, default=False)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    read_at       = db.Column(db.DateTime, nullable=True)

    recipient     = db.relationship("User", foreign_keys=[recipient_id])

    def __repr__(self):
        return f"<Notification {self.notif_type} -> user={self.recipient_id} read={self.is_read}>"
