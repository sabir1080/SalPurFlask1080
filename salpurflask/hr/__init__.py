"""HR module — employees, departments, designations.

Self-contained: the core app registers `hr_bp` and nothing else. Remove the
blueprint registration and TradeFlow runs exactly as before.
"""

from salpurflask.hr.routes import hr_bp

__all__ = ["hr_bp"]
