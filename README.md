# TradeFlow ERP

> **One Software. Complete Business Management.**
> Inventory, Point of Sale, Purchasing, Double-Entry Accounting, and HR/Payroll — in a single Flask application.

---

## 🎯 Overview

TradeFlow ERP is a full business-management platform for small and medium businesses: inventory and multi-warehouse stock, purchasing and sales (including POS), supplier/customer ledgers, production-grade double-entry accounting, and an HR suite covering employees, attendance, leave, and payroll.

### Why TradeFlow ERP?

**For Business Owners**
- One system for inventory, purchases, sales, accounts, and staff — no juggling separate tools
- Real-time profit & loss, balance sheet, and cash flow, always tied back to actual GL postings
- Multi-warehouse stock with per-user location access control
- Optional HR/Attendance/Leave/Payroll modules — enable only what your business needs

**For Enterprise Clients**
- Bank-grade security: Argon2 password hashing, CSRF protection, audit logging, role-based access
- PostgreSQL-backed for production scale and reliability
- JSON database backup/restore for disaster recovery
- A dedicated Developer panel for diagnostics, schema inspection, and module control — separately access-gated from the business app

**For Developers**
- 721 automated tests across 38 test files, run in CI on every push
- Feature-flagged modules so new functionality ships dark and is switched on deliberately
- Ranked search/lookup service shared across every item/supplier/customer picker in the app

---

## ✨ Key Features

### 📦 Inventory & Multi-Warehouse
- Item & Category CRUD with SKU, barcode/QR generation, and per-category custom fields
- Preconfigured business-category catalog (grocery, medical store, garments, electronics, and more) to bootstrap a new company fast
- Stock adjustments, stock report, item ledger, low-stock alerts, bulk import/export
- **Warehouses**: create/deactivate locations, transfer stock between warehouses (with confirm/cancel/reverse), physical stock reconciliation (count → finalize → approve → post)
- **Location Access control**: restrict a manager/staff account to specific warehouses; unrestricted by default

### 🛒 Purchasing
- Purchase Orders with conversion to confirmed Purchases
- Purchases, Purchase Returns, Purchase Invoices
- Supplier Ledger with aging analysis, statement export, single & bulk Supplier Payments

### 🏪 Sales & POS
- Point of Sale with barcode scan, cart, held bills/resume, and receipt printing
- Standard Sales, Sale Returns, Delivery Challans, Quotations
- Customer Ledger with aging/receivables tracking, single & bulk Customer Receipts

### 🔎 Universal Search & Lookup
- Shared ranked search (exact barcode → exact SKU → exact name → starts-with → contains) powering every item/supplier/customer picker across Sales, Purchases, POS, and Quotations

### 💼 Double-Entry Accounting
- General Ledger, Journal Entries (with reversal), Chart of Accounts
- Trial Balance, Balance Sheet, Profit & Loss, Cash Flow Statement, GL Reconciliation Report
- Fixed Assets with depreciation and disposal
- Fiscal Year management with period close
- Tax codes and GST/sales-tax tracking
- Hierarchical cash/bank Financial Accounts, each backed by its own GL leaf

### 👥 HR, Attendance, Leave & Payroll *(optional modules — see below)*
- **HR**: employee records linked to user accounts, departments, designations
- **Attendance**: daily marking, edits, monthly summary
- **Leave**: leave types, allocations, requests with submit/approve/reject/cancel (approval restricted to manager/admin)
- **Payroll**: per-employee salary structures, payroll periods (process → finalize → pay), salary advances, payslips, GL-posted payroll runs (finalize/post is admin-only)
- **Self-Service**: employees view their own profile, attendance, leave requests, and payslips

### 🔒 Modular & Feature-Flagged
HR, Attendance, Leave, and Payroll ship **off by default** and are switched on per-deployment from the Developer panel's Modules page — a business that only needs inventory and accounting never sees the extra menus. Attendance, Leave, and Payroll each require HR to be enabled first.

### 🔐 Security & Access Control
- Role-Based Access Control: **Admin**, **Manager**, **Staff**
- Fine-grained permission checks on top of roles for HR/Payroll actions (e.g. only admins post payroll to the GL, only managers/admins approve leave)
- Per-warehouse location access restriction
- Argon2 password hashing, email verification, CSRF protection (Flask-WTF), audit logging, secure session cookies
- A separate password-protected **Developer panel** (database inspection, schema sync, environment viewer, logs, module toggles) with environment-specific access levels between local development and deployed environments

### 📊 Reporting
- Aging Report (AP/AR), GST Report, Stock Valuation Report, Supplier Payables, Customer Receivables, GL Reconciliation
- CSV & Excel export on every report and subledger

### 📋 Administration
- User management with role assignment
- Business Configuration: category catalog, custom product fields
- Financial Account management (hierarchical cash/bank structure)
- Database Backup/Restore (JSON export/import)
- Audit Log viewer
- Notifications (in-app, mark read/unread)

---

## 🎬 Live Demo

Experience TradeFlow ERP online: **https://TradeFlow-Demo.onrender.com**

- **Email**: `demo@demo.com`
- **Password**: `demo1234`
- First load can take ~40 seconds — the free-tier server sleeps when idle.

---

## 🏗️ Architecture

TradeFlow runs as a Flask application with `app.py` as the entry point, alongside a growing `salpurflask/` package of feature modules:

```
app.py                    Application bootstrap, config, CLI commands,
                           and a set of core routes (admin/users, financial
                           accounts, reports, exports, health check)

salpurflask/
├── auth.py                Access-control decorators (verified_required,
│                           role_required, admin_required, manager_required)
├── extensions/             Flask extension instances (db, login_manager, csrf...)
├── config/                 App configuration
├── forms/                  WTForms definitions
├── models/                 SQLAlchemy models, split by domain
│                           (models, hr, attendance, leave, payroll,
│                            inventory_location, notification, business_config...)
│
├── inventory/              Items, categories, stock, warehouses, transfers,
│                           location access, stock reconciliation
├── purchase/               Purchase Orders, Purchases, Purchase Returns
├── sales/                  Sales, POS, Sale Returns, Delivery Challans
├── supplier/               Supplier CRUD, Payments, Ledger
├── customer/               Customer CRUD, Receipts, Ledger
├── accounting/             GL, Journal Entries, Fixed Assets, Financial
│                           Statements, Fiscal Years, Chart of Accounts
│
├── hr/                     Employees, Departments, Designations
├── attendance/              Daily attendance & monthly summary
├── leave/                  Leave types, allocations, requests/approvals
├── payroll/                Salary structures, payroll runs, payslips
├── selfservice/            Employee self-service portal
├── notifications/          In-app notifications
│
├── routes/                 Auth, Dashboard, Admin Config, Developer panel
└── services/               Shared business logic: category catalog,
                             universal lookup/search, feature flags,
                             location permissions, payroll engine &
                             accounting, HR permissions, transfers
```

Newer functionality (HR/Attendance/Leave/Payroll, Self-Service, Notifications, Admin Config, Developer panel, Auth, Dashboard) is wired as proper Flask blueprints. The core ERP domains (inventory, purchase, sales, supplier, customer, accounting) live in their own `salpurflask/` submodules but are attached to the app directly to preserve existing route/endpoint names. `app.py` itself still owns app bootstrap, all Flask CLI commands, and a handful of admin/report/export routes.

### Design Principles
- **Ledger integrity**: every Purchase, Sale, SupplierPayment, and CustomerPayment write is followed by a matching subledger sync and a full ledger recalculation — balances are never trusted to stay correct on their own
- **Feature flags for new modules**: HR/Attendance/Leave/Payroll are built and tested but ship disabled, so existing deployments are unaffected until a business opts in
- **Non-blocking schema migrations**: startup migrations only ever add nullable columns — no blocking DDL that could hang a zero-downtime deploy

---

## 📂 Project Structure

```
TradeFlow ERP/
│
├── app.py                          # Application entry point, config, CLI commands
├── requirements.txt                # Python dependencies
├── render.yaml                     # Render deployment config
├── Procfile                        # gunicorn app:app
├── CLAUDE.md                       # Developer/AI-assistant guidance
│
├── salpurflask/                    # Feature modules (see Architecture above)
│
├── templates/                      # Jinja2 templates (Bootstrap 5)
│   ├── base.html                   # Master layout, role-aware navigation
│   ├── inventory/ purchase/ sales/
│   ├── supplier/ customer/ accounting/
│   ├── hr/ attendance/ leave/ payroll/ selfservice/
│   └── auth/ admin/ developer/
│
├── static/
│   ├── css/                        # Stylesheets (design tokens, layout, components)
│   ├── js/                         # JavaScript utilities
│   └── exports/                    # Generated CSV/Excel files
│
├── tests/                          # 721 tests across 38 files (pytest)
│   └── conftest.py                 # Shared fixtures
│
├── migrations/                     # Alembic scaffold (present, not wired to runtime boot)
└── instance/                       # Local database file (development)
```

---

## 🚀 Installation

### Prerequisites
- **Python 3.9+**
- **PostgreSQL 12+**
- **Git**
- **Virtual Environment** (recommended)

### Quick Start

**1. Clone the repository:**
```bash
git clone https://github.com/sabir1080/SalPurFlask1080.git
cd SalPurFlask1080
```

**2. Create and activate virtual environment:**
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

**3. Install dependencies:**
```bash
pip install -r requirements.txt
```

**4. Configure environment variables:**

Create a `.env` file with your settings:
```env
# Flask
SECRET_KEY=your-secret-key-here
SECURITY_PASSWORD_SALT=your-salt-here

# Database (PostgreSQL)
DATABASE_URL=postgresql://user:pass@localhost/tradeflow

# Email (Gmail + App Password) — required for verification & password reset
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password

# Feature Flags
ALLOW_SIGNUP=false
```

**5. Create the first admin user:**

Self-registration is disabled by default. Create the first user via CLI:
```bash
flask create-user
```

**6. Run the development server:**
```bash
python app.py
# Or with Flask CLI:
flask run --port 5172 --debug
```

**7. Access the application:**
```
http://localhost:5172
```

---

## ⚙️ Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Flask session secret |
| `SECURITY_PASSWORD_SALT` | Yes | Password reset / email-verification token salt |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `MAIL_USERNAME` | For email features | Gmail account used to send verification/reset emails |
| `MAIL_PASSWORD` | For email features | Gmail App Password |
| `APP_TIMEZONE` | No | Business timezone (e.g. `Asia/Karachi`) |
| `ALLOW_SIGNUP` | No | Enable web-based self-registration (default `false`) |
| `COMPANY_NAME` | No | Display name shown in the app |
| `CURRENCY` | No | Currency symbol |

---

## 🛠️ Flask CLI Commands

| Command | Purpose |
|---|---|
| `flask create-user` | Create the first/any user account |
| `flask create-admin` | Create a user with the admin role |
| `flask seed-accounting` | Seed the Chart of Accounts and fixed-asset GL accounts |
| `flask seed-categories` | Import the built-in business-category catalog |
| `flask seed-data` | Wipe non-user data and build a realistic demo company (`--yes`, `--demo-user`) |
| `flask reset-db` | Drop and recreate all tables — destructive |
| `flask clear-transactions` | Clear transactional data while keeping users/settings/masters |
| `flask backup-db` | Export the database to a backup file |
| `flask fix-sequences` | Repair PostgreSQL sequence drift after manual data loads |
| `flask setup-production` | One-shot production bootstrap (accounts, categories, admin) |

---

## ✅ Testing

```bash
# Run the full suite
pytest

# Run one file
pytest tests/test_accounting.py

# Run one test
pytest tests/test_accounting.py::test_balance_sheet_totals

# Verbose
pytest -v
```

**721 tests** across **38 files**, covering accounting/ledger integrity, inventory & multi-warehouse transfers/reconciliation, POS, HR/Attendance/Leave/Payroll/Self-Service, authorization, audit logging, backup/restore, internationalization, and page-level regressions. Tests run in CI (GitHub Actions) on every push and pull request to `main`.

---

## 🚀 Deployment

TradeFlow currently deploys to two targets:

### Render (demo environment)
- `render.yaml` defines the service (`gunicorn app:app`) and environment variables
- Deploys automatically on push to `main`
- Backed by managed PostgreSQL

### Azure Web App (production)
- `.github/workflows/main_tradeflow.yml` deploys to Azure on every push to `main`
- See `AZURE_DEPLOYMENT.md` / `AZURE_FIX.md` for environment-specific notes

### Production Checklist
- [ ] PostgreSQL provisioned and `DATABASE_URL` set
- [ ] Strong, unique `SECRET_KEY` and `SECURITY_PASSWORD_SALT`
- [ ] HTTPS/SSL enabled
- [ ] `ALLOW_SIGNUP=false` unless self-registration is intended
- [ ] Regular `flask backup-db` scheduled
- [ ] Developer panel access reviewed for the target environment

---

## 📄 License

TradeFlow ERP is released under the **MIT License** — see [LICENSE](LICENSE) for the full text.

---

## 📞 Contact & Links

- 🌐 **Website**: https://tradeflow-website.sabir1212temp.workers.dev
- 💻 **Live Demo**: https://TradeFlow-Demo.onrender.com (`demo@demo.com` / `demo1234`)
- 🎥 **YouTube**: https://youtube.com/@TradeFlowBusinesSolutions
- 📱 **WhatsApp**: https://wa.me/+923453231545
- 📧 **Email**: tradeflowsoftwares@gmail.com
- 📘 **Facebook**: https://www.facebook.com/share/18AxHjtQct/
- 💼 **LinkedIn**: https://www.linkedin.com/in/sabir-shah-19470b351
- 💻 **GitHub**: https://github.com/sabir1080/SalPurFlask1080
- 🐛 **Issues**: [GitHub Issues](https://github.com/sabir1080/SalPurFlask1080/issues)

---

<div align="center">

**TradeFlow ERP — One Software. Complete Business Management.**

[⬆ back to top](#tradeflow-erp)

</div>
