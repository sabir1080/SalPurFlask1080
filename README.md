# TradeFlow ERP

> Inventory, point of sale, purchasing, double-entry accounting and optional HR/payroll for retail and trading businesses, in one Flask application.

---

## Overview

TradeFlow is a trading and accounting system for retail and trading businesses such as departmental stores and pharmacies. It covers stock in one or more warehouses, purchases and sales, a counter POS, customers and suppliers, cash and bank accounts, a double-entry general ledger, fixed assets, and (as optional modules) HR, attendance, leave and payroll. Every posted document writes a journal entry, and the financial statements are summed from the general ledger only.

What it is, and is not:

- **One company and one currency per installation.** It is **not** a multi-tenant SaaS: there is no tenant isolation, and multi-company and multi-currency are not supported.
- **Server-rendered Flask app** (Jinja templates, Bootstrap 5), backed by PostgreSQL.
- **English interface**; the in-app [User Manual](#documentation) and About page also have a Roman Urdu switch.

---

## Features

Everything below is implemented in the code; menu names are the ones shown in the application.

### Inventory and multi-warehouse stock
- Items (stock or service), categories, SKU and barcode, alternate units (for example box and strip), reorder levels and a low-stock email alert.
- Stock adjustments, a stock-movement log, an item ledger with CSV/Excel export, bulk import of items, customers and suppliers from CSV, Excel or JSON, and Stock Valuation at weighted-average cost.
- **Warehouses**: any number; purchases, sales, POS, adjustments, transfers and counts each name a warehouse, and stock is reported per warehouse.
- **Warehouse transfers**: Draft, Confirm, Cancel, Reverse. Transfers move quantity only and post no journal entry.
- **Inventory reconciliation** (physical count): Draft, Counted, Approved, Posted. The approver must be a different admin from the counter, and posting writes one netted journal entry.
- **Location access**: an admin can restrict a manager or staff user to chosen warehouses. A user with no grants is unrestricted.
- **Labels**: assign codes automatically and print Code128 barcode or QR labels.

### Batches and expiry
- Batch tracking and expiry are switched on per business category and per item.
- Purchases carry Batch No and Expiry Date; sales take stock from the batch that expires first (expired batches are skipped), and the POS lets a manager pick batches.
- An *Expiring / Expired Batches* report and Dashboard shortcuts.
- Known gap: purchase returns do not reverse batch quantities.

### Business categories, garments and footwear
- A built-in catalog of business categories (for example Grocery / FMCG, Medical / Pharmacy, Garments / Apparel, Footwear, Electronics, Hardware). An admin can enable or disable categories and add product fields under **Admin → Business Configuration**; an item must belong to an enabled category.
- Garments and footwear categories ship with fields such as Size, Color, Brand, Gender, Material and Style (footwear) and Style No., Fabric, Season and Pattern/Design (garments). These are category fields on the item, not a separate variant/SKU matrix.

### Sales and POS
- Sales are saved as **Draft** and then **Posted** (stock, invoice number, ledger and journal entry happen at post). Returns, quotations (convertible to sales), delivery challans, single and bulk customer receipts.
- **POS** (managers and admins): barcode or name lookup, choice of cash or bank account, change calculation, held bills, Walk-in Customer, and immediate posting. Receipts print on 80 mm paper.
- An admin can correct a posted sale or purchase with a recorded reason, or reverse it.

### Purchasing and suppliers
- Purchase orders (convertible to posted purchases), purchases (Draft then Posted), purchase returns, single and bulk supplier payments, supplier ledger with manual adjustments and exports.

### Customers, accounts, ledgers and reports
- Customer and supplier ledgers; the Customer list shows sales **net of sale returns**.
- Chart of accounts, manual journal entries (with reversal), fiscal years and periods (close and reopen), year-end close into Retained Earnings, tax codes, fixed assets with monthly depreciation and disposal, and hierarchical cash and bank accounts whose balances come straight from the ledger.
- Reports: Balance Sheet, Profit & Loss, Trial Balance, Cash Flow Statement, Stock Valuation, AP/AR Aging, Cash Book, Tax Summary, Subledger Reconciliation, and a Sales & Inventory page (gross and net sales, profit, purchases, returns, party balances).
- CSV and Excel export on the party lists, ledgers, stock report and Sales & Inventory tables (not on every report).

### HR, attendance, leave and payroll (optional modules)
Employees, departments, designations; daily attendance and monthly summary; leave types, allocations and approvals; payroll periods with salary structures, payslips, salary payments and employee advances, posted to the ledger; and an employee self-service **My Work** menu. These modules are **off by default** and are switched on from the Developer Tasks panel (HR first; the others depend on it).

### Users, roles and administration
- Three roles: **Staff**, **Manager**, **Admin**; HR and payroll use an additional permission map (for example only admins finalize and pay payroll).
- User management, warehouses, financial accounts, business configuration, tax codes, audit log, in-app notifications, JSON backup and restore, and two reset levels (delete transactions, delete everything), each requiring a typed confirmation phrase.
- Email-verified accounts with password reset, Argon2 password hashing (via passlib), CSRF protection, rate-limited sign-in, and an audit log of changes.
- A password-protected **Developer Tasks** panel (diagnostics, logs, environment view, optional modules) that is off unless enabled, and by default local-only.

### Printing and output
- **Print PDF** buttons use the browser's print dialog (print or "Save as PDF"). The application does not generate PDF files itself.
- Sale and purchase invoices open as printable pages (reversed documents show a REVERSED watermark); POS receipts use an 80 mm layout.

---

## Documentation

- **User Manual** (one box per facility, English and Roman Urdu): `/manual` in a running installation; source in [`templates/manual.html`](templates/manual.html).
- **About page**: `/about`; source in [`templates/about2.html`](templates/about2.html).
- **Developer guidance** and verified architecture notes: [CLAUDE.md](CLAUDE.md).
- Deployment and environment notes: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) and [ENV_FILE_GUIDE.md](ENV_FILE_GUIDE.md).

Both public pages are reachable without signing in.

---

## Live demo

A public demo has been hosted on Render at <https://tradeflow-demo.onrender.com>. It runs on a free tier that sleeps when idle, so the first load can take a while, and its availability and login details were **not re-verified** for this README. Demo login details are not published here; ask the maintainer.

---

## Technology stack

- **Python** with **Flask 3**, Flask-SQLAlchemy (SQLAlchemy 2), Flask-Login, Flask-WTF (CSRF), Flask-Compress, Flask-Paginate
- **PostgreSQL** (drivers `psycopg` and `psycopg2-binary` are installed; the plain `postgresql://` URL uses psycopg2). SQLite is only a local fallback when `DATABASE_URL` is unset.
- Jinja2 templates, Bootstrap 5, plain JavaScript; `openpyxl` (Excel), `python-barcode` and `qrcode` (labels and QR)
- `passlib[argon2]` / `argon2-cffi` and `bcrypt`, `itsdangerous` tokens, optional `sentry-sdk`
- `gunicorn` for production; pytest for tests
- Deployed with Render (`render.yaml`, `Procfile`); a GitHub Actions workflow for Azure also exists in the repo. The Render build pins Python 3.11; development here also runs on newer Python.

---

## Architecture

`app.py` is the application hub; feature code lives in the `salpurflask/` package.

```
app.py                     Flask app, configuration, blueprint registration,
                           many route registrations (app.add_url_rule), ledger
                           helpers, migrate_database(), CLI commands, reports,
                           expenses, quotations, admin users/accounts/backup

salpurflask/
├── auth.py                access decorators (verified/role/admin/manager)
├── models/                SQLAlchemy models by domain
├── routes/                auth, dashboard, admin_config, developer panel
├── inventory/ purchase/ sales/ supplier/ customer/ accounting/
├── hr/ attendance/ leave/ payroll/ selfservice/ notifications/
├── services/              business rules (transfers, reconciliation, payroll,
│                          feature flags, permissions, lookup, category catalog)
├── utils/                 helpers, import/export, time helpers
└── test_db_guard.py       test-database safety guard

templates/                 Jinja templates (flat folder, plus hr/, leave/,
                           payroll/, attendance/, selfservice/, admin/ ...)
static/                    css/, js/, icons, service worker; exported CSV/Excel
                           files are written here under fixed names
tests/                     pytest suite
migrations/                Alembic scaffold (not wired to startup)
```

Design points: documents are posted to a general ledger and reversed rather than deleted; invoice numbering is gapless per fiscal year; startup migrations only add nullable columns (no blocking DDL during zero-downtime deploys). See [CLAUDE.md](CLAUDE.md) for details.

---

## Local installation

**Prerequisites:** Python 3, PostgreSQL, Git.

```bash
git clone https://github.com/sabir1080/SalPurFlask1080.git
cd SalPurFlask1080

# Windows
python -m venv venv
venv\Scripts\activate
# macOS / Linux
python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

Create a `.env` file in the project root (there is no `.env.example` in the repository) with at least:

```env
SECRET_KEY=<random string>
SECURITY_PASSWORD_SALT=<random string>
DATABASE_URL=postgresql://<user>:<password>@localhost:5432/<database>
```

Add the mail settings if you want email verification and password reset to work, and `ALLOW_SIGNUP=true` only if you want web self-registration. Never commit `.env` files.

Create the first user (self-registration is off by default) and start the server:

```bash
flask create-admin        # or: flask create-user
python app.py             # or: flask run --port 5172 --debug
```

Open <http://localhost:5172>. On first boot the app creates its tables and runs `migrate_database()`.

### Environment variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY`, `SECURITY_PASSWORD_SALT` | Session secret and token salt (required) |
| `DATABASE_URL` | PostgreSQL URL; if unset the app falls back to a local SQLite file |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD` | Outgoing email (verification, reset, low-stock alert) |
| `ALLOW_SIGNUP` | Web self-registration (default `false`) |
| `APP_NAME`, `COMPANY_NAME`, `COMPANY_TAGLINE`, `DESIGNED_DEVELOPED` | Display text |
| `CURRENCY`, `APP_TIMEZONE`, `FISCAL_YEAR_START_MONTH` | Business settings; decide before entering data (changing the fiscal-year start later does not move existing fiscal years) |
| `ENABLE_DEVELOPER_PANEL`, `DEVELOPER_LOCALHOST_ONLY`, `DEVELOPER_PASSWORD_HASH`, `DEVELOPER_SESSION_TIMEOUT`, `DEVELOPER_READ_ONLY_MODE`, `DEVELOPER_SELECTIVE_MODE` | Developer Tasks panel |
| `SENTRY_DSN`, `LOG_LEVEL`, `FLASK_DEBUG`, `PORT`, `DEMO_MODE` | Operations and demo behaviour |
| `ANTHROPIC_API_KEY` | Read by the configuration module; no feature in the application currently uses it |
| `TRADEFLOW_TEST_DATABASE_URL` | Tests only (see below) |

### Flask CLI commands

| Command | Purpose |
|---|---|
| `flask create-user` / `flask create-admin` | Create a user / an admin |
| `flask seed-accounting`, `flask seed-categories` | Seed the chart of accounts / the business-category catalog |
| `flask setup-production` | One-shot production bootstrap |
| `flask backup-db` | Export a backup |
| `flask fix-sequences` | Repair PostgreSQL sequences after manual data loads |
| `flask seed-data` | Replace data with a demo company (**destructive**) |
| `flask clear-transactions`, `flask reset-db` | Remove transactions / drop all tables (**destructive**) |

The destructive commands only check that `DATABASE_URL` is set, **not which database it points to**. Check the host name before running them.

---

## Testing

The suite **deletes every row of every table** in the database it runs against, so it is guarded. `salpurflask/test_db_guard.py` refuses to run unless the database is local PostgreSQL named `tradeflow_test` on `127.0.0.1`/`localhost`, port 5432. The URL comes only from the `TRADEFLOW_TEST_DATABASE_URL` environment variable (never from `.env`), and the guard also checks the live connection before deleting anything. Do not bypass it.

```powershell
# PowerShell, one session
$env:TRADEFLOW_TEST_DATABASE_URL = "postgresql://<user>:<password>@127.0.0.1:5432/tradeflow_test"
pytest -q                                   # full suite (about 15 minutes)
pytest -q tests/test_accounting.py          # one file
```

- Use the plain `postgresql://` scheme. `postgresql+psycopg://` selects the psycopg3 driver, which currently fails a number of tests that compare string parameters with integer or date columns.
- Save output as UTF-8 if you redirect it (Windows PowerShell 5.1 `>` writes UTF-16).
- The last full run on the plain URL: 1532 passed, 1 warning (a third-party passlib/argon2 deprecation).
- A GitHub Actions workflow (`.github/workflows/ci.yml`) runs `pytest -q`; it does not set the test-database variable, so with the guard in place it will stop at start-up until it is given a local-style test database.

---

## Deployment notes

- `render.yaml` defines a web service running `gunicorn app:app`; `DATABASE_URL`, mail and API settings are set in the Render dashboard, never in the repo. The demo service tracks `main` in the Render dashboard, so **pushing to `main` can start a deployment**.
- `.github/workflows/main_tradeflow.yml` can deploy `main` to an Azure Web App; the Azure subscription noted in earlier documentation is described as inactive.
- Before production: provision PostgreSQL, set strong unique secrets, use HTTPS, keep `ALLOW_SIGNUP=false` unless intended, schedule backups (**Admin → Backup & Restore** or `flask backup-db`), and review Developer panel access.

---

## License

TradeFlow is released under the **MIT License**; see [LICENSE](LICENSE).

---

## Contact and links

- Website: https://tradeflow-website.sabir1212temp.workers.dev
- YouTube: https://youtube.com/@TradeFlowBusinesSolutions
- WhatsApp: https://wa.me/+923453231545
- Email: tradeflowsoftwares@gmail.com
- Facebook: https://www.facebook.com/share/18AxHjtQct/
- LinkedIn: https://www.linkedin.com/in/sabir-shah-19470b351
- GitHub: https://github.com/sabir1080/SalPurFlask1080
- Issues: [GitHub Issues](https://github.com/sabir1080/SalPurFlask1080/issues)

---

<div align="center">

**TradeFlow ERP**

[Back to top](#tradeflow-erp)

</div>
