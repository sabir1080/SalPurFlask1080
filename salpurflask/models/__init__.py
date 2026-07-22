"""Database models package."""

# Import everything from models.py and re-export it
# models.py defines __all__ with all models and functions
from salpurflask.models.models import *  # noqa: F401, F403
