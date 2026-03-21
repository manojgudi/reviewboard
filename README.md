# Review Board

> **⚠️ Vibecoded** - Built with cursor, coffee, and mild panic. Use at your own risk. Contributions welcome.

A self‑hosted web service for research labs to submit academic papers and run a lightweight peer‑review workflow with PDF annotation support.

## 🎯 Features

- **Kanban Board** - Tickets shown in columns based on status (`open`, `in_review`, `closed`)
- **PDF Upload & Viewer** - PDFs stored on disk, displayed with PDF.js, supports pagination and keyboard navigation
- **Click-to-Annotate** - Click anywhere on a PDF page to pin a review comment to that location
- **User Roles** - `admin` can manage users; `reviewer` can create tickets and add reviews
- **Reviews** - Plain-text comments with optional PDF page coordinates and highlighted text
- **User Preferences** - Custom avatar colors, default highlight colors, profile settings
- **Security Hardened** - OWASP Top 10 mitigations including rate limiting, CSRF protection, secure cookies, input validation, malicious PDF scanning

## 🛡️ Security Features

| OWASP Category | Protection |
|----------------|------------|
| **A01 - Broken Access Control** | Authorization checks on all sensitive routes, owner/admin permissions enforced |
| **A02 - Cryptographic Failures** | Bcrypt password hashing, SECRET_KEY enforcement from environment |
| **A03 - Injection** | Server-side input length validation, SQLAlchemy ORM (SQL injection safe), bleach HTML sanitization |
| **A04 - Insecure Design** | Login rate limiting (5 attempts/15 min lockout), account lockout after failed attempts |
| **A05 - Security Misconfiguration** | Security headers (CSP, X-Frame-Options, etc.), secure cookie flags |
| **A08 - Software Integrity** | PDF magic bytes validation, deep content scanning for malicious patterns |
| **A09 - Security Logging** | All admin access, login attempts, and suspicious requests logged to `security.log` |

## 🐍 Tech Stack

- **Python 3.10+**
- **Flask** (Flask-Login, Flask-SQLAlchemy, Flask-WTF, Flask-Bcrypt)
- **SQLite** - single-file database (`reviewboard.db`)
- **Bootstrap 5** (CDN)
- **PDF.js** (CDN) for PDF rendering
- **pdftoppm/pdfinfo** (system dependencies) for PDF page rendering

## 🚀 Deployment

### Prerequisites

```bash
# System dependencies (Debian/Ubuntu)
sudo apt-get install poppler-utils  # provides pdftoppm, pdfinfo
```

### Quick Start

```bash
# 1. Clone/copy the repository
git clone <repo-url> reviewboard
cd reviewboard

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Set required environment variables
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

# 5. Run the app
python app.py
```

The service will listen on **http://0.0.0.0:8090**.

### Using the Startup Script

```bash
# Auto-generates SECRET_KEY if not set
./start_app.sh
```

### Production Deployment

```bash
# 1. Generate a strong SECRET_KEY
export SECRET_KEY="your-production-secret-key-here"

# 2. Use a reverse proxy (nginx) for HTTPS in production
# See docs/nginx.conf.example for configuration

# 3. Run with gunicorn for production
pip install gunicorn
gunicorn -w 4 -b 127.0.0.1:8090 'app:create_app()'
```

### Docker Deployment (Optional)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y poppler-utils && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
RUN mkdir -p static/uploads

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8090", "--access-logfile", "-", "app:create_app()"]
```

## ⚙️ Configuration

All configuration is done via environment variables:

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `SECRET_KEY` | - | **Yes** | Session signing key (64-char hex). Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `FLASK_DEBUG` | `false` | No | Enable debug mode (never in production!) |
| `SQLALCHEMY_DATABASE_URI` | `sqlite:///reviewboard.db` | No | Database connection string |
| `PERMANENT_SESSION_LIFETIME` | 7 days | No | Session cookie lifetime |

## 📁 Project Structure

```
reviewboard/
├── app.py                    # Flask factory & entry point
├── models.py                 # SQLAlchemy models (User, Ticket, Review, Annotation)
├── requirements.txt          # Python dependencies
├── start_app.sh              # Startup script (auto-generates SECRET_KEY)
├── reviewboard.db            # SQLite database (created on first run)
├── security.log              # Security event log
├── routes/
│   ├── auth.py              # Authentication, rate limiting, profile settings
│   ├── tickets.py           # Board, ticket CRUD, PDF upload/validation
│   ├── reviews.py           # Reviews & annotations
│   ├── annotations.py       # Annotation API (save/update/delete)
│   └── admin.py             # User management (admin only)
├── templates/
│   ├── base.html            # Base template with nav
│   ├── login.html           # Login page
│   ├── profile.html         # User profile settings
│   ├── board.html           # Kanban board view
│   ├── ticket_new.html      # Create ticket form
│   ├── ticket_detail.html   # Ticket detail with PDF viewer & annotations
│   ├── ticket_edit.html     # Edit ticket form
│   ├── edit_review.html     # Edit review form
│   └── admin/
│       ├── users.html       # User management
│       ├── user_create.html # Create user (admin only)
│       └── user_edit.html   # Edit user
├── static/
│   ├── uploads/             # Uploaded PDFs (gitignored)
│   └── css/
├── migrations/              # Database migrations
├── tests/                   # pytest test suite
└── README.md
```

## 🔧 First-Time Setup

1. Open the app in your browser (default: http://localhost:8090)
2. Access the admin panel to create your first admin account
3. Log in and use the admin panel to create reviewers

## 🔧 Maintenance

### Database Backups

```bash
# Simple file backup (SQLite)
cp reviewboard.db reviewboard.db.backup-$(date +%Y%m%d)

# Automated backup script
#!/bin/bash
BACKUP_DIR="/backups/reviewboard"
mkdir -p "$BACKUP_DIR"
cp reviewboard.db "$BACKUP_DIR/reviewboard-$(date +%Y%m%d-%H%M%S).db"
find "$BACKUP_DIR" -name "reviewboard-*.db" -mtime +30 -delete  # Keep 30 days
```

### Logs

| File | Contents |
|------|----------|
| `security.log` | Admin access, login attempts, suspicious requests |
| `app.log` | Application errors |
| `nohup.out` | stdout/stderr when running with `start_app.sh` |

### Clearing Uploaded PDFs

```bash
# List PDFs by size
du -h static/uploads/* | sort -h

# Remove all PDFs (with confirmation)
rm -i static/uploads/*.pdf

# Note: Orphaned PDFs without tickets are automatically cleaned on ticket delete
```

### Resetting the Database

```bash
# Full reset (WARNING: destroys all data)
rm reviewboard.db
rm -rf static/uploads/*
# Restart the app - database and uploads will be recreated
python app.py
```

### Updating the Application

```bash
# Pull new code
git pull

# Activate venv and update dependencies
source .venv/bin/activate
pip install -r requirements.txt

# Restart the app
pkill -f "gunicorn"  # Stop old process (if using gunicorn)
# or
pkill -f "python app.py"  # Stop old process (if using Flask dev server)
./start_app.sh             # Start new process
```

## 🧪 Testing

```bash
# Install test dependencies
pip install pytest

# Run all tests
pytest -v

# Run specific test file
pytest tests/test_tickets.py -v

# Run with coverage
pytest --cov=. --cov-report=html
```

## 🐛 Troubleshooting

### PDF Upload Fails with "Malicious PDF"

The upload validator blocks PDFs containing potentially dangerous content. Legitimate PDFs with hyperlinks (`/URI`) or standard view actions (`/OpenAction`) may be incorrectly flagged.

Current blocked patterns (see `routes/tickets.py`):
- `/JS`, `/JavaScript` - JavaScript execution
- `/AA` - Auto-execute on events
- `/Launch` - External program execution
- `/SubmitForm`, `/GoToR`, `/ImportData` - Form submission
- `/EmbeddedFile` - Embedded files
- `/XFA` - Dynamic forms with scripting
- `%OS/` - OS-specific actions
- `/RichMedia` - Flash/media content

If your legitimate PDF is blocked, verify it's not malware, then edit the blocked patterns in `routes/tickets.py`.

### User Deletion Fails

If deletion fails with `NOT NULL constraint failed`, ensure you're running the latest code with cascade delete support.

### pdftoppm Errors

Ensure `poppler-utils` is installed:

```bash
# Debian/Ubuntu
sudo apt-get install poppler-utils

# Check if pdftoppm works
pdftoppm -v
```

### Rate Limited on Login

If you see "Too many login attempts", wait 15 minutes for the lockout to expire. The rate limit is 5 failed attempts per 15-minute window per IP address.

## 📄 License

MIT License - See LICENSE file

---

*Built with ❤️ and occasional screaming. No warranty, no guarantees, no support SLA.*
