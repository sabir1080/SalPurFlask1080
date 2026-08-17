"""Attendance module — daily attendance, history and monthly summary.

A sub-module of HR, not a part of it: HR does not import this package, so
employees work exactly the same whether attendance is on or off.
"""

from salpurflask.attendance.routes import attendance_bp

__all__ = ["attendance_bp"]
