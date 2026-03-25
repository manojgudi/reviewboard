"""Flask application factory for the Review Board web service.

Run with:
    python app.py
"""

import os
import re
import logging
from datetime import timedelta
from io import BytesIO
from logging.handlers import RotatingFileHandler

from markupsafe import Markup
from markdown2 import markdown
from flask import Flask, redirect, url_for, send_from_directory, send_file, abort, request
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect

from models import db, User
from datetime import timezone as dt_tz, timedelta

CET_TZ = dt_tz(timedelta(hours=1))

# Extensions (accessible from other modules)
bcrypt = Bcrypt()

# Blueprint imports
from routes.auth import auth_bp
from routes.tickets import tickets_bp
from routes.reviews import reviews_bp
from routes.admin import admin_bp
from routes.annotations import annotations_bp
from routes.verdicts import verdicts_bp
from routes.ai_review import ai_review_bp


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=False)
    
    # Security: Enforce SECRET_KEY from environment variable (skip for testing)
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key and not (test_config and test_config.get("TESTING")):
        raise ValueError("SECRET_KEY environment variable must be set. "
                        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"")
    
    # SECURITY: Set SESSION_COOKIE_SECURE based on whether we're behind HTTPS
    # - Production (Cloudflare tunnel): X-Forwarded-Proto=https → secure cookies
    # - Test/Dev (HTTP): Use non-secure cookies for session to work
    #
    # NOTE: We must defer this check because request context doesn't exist at app creation time.
    # For TESTING mode, we default to False to avoid "Working outside of request context" errors.
    # In production with gunicorn, this runs within request context via before_request.
    if test_config and test_config.get("TESTING"):
        is_https = False
    else:
        # Use a try/except to handle cases where this runs outside request context
        try:
            is_https = request.headers.get('X-Forwarded-Proto', '').lower() == 'https'
        except RuntimeError:
            # Outside request context (e.g., gunicorn preload)
            is_https = os.getenv('X_FORWARDED_PROTO', 'http').lower() == 'https'
    
    app.config['SESSION_COOKIE_SECURE'] = is_https
    
    app.config.from_mapping(
        SECRET_KEY=secret_key,
        SQLALCHEMY_DATABASE_URI="sqlite:///"
            + os.path.join(app.root_path, "reviewboard.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        # SESSION_COOKIE_SECURE set above based on HTTPS detection
        SESSION_COOKIE_HTTPONLY=True,          # A05: HttpOnly flag prevents XSS cookie theft
        SESSION_COOKIE_SAMESITE='Lax',         # A05: CSRF protection via SameSite
        UPLOAD_FOLDER=os.path.join(app.root_path, "static", "uploads"),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,  # 20 MiB max upload
        WTF_CSRF_TIME_LIMIT=3600,              # 1 hour CSRF token lifetime
    )

    # ⚠️ Validate Ollama endpoint configuration
    ollama_endpoint = os.getenv('OLLAMA_ENDPOINT', '')
    if ollama_endpoint:
        if '/api/chat' in ollama_endpoint:
            print("⚠️  ERROR: OLLAMA_ENDPOINT uses /api/chat which returns streaming NDJSON!")
            print("⚠️  Please change to /v1/chat/completions (OpenAI-compatible)")
            print(f"⚠️  Current: {ollama_endpoint}")
            # Auto-fix by replacing
            fixed_endpoint = ollama_endpoint.replace('/api/chat', '/v1/chat/completions')
            os.environ['OLLAMA_ENDPOINT'] = fixed_endpoint
            print(f"⚠️  Auto-fixed to: {fixed_endpoint}")
        elif '/v1/chat/completions' not in ollama_endpoint:
            print(f"⚠️  WARNING: OLLAMA_ENDPOINT may be incorrect: {ollama_endpoint}")
            print("⚠️  Expected format: http://host:port/v1/chat/completions")

    # SAFETY: Force test database when TESTING flag is set AND no URI specified
    # This prevents tests from accidentally touching production data
    if test_config and test_config.get("TESTING"):
        if "SQLALCHEMY_DATABASE_URI" not in test_config:
            # Use a separate test database file
            db_path = os.path.join(os.path.dirname(app.root_path), "test_reviewboard.db")
            app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
            print(f"⚠️  SAFETY: TESTING mode - using isolated database: {db_path}")

    if test_config:
        app.config.update(test_config)

    # Ensure upload folder exists
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager = LoginManager(app)
    login_manager.login_view = "auth.login"
    csrf = CSRFProtect(app)

    # Register custom Jinja filter to convert newlines to <br> (with XSS protection)
    def nl2br(value):
        if not value:
            return ""
        # Escape HTML first, then replace newlines with <br>
        from markupsafe import escape
        escaped = escape(value)
        return Markup(escaped.replace('\n', '<br>'))
    app.jinja_env.filters['nl2br'] = nl2br

    # Register custom Jinja filter to render markdown (with XSS sanitization)
    def render_markdown(value):
        if not value:
            return ""
        import bleach
        # Allow safe markdown-compatible tags only
        allowed_tags = list(bleach.ALLOWED_TAGS) + ['p', 'br', 'strong', 'em', 'u', 'code', 'pre', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr']
        allowed_attributes = {'a': ['href', 'title'], 'th': ['align'], 'td': ['align']}
        html = markdown(value, extras=['fenced-code-blocks', 'tables', 'break-on-backslash'])
        # Strip dangerous protocols from href/src
        cleaned = bleach.clean(html, tags=allowed_tags, attributes=allowed_attributes, strip=True)
        return Markup(cleaned)
    app.jinja_env.filters['markdown'] = render_markdown

    # Register safe_color filter to prevent XSS via CSS injection
    def safe_color(value):
        """Validate and sanitize a hex color code to prevent CSS injection."""
        import re
        if not value:
            return "#0052CC"  # Default fallback
        # Only allow valid 6-digit hex colors
        if re.match(r'^#[0-9A-Fa-f]{6}$', value):
            return value
        # If invalid, return default
        return "#0052CC"
    app.jinja_env.filters['safe_color'] = safe_color

    # Register custom Jinja filter to display datetimes in CET
    def cet_time(value):
        """Convert UTC datetime to CET for display."""
        if not value:
            return ""
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt_tz.utc)
        cet = value.astimezone(CET_TZ)
        return cet
    app.jinja_env.filters['cet_time'] = cet_time

    # Register strftime filter for formatting dates
    def format_date(value, fmt='%Y-%m-%d %H:%M'):
        """Format a datetime object using strftime."""
        if not value:
            return ""
        return value.strftime(fmt)
    app.jinja_env.filters['strftime'] = format_date

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(tickets_bp, url_prefix="/tickets")
    app.register_blueprint(reviews_bp, url_prefix="/reviews")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(annotations_bp, url_prefix="/api/annotation")
    app.register_blueprint(verdicts_bp)
    app.register_blueprint(ai_review_bp)

    # ── Security Headers (A05: Security Misconfiguration) ──
    @app.after_request
    def add_security_headers(response):
        # Prevent MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Prevent clickjacking
        response.headers['X-Frame-Options'] = 'DENY'
        # XSS protection (legacy browsers)
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # Content Security Policy - restrict sources
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-src 'none'; "
            "object-src 'none'; "
            "base-uri 'self';"
        )
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Permissions policy (restrict features)
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        return response

    # ── AI Review Service Logging ──
    # Use root logger '' to capture all logging from child loggers like 'services.ai_reviewer'
    # This ensures the debug messages from ai_reviewer.py appear in our log file
    ai_logger = logging.getLogger('')  # Root logger to catch all
    ai_logger.setLevel(logging.DEBUG)
    
    # Also set the 'ai_review' logger specifically (used by worker.py)
    ai_review_logger = logging.getLogger('ai_review')
    ai_review_logger.setLevel(logging.DEBUG)
    
    # Remove any existing handlers to avoid duplicates
    ai_logger.handlers = []
    
    # Log to dedicated AI review log file
    try:
        ai_handler = RotatingFileHandler(
            os.path.join(app.root_path, 'ai_review.log'),
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        ai_handler.setFormatter(logging.Formatter(
            '[%(asctime)s] %(levelname)s [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        ai_handler.setLevel(logging.DEBUG)
        ai_logger.addHandler(ai_handler)
        ai_review_logger.addHandler(ai_handler)  # Also add to ai_review logger
        print(f"✅ AI review logging to: {os.path.join(app.root_path, 'ai_review.log')}")
    except Exception as e:
        print(f"⚠️  Failed to create AI log file handler: {e}")
    
    # Also log to stderr for real-time monitoring
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S'
    ))
    ai_logger.addHandler(console_handler)
    
    # ── Security Logging (A09: Security Logging) ──
    security_logger = logging.getLogger('security')
    security_logger.setLevel(logging.INFO)
    
    # Log to file
    try:
        handler = RotatingFileHandler(
            os.path.join(app.root_path, 'security.log'),
            maxBytes=5*1024*1024,  # 5MB
            backupCount=5
        )
        handler.setFormatter(logging.Formatter(
            '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
        ))
        security_logger.addHandler(handler)
    except Exception:
        pass  # Don't crash if logging fails
    
    @app.before_request
    def update_last_seen():
        """Update last_seen timestamp for authenticated users."""
        from models import db, User
        from flask_login import current_user
        if current_user.is_authenticated:
            from datetime import datetime, timezone
            current_user.last_seen = datetime.now(timezone.utc)
            db.session.commit()
    
    @app.before_request
    def log_security_event():
        """Log suspicious requests for security monitoring."""
        # Get real client IP (handles Cloudflare Tunnel)
        def get_real_client_ip():
            cf_ip = request.headers.get('CF-Connecting-IP')
            if cf_ip:
                return cf_ip
            x_forwarded = request.headers.get('X-Forwarded-For', '')
            if x_forwarded:
                return x_forwarded.split(',')[0].strip()
            return request.remote_addr
        
        client_ip = get_real_client_ip()
        
        # Log failed login attempts
        if request.endpoint == 'auth.login' and request.method == 'POST':
            # This is handled in the login route itself for access to form data
            pass
        
        # Log access to admin routes
        if request.path.startswith('/admin'):
            security_logger.info(
                f"Admin access: user={current_user.username if current_user.is_authenticated else 'anonymous'}, "
                f"path={request.path}, method={request.method}, ip={client_ip}"
            )
        
        # Log suspicious path traversal attempts
        if '..' in request.path or request.path.startswith('/uploads/../'):
            security_logger.warning(
                f"Potential path traversal: path={request.path}, ip={client_ip}"
            )

    # Home → board or login
    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("tickets.board"))
        return redirect(url_for("auth.login"))

    # Serve uploaded PDFs (with path traversal protection)
    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        # A08: Prevent path traversal attacks
        import os
        # Block any path with directory separators or parent references
        safe_filename = os.path.basename(filename)  # Only get the filename, not any path
        upload_folder = app.config["UPLOAD_FOLDER"]
        filepath = os.path.join(upload_folder, safe_filename)
        
        # Additional check: ensure the resolved path is within upload folder
        realpath = os.path.realpath(filepath)
        real_upload_folder = os.path.realpath(upload_folder)
        if not realpath.startswith(real_upload_folder + os.sep):
            abort(403)  # Forbidden
        
        if not os.path.exists(filepath):
            abort(404)
        return send_from_directory(app.config["UPLOAD_FOLDER"], safe_filename)

    # Serve a specific PDF page as an image (for image-based PDFs)
    @app.route("/pdf-page/<path:filename>/<int:page_num>")
    def pdf_page_image(filename, page_num):
        """Convert a PDF page to an image using pdftoppm."""
        import subprocess
        from io import BytesIO
        
        try:
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            if not os.path.exists(filepath):
                abort(404)
            
            # Use pdftoppm to convert page to PNG
            proc = subprocess.run(
                ['pdftoppm', '-png', '-r', '150', '-f', str(page_num), '-l', str(page_num), filepath, '-'],
                capture_output=True,
                timeout=30
            )
            
            if proc.returncode != 0 or not proc.stdout:
                print(f"pdftoppm error: {proc.stderr.decode()}")
                abort(500)
            
            return send_file(
                BytesIO(proc.stdout),
                mimetype='image/png'
            )
        except subprocess.TimeoutExpired:
            abort(504)
        except Exception as e:
            print(f"PDF page render error: {e}")
            abort(500)

    # Get PDF info (page count)
    @app.route("/pdf-info/<path:filename>")
    def pdf_info(filename):
        """Return PDF metadata including page count."""
        import subprocess
        from io import BytesIO
        
        try:
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            if not os.path.exists(filepath):
                abort(404)
            
            # Use pdfinfo to get page count
            proc = subprocess.run(
                ['pdfinfo', filepath],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if proc.returncode != 0:
                abort(500)
            
            # Parse output for page count
            import re
            match = re.search(r'Pages:\s*(\d+)', proc.stdout)
            if match:
                page_count = int(match.group(1))
                return {'pages': page_count}
            else:
                abort(500)
        except subprocess.TimeoutExpired:
            abort(504)
        except Exception as e:
            print(f"PDF info error: {e}")
            abort(500)

    # Create tables on startup
    with app.app_context():
        db.create_all()

    return app


if __name__ == "__main__":
    # Security: Only run with debug=True in development
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    create_app().run(host="0.0.0.0", port=8090, debug=debug_mode)
