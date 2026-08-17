"""Database models package."""

# Import everything from models.py and re-export it
# models.py defines __all__ with all models and functions
from salpurflask.models.models import *  # noqa: F401, F403

# Import business configuration models
from salpurflask.models.business_config import (  # noqa: F401, F403
    BusinessCategory,
    ProductField,
    ProductCategoryData,
    CategoryMenuItem,
    CategoryReport,
    CategoryValidation,
    ConfigurationSnapshot,
)

# Import application configuration model
from salpurflask.models.configuration import AppConfiguration  # noqa: F401

# Import HR models (optional module — tables are created but stay empty and
# unreferenced while the HR module is switched off)
from salpurflask.models.hr import (  # noqa: F401
    Department,
    Designation,
    Employee,
    EMPLOYMENT_STATUSES,
)
