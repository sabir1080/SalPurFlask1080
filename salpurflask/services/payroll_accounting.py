"""Payroll → general ledger.

This is the only place payroll meets accounting. It consumes finalised payroll
figures and calls the same `post_entry` / `reverse_entry` / `posted_entry`
services every other document uses — there is no second accounting system here,
and no debit or credit is computed in this file that the payroll engine did not
already work out.

Shape of a payroll posting:

    Salaries & Wages          DR   basic
    Allowances                DR   allowances + bonus
    Overtime                  DR   overtime
    Other Salary Expense      DR   anything else earned
    ------------------------------------------------
        Salaries Payable          CR   net pay
        Payroll Tax Payable       CR   tax withheld
        Employee Advances         CR   advance recovered
        Salaries Payable          CR   other deductions (still owed to someone)

Debits are what the period cost the business (gross). Credits split that between
what the employee is owed and what was withheld on their behalf. The two sides
balance because gross earnings less deductions is exactly net pay.

One entry per period, not per employee: the finalisable document is the period,
every existing poster is one-document-one-entry, and per-employee detail already
lives in PayrollItem. See the phase 3B design note.

Advance recovery credits Employee Advances (an asset) rather than a liability —
the employee owes less, so the receivable falls.
"""

from decimal import Decimal

from salpurflask.extensions import db

MONEY = Decimal("0.01")

# (role, preferred code, name, type, parent code, cash-flow section)
# Seeded the way the fixed-asset module seeds its own: found by role, and if the
# preferred code is already taken it lands on the next free one, so a chart that
# is already in use is never disturbed.
PAYROLL_ACCOUNTS = [
    ("salary_expense",       "6020", "Salaries & Wages",     "Expense",   "6000", None),
    ("allowance_expense",    "6021", "Allowances",           "Expense",   "6000", None),
    ("overtime_expense",     "6022", "Overtime",             "Expense",   "6000", None),
    ("other_salary_expense", "6023", "Other Salary Expense", "Expense",   "6000", None),
    ("salaries_payable",     "2110", "Salaries Payable",     "Liability", "2000", "Operating"),
    ("payroll_tax_payable",  "2120", "Payroll Tax Payable",  "Liability", "2000", "Operating"),
    ("employee_advance",     "1150", "Employee Advances",    "Asset",     "1000", "Operating"),
]
PAYROLL_ROLES = tuple(r for r, *_ in PAYROLL_ACCOUNTS)

SOURCE_TYPE = "payroll"

# Which expense account each earning component lands in. A component not named
# here goes to Other Salary Expense rather than being dropped — a missing line
# would unbalance the entry, and silently losing salary cost is worse than
# putting it in a slightly coarse account.
EARNING_ACCOUNTS = {
    "BASIC":       "salary_expense",
    "OVERTIME":    "overtime_expense",
    "HRA":         "allowance_expense",
    "MEDICAL":     "allowance_expense",
    "CONVEYANCE":  "allowance_expense",
    "OTHER_ALLOW": "allowance_expense",
    "BONUS":       "allowance_expense",
}

# Where each deduction is parked. TAX is withheld and owed to the authority;
# ADVANCE reduces the employee receivable; anything else stays inside Salaries
# Payable, because it is still money owed to somebody out of this payroll.
DEDUCTION_ACCOUNTS = {
    "TAX":     "payroll_tax_payable",
    "ADVANCE": "employee_advance",
}
DEFAULT_DEDUCTION_ROLE = "salaries_payable"


def _money(value):
    return Decimal(str(value or 0)).quantize(MONEY)


def seed_payroll_accounts():
    """Create the payroll accounts once. Idempotent, and safe on a chart already
    in use — mirrors seed_fixed_asset_accounts()."""
    from salpurflask.models.models import Account, _free_code

    created = 0
    for role, preferred, name, type_, parent_code, cf_section in PAYROLL_ACCOUNTS:
        if Account.query.filter_by(role=role).first():
            continue
        # Reuse an existing account on the preferred code when it is already the
        # right thing -- 6020 Salaries & Wages ships with the base chart, and a
        # second salary account beside it would split the same cost in two.
        existing = Account.query.filter_by(code=preferred).first()
        if existing is not None and existing.type == type_ and not existing.is_group:
            existing.role = role
            created += 1
            continue

        parent = Account.query.filter_by(code=parent_code).first() if parent_code else None
        db.session.add(Account(
            code=_free_code(preferred), name=name, type=type_,
            parent_id=parent.id if parent else None,
            is_group=False, is_control=False, role=role,
            cash_flow_section=cf_section))
        created += 1
    db.session.commit()
    return created


def account_for(role):
    """The account holding `role`, seeding the payroll set if it is absent."""
    from salpurflask.models.models import Account

    acct = Account.query.filter_by(role=role).first()
    if acct is None:
        seed_payroll_accounts()
        acct = Account.query.filter_by(role=role).first()
    if acct is None:
        raise LookupError(f"Payroll account for role '{role}' is missing")
    return acct


def posted_journal_entry(period):
    """The live (non-reversed) entry for this period, or None."""
    from app import posted_entry
    return posted_entry(SOURCE_TYPE, period.id)


def build_lines(period):
    """Turn a period's payslips into balanced journal lines.

    Reads only what the payroll engine already stored. Amounts are summed per
    account so the entry stays small however many employees there are.
    """
    debits, credits = {}, {}

    for entry in period.entries.all():
        for item in entry.items:
            amount = _money(item.amount)
            if amount <= 0:
                continue
            if item.component_type == "earning":
                role = EARNING_ACCOUNTS.get(item.code, "other_salary_expense")
                debits[role] = debits.get(role, Decimal("0")) + amount
            else:
                role = DEDUCTION_ACCOUNTS.get(item.code, DEFAULT_DEDUCTION_ROLE)
                credits[role] = credits.get(role, Decimal("0")) + amount

        # What the employee actually takes home is owed to them.
        net = _money(entry.net_salary)
        if net > 0:
            credits["salaries_payable"] = credits.get("salaries_payable",
                                                      Decimal("0")) + net

    lines = []
    for role, amount in sorted(debits.items()):
        if amount <= 0:
            continue
        acct = account_for(role)
        lines.append({"account_id": acct.id, "debit": amount, "credit": 0,
                      "memo": f"Payroll {period.name}"})
    for role, amount in sorted(credits.items()):
        if amount <= 0:
            continue
        acct = account_for(role)
        lines.append({"account_id": acct.id, "debit": 0, "credit": amount,
                      "memo": f"Payroll {period.name}"})
    return lines


def post_payroll_period(period, created_by_id=None):
    """Post one balanced entry for `period`, or return None if already posted.

    Idempotent through `posted_entry(source_type, source_id)` — the same guard
    every other document uses. Does NOT commit: the caller owns the transaction,
    so a payroll that fails to post is not left finalised.

    Raises PostingError, which the route turns into a flash and a rollback.
    """
    from app import post_entry, PostingError

    if period.status == "Cancelled":
        raise PostingError(f"{period.name} is cancelled and cannot be posted.")

    if posted_journal_entry(period) is not None:
        return None                     # already posted — never post twice

    lines = build_lines(period)
    if not lines:
        raise PostingError(f"{period.name} has no payslips to post.")

    total_dr = sum(l["debit"] for l in lines)
    total_cr = sum(l["credit"] for l in lines)
    if total_dr != total_cr:
        # post_entry would refuse this too; failing here names payroll as the
        # cause instead of leaving a bare "out of balance".
        raise PostingError(
            f"Payroll for {period.name} does not balance: debits {total_dr:,.2f} "
            f"vs credits {total_cr:,.2f}. No entry was written.")

    return post_entry(
        entry_date=period.end_date,
        description=f"Payroll: {period.name}",
        reference=f"PAY-{period.id}",
        source_type=SOURCE_TYPE, source_id=period.id,
        allow_control=True,
        created_by_id=created_by_id,
        lines=lines,
    )


def reverse_payroll_period(period, created_by_id=None):
    """Reverse this period's posting. The original entry is never deleted.

    Returns the reversal, or None when there is nothing live to reverse — so
    cancelling twice cannot produce two reversals.
    """
    from app import reverse_entry

    entry = posted_journal_entry(period)
    if entry is None:
        return None
    return reverse_entry(entry, created_by_id=created_by_id)


def accounting_status(period):
    """NOT_POSTED / POSTED / REVERSED, derived from the ledger itself.

    Deliberately not stored on the period: a duplicated status column can drift
    out of step with the entries it claims to describe, and the journal is the
    record that matters.
    """
    from app import JournalEntry

    live = posted_journal_entry(period)
    if live is not None:
        return "POSTED"
    any_entry = (JournalEntry.query
                 .filter_by(source_type=SOURCE_TYPE, source_id=period.id)
                 .first())
    return "REVERSED" if any_entry is not None else "NOT_POSTED"


def journal_summary(period):
    """What the period detail page shows about the posting."""
    from app import JournalEntry

    entry = posted_journal_entry(period)
    if entry is None:
        reversal = (JournalEntry.query
                    .filter_by(source_type=SOURCE_TYPE, source_id=period.id)
                    .filter(JournalEntry.reversal_of_id.isnot(None))
                    .order_by(JournalEntry.id.desc()).first())
        if reversal is None:
            return None
        return {"status": "REVERSED", "entry": reversal,
                "reference": reversal.reference,
                "entry_date": reversal.entry_date,
                "total_debit": reversal.total_debit,
                "total_credit": reversal.total_credit,
                "reverses": reversal.reversal_of_id}
    return {"status": "POSTED", "entry": entry, "reference": entry.reference,
            "entry_date": entry.entry_date,
            "total_debit": entry.total_debit,
            "total_credit": entry.total_credit, "reverses": None}


__all__ = ["PAYROLL_ACCOUNTS", "PAYROLL_ROLES", "SOURCE_TYPE",
           "EARNING_ACCOUNTS", "DEDUCTION_ACCOUNTS",
           "seed_payroll_accounts", "account_for", "posted_journal_entry",
           "build_lines", "post_payroll_period", "reverse_payroll_period",
           "accounting_status", "journal_summary"]
