"""Tests for ticket CRUD, board view, and security features."""

import pytest
import io
from app import create_app, bcrypt
from models import db, User, Ticket


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
            user = User(
                username="alice", email="alice@example.com",
                full_name="Alice", password_hash=pw, role="reviewer",
            )
            admin = User(
                username="admin", email="admin@example.com",
                full_name="Admin", password_hash=pw, role="admin",
            )
            db.session.add(user)
            db.session.add(admin)
            db.session.commit()
        yield client


def _login(client, username="alice", password="secret"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_board_requires_login(client):
    rv = client.get("/tickets/board", follow_redirects=True)
    assert b"Login" in rv.data


def test_create_ticket(client):
    _login(client)
    rv = client.post(
        "/tickets/new",
        data={"title": "My Paper", "description": "Please review"},
        follow_redirects=True,
    )
    assert b"Ticket created" in rv.data
    assert b"My Paper" in rv.data


def test_create_ticket_with_deadline(client):
    _login(client)
    rv = client.post(
        "/tickets/new",
        data={
            "title": "Paper with Deadline",
            "description": "Has a deadline",
            "deadline": "2026-12-31T23:59"
        },
        follow_redirects=True,
    )
    assert b"Ticket created" in rv.data


def test_create_ticket_no_title(client):
    """Test that empty title is rejected."""
    _login(client)
    rv = client.post(
        "/tickets/new",
        data={"title": "", "description": "No title"},
        follow_redirects=True,
    )
    assert b"required" in rv.data.lower()


def test_create_ticket_title_too_long(client):
    """Test that excessively long title is rejected."""
    _login(client)
    long_title = "A" * 500  # Exceeds 300 char limit
    rv = client.post(
        "/tickets/new",
        data={"title": long_title, "description": "Too long"},
        follow_redirects=True,
    )
    assert b"300 characters" in rv.data


def test_create_ticket_description_too_long(client):
    """Test that excessively long description is rejected."""
    _login(client)
    long_desc = "B" * 15000  # Exceeds 10000 char limit
    rv = client.post(
        "/tickets/new",
        data={"title": "Valid Title", "description": long_desc},
        follow_redirects=True,
    )
    assert b"10000 characters" in rv.data


def test_board_shows_ticket(client):
    _login(client)
    client.post("/tickets/new", data={"title": "Board Test", "description": ""}, follow_redirects=True)
    rv = client.get("/tickets/board")
    assert b"Board Test" in rv.data


def test_ticket_detail(client):
    _login(client)
    client.post("/tickets/new", data={"title": "Detail Test", "description": "desc"}, follow_redirects=True)
    with client.application.app_context():
        t = Ticket.query.first()
        tid = t.id
    rv = client.get(f"/tickets/{tid}")
    assert rv.status_code == 200
    assert b"Detail Test" in rv.data


def test_ticket_detail_page_param(client):
    """Test that page parameter is validated."""
    _login(client)
    client.post("/tickets/new", data={"title": "Page Test", "description": "desc"}, follow_redirects=True)
    with client.application.app_context():
        t = Ticket.query.first()
        tid = t.id
    
    # Valid page
    rv = client.get(f"/tickets/{tid}?page=1")
    assert rv.status_code == 200
    
    # Invalid page (negative) should default to 1
    rv = client.get(f"/tickets/{tid}?page=-5")
    assert rv.status_code == 200


def test_edit_ticket_owner(client):
    """Test that ticket owner can edit."""
    _login(client)
    client.post("/tickets/new", data={"title": "Edit Test", "description": "original"}, follow_redirects=True)
    with client.application.app_context():
        t = Ticket.query.first()
        tid = t.id
    
    rv = client.post(
        f"/tickets/{tid}/edit",
        data={"title": "Edited Title", "description": "updated"},
        follow_redirects=True,
    )
    assert b"updated" in rv.data.lower() or b"success" in rv.data.lower()


def test_edit_ticket_non_owner_forbidden(client):
    """Test that non-owner cannot edit ticket."""
    # Login as alice and create ticket
    _login(client)
    client.post("/tickets/new", data={"title": "Alice's Ticket", "description": "original"}, follow_redirects=True)
    
    # Logout and login as reviewer
    client.get("/auth/logout", follow_redirects=True)
    _login(client, username="reviewer")
    
    with client.application.app_context():
        t = Ticket.query.first()
        tid = t.id
    
    rv = client.post(
        f"/tickets/{tid}/edit",
        data={"title": "Hacked Title", "description": "hacked"},
        follow_redirects=True,
    )
    assert rv.status_code == 403


def test_delete_ticket_owner(client):
    """Test that ticket owner can delete."""
    _login(client)
    client.post("/tickets/new", data={"title": "Delete Test", "description": "to delete"}, follow_redirects=True)
    with client.application.app_context():
        t = Ticket.query.first()
        tid = t.id
    
    rv = client.post(f"/tickets/{tid}/delete", follow_redirects=True)
    assert b"deleted" in rv.data.lower()


def test_close_ticket(client):
    """Test closing a ticket."""
    _login(client)
    client.post("/tickets/new", data={"title": "Close Test", "description": ""}, follow_redirects=True)
    with client.application.app_context():
        t = Ticket.query.first()
        tid = t.id
    
    rv = client.post(
        f"/tickets/{tid}/status",
        data={"status": "closed"},
        follow_redirects=True,
    )
    assert b"closed" in rv.data.lower() or b"success" in rv.data.lower()


def test_reopen_ticket(client):
    """Test reopening a closed ticket."""
    _login(client)
    client.post("/tickets/new", data={"title": "Reopen Test", "description": ""}, follow_redirects=True)
    with client.application.app_context():
        t = Ticket.query.first()
        tid = t.id
    
    # Close first
    client.post(f"/tickets/{tid}/status", data={"status": "closed"}, follow_redirects=True)
    
    # Then reopen
    rv = client.post(f"/tickets/{tid}/reopen", follow_redirects=True)
    assert b"reopened" in rv.data.lower()


def test_upload_malicious_pdf_rejected(client):
    """Test that PDFs with malicious content are rejected."""
    _login(client)
    
    # Create a PDF with JavaScript content
    malicious_pdf = b"%PDF-1.4\n/JS (app.alert('XSS'))\n%%EOF"
    
    rv = client.post(
        "/tickets/new",
        data={
            "title": "Malicious PDF Test",
            "description": "Upload attempt"
        },
        buffered=True,
        content_type="multipart/form-data",
        files={"pdf": ("malicious.pdf", malicious_pdf, "application/pdf")}
    )
    assert b"dangerous" in rv.data.lower() or b"malicious" in rv.data.lower()


def test_upload_non_pdf_rejected(client):
    """Test that non-PDF files are rejected."""
    _login(client)
    
    rv = client.post(
        "/tickets/new",
        data={
            "title": "Non-PDF Test",
            "description": "Upload attempt"
        },
        buffered=True,
        content_type="multipart/form-data",
        files={"pdf": ("malicious.txt", b"This is not a PDF", "text/plain")}
    )
    # Should show error about PDF format
    assert b"PDF" in rv.data
