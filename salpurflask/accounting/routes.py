"""Accounting and financial management routes."""

from decimal import Decimal, InvalidOperation
from datetime import datetime, date
from flask import render_template, redirect, url_for, flash, request, abort
from flask_login import current_user


def accounts():
    from app import (
        db, FinancialAccount, get_active_control_accounts, get_account_balance
    )

    control_accounts = get_active_control_accounts()
    standalone_accounts = FinancialAccount.query.filter_by(is_control=False, parent_id=None, is_active=True).order_by(FinancialAccount.name).all()

    rows = []
    total = Decimal("0")

    # Add control accounts with their subsidiaries
    for control in control_accounts:
        control_balance = Decimal("0")
        control_row_idx = len(rows)
        rows.append({
            "acct": control,
            "balance": None,
            "is_control": True,
            "level": 0
        })

        # Add subsidiaries and calculate control balance
        for subsidiary in control.children:
            if subsidiary.is_active:
                balance = get_account_balance(subsidiary)
                control_balance += Decimal(str(balance))
                rows.append({
                    "acct": subsidiary,
                    "balance": balance,
                    "is_control": False,
                    "level": 1
                })

        # Set control account balance to sum of children
        rows[control_row_idx]["balance"] = float(control_balance)
        total += control_balance

    # Add standalone accounts
    for standalone in standalone_accounts:
        balance = get_account_balance(standalone)
        rows.append({
            "acct": standalone,
            "balance": balance,
            "is_control": False,
            "level": 0
        })
        total += Decimal(str(balance))

    return render_template("accounts.html", rows=rows, total=float(total))


def new_account():
    from app import (
        db, FinancialAccount, account_name_taken, new_account_method_token,
        ensure_gl_account_for_financial, post_account_opening, record_audit
    )

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ob_str = request.form.get("opening_balance", "0").strip().replace(",", "")
        acc_type = request.form.get("account_type", "Bank").strip()
        if not name:
            flash("Account name is required!", "danger")
        elif account_name_taken(name):
            flash(f"An account named '{name}' already exists!", "danger")
        elif ob_str and not ob_str.replace("-", "", 1).replace(".", "", 1).isdigit():
            flash("Opening balance must be a valid number!", "danger")
        else:
            acct = FinancialAccount(
                name=name,
                method=new_account_method_token(),
                account_type=acc_type if acc_type in ("Cash", "Bank") else "Bank",
                opening_balance=float(ob_str or 0),
            )
            db.session.add(acct)
            db.session.flush()
            ensure_gl_account_for_financial(acct)
            post_account_opening(acct)
            db.session.commit()
            record_audit("create", "Account", acct.id, f"Account '{acct.name}' created ({acct.account_type})")
            flash(f"Account '{acct.name}' created. Select it when recording payments, receipts or expenses.", "success")
            return redirect(url_for("accounts"))
    return render_template("new_account.html")


def edit_account(id):
    from app import (
        db, FinancialAccount, account_name_taken, post_account_opening, record_audit
    )

    acct = db.session.get(FinancialAccount, id) or abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ob_str = request.form.get("opening_balance", "0").strip().replace(",", "")
        acc_type = request.form.get("account_type", "Cash").strip()
        if not name:
            flash("Account name is required!", "danger")
        elif account_name_taken(name, exclude_id=acct.id):
            flash(f"An account named '{name}' already exists!", "danger")
        elif ob_str and not ob_str.replace("-", "", 1).replace(".", "", 1).isdigit():
            flash("Opening balance must be a valid number!", "danger")
        else:
            acct.name = name
            acct.opening_balance = float(ob_str or 0)
            acct.account_type = acc_type if acc_type in ("Cash", "Bank") else "Cash"
            db.session.flush()
            post_account_opening(acct)
            db.session.commit()
            record_audit("update", "Account", acct.id, f"Account '{acct.name}' opening balance set to {float(acct.opening_balance):,.2f}")
            flash("Account updated successfully!", "success")
            return redirect(url_for("accounts"))
    return render_template("edit_account.html", acct=acct)


def account_ledger(id):
    from app import (
        db, FinancialAccount, account_transactions, get_account_balance, now_local
    )

    acct = db.session.get(FinancialAccount, id) or abort(404)
    txns = account_transactions(acct)
    running = float(acct.opening_balance or 0)
    ledger = []
    for t in txns:
        running += t["inflow"] - t["outflow"]
        ledger.append({**t, "balance": running})
    return render_template("account_ledger.html", acct=acct, ledger=ledger,
                           opening=float(acct.opening_balance or 0),
                           closing=get_account_balance(acct))




def report_balance_sheet():
    from app import accounting_position, parse_as_of

    return render_template("report_balance_sheet.html", p=accounting_position(parse_as_of()))


def report_trial_balance():
    from app import db, Account, gl_balances, parse_as_of

    as_of = parse_as_of()
    balances = gl_balances(as_of=as_of)
    rows, total_dr, total_cr = [], Decimal("0"), Decimal("0")
    for acct in Account.query.filter_by(is_group=False).order_by(Account.code).all():
        raw = balances.get(acct.id, Decimal("0"))
        if not raw:
            continue
        debit  = raw if raw > 0 else Decimal("0")
        credit = -raw if raw < 0 else Decimal("0")
        rows.append((acct, debit, credit))
        total_dr += debit
        total_cr += credit
    return render_template("report_trial_balance.html", rows=rows,
                           total_dr=total_dr, total_cr=total_cr, as_of=as_of)


def journal():
    from app import db, JournalEntry, Account, JournalLine
    from sqlalchemy import func

    entries = JournalEntry.query.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).all()
    summary = (db.session.query(
                    Account.code, Account.name,
                    func.sum(JournalLine.debit).label("debit"),
                    func.sum(JournalLine.credit).label("credit"))
               .join(Account, JournalLine.account_id == Account.id)
               .group_by(Account.code, Account.name)
               .order_by(Account.code).all())
    return render_template("journal.html", entries=entries, summary=summary)


def journal_new():
    from app import (
        db, postable_accounts, post_entry, PostingError, record_audit
    )

    accounts = postable_accounts()
    if request.method == "POST":
        date_str    = request.form.get("entry_date", "").strip()
        description = request.form.get("description", "").strip()
        reference   = request.form.get("reference", "").strip()
        account_ids = request.form.getlist("account_id[]")
        debits      = request.form.getlist("debit[]")
        credits     = request.form.getlist("credit[]")

        lines = [{"account_id": int(a), "debit": d.replace(",", ""), "credit": c.replace(",", "")}
                 for a, d, c in zip(account_ids, debits, credits) if a.strip().isdigit()]
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            flash("A valid date is required.", "danger")
            return render_template("journal_new.html", accounts=accounts)

        try:
            entry = post_entry(entry_date=entry_date, description=description,
                               reference=reference, lines=lines,
                               created_by_id=current_user.id)
            db.session.commit()
        except PostingError as e:
            db.session.rollback()
            flash(str(e), "danger")
            return render_template("journal_new.html", accounts=accounts)

        record_audit("create", "JournalEntry", entry.id,
                     f"Journal #{entry.id}: {entry.description} ({float(entry.total_debit):,.2f})")
        flash(f"Journal entry #{entry.id} posted.", "success")
        return redirect(url_for("journal"))
    return render_template("journal_new.html", accounts=accounts)


def journal_view(id):
    from app import db, JournalEntry

    entry = db.session.get(JournalEntry, id) or abort(404)
    reversal = JournalEntry.query.filter_by(reversal_of_id=entry.id).first()
    return render_template("journal_view.html", entry=entry, reversal=reversal,
                           total_dr=float(entry.total_debit),
                           total_cr=float(entry.total_credit))


def journal_reverse(id):
    from app import (
        db, JournalEntry, reverse_entry, unwind_asset_entry, PostingError, record_audit
    )

    entry = db.session.get(JournalEntry, id) or abort(404)
    try:
        reversal = reverse_entry(entry, created_by_id=current_user.id)
        unwind_asset_entry(entry)
        db.session.commit()
    except PostingError as e:
        db.session.rollback()
        flash(str(e), "danger")
        return redirect(url_for("journal_view", id=id))

    record_audit("reverse", "JournalEntry", entry.id,
                 f"Journal #{entry.id} reversed by #{reversal.id}")
    flash(f"Entry #{entry.id} reversed by #{reversal.id}. Both remain in the ledger.", "success")
    return redirect(url_for("journal_view", id=reversal.id))


def reverse_document_route(kind, id):
    from app import (
        db, Purchase, Sale, SupplierPayment, CustomerPayment, Expense,
        PurchaseReturn, SaleReturn, StockAdjustment,
        reverse_document, DOCUMENT_LABELS, record_audit
    )

    DOCUMENT_MODELS = {
        "purchase":         (lambda: Purchase,        "purchase"),
        "sale":             (lambda: Sale,            "sale"),
        "payment":          (lambda: SupplierPayment, "supplier_payment"),
        "receipt":          (lambda: CustomerPayment, "customer_receipt"),
        "expense":          (lambda: Expense,         "expenses"),
        "purchase_return":  (lambda: PurchaseReturn,  "purchase_return"),
        "sale_return":      (lambda: SaleReturn,      "sale_return"),
        "stock_adjustment": (lambda: StockAdjustment, "stock_adjustment"),
    }

    if kind not in DOCUMENT_MODELS:
        abort(404)
    model_fn, list_endpoint = DOCUMENT_MODELS[kind]
    doc = db.session.get(model_fn(), id) or abort(404)

    reversal = reverse_document(kind, doc)
    db.session.commit()

    label = DOCUMENT_LABELS[kind]
    record_audit("reverse", label.replace(" ", ""), doc.id,
                 f"{label} #{doc.id} reversed by journal entry #{reversal.id}")
    flash(f"{label} #{doc.id} reversed. Journal entry #{reversal.id} cancels it; "
          f"stock and ledgers have been corrected.", "success")
    return redirect(request.referrer or url_for(list_endpoint))


def fixed_assets():
    from app import db, FixedAsset, JournalEntry, now_local

    assets = FixedAsset.query.order_by(FixedAsset.acquisition_date.desc(),
                                       FixedAsset.id.desc()).all()
    live = [a for a in assets if a.status != "Disposed"]
    totals = {
        "cost":  sum((Decimal(str(a.cost)) for a in live), Decimal("0")),
        "accum": sum((a.accumulated for a in live), Decimal("0")),
    }
    totals["nbv"] = totals["cost"] - totals["accum"]
    last_run = (JournalEntry.query.filter_by(source_type="depreciation")
                .order_by(JournalEntry.entry_date.desc()).first())
    return render_template("fixed_assets.html", assets=assets, totals=totals,
                           last_run=last_run, today=now_local())


def fixed_asset_new():
    from app import (
        db, FixedAsset, postable_accounts, post_asset_acquisition,
        record_audit, DEPRECIATION_METHODS
    )

    accounts = postable_accounts()
    if request.method == "POST":
        name    = request.form.get("name", "").strip()
        tag     = request.form.get("tag", "").strip()
        method  = request.form.get("method", "Straight Line").strip()
        notes   = request.form.get("notes", "").strip()
        credit_raw = request.form.get("credit_account_id", "").strip()

        def _num(field, default="0"):
            return Decimal((request.form.get(field, default) or default).strip().replace(",", "") or default)

        try:
            acq_date = datetime.strptime(request.form.get("acquisition_date", "").strip(), "%Y-%m-%d")
            cost     = _num("cost")
            salvage  = _num("salvage_value")
            life     = int(request.form.get("useful_life_months", "0") or 0)
            rate     = _num("rate_percent")
        except (ValueError, ArithmeticError):
            flash("Check the date and the amounts — they must be valid numbers.", "danger")
            return render_template("fixed_asset_new.html", accounts=accounts,
                                   methods=DEPRECIATION_METHODS, form_data=request.form)

        error = None
        if not name:
            error = "The asset needs a name."
        elif cost <= 0:
            error = "Cost must be greater than zero."
        elif salvage < 0 or salvage >= cost:
            error = "Salvage value must be zero or more, and less than the cost."
        elif method == "Straight Line" and life <= 0:
            error = "Straight Line needs a useful life in months."
        elif method == "Reducing Balance" and rate <= 0:
            error = "Reducing Balance needs a yearly rate."
        elif not credit_raw.isdigit():
            error = "Choose the account that paid for the asset."
        if error:
            flash(error, "danger")
            return render_template("fixed_asset_new.html", accounts=accounts,
                                   methods=DEPRECIATION_METHODS, form_data=request.form)

        asset = FixedAsset(
            name=name, tag=tag or None, acquisition_date=acq_date, cost=cost,
            salvage_value=salvage, method=method,
            useful_life_months=life if method == "Straight Line" else None,
            rate_percent=rate if method == "Reducing Balance" else None,
            notes=notes or None)
        db.session.add(asset)
        db.session.flush()
        post_asset_acquisition(asset, int(credit_raw), created_by_id=current_user.id)
        db.session.commit()

        record_audit("create", "FixedAsset", asset.id,
                     f"Fixed asset '{asset.name}' acquired for {float(asset.cost):,.2f}")
        flash(f"Fixed asset '{asset.name}' recorded and posted.", "success")
        return redirect(url_for("fixed_assets"))

    return render_template("fixed_asset_new.html", accounts=accounts,
                           methods=DEPRECIATION_METHODS, form_data={})


def fixed_asset_view(id):
    from app import db, FixedAsset, FinancialAccount, now_local

    asset = db.session.get(FixedAsset, id) or abort(404)
    charges = sorted(asset.charges, key=lambda c: c.period_end)
    fin_accounts = (FinancialAccount.query.filter_by(is_active=True)
                    .order_by(FinancialAccount.name).all())
    return render_template("fixed_asset_view.html", asset=asset, charges=charges,
                           fin_accounts=fin_accounts, today=now_local())


def fixed_assets_depreciation():
    from app import db, run_depreciation, month_end, record_audit

    try:
        when = datetime.strptime(request.form.get("month", "").strip(), "%Y-%m")
    except ValueError:
        flash("Pick the month to depreciate.", "danger")
        return redirect(url_for("fixed_assets"))

    period_end = month_end(when)
    entry, total, count = run_depreciation(period_end, created_by_id=current_user.id)
    db.session.commit()

    record_audit("create", "Depreciation", entry.id,
                 f"Depreciation for {period_end:%B %Y}: {float(total):,.2f} over {count} asset(s)")
    flash(f"Depreciation for {period_end:%B %Y} posted — {float(total):,.2f} "
          f"across {count} asset(s).", "success")
    return redirect(url_for("fixed_assets"))


def fixed_asset_dispose(id):
    from app import (
        db, FixedAsset, FinancialAccount, post_asset_disposal, _cash_gl, record_audit
    )

    asset = db.session.get(FixedAsset, id) or abort(404)
    fa_raw = request.form.get("financial_account_id", "").strip()
    try:
        disposal_date = datetime.strptime(request.form.get("disposal_date", "").strip(), "%Y-%m-%d")
        proceeds = Decimal((request.form.get("proceeds", "0") or "0").strip().replace(",", "") or "0")
    except (ValueError, ArithmeticError):
        flash("Check the disposal date and the proceeds.", "danger")
        return redirect(url_for("fixed_asset_view", id=id))
    if proceeds < 0:
        flash("Proceeds cannot be negative.", "danger")
        return redirect(url_for("fixed_asset_view", id=id))

    fin = db.session.get(FinancialAccount, int(fa_raw)) if fa_raw.isdigit() else None
    if proceeds > 0 and fin is None:
        flash("Choose the account the money was received into.", "danger")
        return redirect(url_for("fixed_asset_view", id=id))

    entry, gain = post_asset_disposal(asset, disposal_date, proceeds,
                                      _cash_gl(fin) if fin else None,
                                      created_by_id=current_user.id)
    db.session.commit()

    outcome = (f"gain of {float(gain):,.2f}" if gain > 0
               else f"loss of {float(-gain):,.2f}" if gain < 0 else "no gain or loss")
    record_audit("update", "FixedAsset", asset.id,
                 f"Fixed asset '{asset.name}' disposed — {outcome}")
    flash(f"'{asset.name}' disposed — {outcome}. Journal entry #{entry.id} posted.", "success")
    return redirect(url_for("fixed_assets"))


def periods():
    from app import db, FiscalYear, now_local

    years = FiscalYear.query.order_by(FiscalYear.start_date.desc()).all()
    return render_template("periods.html", years=years, today=now_local().date())


def toggle_period(id):
    from app import db, AccountingPeriod, PostingError, record_audit

    period = db.session.get(AccountingPeriod, id) or abort(404)
    if period.fiscal_year.is_closed:
        raise PostingError(f"Fiscal year {period.fiscal_year.name} is closed; "
                           f"its periods cannot be reopened individually.")
    period.is_closed = not period.is_closed
    db.session.commit()
    state = "closed" if period.is_closed else "reopened"
    record_audit("update", "AccountingPeriod", period.id, f"Period {period.name} {state}")
    flash(f"{period.name} {state}.", "success")
    return redirect(url_for("periods"))


def close_year(id):
    from app import db, FiscalYear, close_fiscal_year, record_audit

    fy = db.session.get(FiscalYear, id) or abort(404)
    profit = close_fiscal_year(fy, created_by_id=current_user.id)
    db.session.commit()
    record_audit("close", "FiscalYear", fy.id,
                 f"Fiscal year {fy.name} closed, {float(profit):,.2f} moved to Retained Earnings")
    flash(f"Fiscal year {fy.name} closed. {float(profit):,.2f} "
          f"{'profit' if profit >= 0 else 'loss'} moved to Retained Earnings. "
          f"This cannot be undone.", "success")
    return redirect(url_for("periods"))


def new_fiscal_year():
    from app import db, seed_fiscal_year, record_audit

    try:
        year = int(request.form.get("year", "").strip())
    except ValueError:
        flash("Enter a valid year.", "danger")
        return redirect(url_for("periods"))
    if not 1900 <= year <= 2200:
        flash("Enter a valid year.", "danger")
        return redirect(url_for("periods"))
    created = seed_fiscal_year(year)
    if created:
        record_audit("create", "FiscalYear", 0, f"Fiscal year {year} created")
        flash(f"Fiscal year {year} created with 12 periods.", "success")
    else:
        flash(f"Fiscal year {year} already exists.", "warning")
    return redirect(url_for("periods"))


def report_reconciliation():
    from app import (
        db, Customer, Supplier, Item, gl_balances, get_account,
        get_customer_balance, get_supplier_balance, natural_balance,
        now_local, ACC_AR, ACC_AP, ACC_INVENTORY
    )
    from sqlalchemy import func

    balances = gl_balances()

    def gl_of(code):
        acct = get_account(code)
        return acct, natural_balance(acct, balances.get(acct.id, Decimal("0")))

    ar_acct, gl_ar   = gl_of(ACC_AR)
    ap_acct, gl_ap   = gl_of(ACC_AP)
    inv_acct, gl_inv = gl_of(ACC_INVENTORY)

    sub_ar = sum(Decimal(str(get_customer_balance(c.id))) for c in Customer.query.all())
    sub_ap = sum(Decimal(str(get_supplier_balance(s.id))) for s in Supplier.query.all())
    sub_inv = Decimal(str(db.session.query(func.sum(Item.inventory_value)).scalar() or 0))

    rows = [
        {"acct": ar_acct,  "gl": gl_ar,  "sub": sub_ar,
         "sub_label": "Sum of customer ledger balances"},
        {"acct": ap_acct,  "gl": gl_ap,  "sub": sub_ap,
         "sub_label": "Sum of supplier ledger balances"},
        {"acct": inv_acct, "gl": gl_inv, "sub": sub_inv,
         "sub_label": "Stock on hand × cost"},
    ]
    for r in rows:
        r["diff"] = Decimal(str(r["gl"])) - Decimal(str(r["sub"]))
        r["ok"] = abs(r["diff"]) < Decimal("0.01")

    customers = [(c, get_customer_balance(c.id)) for c in Customer.query.order_by(Customer.name).all()]
    suppliers = [(s, get_supplier_balance(s.id)) for s in Supplier.query.order_by(Supplier.name).all()]

    return render_template("report_reconciliation.html", rows=rows, as_of=now_local(),
                           customers=[c for c in customers if abs(c[1]) > 0.001],
                           suppliers=[s for s in suppliers if abs(s[1]) > 0.001],
                           all_ok=all(r["ok"] for r in rows))


def chart_of_accounts():
    from app import db, Account, gl_balances, natural_balance, SYSTEM_ACCOUNT_CODES, parse_as_of

    as_of = parse_as_of()
    accounts = Account.query.order_by(Account.code).all()
    balances = gl_balances(as_of=as_of)

    parent_of = {a.id: a.parent_id for a in accounts}
    def depth(a):
        d, pid = 1, a.parent_id
        while pid is not None:
            d, pid = d + 1, parent_of.get(pid)
        return d

    rolled = {a.id: balances.get(a.id, Decimal("0")) for a in accounts}
    for a in accounts:
        if a.is_group:
            continue
        pid = a.parent_id
        while pid is not None:
            rolled[pid] = rolled.get(pid, Decimal("0")) + balances.get(a.id, Decimal("0"))
            pid = parent_of.get(pid)

    rows = [(a, depth(a), natural_balance(a, rolled.get(a.id, Decimal("0")))) for a in accounts]
    return render_template("chart_of_accounts.html", rows=rows, as_of=as_of,
                           system_codes=SYSTEM_ACCOUNT_CODES)


def new_gl_account():
    from app import (
        db, Account, ACCOUNT_TYPES, record_audit, _validate_account_form, parse_cf_section
    )

    groups = Account.query.filter_by(is_group=True, is_active=True).order_by(Account.code).all()
    if request.method == "POST":
        code      = request.form.get("code", "").strip()
        name      = request.form.get("name", "").strip()
        type_     = request.form.get("type", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        is_group  = request.form.get("is_group") == "1"
        parent    = db.session.get(Account, int(parent_id)) if parent_id.isdigit() else None

        if parent is not None and not type_:
            type_ = parent.type

        error = _validate_account_form(code, name, type_, parent, is_group)
        if error:
            flash(error, "danger")
            return render_template("gl_account_new.html", groups=groups,
                                   types=ACCOUNT_TYPES, form_data=request.form)

        acct = Account(code=code, name=name, type=type_,
                       parent_id=parent.id if parent else None,
                       is_group=is_group, is_control=False,
                       cash_flow_section=parse_cf_section(request.form.get("cash_flow_section")))
        db.session.add(acct)
        db.session.commit()
        record_audit("create", "Account", acct.id,
                     f"Account {acct.code} {acct.name} created ({'heading' if is_group else acct.type})")
        flash(f"Account {acct.code} — {acct.name} created.", "success")
        return redirect(url_for("chart_of_accounts"))
    return render_template("gl_account_new.html", groups=groups, types=ACCOUNT_TYPES,
                           form_data={})


def edit_gl_account(id):
    from app import (
        db, Account, account_is_system, account_has_activity,
        ACCOUNT_TYPES, record_audit, _validate_account_form, parse_cf_section
    )

    acct = db.session.get(Account, id) or abort(404)
    groups = (Account.query.filter(Account.is_group.is_(True), Account.is_active.is_(True),
                                   Account.id != acct.id)
              .order_by(Account.code).all())
    is_system = account_is_system(acct)

    if request.method == "POST":
        name      = request.form.get("name", "").strip()
        code      = acct.code if is_system else request.form.get("code", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        is_active = request.form.get("is_active") == "1"
        parent    = db.session.get(Account, int(parent_id)) if parent_id.isdigit() else None

        error = _validate_account_form(code, name, acct.type, parent, acct.is_group,
                                       exclude_id=acct.id)
        if error is None and parent is not None and parent.id == acct.id:
            error = "An account cannot be its own parent."
        if error is None and not is_active:
            if account_has_activity(acct):
                error = "This account has journal entries; it cannot be deactivated."
            elif acct.is_group and any(c.is_active for c in acct.children):
                error = "This heading still has active sub-accounts."
        if error:
            flash(error, "danger")
            return redirect(url_for("edit_gl_account", id=id))

        acct.code, acct.name = code, name
        acct.parent_id = parent.id if parent else None
        acct.is_active = is_active
        acct.cash_flow_section = parse_cf_section(request.form.get("cash_flow_section"))
        db.session.commit()
        record_audit("update", "Account", acct.id, f"Account {acct.code} {acct.name} edited")
        flash(f"Account {acct.code} — {acct.name} updated.", "success")
        return redirect(url_for("chart_of_accounts"))

    return render_template("gl_account_edit.html", acct=acct, groups=groups, is_system=is_system)


def delete_gl_account(id):
    from app import (
        db, Account, account_is_system, account_has_activity,
        FinancialAccount, ExpenseCategory, TaxComponent, record_audit
    )

    acct = db.session.get(Account, id) or abort(404)
    if account_is_system(acct):
        flash(f"{acct.code} {acct.name} is used by the posting layer and cannot be deleted.", "danger")
    elif account_has_activity(acct):
        flash(f"{acct.code} {acct.name} has journal entries. Deactivate it instead.", "danger")
    elif acct.children:
        flash(f"{acct.code} {acct.name} still has sub-accounts.", "danger")
    elif FinancialAccount.query.filter_by(gl_account_id=acct.id).first():
        flash(f"{acct.code} {acct.name} belongs to a cash/bank account.", "danger")
    elif ExpenseCategory.query.filter_by(gl_account_id=acct.id).first():
        flash(f"{acct.code} {acct.name} is used by an expense category.", "danger")
    elif TaxComponent.query.filter((TaxComponent.input_account_id == acct.id) |
                                   (TaxComponent.output_account_id == acct.id)).first():
        flash(f"{acct.code} {acct.name} is used by a tax code.", "danger")
    else:
        code, name = acct.code, acct.name
        db.session.delete(acct)
        db.session.commit()
        record_audit("delete", "Account", id, f"Account {code} {name} deleted")
        flash(f"Account {code} — {name} deleted.", "success")
    return redirect(url_for("chart_of_accounts"))


def tax_codes():
    from app import db, TaxCode, TaxComponent, PostingError, record_audit
    from decimal import InvalidOperation

    if request.method == "POST":
        try:
            for comp in TaxComponent.query.all():
                raw = request.form.get(f"rate_{comp.id}")
                if raw is None:
                    continue
                rate = Decimal(str(raw).strip() or 0)
                if rate < 0 or rate > 100:
                    raise PostingError(f"Rate for {comp.name} must be between 0 and 100.")
                comp.rate = rate
            db.session.commit()
        except (InvalidOperation, PostingError) as e:
            db.session.rollback()
            flash(str(e) if isinstance(e, PostingError) else "Rates must be valid numbers.", "danger")
            return redirect(url_for("tax_codes"))
        record_audit("update", "TaxCode", 0, "Tax rates updated")
        flash("Tax rates updated.", "success")
        return redirect(url_for("tax_codes"))
    return render_template("tax_codes.html", codes=TaxCode.query.order_by(TaxCode.name).all())


def report_gst():
    from app import db, Purchase, Sale, now_local

    start_str = request.args.get("start", "")
    end_str   = request.args.get("end", "")
    today = now_local()
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d") if start_str else datetime(today.year, today.month, 1)
        end   = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_str else today
    except ValueError:
        start = datetime(today.year, today.month, 1)
        end   = today

    input_rows = []
    input_total = 0.0
    for pur in Purchase.query.filter(Purchase.date >= start, Purchase.date <= end).order_by(Purchase.date).all():
        tax = sum(float(pi.tax_amount or 0) for pi in pur.line_items)
        if tax > 0:
            input_rows.append({"id": pur.id, "date": pur.date,
                "party": pur.id_supplier.name, "tax": tax})
            input_total += tax

    output_rows = []
    output_total = 0.0
    for sal in Sale.query.filter(Sale.date >= start, Sale.date <= end).order_by(Sale.date).all():
        tax = sum(float(si.tax_amount or 0) for si in sal.line_items)
        if tax > 0:
            output_rows.append({"id": sal.id, "date": sal.date,
                "party": sal.id_customer.name, "tax": tax})
            output_total += tax

    net_gst = output_total - input_total

    return render_template("report_gst.html",
        start=start, end=end,
        input_rows=input_rows, input_total=input_total,
        output_rows=output_rows, output_total=output_total,
        net_gst=net_gst)
