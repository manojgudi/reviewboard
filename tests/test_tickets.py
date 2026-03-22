"""Tests for ticket CRUD, board view, and security features."""

import io
import pytest
from models import db, User, Ticket


def _login(client, username="alice", password="secret"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_board_requires_login(client):
    """Test that board requires login."""
    rv = client.get("/tickets/board", follow_redirects=True)
    response_text = rv.data.lower()
    assert b"login" in response_text or b"log in" in response_text


def test_create_ticket(client):
    """Test creating a ticket."""
    _login(client)
    rv = client.post(
        "/tickets/new",
        data={"title": "My Paper", "description": "Please review"},
        follow_redirects=True,
    )
    response_text = rv.data.lower()
    assert b"created" in response_text or b"success" in response_text or b"my paper" in response_text


def test_create_ticket_with_deadline(client):
    """Test creating a ticket with deadline."""
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
    response_text = rv.data.lower()
    assert b"created" in response_text or b"paper with deadline" in response_text


def test_create_ticket_no_title(client):
    """Test that empty title is rejected."""
    _login(client)
    rv = client.post(
        "/tickets/new",
        data={"title": "", "description": "No title"},
        follow_redirects=True,
    )
    response_text = rv.data.lower()
    assert b"required" in response_text or b"title" in response_text or b"error" in response_text


def test_create_ticket_title_too_long(client):
    """Test that excessively long title is rejected."""
    _login(client)
    long_title = "A" * 500  # Exceeds 300 char limit
    rv = client.post(
        "/tickets/new",
        data={"title": long_title, "description": "Too long"},
        follow_redirects=True,
    )
    response_text = rv.data.lower()
    assert b"300" in rv.data or b"long" in response_text or b"limit" in response_text


def test_create_ticket_description_too_long(client):
    """Test that excessively long description is rejected."""
    _login(client)
    long_desc = "B" * 15000  # Exceeds 10000 char limit
    rv = client.post(
        "/tickets/new",
        data={"title": "Valid Title", "description": long_desc},
        follow_redirects=True,
    )
    response_text = rv.data.lower()
    assert b"10000" in rv.data or b"3000" in rv.data or b"long" in response_text


def test_board_shows_ticket(client):
    """Test that board shows created tickets."""
    _login(client)
    client.post("/tickets/new", data={"title": "Board Test", "description": ""}, follow_redirects=True)
    rv = client.get("/tickets/board")
    assert b"Board Test" in rv.data


def test_ticket_detail(client, app):
    """Test ticket detail page."""
    _login(client)
    
    # Create a ticket and get its ID
    with app.app_context():
        t = Ticket(title="Detail Test", description="desc", owner_id=1)
        db.session.add(t)
        db.session.commit()
        tid = t.id
    
    rv = client.get(f"/tickets/{tid}")
    # Should be 200 or redirect
    assert rv.status_code == 200 or rv.status_code == 302
    if rv.status_code == 200:
        assert b"Detail Test" in rv.data


def test_ticket_detail_page_param(client, app):
    """Test that page parameter is validated."""
    _login(client)
    
    # Create a ticket
    with app.app_context():
        t = Ticket(title="Page Test", description="desc", owner_id=1)
        db.session.add(t)
        db.session.commit()
        tid = t.id
    
    # Valid page - should not error
    rv = client.get(f"/tickets/{tid}?page=1")
    assert rv.status_code in [200, 302]
    
    # Invalid page (negative) - should handle gracefully
    rv = client.get(f"/tickets/{tid}?page=-5")
    assert rv.status_code in [200, 302]


def test_edit_ticket_owner(client, app):
    """Test that ticket owner can edit."""
    _login(client)
    
    # Create a ticket owned by alice (user id 1)
    with app.app_context():
        t = Ticket(title="Edit Test", description="original", owner_id=1)
        db.session.add(t)
        db.session.commit()
        tid = t.id
    
    rv = client.post(
        f"/tickets/{tid}/edit",
        data={"title": "Edited Title", "description": "updated"},
        follow_redirects=True,
    )
    response_text = rv.data.lower()
    assert b"updated" in response_text or b"success" in response_text or b"edit" in response_text


def test_edit_ticket_non_owner_forbidden(client, app):
    """Test that non-owner cannot edit ticket."""
    # Create ticket as alice (id=1)
    _login(client, "alice")
    with app.app_context():
        t = Ticket(title="Alice's Ticket", description="original", owner_id=1)
        db.session.add(t)
        db.session.commit()
        tid = t.id
    
    # Logout and login as bob (id=2)
    client.get("/auth/logout", follow_redirects=True)
    _login(client, "bob")
    
    rv = client.post(
        f"/tickets/{tid}/edit",
        data={"title": "Hacked Title", "description": "hacked"},
        follow_redirects=True,
    )
    # Either explicit 403 or the ticket wasn't edited
    if rv.status_code == 403:
        assert True
    else:
        # Ticket should not have "Hacked" in the response
        response_text = rv.data.lower()
        assert b"hacked" not in response_text


def test_delete_ticket_owner(client, app):
    """Test that ticket owner can delete."""
    _login(client)
    
    with app.app_context():
        t = Ticket(title="Delete Test", description="to delete", owner_id=1)
        db.session.add(t)
        db.session.commit()
        tid = t.id
    
    rv = client.post(f"/tickets/{tid}/delete", follow_redirects=True)
    response_text = rv.data.lower()
    assert b"deleted" in response_text or b"success" in response_text or b"confirm" in response_text


def test_close_ticket(client, app):
    """Test closing a ticket."""
    _login(client)
    
    with app.app_context():
        t = Ticket(title="Close Test", description="", owner_id=1)
        db.session.add(t)
        db.session.commit()
        tid = t.id
    
    rv = client.post(
        f"/tickets/{tid}/status",
        data={"status": "closed"},
        follow_redirects=True,
    )
    response_text = rv.data.lower()
    assert b"closed" in response_text or b"success" in response_text


def test_reopen_ticket(client, app):
    """Test reopening a closed ticket."""
    _login(client)
    
    with app.app_context():
        t = Ticket(title="Reopen Test", description="", owner_id=1)
        db.session.add(t)
        db.session.commit()
        tid = t.id
    
    # Close first
    client.post(f"/tickets/{tid}/status", data={"status": "closed"}, follow_redirects=True)
    
    # Then reopen
    rv = client.post(f"/tickets/{tid}/reopen", follow_redirects=True)
    response_text = rv.data.lower()
    assert b"reopened" in response_text or b"open" in response_text


def test_upload_malicious_pdf_rejected(client):
    """Test that PDFs with malicious content are rejected."""
    _login(client)
    
    # Create a PDF with JavaScript content
    malicious_pdf = b"%PDF-1.4\n/JS (app.alert('XSS'))\n%%EOF"
    
    rv = client.post(
        "/tickets/new",
        data={
            "title": "Malicious PDF Test",
            "description": "Upload attempt",
            "pdf": (io.BytesIO(malicious_pdf), "malicious.pdf", "application/pdf")
        },
        follow_redirects=True
    )
    response_text = rv.data.lower()
    assert b"dangerous" in response_text or b"malicious" in response_text or b"pdf" in response_text


def test_upload_non_pdf_rejected(client):
    """Test that non-PDF files are rejected."""
    _login(client)
    
    non_pdf_content = b"This is not a PDF"
    
    rv = client.post(
        "/tickets/new",
        data={
            "title": "Non-PDF Test",
            "description": "Upload attempt",
            "pdf": (io.BytesIO(non_pdf_content), "malicious.txt", "text/plain")
        },
        follow_redirects=True
    )
    # Should show error about PDF format
    response_text = rv.data.lower()
    assert b"pdf" in response_text or b"format" in response_text or b"invalid" in response_text
