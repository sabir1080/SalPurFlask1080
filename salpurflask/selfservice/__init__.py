"""Employee self-service — a read-mostly view of your own HR records.

Depends on HR for the employee link; each page also respects the flag of the
module it reads (attendance, leave, payroll), so a feature that is switched off
is simply absent rather than broken.
"""

from salpurflask.selfservice.routes import selfservice_bp

__all__ = ["selfservice_bp"]
