"""Routes package."""

from salpurflask.routes.auth import auth_bp
from salpurflask.routes.dashboard import dashboard_bp
from salpurflask.routes.developer import dev_bp

__all__ = ['auth_bp', 'dashboard_bp', 'dev_bp']
