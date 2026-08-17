"""Payroll module — salary structures, periods, runs and payslips.

Depends on HR for employees and reads attendance when that module is on, but
neither imports this package, so both work unchanged whether payroll is on or
off. Phase 3B adds the accounting integration; nothing here posts to the GL.
"""

from salpurflask.payroll.routes import payroll_bp

__all__ = ["payroll_bp"]
