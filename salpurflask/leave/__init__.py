"""Leave module — types, allocations, requests and approval.

Depends on HR for employees. Payroll reads approved leave through one function
(`leave_facts`); neither HR nor attendance imports this package, so both work
unchanged whether leave is on or off.
"""

from salpurflask.leave.routes import leave_bp

__all__ = ["leave_bp"]
