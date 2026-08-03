"""Developer Tasks - Admin Panel Routes"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, jsonify
from functools import wraps
from sqlalchemy import text
import os
import sys
from pathlib import Path

dev_bp = Blueprint('developer', __name__, url_prefix='/developer')

# Correct password
DEVELOPER_PASSWORD = "_@sabir@_"
DEVELOPER_HINT = "-0sbr0-"

def developer_login_required(f):
    """Decorator to check if developer is logged in"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "developer_authenticated" not in session or not session["developer_authenticated"]:
            return redirect(url_for("developer.login"))
        return f(*args, **kwargs)
    return decorated_function

@dev_bp.route("/login", methods=["GET", "POST"])
def login():
    """Developer login page"""
    if request.method == "POST":
        password = request.form.get("password", "").strip()

        if password == DEVELOPER_PASSWORD:
            session["developer_authenticated"] = True
            flash("Developer access granted!", "success")
            return redirect(url_for("developer.dashboard"))
        else:
            flash(f"Incorrect password. Hint: {DEVELOPER_HINT}", "danger")
            return render_template("developer/login.html", hint=DEVELOPER_HINT)

    return render_template("developer/login.html")

@dev_bp.route("/logout")
def logout():
    """Developer logout"""
    session.pop("developer_authenticated", None)
    flash("Logged out from developer panel", "info")
    return redirect(url_for("developer.login"))

@dev_bp.route("/dashboard")
@developer_login_required
def dashboard():
    """Developer dashboard"""
    return render_template("developer/dashboard.html")

# ==================== DATABASE TOOLS ====================

@dev_bp.route("/database-manager")
@developer_login_required
def database_manager():
    """Launch database manager info page"""
    return render_template("developer/database_manager.html")

@dev_bp.route("/sync-schema")
@developer_login_required
def sync_schema():
    """Launch schema sync tool info page"""
    return render_template("developer/sync_schema.html")

@dev_bp.route("/data-display")
@developer_login_required
def data_display():
    """Launch data display manager info page"""
    return render_template("developer/data_display.html")

# ==================== APP DIAGNOSTICS ====================

@dev_bp.route("/diagnostics")
@developer_login_required
def diagnostics():
    """App diagnostics and health check"""
    from salpurflask.extensions import db

    diagnostics_info = {
        "app_name": current_app.config.get("APP_NAME", "Unknown"),
        "company": current_app.config.get("COMPANY_NAME", "Unknown"),
        "timezone": current_app.config.get("APP_TIMEZONE", "Unknown"),
        "currency": current_app.config.get("CURRENCY", "Unknown"),
        "debug_mode": current_app.debug,
    }

    try:
        # Database connection test
        with db.engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
        diagnostics_info["database_status"] = "Connected"
    except Exception as e:
        diagnostics_info["database_status"] = f"Failed: {str(e)[:100]}"

    try:
        # Count tables
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        diagnostics_info["table_count"] = len(inspector.get_table_names())
    except:
        diagnostics_info["table_count"] = "N/A"

    return render_template("developer/diagnostics.html", diagnostics=diagnostics_info)

# ==================== DATABASE STATS ====================

@dev_bp.route("/database-stats")
@developer_login_required
def database_stats():
    """Database statistics and record counts"""
    from salpurflask.extensions import db
    from sqlalchemy import inspect, func, text

    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()

        stats = {}
        for table in sorted(tables):
            try:
                count = db.session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                stats[table] = count
            except:
                stats[table] = "Error"

        return render_template("developer/database_stats.html", stats=stats)
    except Exception as e:
        flash(f"Error fetching stats: {str(e)}", "danger")
        return redirect(url_for("developer.dashboard"))

# ==================== ENVIRONMENT INFO ====================

@dev_bp.route("/environment")
@developer_login_required
def environment():
    """Environment variables and configuration"""
    env_info = {
        "app_name": current_app.config.get("APP_NAME", os.getenv("APP_NAME", "Not set")),
        "company_name": current_app.config.get("COMPANY_NAME", os.getenv("COMPANY_NAME", "Not set")),
        "timezone": os.getenv("APP_TIMEZONE", "Not set"),
        "currency": os.getenv("CURRENCY", "Not set"),
        "database_url": "***" if os.getenv("DATABASE_URL") else "Not set (using SQLite)",
        "allow_signup": os.getenv("ALLOW_SIGNUP", "false"),
        "fiscal_year_start": os.getenv("FISCAL_YEAR_START_MONTH", "Not set"),
        "mail_server": current_app.config.get("MAIL_SERVER", os.getenv("MAIL_SERVER", "Not set")),
        "mail_port": current_app.config.get("MAIL_PORT", os.getenv("MAIL_PORT", "Not set")),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }

    return render_template("developer/environment.html", env=env_info)

# ==================== LOG VIEWER ====================

@dev_bp.route("/logs")
@developer_login_required
def logs():
    """View application logs"""
    log_file = Path("logs/app.log")

    logs_data = []
    if log_file.exists():
        try:
            with open(log_file, "r") as f:
                logs_data = f.readlines()[-100:]  # Last 100 lines
        except:
            logs_data = ["Error reading log file"]
    else:
        logs_data = ["No log file found. Check logs/ directory"]

    return render_template("developer/logs.html", logs=logs_data)

# ==================== MIGRATION TOOLS ====================

@dev_bp.route("/migrations")
@developer_login_required
def migrations():
    """Database migration status and tools"""
    return render_template("developer/migrations.html")

# ==================== API ENDPOINTS ====================

@dev_bp.route("/api/check-connection", methods=["POST"])
@developer_login_required
def check_connection():
    """API endpoint to check database connection"""
    from salpurflask.extensions import db

    try:
        with db.engine.connect() as conn:
            conn.execute("SELECT 1")
        return jsonify({"status": "ok", "message": "Database connected"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@dev_bp.route("/api/table-count", methods=["GET"])
@developer_login_required
def table_count():
    """API endpoint to get table count"""
    from salpurflask.extensions import db
    from sqlalchemy import inspect

    try:
        inspector = inspect(db.engine)
        count = len(inspector.get_table_names())
        return jsonify({"status": "ok", "count": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
