"""Developer Tasks - Admin Panel Routes with Full Security"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, jsonify
from functools import wraps
from sqlalchemy import text
from passlib.context import CryptContext
from datetime import datetime, timedelta
import os
import sys
import logging
from pathlib import Path

dev_bp = Blueprint('developer', __name__, url_prefix='/developer')
logger = logging.getLogger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=['argon2'], deprecated='auto')

# Track failed login attempts for rate limiting
failed_attempts = {}
MAX_ATTEMPTS = 5
LOCKOUT_DURATION = 15 * 60  # 15 minutes

def check_developer_enabled():
    """Check if developer panel is enabled for this environment"""
    enabled = os.getenv("ENABLE_DEVELOPER_PANEL", "false").lower() == "true"
    if not enabled:
        logger.warning("Developer panel access attempted but ENABLE_DEVELOPER_PANEL=false")
        return False
    return True

def check_localhost_only():
    """Check if developer panel should be restricted to localhost"""
    localhost_only = os.getenv("DEVELOPER_LOCALHOST_ONLY", "true").lower() == "true"
    if localhost_only and request.remote_addr not in ('127.0.0.1', 'localhost'):
        logger.warning(f"Developer panel access attempted from non-localhost: {request.remote_addr}")
        return False
    return True

def verify_password(password_hash, password):
    """Verify password against hash"""
    try:
        return pwd_context.verify(password, password_hash)
    except Exception as e:
        logger.error(f"Password verification error: {str(e)}")
        return False

def check_rate_limit(ip_addr):
    """Check if IP is rate-limited"""
    if ip_addr not in failed_attempts:
        return True

    attempts, lockout_time = failed_attempts[ip_addr]
    if attempts >= MAX_ATTEMPTS:
        if datetime.now() < lockout_time:
            return False
        else:
            del failed_attempts[ip_addr]
            return True
    return True

def record_failed_attempt(ip_addr):
    """Record failed login attempt"""
    if ip_addr not in failed_attempts:
        failed_attempts[ip_addr] = [1, datetime.now() + timedelta(seconds=LOCKOUT_DURATION)]
    else:
        attempts, _ = failed_attempts[ip_addr]
        failed_attempts[ip_addr] = [attempts + 1, datetime.now() + timedelta(seconds=LOCKOUT_DURATION)]
        logger.warning(f"Developer login failed attempt {failed_attempts[ip_addr][0]} from {ip_addr}")

def clear_failed_attempts(ip_addr):
    """Clear failed attempts on successful login"""
    if ip_addr in failed_attempts:
        del failed_attempts[ip_addr]

def developer_login_required(f):
    """Decorator to check if developer is logged in with full security"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if developer panel is enabled
        if not check_developer_enabled():
            return render_template("errors/403.html", message="Developer panel is disabled"), 403

        # Check localhost restriction
        if not check_localhost_only():
            logger.warning(f"Developer access blocked: non-localhost request from {request.remote_addr}")
            return render_template("errors/403.html", message="Developer panel is restricted to localhost"), 403

        # Check session
        if "developer_authenticated" not in session or not session["developer_authenticated"]:
            return redirect(url_for("developer.login"))

        # Check session timeout
        if "developer_login_time" in session:
            session_timeout = int(os.getenv("DEVELOPER_SESSION_TIMEOUT", "30"))
            login_time = session["developer_login_time"]
            # Handle both naive and aware datetimes
            if login_time.tzinfo is not None:
                # Aware datetime - use timezone-aware now()
                from datetime import timezone
                current_time = datetime.now(timezone.utc)
            else:
                # Naive datetime - use naive now()
                current_time = datetime.now()
            elapsed = (current_time - login_time).total_seconds() / 60
            if elapsed > session_timeout:
                session.pop("developer_authenticated", None)
                session.pop("developer_login_time", None)
                flash(f"Session expired after {session_timeout} minutes", "warning")
                return redirect(url_for("developer.login"))

        return f(*args, **kwargs)
    return decorated_function

@dev_bp.route("/login", methods=["GET", "POST"])
def login():
    """Developer login page with security checks"""
    # Check if developer panel is enabled
    if not check_developer_enabled():
        return render_template("errors/403.html", message="Developer panel is disabled"), 403

    # Check localhost restriction for local instances
    if not check_localhost_only():
        logger.warning(f"Developer login attempted from non-localhost: {request.remote_addr}")
        return render_template("errors/403.html", message="Developer panel is restricted to localhost"), 403

    ip_addr = request.remote_addr

    if request.method == "POST":
        # Check rate limiting
        if not check_rate_limit(ip_addr):
            remaining = int(os.getenv("DEVELOPER_SESSION_TIMEOUT", "30"))
            logger.warning(f"Developer login rate-limited from {ip_addr}")
            flash(f"Too many failed attempts. Try again in {remaining} minutes.", "danger")
            return render_template("developer/login.html"), 429

        password = request.form.get("password", "").strip()
        password_hash = os.getenv("DEVELOPER_PASSWORD_HASH", "")

        if not password_hash:
            logger.error("DEVELOPER_PASSWORD_HASH not configured in environment")
            flash("Server configuration error", "danger")
            return render_template("developer/login.html"), 500

        # Verify password
        if password and verify_password(password_hash, password):
            session["developer_authenticated"] = True
            session["developer_login_time"] = datetime.now()
            session.permanent = True
            session_timeout = int(os.getenv("DEVELOPER_SESSION_TIMEOUT", "30"))
            current_app.permanent_session_lifetime = timedelta(minutes=session_timeout)

            clear_failed_attempts(ip_addr)
            logger.info(f"Developer access granted to {ip_addr}")
            flash("Developer access granted! 🔓", "success")
            return redirect(url_for("developer.dashboard"))
        else:
            record_failed_attempt(ip_addr)
            logger.warning(f"Developer login failed from {ip_addr} - {failed_attempts[ip_addr][0]} attempts")
            flash("Incorrect password.", "danger")
            return render_template("developer/login.html")

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
    """Developer dashboard with security info"""
    read_only_mode = os.getenv("DEVELOPER_READ_ONLY_MODE", "false").lower() == "true"
    localhost_only = os.getenv("DEVELOPER_LOCALHOST_ONLY", "true").lower() == "true"

    # Determine environment (local vs render)
    db_url = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    is_production = db_url.startswith("postgresql")

    context = {
        "read_only_mode": read_only_mode,
        "localhost_only": localhost_only,
        "session_timeout": int(os.getenv("DEVELOPER_SESSION_TIMEOUT", "30")),
        "environment": "Production (Render)" if is_production else "Development (Local)",
        "is_production": is_production,
    }
    return render_template("developer/dashboard.html", **context)

# ==================== DATABASE TOOLS ====================

@dev_bp.route("/database-manager")
@developer_login_required
def database_manager():
    """Launch database manager info page"""
    return render_template("developer/database_manager.html")

@dev_bp.route("/sync-schema")
@developer_login_required
def sync_schema():
    """Launch schema sync tool info page (disabled in read-only mode)"""
    read_only_mode = os.getenv("DEVELOPER_READ_ONLY_MODE", "false").lower() == "true"
    if read_only_mode:
        flash("Schema sync is disabled in read-only mode", "warning")
        return redirect(url_for("developer.dashboard"))
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
    from salpurflask.models import AppConfiguration

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

    # Get all configurations from DB
    all_configs = AppConfiguration.query.all()
    configs_by_category = {}
    for config in all_configs:
        if config.category not in configs_by_category:
            configs_by_category[config.category] = []
        configs_by_category[config.category].append(config)

    return render_template("developer/environment.html", env=env_info, configs=configs_by_category)

@dev_bp.route("/environment/edit", methods=["POST"])
@developer_login_required
def environment_edit():
    """Update configuration values"""
    from salpurflask.models import AppConfiguration

    key = request.form.get("key")
    value = request.form.get("value")

    if not key or value is None:
        flash("Invalid configuration update", "danger")
        return redirect(url_for("developer.environment"))

    try:
        config = AppConfiguration.query.filter_by(key=key).first()
        if not config:
            flash(f"Configuration {key} not found", "danger")
        elif not config.editable:
            flash(f"Configuration {key} is read-only", "warning")
        else:
            config.value = str(value)
            config.updated_at = datetime.now()
            config.updated_by = session.get("developer_authenticated", "unknown")
            db.session.commit()

            # Update .env file
            try:
                env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
                _update_env_file(key, str(value), env_file)
            except Exception as e:
                logger.warning(f"Could not update .env file: {str(e)}")

            msg = f"Configuration '{key}' updated to '{value}'"
            if config.requires_restart:
                msg += " (requires app restart)"
                flash(msg + " ⚠️", "warning")
            else:
                flash(msg, "success")

    except Exception as e:
        logger.error(f"Error updating configuration: {str(e)}")
        flash(f"Error updating configuration: {str(e)}", "danger")

    return redirect(url_for("developer.environment"))

def _update_env_file(key, value, env_file):
    """Update or add configuration in .env file"""
    lines = []
    found = False

    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(env_file, "w") as f:
        f.writelines(new_lines)

# ==================== LOG VIEWER ====================

@dev_bp.route("/logs")
@developer_login_required
def logs():
    """View application logs with color coding"""
    log_file = Path("logs/app.log")

    logs_data = []
    if log_file.exists():
        try:
            with open(log_file, "r") as f:
                raw_logs = f.readlines()[-100:]  # Last 100 lines

            # Color-code logs based on level
            for line in raw_logs:
                if " WARNING " in line:
                    logs_data.append(f'<span style="color: #ffc107;">{line}</span>')
                elif " ERROR " in line:
                    logs_data.append(f'<span style="color: #dc3545;">{line}</span>')
                elif " INFO " in line:
                    logs_data.append(f'<span style="color: #17a2b8;">{line}</span>')
                else:
                    logs_data.append(f'<span style="color: #d4d4d4;">{line}</span>')
        except:
            logs_data = ["<span style='color: #dc3545;'>Error reading log file</span>"]
    else:
        logs_data = ["<span style='color: #ffc107;'>No log file found. Check logs/ directory</span>"]

    return render_template("developer/logs.html", logs=logs_data, is_html=True)

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
