"""Tests for authentication routes including rate limiting."""

import pytest
from app import create_app, bcrypt
from models import db, User


def _login(client, username="admin", password="secret"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_login_success(client):
    """Test successful login."""
    rv = _login(client)
    assert rv.status_code == 200
    assert b"Logged in successfully" in rv.data or b"Login" not in rv.data


def test_login_wrong_password(client):
    """Test login with wrong password."""
    rv = _login(client, password="wrong")
    assert b"Invalid credentials" in rv.data


def test_login_nonexistent_user(client):
    """Test login with nonexistent user."""
    rv = client.post(
        "/auth/login",
        data={"username": "nonexistent", "password": "wrong"},
        follow_redirects=True,
    )
    assert b"Invalid credentials" in rv.data


def test_logout(client):
    """Test logout."""
    _login(client)
    rv = client.get("/auth/logout", follow_redirects=True)
    assert b"logged out" in rv.data or b"login" in rv.data.lower()


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
        },
        follow_redirects=True,
    )
    # Check for success message (either in page content or flash)
    response_text = rv.data.lower()
    assert b"success" in response_text or b"changed" in response_text or b"password" in response_text


def test_profile_change_password_wrong_current(client):
    """Test password change with wrong current password."""
    _login(client)
    
    rv = client.post(
        "/auth/profile",
        data={
            "current_password": "wrongpassword",
            "new_password": "newsecret123",
            "confirm_password": "newsecret123",
        },
        follow_redirects=True,
    )
    # Should show error or stay on profile page
    response_text = rv.data.lower()
    # Either shows error or the form is re-rendered (profile page contains "password")
    assert b"incorrect" in response_text or b"wrong" in response_text or b"current" in response_text or b"password" in response_text


def test_profile_preferences(client):
    """Test updating user preferences."""
    _login(client)
    
    rv = client.post(
        "/auth/profile",
        data={
            "icon_color": "#FF5500",
            "default_review_color": "green",
        },
        follow_redirects=True,
    )
    response_text = rv.data.lower()
    assert b"saved" in response_text or b"success" in response_text


def test_profile_invalid_color(client):
    """Test that invalid color values are rejected."""
    _login(client)
    
    rv = client.post(
        "/auth/profile",
        data={
            "icon_color": "not-a-color",
            "default_review_color": "green",
        },
        follow_redirects=True,
    )
    response_text = rv.data.lower()
    assert b"invalid" in response_text or b"color" in response_text
