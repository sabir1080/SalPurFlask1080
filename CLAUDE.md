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

### Single-file design
All application logic lives in `app.py` (~2490 lines). There is no blueprints/module split. Models, routes, helpers, and CLI commands are all in this one file. `models.py` exists in the repo but is unused — `app.py` defines all models directly.

### Database
SQLite at `instance/database.db`. Schema migrations run automatically on startup via `migrate_database()` (called inside `with app.app_context()`). This function uses SQLAlchemy `inspect` to add missing columns with raw `ALTER TABLE` — no Alembic is used at runtime (the `migrations/` folder is present but not wired to the app).

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
Two decorators:
- `@login_required` — Flask-Login, just checks session
- `@verified_required` — custom decorator wrapping `@login_required`, additionally checks `current_user.verified`

Most data routes use `@verified_required`. Email verification tokens use `itsdangerous.URLSafeTimedSerializer`.

### Templates
All templates extend `templates/base.html` (Bootstrap 5). A `context_processor` in `app.py` injects balance/payment helper functions into every template so they can be called directly from Jinja without being passed explicitly in each route's `render_template` call.

Custom Jinja filter `fmt_num` formats floats as `1,234,567.89`.

### CSV exports
Export routes write files directly to the `static/` folder, then serve them with `send_from_directory`. File names are fixed (e.g., `static/suppliers.csv`) so repeated exports overwrite the previous file.

### Payment methods
Defined as a module-level tuple: `PAYMENT_METHODS = ("Cash", "Bank", "Cheque", "Online")`. Used for validation and passed to templates via context processor.
