# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
# Activate virtual environment (Windows)
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run development server (port 5172)
python app.py

# Alternative via Flask CLI
flask run --port 5172 --debug
```

## Creating the First Admin User

Self-registration is disabled by default (`ALLOW_SIGNUP=false`). Create users via the CLI:

```bash
flask create-user
```

To enable web-based signup temporarily: set `ALLOW_SIGNUP=true` in `.env`.

## Environment Setup

Copy `.env.example` to `.env` and fill in:
- `SECRET_KEY` / `SECURITY_PASSWORD_SALT` — any random strings
- `MAIL_USERNAME` / `MAIL_PASSWORD` — Gmail + App Password (required for email verification and password reset)
- `ANTHROPIC_API_KEY` — from console.anthropic.com (for AI features)

## Architecture

### Purpose
TradeFlow is a trading, inventory and accounting system for retail businesses such as departmental stores and pharmacies (the repo is `sabir1080/SalPurFlask1080`; the first client is Yasir Departmental Store & Pharmacy). One company, one currency per installation. It runs stock across one or more warehouses, purchases and sales, a counter POS, customers and suppliers, cash and bank accounts, a double-entry general ledger, fixed assets, and optional HR / attendance / leave / payroll modules. Configurable *business categories* (a built-in catalog such as Grocery / FMCG, Medical / Pharmacy, Garments, Electronics, Hardware) add product-specific fields and optional batch/expiry tracking. The UI and the User Manual / About pages are in English, with a Roman Urdu switch on Manual and About.

### Code layout
(Corrected: this section used to say everything lives in one ~2,490-line `app.py` and that there is no module split. That is no longer true.)

- **`app.py` (~5,100 lines)** is still the hub. It builds the Flask app, reads configuration, picks the database URL, registers the blueprints, registers many routes with `app.add_url_rule(...)` (see below), defines `migrate_database()`, the ledger `sync_*` helpers, validation helpers, the CLI commands, and the routes for reports, expenses, quotations, admin users, financial accounts, audit, backup/restore and the two data resets.
- **`salpurflask/`** holds everything else:
  - `models/` — `models.py` (users, parties, items, purchases, sales, payments, returns, adjustments, expenses, cash/bank accounts, chart of accounts, journal entries, fixed assets, fiscal years, document sequences, audit log, import log, quotations, purchase orders, challans, POS holds, batches), `inventory_location.py` (Branch, Location, ItemStock, BatchStock, UserLocationAccess, Transfer, InventoryReconciliation, StockMovement), `business_config.py` (BusinessCategory, ProductField and related), `hr.py`, `attendance.py`, `leave.py`, `payroll.py`, `payroll_payment.py`, `notification.py`, `configuration.py` (AppConfiguration key/value, also stores module flags).
  - `routes/` — `auth.py`, `dashboard.py` (home, dashboard, Business Insights, `/about`, `/manual`), `admin_config.py` (Business Configuration), `developer.py` (Developer Tasks panel).
  - Feature packages with `routes.py`: `sales/` (blueprint `sales_bp`, includes POS), `purchase/`, `inventory/` (items, adjustments, labels, import, transfers, reconciliations, warehouse admin, location access), `customer/`, `supplier/`, `accounting/` (chart, journal, fixed assets, periods, tax codes, document reversal), `hr/`, `attendance/`, `leave/`, `payroll/`, `selfservice/` (`/my`), `notifications/`.
  - `services/` — business rules kept out of routes: `transfers.py`, `inventory_reconciliation.py`, `payroll_engine.py`, `payroll_accounting.py`, `config_service.py`, `category_catalog.py`, `lookup_service.py`, `notifications.py`, `self_service.py`, `feature_flags.py`, `hr_permissions.py`, `location_permissions.py`.
  - `utils/` — helpers, import/export, form restore, and `timeutil.utcnow()` (see Testing and warnings). `salpurflask/test_db_guard.py` (package root) is the test-database safety guard.
- **Routing**: blueprints are registered in `app.py` for `auth`, `dashboard`, `admin_config` (`/admin/config`), `sales`, `developer` (`/developer`), `hr` (`/hr`), `attendance`, `payroll`, `leave`, `selfservice` (`/my`) and `notifications`. Inventory, purchase, customer, supplier and accounting views are plain functions registered with `app.add_url_rule(...)` in `app.py` so their old endpoint names (`item`, `purchase`, `sale`, `report_stock`, ...) keep working in `url_for` and templates. Before adding a route, grep for the endpoint name in both places. The sales views are reachable by blueprint paths and by the legacy `add_url_rule` paths.
- There is no root-level `models.py`.

### Database
SQLite at `instance/database.db` is only a local fallback, used when `DATABASE_URL` is unset (see `app.py` lines ~45-63). It is legacy/unused now — the app runs on PostgreSQL. `instance/database.db` itself and any backup of it are not needed. Schema migrations run automatically on startup via `migrate_database()` (called inside `with app.app_context()`). This function uses SQLAlchemy `inspect` to add missing columns with raw `ALTER TABLE` — no Alembic is used at runtime (the `migrations/` folder is present but not wired to the app).

### Deployment / database hosting
Two separate Render services are deployed from this repo, each with its own `DATABASE_URL` set in the Render dashboard (`sync: false` in `render.yaml`, so the value is never stored in the repo):
- **development** service → Neon Postgres database named `development`
- **production** service → Neon Postgres database named `production`

`tradeflow_test` (a local Postgres database) is used only for running Python tests — it is not one of the app's real dev/prod databases and holds no real data. **The test database URL comes only from the `TRADEFLOW_TEST_DATABASE_URL` environment variable, never from `.env`** (corrected: this paragraph used to say it came from `DATABASE_URL` in `.env`). See *Testing and the database safety guard*.

Other project notes describe the same two Render deployments as a public demo (`tradeflow-demo`) and a client service (`salpurflask`); the names above are from the Render dashboard wording used here. Confirm which service points at which Neon database in the Render dashboard before any destructive operation — nothing in the code checks which database a command is aimed at. `.github/workflows/main_tradeflow.yml` separately deploys `main` to an Azure Web App.

### Models
- `User` — authentication only; `verified` bool gates access
- `Supplier` / `Customer` — have `opening_balance` and one-to-many to transactions
- `Item` → `Category` (FK), tracks `stock` and `reorder_level`
- `Purchase` / `Sale` — inventory transactions; each mutates `Item.stock` on create/edit/delete
- `SupplierPayment` / `CustomerPayment` — payment records with optional link to a specific bill
- `SupplierLedgerEntry` / `CustomerLedgerEntry` — append-only accounting ledger, recomputed as needed

### Ledger system (critical invariant)
Every write to `Purchase`, `Sale`, `SupplierPayment`, or `CustomerPayment` must be followed by the matching `sync_*` function, then `db.session.commit()`. The pattern is always:

```python
db.session.add(record)
db.session.flush()          # get the record ID before commit
sync_supplier_purchase(record)  # or sync_customer_sale, sync_supplier_payment, etc.
db.session.commit()
```

On delete, call `remove_*_ledger_entry(source_type, source_id)` then `recalculate_*_ledger(entity_id)` after commit.

`recalculate_*_ledger()` re-walks all entries in chronological order and recomputes `balance_after` from scratch — it must be called whenever any entry changes.

`source_type` values used in ledger entries: `"opening"`, `"purchase"`, `"payment"`, `"sale"`, `"receipt"`, `"adjustment"`.

### Auth / access control
Decorators live in `salpurflask/auth.py`:
- `@login_required` — Flask-Login, just checks session
- `@verified_required` — wraps `@login_required`, additionally checks `current_user.verified`
- `@role_required(*roles)`, `@manager_required` (admin or manager), `@admin_required` — verified user with the role; on refusal they flash "You do not have permission to access this page." and redirect

Three roles exist, stored on `User.role` (default `staff`): `staff`, `manager`, `admin`. `User.is_manager` is true for manager **and** admin; `User.is_admin` only for admin. In practice:
- **Staff** can view lists (suppliers, customers, items, purchases, sales), record supplier payments, customer receipts, bulk payments/receipts and purchase/sale returns, and view delivery challans. Several routes are `@verified_required` but refuse the POST inside the view for non-managers (`customer`, `supplier`, `item`, `category`, `purchase`, `sale`, and ledger adjustments).
- **Manager** adds/edits parties, items, purchases, sales; posts drafts; POS; adjustments, transfers, reconciliations (create/count/finalize/reopen/cancel); labels; bulk import (menu entry is manager-only, the route itself is `@verified_required`); purchase orders, quotations, expenses, journal entries, fixed assets, all reports, Dashboard.
- **Admin** reverses/corrects/deletes documents, approves and posts reconciliations, reverses transfers, closes fiscal years and opens/closes periods, manages users, warehouses, location access, financial accounts, business configuration, tax codes, chart of accounts, audit log, backup/restore/resets.
- **HR** uses a permission map (`salpurflask/services/hr_permissions.py`, `PERMISSIONS`, `permission_required`, `has_permission`) built on the same three roles. Payroll finalize/pay/cancel/reverse is admin-only because it writes to the ledger.
- **Optional modules** (`module_hr`, `module_attendance`, `module_leave`, `module_payroll`) are switched in the Developer Tasks panel (stored in `app_configuration`, **default OFF**; attendance, leave and payroll also require HR). Routes use `module_required`, menus use `module_enabled`. Turning a module off hides it; it never deletes rows.
- **Warehouse access** (`salpurflask/services/location_permissions.py`): admins are unrestricted; a non-admin with **zero** `UserLocationAccess` rows is also unrestricted; one or more rows restrict that user to exactly those warehouses. Do not flip the zero-rows default.
- **Self-service** (`/my`, menu "My Work") needs an `Employee` whose `user_id` is the signed-in user.
- Sign-in is rate limited (5 attempts / 5 minutes per IP, stored in the database); password reset links last 1 hour; email verification uses `itsdangerous.URLSafeTimedSerializer`. `ALLOW_SIGNUP` (default false) gates self-registration. The Developer panel needs `ENABLE_DEVELOPER_PANEL=true`, is localhost-only by default, uses a hashed password, locks for 15 minutes after 5 failures, and times out.

Known issue (found by reading the code, not by running it): the **My Profile → Change Password** form posts `current_password` / `new_password` / `confirm_password`, but `auth.profile()` reads `name` / `password`, so it answers "Name cannot be empty!" and does not change the password. Users can reset via Forgot Password or an admin can set it in Admin → Users. Fix it deliberately; the manual currently documents the problem.

### Templates
All templates extend `templates/base.html` (Bootstrap 5). A `context_processor` in `app.py` injects balance/payment helper functions into every template so they can be called directly from Jinja without being passed explicitly in each route's `render_template` call.

Custom Jinja filter `fmt_num` formats floats as `1,234,567.89`.

### CSV exports
Export routes write files directly to the `static/` folder, then serve them with `send_from_directory`. File names are fixed (e.g., `static/suppliers.csv`) so repeated exports overwrite the previous file.

### Payment methods
Defined as a module-level tuple: `PAYMENT_METHODS = ("Cash", "Bank", "Cheque", "Online")`. Used for validation and passed to templates via context processor.

It has a second job: it decides which `FinancialAccount` rows absorb legacy movements (see below). Adding or removing a member changes which cash/bank account untagged records fall into — don't edit it casually.

### Cash / bank accounts
`FinancialAccount` rows are cash or bank accounts. Each one has its own GL leaf (`gl_account_id`). Balances are never stored: `get_account_balance()` sums the journal lines on that leaf — the same place every report reads.

**Do not compute a cash balance any other way.** It used to be derived from the account's receipts, payments and expenses, which was true once and then quietly stopped being true: buying a fixed asset spends cash, selling one brings it in, and a manual journal entry can be posted straight to the bank, and none of those three is a receipt, a payment or an expense. The page read 420,000 while the balance sheet read 300,000 — one bank account, two numbers.

A movement (`SupplierPayment` / `CustomerPayment` / `Expense`) is *posted* to an account's leaf by `_resolve_financial_account()`, in one of two ways:

1. **Explicitly** — its nullable `account_id` FK points at the account. Every movement created through the UI is tagged this way (the `_account_select.html` partial renders the dropdown).
2. **Implicitly (legacy)** — `account_id IS NULL` and `payment_method == account.method`. This is how rows created before `account_id` existed keep counting, with no backfill.

Only the four seeded accounts carry a real `method` (one of `PAYMENT_METHODS`), so only they absorb untagged rows. Accounts created later get a synthetic `method` token from `new_account_method_token()` that matches no payment method — `method` is `NOT NULL UNIQUE` in the existing schema, and relaxing that would mean a table rebuild on SQLite and locking DDL on Postgres at startup (see the deploy note below).

Account names are unique case- and space-insensitively, enforced in the app by `account_name_taken()` — not by a DB constraint, for the same DDL reason.

### Migrations at startup
`migrate_database()` runs on every boot. Adding a **nullable** column is safe on both engines. Never add blocking DDL (`ALTER COLUMN TYPE`, adding a `UNIQUE` index, dropping a constraint) — during Render's zero-downtime deploy the old instance holds locks and the ALTER hangs until the deploy times out. The money-column `Float`→`Numeric` migration is already best-effort with a `lock_timeout` and skip-on-busy for exactly this reason.

### General ledger and document lifecycle
- The subledger `sync_*` functions above keep customer/supplier ledgers. The **general ledger** is a second, separate layer in `salpurflask/models/models.py`: `post_entry()` (validates and writes one balanced `JournalEntry`), `reverse_entry()`, `post_document(kind, doc)`, `reverse_document(kind, doc)`, `posted_entry()`, `assert_not_posted()`, `assert_not_numbered()`, `allocate_document_number()` (gapless numbering from `DocumentSequence`, restarting each fiscal year, rolled back with the transaction) and `PostingError`. Reports (Balance Sheet, P&L, Trial Balance, Cash Flow, cash/bank balances) are summed from journal lines only.
- `post_entry` refuses: no description, a date with no fiscal year, a **closed period** or closed fiscal year, negative or two-sided lines, inactive or heading accounts, **control accounts** (Receivables, Payables, Inventory cannot be posted to by hand), fewer than two lines, and unbalanced entries.
- **Purchase and Sale** have `status` `draft` / `posted` (`STATUS_DRAFT`, `STATUS_POSTED`). Saving creates a draft with no stock, ledger or journal effect. `post_purchase_route` / `post_sale_route` (manager) do the real work: stock (batches via FEFO for sales), invoice number, subledger sync, journal entry. A posted document cannot be edited or deleted (`assert_not_posted`); an admin can **Correct** it (`correct_purchase_route` / `correct_sale_route`, reason required, refused when returns exist or stock would go negative) or **Reverse** it (`reverse_document_route`, kinds: purchase, sale, payment, receipt, expense, purchase_return, sale_return, stock_adjustment; reversing a document with live payments needs an explicit confirm and may leave a customer/supplier credit).
- **Everything else posts on save**: supplier payments, customer receipts, expenses, purchase and sale returns, stock adjustments, POS checkout (it creates a posted Sale and a receipt immediately). The payment/receipt Edit buttons exist but the views call `assert_not_posted`, so they refuse; the way to correct one is Reverse and re-enter. Purchase-order conversion and quotation conversion create already-posted purchases and sales (a PO converts into the default warehouse).
- Opening balances (suppliers, customers, cash/bank accounts) are journal entries against *Opening Balance Equity* (`post_*_opening`, `_repost_opening` reverses and re-posts on edit). Item opening stock goes to the default warehouse.
- **Manual customer/supplier ledger adjustments** write only to the subledger (no journal entry), so they can show up as a difference on the Subledger Reconciliation report.
- The Customer List "Sales" figure is **net of sale returns** (`get_customer_net_sale`); the Reports page separates Gross Sale, Sale Returns and Net Sales. Do not call one the other.
- Fiscal years/periods: admin creates a year (12 periods), closes/reopens periods, and closes a year (profit moved to Retained Earnings, all periods locked). Fixed assets post a purchase journal, monthly depreciation (one entry per month, repeat refused) and disposal (gain/loss).
- Data resets (`_KEEP_ON_FACTORY_RESET = {"user"}`; the transaction reset also keeps suppliers, customers, items, categories, expense categories, chart, tax codes, fiscal years/periods, financial accounts and zeroes their opening balances and stock) require typed phrases `DELETE ALL DATA` / `DELETE ALL TRANSACTIONS`. A transaction reset reopens a closed fiscal year.

### Inventory, warehouses, batches and expiry
- Stock lives per warehouse in `ItemStock` (`Location`, default warehouse via `get_or_create_default_location`); `Item.stock` must equal the sum of its `ItemStock` rows. The **only** doors into stock are `item_add_stock()` / `item_remove_stock()` and their `*_batched()` variants in `models.py`; every change writes a `StockMovement`. Cost is weighted average (`Item.inventory_value`); a stock decrease that crosses the reorder level notifies admins and managers.
- Batch tracking is enabled per `BusinessCategory` (`batch_tracking_enabled`, `stock_expiry_enabled`) and per item (`batch_tracked`; `enable_item_batch_tracking` converts an existing item, admin only). `Batch` / `BatchStock` hold lots with expiry; sales use `fefo_allocate_batches()` (earliest expiry first, expired batches excluded unless `allow_expired=True`); the POS lets a manager choose batches. Purchases carry Batch No and Expiry Date and create the batch at **post** time (`PurchaseItem.pending_batch_no`). Purchase Returns do not reverse batches (known gap).
- **Transfers** (`services/transfers.py`): Draft → Confirmed (stock moves) → Reversed (admin), or Cancelled from Draft; no journal entry; statuses in `TRANSFER_STATUSES`.
- **Reconciliations** (`services/inventory_reconciliation.py`): Draft → Counted (finalize snapshots system quantity) → Approved (admin, must not be the person who counted) → Posted (admin; refused if system stock changed since counting). Cancel is allowed only from Draft/Counted. Posting writes one netted journal entry against Inventory / Stock Adjustment.
- `Unit` handling: items have a base unit plus `ItemUnit` alternates (`= Base Units` factor); stock always moves in base units; returns use the original line's unit.
- Labels and barcodes: `/labels` assigns codes and prints Code128 or QR labels; a barcode scanner simply types into the POS/item search box.

### HR, attendance, leave and payroll (optional modules)
Models in `models/hr.py`, `attendance.py`, `leave.py`, `payroll.py`, `payroll_payment.py`. Employee codes are issued by `next_employee_code()` and never edited. Leave: types (standard types cannot be deleted), allocations per year, requests Draft → Pending → Approved/Rejected/Cancelled, approval checks balance and refuses to change a finalized payroll period. Payroll (`services/payroll_engine.py`, `payroll_accounting.py`): needs an active `SalaryStructure` per employee (employees without one are skipped); period statuses Draft / Processing / Finalized / Cancelled; overtime, absence and advance recovery come from attendance, leave and `EmployeeAdvance`; finalizing posts one journal entry; salary payments (`PayrollPayment`) post a second entry from a chosen cash/bank account and can be reversed; a period with payments cannot be cancelled. `Employee.basic_salary/allowances/deductions` are informational only; payroll reads the structure. Notifications (`services/notifications.py`) are raised for low stock, leave requests/decisions and payroll finalize/cancel.

### Business categories and product fields
`BusinessCategory` + `ProductField` (admin → Business Configuration, `routes/admin_config.py`, `services/config_service.py`, built-in catalog in `services/category_catalog.py`) drive the Category dropdown and extra item fields; an item needs an **enabled** business category. The older `Category` table and `/category` page still exist but are not linked from any menu. `/admin/config/import-categories` is a JSON endpoint with no UI.

### Reports, exports and printed output
Statements (Balance Sheet, P&L, Trial Balance, Cash Flow, Cash Book, Tax Summary, AP/AR Aging, Subledger Reconciliation) are in `app.py` / `accounting/routes.py`; Stock Valuation, Stock Movements and Expiring Batches are in `inventory/routes.py`; the "Sales & Inventory" page is `reports()` in `app.py`. CSV and Excel exports write fixed-name files under `static/` and serve them (see *CSV exports*). **"Print PDF" is the browser's print dialog** (`static/js/print_report.js` clones a table into a print window); the app has no server-side PDF generator. Invoices (`invoice_sale.html`, `invoice_purchase.html`) are printable pages with a REVERSED watermark; POS receipts use `pos_receipt_thermal.html` (80 mm), with `?format=legacy` for the older layout.

### Testing and the database safety guard
- `pytest.ini` sets `pythonpath = .` and `testpaths = tests`. There are ~88 test files (about 1,530 tests; last full run on plain `postgresql://`: 1532 passed, 1 warning, about 15 minutes).
- **The suite deletes every row of every table.** `salpurflask/test_db_guard.py` therefore refuses to run against anything except local PostgreSQL `tradeflow_test` on `127.0.0.1`/`localhost`, port 5432. The URL must come from the `TRADEFLOW_TEST_DATABASE_URL` environment variable (never `.env`, never a default). It is checked in `tests/conftest.py` before `app` is imported, again in `app.py` when `pytest` is loaded, and against the live connection (`current_database()`, server address, port) before any row is deleted. Neon hosts, `tradeflow_dev` and `tradeflow` are rejected by name. Do not bypass it.
- Use the **plain `postgresql://` scheme** (psycopg2, SQLAlchemy's default). `postgresql+psycopg://` selects psycopg3, which sends typed parameters and fails 13 tests where a string is compared with an integer or timestamp column (for example `SupplierPayment.purchase_id == purchase_id` in `get_purchase_paid`, and `date_from` strings in `lookup_service.py`). Production and every `.env*` file use the plain scheme.
- PowerShell: `$env:TRADEFLOW_TEST_DATABASE_URL = "postgresql://<user>:<password>@127.0.0.1:5432/tradeflow_test"`, then `pytest -q`. Run focused files first; save output as UTF-8 (`python -X utf8 -m pytest ... > out.txt`), because Windows PowerShell 5.1 `>` writes UTF-16.
- `.github/workflows/ci.yml` runs `pytest -q` on Python 3.11 without `TRADEFLOW_TEST_DATABASE_URL`; with the guard in place that run exits at collection until CI is given a local-style test database. Verify before relying on CI.
- Warning hygiene: use `salpurflask.utils.timeutil.utcnow()` (naive UTC, a drop-in for the deprecated `datetime.utcnow()`) for column defaults and timestamps, and `db.session.get(Model, id)` instead of `Model.query.get(id)`. The one remaining warning is passlib 1.7.4 reading `argon2.__version__` (third-party; passlib is unmaintained, so plan a move to `argon2-cffi`'s `PasswordHasher` rather than pinning).

### Environment variables (names only, never commit values)
`DATABASE_URL`, `SECRET_KEY`, `SECURITY_PASSWORD_SALT`, `MAIL_SERVER`/`MAIL_PORT`/`MAIL_USERNAME`/`MAIL_PASSWORD`, `ANTHROPIC_API_KEY`, `ALLOW_SIGNUP`, `APP_NAME`, `COMPANY_NAME`, `COMPANY_TAGLINE`, `DESIGNED_DEVELOPED`, `CURRENCY`, `APP_TIMEZONE`, `FISCAL_YEAR_START_MONTH` (decide before entering data; changing it later does not move existing fiscal years), `DEMO_MODE` (cosmetic, e.g. disables password change; it does **not** gate destructive commands), `SENTRY_DSN`, `LOG_LEVEL`, `FLASK_DEBUG`, `PORT`, `ENABLE_DEVELOPER_PANEL`, `DEVELOPER_LOCALHOST_ONLY`, `DEVELOPER_PASSWORD_HASH`, `DEVELOPER_SESSION_TIMEOUT`, `DEVELOPER_READ_ONLY_MODE`, `DEVELOPER_SELECTIVE_MODE`, `TRADEFLOW_TEST_DATABASE_URL` (tests only). Several `.env*` files exist locally (`.env`, `.env.local`, `.env.render`, `.env.render-salpurflask`, `.env.render-tradeflow`); they hold real connection strings, so read them only to check a hostname and never echo a value.

### CLI commands (`flask <name>`)
`create-user`, `create-admin` (`--sync`), `seed-accounting`, `seed-categories`, `seed-data` (small demo data set), `setup-production`, `fix-sequences`, `backup-db`, `clear-transactions`, `reset-db` (needs `--yes` and a second flag for a deployed database). `reset-db`, `clear-transactions` and `seed-data` check only that `DATABASE_URL` is set, **not which database it points to** — verify the hostname by eye first.

### Git and deployment cautions
- Every commit must also reach `main`: the demo deployment runs off `main`. Pushing `main` can start a deployment (Render per the notes above, and the Azure workflow `main_tradeflow.yml`), so push only when the owner has approved it.
- Commit only the files you changed on purpose; never `git add -A` (`.env*` files, `pytest_full.log`, `logs/` and `instance/` are local).
- Documentation changes (`CLAUDE.md`, `templates/about2.html`, `templates/manual.html`) are not code, but the Manual and About pages are public pages served by `dashboard.manual()` / `dashboard.about()`; check they still render after editing.

### Known limitations and items that still need verification
- One company, one currency; no budgets, cost centres, recurring entries, FIFO/LIFO costing, server-side PDF generation or multi-company.
- `/category`, the legacy `new_account.html` / `edit_account` cash-bank pages and `/admin/config/import-categories` have no menu link or UI; use Admin → Business Configuration and Admin → Financial Accounts.
- The Change Password form bug above; payment/receipt Edit buttons that always refuse; bulk import is reachable by any verified user by URL although the menu greys it for staff.
- `reset-db`/`seed-data`/`clear-transactions` have no hostname allowlist; the Developer panel's Database Manager and Schema Sync are powerful and were not audited in this documentation pass.
- Unverified in this audit (read from code, not exercised in a browser): the exact behaviour of every flash message listed in the User Manual, deployment names on Render, and whether CI can run with the test-database guard.
