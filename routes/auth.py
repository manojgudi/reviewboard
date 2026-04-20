"""Authentication routes: login, logout, register."""

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta

from models import db, User
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError

# Simple in-memory rate limiting for login attempts
_login_attempts = {}  # {ip: [timestamp, timestamp, ...]}
_MAX_ATTEMPTS = 5
_LOCKOUT_DURATION = timedelta(minutes=5)


def get_real_client_ip():
    """Get real client IP, accounting for Cloudflare and other proxies.
    
    Cloudflare Tunnel passes the real IP via CF-Connecting-IP header.
    X-Forwarded-For may contain multiple IPs (client, proxy1, proxy2).
    """
    # Cloudflare provides the real client IP
    cf_ip = request.headers.get('CF-Connecting-IP')
    if cf_ip:
        return cf_ip
    
    # Fallback: First IP in X-Forwarded-For (client)
    x_forwarded = request.headers.get('X-Forwarded-For', '')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    
    # Last resort: Direct connection IP
    return request.remote_addr

auth_bp = Blueprint("auth", __name__)


# ── Forms ──────────────────────────────────────────────────────────────
class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(3, 80)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log In")


# ── Helpers ────────────────────────────────────────────────────────────
def _get_bcrypt():
    """Import bcrypt from app module to avoid circular imports."""
    from app import bcrypt
    return bcrypt


# ── Routes ─────────────────────────────────────────────────────────────
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("tickets.board"))
    
    # A04/A07: Rate limiting - check if IP is locked out
    # Use get_real_client_ip() to handle Cloudflare Tunnel (SSL offloading)
    client_ip = get_real_client_ip()
    now = datetime.now()
    if client_ip in _login_attempts:
        # Clean old attempts outside lockout window
        _login_attempts[client_ip] = [
            t for t in _login_attempts[client_ip]
            if now - t < _LOCKOUT_DURATION
        ]
        if len(_login_attempts[client_ip]) >= _MAX_ATTEMPTS:
            flash("Too many login attempts. Please try again in 15 minutes.", "danger")
            return render_template("login.html", form=LoginForm())
    
    form = LoginForm()
    if form.validate_on_submit():
        # A04/A07: Record this attempt
        if client_ip not in _login_attempts:
            _login_attempts[client_ip] = []
        _login_attempts[client_ip].append(now)
        
        user = User.query.filter_by(username=form.username.data).first()
        if user and _get_bcrypt().check_password_hash(user.password_hash, form.password.data):
            # A09: Security Logging - Log successful login
            # Reset failed attempts on success
            _login_attempts[client_ip] = []
            import logging
            try:
                logging.getLogger('security').info(
                    f"Login success: user={user.username}, ip={client_ip}"
                )
            except Exception:
                pass
            login_user(user, remember=True)
            flash("Logged in successfully.", "success")
            return redirect(url_for("tickets.board"))
        # A09: Security Logging - Log failed login attempt
        import logging
        try:
            logging.getLogger('security').warning(
                f"Login failed: username={form.username.data}, ip={client_ip}"
            )
        except Exception:
            pass
        flash("Invalid credentials.", "danger")
    return render_template("login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Registration is disabled - only admins can create users via the admin panel."""
    flash("User registration is disabled. Please contact an administrator.", "warning")
    return redirect(url_for("auth.login"))



# ── Profile Settings Forms ────────────────────────────────────────────
class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current Password", validators=[DataRequired()])
    new_password = PasswordField("New Password", validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField("Confirm New Password", 
                                     validators=[DataRequired(), EqualTo("new_password")])
    submit = SubmitField("Change Password")


class PreferencesForm(FlaskForm):
    icon_color = StringField("Profile Icon Color", validators=[DataRequired()])
    default_review_color = StringField("Default Review Color", validators=[DataRequired()])
    submit = SubmitField("Save Preferences")


# ── Profile Routes ─────────────────────────────────────────────────────
@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """User profile settings: password change and preferences."""
    password_form = ChangePasswordForm()
    prefs_form = PreferencesForm()
    
    # Populate preferences form with current values
    if request.method == "GET":
        prefs_form.icon_color.data = current_user.icon_color
        prefs_form.default_review_color.data = current_user.default_review_color
    
    password_success = False
    prefs_success = False
    
    # Handle password change
    if password_form.validate_on_submit():
        if not _get_bcrypt().check_password_hash(current_user.password_hash, 
                                                  password_form.current_password.data):
            flash("Current password is incorrect.", "danger")
        else:
            current_user.password_hash = _get_bcrypt().generate_password_hash(
                password_form.new_password.data).decode("utf-8")
            db.session.commit()
            flash("Password changed successfully.", "success")
            password_success = True
    
    # Handle preferences update
    if prefs_form.validate_on_submit():
        import re
        prefs_success = True  # Start optimistic, set False on any failure
        
        # Validate icon_color is a proper hex color
        icon_color = prefs_form.icon_color.data
        if re.match(r'^#[0-9A-Fa-f]{6}$', icon_color):
            current_user.icon_color = icon_color
        else:
            flash("Invalid color format. Use hex format like #0052CC", "danger")
            prefs_success = False
        
        # Validate default_review_color
        valid_colors = ['yellow', 'green', 'blue', 'pink', 'orange']
        if prefs_form.default_review_color.data not in valid_colors:
            flash("Invalid review color. Choose yellow, green, blue, pink, or orange.", "danger")
            prefs_success = False
        
        if prefs_success:
            current_user.default_review_color = prefs_form.default_review_color.data
            db.session.commit()
            flash("Preferences saved.", "success")
            prefs_success = True
    
    return render_template("profile.html", 
                           password_form=password_form, 
                           prefs_form=prefs_form,
                           password_success=password_success,
                           prefs_success=prefs_success)
