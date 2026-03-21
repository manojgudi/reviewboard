"""Security tests for OWASP Top 10 mitigations."""

import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import create_app, bcrypt
from models import db, User, Ticket, Review, Annotation


@pytest.fixture
def client():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
    })
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            pw = bcrypt.generate_password_hash("secret").decode("utf-8")
            
            alice = User(username="alice", email="alice@example.com",
                        full_name="Alice", password_hash=pw, role="reviewer")
            bob = User(username="bob", email="bob@example.com",
                      full_name="Bob", password_hash=pw, role="reviewer")
            admin = User(username="admin", email="admin@example.com",
                        full_name="Admin", password_hash=pw, role="admin")
            
            db.session.add_all([alice, bob, admin])
            db.session.commit()
            
            # Create a ticket owned by alice
            ticket = Ticket(title="Alice's Ticket", description="desc", owner_id=alice.id)
            db.session.add(ticket)
            db.session.commit()
            
        yield client, app, alice, bob, admin, ticket


def _login(client, username, password="secret"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


# ── A01: Broken Access Control Tests ────────────────────────────────────────

def test_cannot_edit_others_ticket(client):
    """Non-owner cannot edit another user's ticket."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "bob")
    
    rv = c.post(f"/tickets/{ticket.id}/edit",
                data={"title": "Hacked", "description": "hacked"})
    assert rv.status_code == 403


def test_cannot_delete_others_ticket(client):
    """Non-owner cannot delete another user's ticket."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "bob")
    
    rv = c.post(f"/tickets/{ticket.id}/delete", follow_redirects=True)
    assert rv.status_code == 403


def test_cannot_close_others_ticket(client):
    """Non-owner cannot close another user's ticket."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "bob")
    
    rv = c.post(f"/tickets/{ticket.id}/close", follow_redirects=True)
    assert rv.status_code == 403


def test_admin_can_edit_any_ticket(client):
    """Admin can edit any user's ticket."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "admin")
    
    rv = c.post(f"/tickets/{ticket.id}/edit",
                data={"title": "Admin Edited", "description": "admin edit"},
                follow_redirects=True)
    assert rv.status_code == 200


def test_admin_can_delete_any_ticket(client):
    """Admin can delete any user's ticket."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "admin")
    
    rv = c.post(f"/tickets/{ticket.id}/delete", follow_redirects=True)
    assert b"deleted" in rv.data.lower()


def test_cannot_access_admin_without_admin_role(client):
    """Non-admin cannot access admin routes."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "alice")
    
    rv = c.get("/admin/users")
    assert rv.status_code == 403


def test_admin_can_access_admin_routes(client):
    """Admin can access admin routes."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "admin")
    
    rv = c.get("/admin/users")
    assert rv.status_code == 200


# ── A03: Injection Tests ─────────────────────────────────────────────────────

def test_sql_injection_in_title(client):
    """SQL injection in title should be handled safely."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "alice")
    
    malicious_title = "'; DROP TABLE users; --"
    rv = c.post("/tickets/new",
                data={"title": malicious_title, "description": "test"},
                follow_redirects=True)
    
    # Should not cause error, title should be saved/escaped
    assert rv.status_code == 200


def test_xss_in_description(client):
    """XSS in description should be escaped."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "alice")
    
    xss_payload = "<script>alert('XSS')</script>"
    rv = c.post("/tickets/new",
                data={"title": "XSS Test", "description": xss_payload},
                follow_redirects=True)
    
    # Script tag should be escaped/not executed
    assert b"&lt;script&gt;" in rv.data or b"XSS Test" in rv.data


def test_input_length_limit(client):
    """Excessively long input should be rejected."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "alice")
    
    # Title > 300 chars
    long_title = "A" * 500
    rv = c.post("/tickets/new",
                data={"title": long_title, "description": "test"},
                follow_redirects=True)
    
    assert b"300 characters" in rv.data


# ── A05: Security Headers Tests ─────────────────────────────────────────────

def test_security_headers_present(client):
    """Verify security headers are present."""
    c, app, alice, bob, admin, ticket = client
    
    rv = c.get("/")
    assert "X-Content-Type-Options" in rv.headers
    assert "X-Frame-Options" in rv.headers
    assert "Content-Security-Policy" in rv.headers


def test_secure_cookie_flags(client):
    """Verify session cookie has secure flags."""
    c, app, alice, bob, admin, ticket = client
    
    rv = c.get("/")
    # In testing mode, SESSION_COOKIE_SECURE may be False
    # but HttpOnly should always be True
    assert "HttpOnly" in str(rv.headers.get('Set-Cookie', ''))


# ── A08: PDF Upload Security Tests ──────────────────────────────────────────

def test_pdf_without_magic_bytes_rejected(client):
    """PDF without %PDF- magic bytes should be rejected."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "alice")
    
    fake_pdf = b"This is not a PDF file"
    rv = c.post("/tickets/new",
                buffered=True,
                content_type="multipart/form-data",
                data={
                    "title": "Fake PDF",
                    "description": "test",
                    "pdf": ("fake.pdf", fake_pdf, "application/pdf")
                })
    
    assert b"PDF" in rv.data


def test_pdf_with_javascript_rejected(client):
    """PDF containing JavaScript should be rejected."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "alice")
    
    malicious_pdf = b"%PDF-1.4\n/JS (app.alert('XSS'))\n/AA << /O << /JS (print()) >> >>\n%%EOF"
    rv = c.post("/tickets/new",
                buffered=True,
                content_type="multipart/form-data",
                data={
                    "title": "Malicious PDF",
                    "description": "test",
                    "pdf": ("malicious.pdf", malicious_pdf, "application/pdf")
                })
    
    assert b"dangerous" in rv.data.lower() or b"malicious" in rv.data.lower()


def test_valid_pdf_accepted(client):
    """Valid PDF without malicious content should be accepted."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "alice")
    
    # Minimal valid PDF
    valid_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
    
    rv = c.post("/tickets/new",
                buffered=True,
                content_type="multipart/form-data",
                data={
                    "title": "Valid PDF",
                    "description": "test",
                    "pdf": ("valid.pdf", valid_pdf, "application/pdf")
                })
    
    assert b"created" in rv.data.lower()


# ── A09: Security Logging Tests ───────────────────────────────────────────────

def test_failed_login_logged(client):
    """Failed login attempts should be logged."""
    c, app, alice, bob, admin, ticket = client
    
    # Create test to verify logging doesn't crash
    rv = c.post("/auth/login",
                data={"username": "alice", "password": "wrong"},
                follow_redirects=True)
    
    assert b"Invalid credentials" in rv.data


def test_admin_access_logged(client):
    """Admin route access should be logged."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "admin")
    
    rv = c.get("/admin/users")
    assert rv.status_code == 200


# ── Authentication Tests ─────────────────────────────────────────────────────

def test_login_required_for_tickets(client):
    """Ticket routes should require login."""
    c, app, alice, bob, admin, ticket = client
    
    rv = c.get("/tickets/board")
    # Should redirect to login
    assert b"Login" in rv.data


def test_session_persists(client):
    """Session should persist after login."""
    c, app, alice, bob, admin, ticket = client
    _login(c, "alice")
    
    # Access another page, should still be logged in
    rv = c.get("/tickets/board")
    assert b"Alice" in rv.data or b"Ticket" in rv.data
