"""Salary payments — money actually leaving the business.

Phase 3B recorded what payroll *cost* and what is *owed*:

    Salary expense    DR
        Salaries Payable  CR

This is the other half — settling that liability:

    Salaries Payable  DR
        Cash / Bank       CR

Deliberately its own table, one row per payment, modelled on SupplierPayment.
That shape is what makes partial payment safe: idempotency is per payment record
(`source_id` = this row's id), not per period, so paying half now and half later
is two ordinary payments rather than a special case bolted onto the period.

Nothing here posts. `payroll_accounting` owns the journal entry, the same way it
owns the payroll posting, so there is exactly one place payroll meets the ledger.
"""

from datetime import datetime
from decimal import Decimal

from salpurflask.extensions import db

MONEY = Decimal("0.0001")


class PayrollPayment(db.Model):
    """One payment against one finalised payroll period.

    A period may have several: `payable_balance()` on the period is what remains.
    `is_reversed` mirrors SupplierPayment — a reversed payment keeps its row and
    its journal entry, because deleting either would orphan the other.
    """
    __tablename__ = "hr_payroll_payment"
    __table_args__ = (
        db.Index("ix_payroll_payment_period", "period_id", "is_reversed"),
    )

    id        = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey("hr_payroll_period.id"),
                          nullable=False, index=True)

    amount       = db.Column(db.Numeric(14, 4), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, index=True)

    # Same cash/bank mechanism every other payment in TradeFlow uses: an explicit
    # FinancialAccount, resolved to its GL leaf at posting time. No hard-coded id.
    account_id     = db.Column(db.Integer, db.ForeignKey("financial_account.id"),
                               nullable=False)
    payment_method = db.Column(db.String(20), nullable=True)

    reference_no = db.Column(db.String(100), nullable=True)
    notes        = db.Column(db.String(300), nullable=True)

    is_reversed = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at = db.Column(db.DateTime, nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                              onupdate=datetime.utcnow)

    period  = db.relationship("PayrollPeriod",
                              backref=db.backref("payments", lazy="dynamic",
                                                 cascade="all, delete-orphan"))
    account = db.relationship("FinancialAccount", lazy="joined")

    def __repr__(self):
        return f"<PayrollPayment period={self.period_id} {self.amount}>"


def period_net_total(period):
    """What the period's payslips add up to — the liability that was posted."""
    return sum((Decimal(str(e.net_salary or 0)) for e in period.entries.all()),
               Decimal("0")).quantize(MONEY)


def period_paid_total(period):
    """What has been paid against it, ignoring reversed payments."""
    return sum((Decimal(str(p.amount or 0))
                for p in period.payments.filter_by(is_reversed=False).all()),
               Decimal("0")).quantize(MONEY)


def period_payable_balance(period):
    """Still owed. Never negative — an over-payment is refused before it is
    written, so a negative balance here would mean a bug rather than a fact."""
    return max(Decimal("0"), period_net_total(period) - period_paid_total(period))


def period_payment_status(period):
    """UNPAID / PARTIALLY_PAID / PAID, derived from the payment rows.

    Derived rather than stored, for the same reason the accounting status is: a
    duplicated status column drifts away from the records it claims to describe.
    """
    net = period_net_total(period)
    paid = period_paid_total(period)
    if paid <= 0:
        return "UNPAID"
    if paid >= net:
        return "PAID"
    return "PARTIALLY_PAID"


__all__ = ["PayrollPayment", "period_net_total", "period_paid_total",
           "period_payable_balance", "period_payment_status"]
