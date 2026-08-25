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

# Attendance (optional sub-module of HR)
from salpurflask.models.attendance import (  # noqa: F401
    Attendance,
    ATTENDANCE_STATUSES,
    WORKED_STATUSES,
    STANDARD_DAY_HOURS,
)

# Payroll (optional module — depends on HR for employees)
from salpurflask.models.payroll import (  # noqa: F401
    SalaryComponent,
    SalaryStructure,
    SalaryStructureLine,
    PayrollPeriod,
    PayrollEntry,
    PayrollItem,
    EmployeeAdvance,
    PERIOD_STATUSES,
    ADVANCE_STATUSES,
    seed_default_components,
)

# Leave (optional module — depends on HR for employees)
from salpurflask.models.leave import (  # noqa: F401
    LeaveType,
    LeaveAllocation,
    LeaveRequest,
    LEAVE_STATUSES,
    leave_facts,
    seed_leave_types,
)

# Salary payments (settles the liability payroll posting creates)
from salpurflask.models.payroll_payment import (  # noqa: F401
    PayrollPayment,
    period_net_total,
    period_paid_total,
    period_payable_balance,
    period_payment_status,
)

# Notifications (reusable by any module)
from salpurflask.models.notification import (  # noqa: F401
    Notification,
    SEVERITIES,
)

# Multi-branch / multi-warehouse foundation (Phase 1 — Item.stock stays the
# company-wide total; ItemStock is per-location, kept in sync)
from salpurflask.models.inventory_location import (  # noqa: F401
    Branch,
    Location,
    ItemStock,
    UserLocationAccess,
    Transfer,
    TransferItem,
    TRANSFER_STATUSES,
    InventoryReconciliation,
    InventoryReconciliationLine,
    RECONCILIATION_STATUSES,
    StockMovement,
    MOVEMENT_TYPES,
    MOVEMENT_DIRECTIONS,
    record_stock_movement,
    get_or_create_default_location,
    get_item_stock_locked,
    get_or_create_item_stock,
    backfill_item_stock_locations,
    stock_at_location,
    resolve_location_id,
)
