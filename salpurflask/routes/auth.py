"""Authentication routes."""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from flask_login import login_required, login_user, logout_user, current_user
from datetime import datetime, timedelta, timezone
import secrets
import os
from sqlalchemy.exc import OperationalError

from salpurflask.extensions import db, pwd_context
from salpurflask.models import User, RateLimitHit, AuditLog, record_audit
from itsdangerous import URLSafeTimedSerializer

def _is_signup_allowed():
    """Check if user signup is allowed via environment variable."""
    return os.getenv("ALLOW_SIGNUP", "false").lower() in ("1", "true", "yes")

def _generate_verification_token(email):
    """Generate email verification token."""
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return serializer.dumps(email, salt=current_app.config["SECURITY_PASSWORD_SALT"])

def _verify_token(token, expiration=3600):
    """Verify email token."""
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    try:
        email = serializer.loads(token, salt=current_app.config["SECURITY_PASSWORD_SALT"], max_age=expiration)
        return email
    except Exception:
        return None

def _send_email(to_email, subject, body):
    """Import and call app's send_email function."""
    from app import send_email
    return send_email(to_email, subject, body)

def _check_rate_limit(key, max_attempts=5, window_seconds=300):
    """Import and call app's check_rate_limit function."""
    from app import check_rate_limit
    return check_rate_limit(key, max_attempts, window_seconds)

auth_bp = Blueprint('auth', __name__)

@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if not _is_signup_allowed():
        flash("Registration is disabled. Contact the administrator.", "warning")
        return redirect(url_for("auth.signin"))
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        if not name or not email or not password:
            flash("All fields are required!", "danger")
            return render_template("signup.html")
        if User.query.filter_by(email=email).first():
            flash(f"Email {email} is already registered!", "danger")
            return render_template("signup.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters!", "danger")
            return render_template("signup.html")
        hashed_password = pwd_context.hash(password)
        user = User(name=name, email=email, password=hashed_password, verified=False)
        try:
            db.session.add(user)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Database error: {str(e)}")
            flash("Failed to register user. Please try again.", "danger")
            return render_template("signup.html")
        token = _generate_verification_token(email)
        verification_url = url_for("auth.verify_email", token=token, _external=True)
        body = f"""
        Hello {name},

        Thank you for registering with {current_app.config['APP_NAME']}. Please click the link below to verify your email address:

        {verification_url}

        This link will expire in 1 hour.

        Regards,
        {current_app.config['APP_NAME']} Team
        """

        if _send_email(email, f"Verify Your Email - {current_app.config['APP_NAME']}", body):
           flash(f"A verification email has been sent to {email}. Please check your inbox.", "success")
        else:
            db.session.delete(user)
            db.session.commit()
            return render_template("signup.html")
        return redirect(url_for("auth.signin"))
    return render_template("signup.html")

@auth_bp.route("/signin", methods=["GET", "POST"])
def signin():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        try:
            if not _check_rate_limit(f"signin:{request.remote_addr}"):
                flash("Too many login attempts. Please try again in a few minutes.", "danger")
                return render_template("signin.html", just_reset=session.get("just_reset_email"))

            user = User.query.filter_by(email=email).first()
            if user:
                try:
                    db.session.refresh(user)
                    if pwd_context.verify(password, user.password):
                        if not user.verified:
                            flash(f"Please verify {email} before signing in. Check your inbox for the verification link.", "danger")
                            return render_template("signin.html", just_reset=session.get("just_reset_email"))
                        login_user(user)
                        session["user_id"] = user.id
                        session.pop("just_reset_email", None)
                        record_audit("login", "User", user.id, f"Signed in from {request.remote_addr}")
                        current_app.logger.info("Login OK: %s (role=%s) from %s", email, user.role, request.remote_addr)
                        flash("Signed in successfully!", "success")
                        return redirect(url_for("dashboard.index"))
                    current_app.logger.warning("Login FAILED (bad password): %s from %s", email, request.remote_addr)
                    flash("Invalid email or password!", "danger")
                except Exception as e:
                    current_app.logger.exception("Login error for %s: %s", email, e)
                    flash("Invalid email or password!", "danger")
            else:
                current_app.logger.warning("Login FAILED (unknown email): %s from %s", email, request.remote_addr)
                flash("Invalid email or password!", "danger")

        except OperationalError as db_err:
            current_app.logger.error("Database connection error during login: %s", str(db_err))
            flash("Database connection lost. Please try again in a few moments.", "danger")
            return render_template("signin.html", just_reset=session.get("just_reset_email"))
        except Exception as e:
            current_app.logger.exception("Unexpected error during login: %s", str(e))
            flash("An unexpected error occurred. Please try again.", "danger")
            return render_template("signin.html", just_reset=session.get("just_reset_email"))

    just_reset = session.get("just_reset_email")
    return render_template("signin.html", just_reset=just_reset)

@auth_bp.route("/signout")
@login_required
def signout():
    logout_user()
    session.pop("user_id", None)
    flash("Signed out successfully!", "success")
    return redirect(url_for("auth.signin"))

@auth_bp.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not _check_rate_limit(f"forgot_password:{request.remote_addr}"):
            flash("Too many attempts. Please try again in a few minutes.", "danger")
            return render_template("forgot_password.html")
        user = User.query.filter_by(email=email).first()
        generic_msg = "If that email is registered, a password reset link has been sent. Check your inbox and spam/junk folder."
        if not user:
            flash(generic_msg, "success")
        else:
            token = secrets.token_urlsafe(32)
            expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            body = (
                f"Dear User,\n\n"
                f"To reset your password, open this link:\n{reset_url}\n\n"
                f"This link expires in 1 hour. If you did not request this, ignore this email.\n\n"
                f"Regards,\n{current_app.config['APP_NAME']} Team"
            )
            if _send_email(email, f"Password Reset Request - {current_app.config['APP_NAME']}", body):
                user.reset_token = token
                user.reset_token_expiry = expiry
                db.session.commit()
                flash(generic_msg, "success")
                print(f"PASSWORD RESET LINK for {email}: {reset_url}")
                if current_app.debug:
                    flash(f"Development — reset link for {email}: {reset_url}", "info")
                return redirect(url_for("auth.signin"))
        return render_template("forgot_password.html")
    return render_template("forgot_password.html")

@auth_bp.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.reset_token_expiry or user.reset_token_expiry < datetime.now(timezone.utc).replace(tzinfo=None):
        flash("Invalid or expired reset link!", "danger")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        if len(password) < 6:
            flash("Password must be at least 6 characters!", "danger")
        elif password != confirm_password:
            flash("Passwords do not match!", "danger")
        else:
            user.password = pwd_context.hash(password)
            user.reset_token = None
            user.reset_token_expiry = None
            db.session.commit()
            db.session.refresh(user)
            if not pwd_context.verify(password, user.password):
                flash("Password could not be saved. Please try again.", "danger")
                return render_template("reset_password.html", token=token)
            session["just_reset_email"] = user.email
            flash(f"Password for {user.email} reset successfully! Please sign in with your new password.", "success")
            return redirect(url_for("auth.signin"))
    return render_template("reset_password.html", token=token)

@auth_bp.route("/verify_email/<token>")
def verify_email(token):
    email = _verify_token(token)
    if not email:
        flash("Invalid or expired verification link!", "danger")
        return redirect(url_for("auth.signin"))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash("User not found!", "danger")
        return redirect(url_for("auth.signin"))
    if user.verified:
        flash(f"Email {email} is already verified! Please sign in.", "success")
        return redirect(url_for("auth.signin"))
    try:
        user.verified = True
        db.session.commit()
        print(f"User verified: {user.email}")
        flash(f"Email {email} verified successfully! You can now sign in.", "success")
    except Exception as e:
        db.session.rollback()
        print(f"Verification error: {str(e)}")
        flash(f"Failed to verify email {email}. Please try again.", "danger")
    return redirect(url_for("auth.signin"))

@auth_bp.route("/resend_verification", methods=["GET", "POST"])
def resend_verification():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash(f"Email {email} not found!", "danger")
            return render_template("resend_verification.html")
        if user.verified:
            flash(f"Email {email} is already verified! Please sign in.", "success")
            return redirect(url_for("auth.signin"))
        token = _generate_verification_token(email)
        verification_url = url_for("auth.verify_email", token=token, _external=True)
        body = f"""
        Hello {user.name},

        Please click the link below to verify your email address:

        {verification_url}

        This link will expire in 1 hour.

        Regards,
        {current_app.config['APP_NAME']} Team
        """
        if _send_email(email, f"Verify Your Email - {current_app.config['APP_NAME']}", body):
            flash(f"A new verification email has been sent to {email}. Please check your inbox.", "success")
        return redirect(url_for("auth.signin"))
    return render_template("resend_verification.html")

@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        if not name:
            flash("Name cannot be empty!", "danger")
        else:
            try:
                current_user.name = name
                if password:
                    if len(password) < 6:
                        flash("Password must be at least 6 characters!", "danger")
                        return render_template("profile.html", user=current_user)
                    current_user.password = pwd_context.hash(password)
                    flash("Profile and password updated successfully!", "success")
                else:
                    flash("Profile updated successfully!", "success")
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(f"Error: {str(e)}", "danger")
        return render_template("profile.html", user=current_user)
    return render_template("profile.html", user=current_user)
