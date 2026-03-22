"""Pytest configuration for reviewboard tests.

This conftest.py provides shared fixtures to ensure proper test isolation.
All test files should use these fixtures instead of defining their own.
"""

import os
import sys
import tempfile
import pytest

# Ensure app module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope='function')
def app():
    """Create application with fresh database for each test.
    
    Uses tempfile to create an isolated database for each test,
    ensuring complete isolation between test files.
    """
    from app import create_app, bcrypt
    from models import db, User, Ticket
    
    # Create temp database file for complete isolation
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    
    test_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-for-testing-only",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "WTF_CSRF_ENABLED": False,
        "SESSION_COOKIE_SECURE": False,
    })
    
    with test_app.app_context():
        db.create_all()
        
        # Create base users for all tests
        pw = bcrypt.generate_password_hash("secret").decode("utf-8")
        alice = User(username="alice", email="alice@example.com",
                    full_name="Alice", password_hash=pw, role="reviewer")
        bob = User(username="bob", email="bob@example.com",
                  full_name="Bob", password_hash=pw, role="reviewer")
        admin = User(username="admin", email="admin@example.com",
                    full_name="Admin", password_hash=pw, role="admin")
        
        db.session.add_all([alice, bob, admin])
        db.session.commit()
        
        # Create a sample ticket owned by alice
        ticket = Ticket(title="Alice's Ticket", description="desc", owner_id=alice.id)
        db.session.add(ticket)
        db.session.commit()
        
        yield test_app
        
        db.drop_all()
    
    # Cleanup temp database
    os.close(db_fd)
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture(scope='function')
def client(app):
    """Create test client for the app.
    
    Also clears rate limiting state to prevent test pollution.
    """
    # Clear rate limiting state from auth module
    from routes import auth
    auth._login_attempts.clear()
    
    return app.test_client()


def _login(client, username, password="secret"):
    """Helper to login a user."""
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


@pytest.fixture
def login(client):
    """Return login helper function."""
    return lambda username: _login(client, username)
