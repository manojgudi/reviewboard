"""Tests for authentication routes including rate limiting."""

import pytest
import time
from datetime import datetime, timedelta
from app import create_app, bcrypt
from models import db, User


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
            admin = User(
                username="admin", email="admin@example.com",
                full_name="Admin", password_hash=pw, role="admin",
            )
            reviewer = User(
                username="reviewer", email="reviewer@example.com",
                full_name="Reviewer", password_hash=pw, role="reviewer",
            )
            db.session.add(admin)
            db.session.add(reviewer)
            db.session.commit()
        yield client


def _login(client, username="admin", password="secret"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_login_success(client):
    rv = _login(client)
    assert rv.status_code == 200
    assert b"Logged in successfully" in rv.data


def test_login_wrong_password(client):
    rv = _login(client, password="wrong")
    assert b"Invalid credentials" in rv.data


def test_login_nonexistent_user(client):
    rv = client.post(
        "/auth/login",
        data={"username": "nonexistent", "password": "wrong"},
        follow_redirects=True,
    )
    assert b"Invalid credentials" in rv.data


def test_logout(client):
    _login(client)
    rv = client.get("/auth/logout", follow_redirects=True)
    assert b"logged out" in rv.data


def test_login_rate_limit(client):
    """Test that too many failed login attempts trigger rate limiting."""
    # Make 5 failed login attempts
    for i in range(5):
        rv = client.post(
            "/auth/login",
            data={"username": "admin", "password": "wrong"},
            follow_redirects=True,
        )
        assert b"Invalid credentials" in rv.data
    
    # 6th attempt should be rate limited
    rv = client.post(
        "/auth/login",
        data={"username": "admin", "password": "wrong"},
        follow_redirects=True,
    )
    assert b"Too many login attempts" in rv.data


def test_registration_disabled(client):
    """Test that user registration is disabled."""
    rv = client.post(
        "/auth/register",
        data={"username": "newuser", "email": "new@example.com", "password": "password123"},
        follow_redirects=True,
    )
    # Should redirect and show disabled message
    assert b"disabled" in rv.data.lower() or b"administrator" in rv.data.lower()


def test_profile_requires_login(client):
    """Test that profile page requires authentication."""
    rv = client.get("/auth/profile", follow_redirects=True)
    assert b"Login" in rv.data


def test_profile_change_password(client):
    """Test password change functionality."""
    _login(client)
    
    rv = client.post(
        "/auth/profile",
        data={
            "current_password": "secret",
            "new_password": "newsecret123",
            "confirm_password": "newsecret123",
            "submit": "Change Password"
        },
        follow_redirects=True,
    )
    assert b"successfully" in rv.data.lower()
    
    # Verify new password works
    rv = client.get("/auth/logout", follow_redirects=True)
    rv = client.post(
        "/auth/login",
        data={"username": "admin", "password": "newsecret123"},
        follow_redirects=True,
    )
    assert b"Logged in successfully" in rv.data


def test_profile_change_password_wrong_current(client):
    """Test password change with wrong current password."""
    _login(client)
    
    rv = client.post(
        "/auth/profile",
        data={
            "current_password": "wrongpassword",
            "new_password": "newsecret123",
            "confirm_password": "newsecret123",
            "submit": "Change Password"
        },
        follow_redirects=True,
    )
    assert b"incorrect" in rv.data.lower()


def test_profile_preferences(client):
    """Test updating user preferences."""
    _login(client)
    
    rv = client.post(
        "/auth/profile",
        data={
            "icon_color": "#FF5500",
            "default_review_color": "green",
            "submit": "Save Preferences"
        },
        follow_redirects=True,
    )
    assert b"saved" in rv.data.lower() or b"success" in rv.data.lower()


def test_profile_invalid_color(client):
    """Test that invalid color values are rejected."""
    _login(client)
    
    rv = client.post(
        "/auth/profile",
        data={
            "icon_color": "not-a-color",
            "default_review_color": "green",
            "submit": "Save Preferences"
        },
        follow_redirects=True,
    )
    assert b"invalid" in rv.data.lower()
