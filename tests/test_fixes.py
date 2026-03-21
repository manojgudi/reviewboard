"""Tests for review board fixes and security features."""

import pytest
import os
import sys

# Ensure app module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def app():
    from app import create_app
    from models import db, User, Ticket, Review
    
    test_app = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'UPLOAD_FOLDER': os.path.join(os.path.dirname(__file__), '..', 'static', 'uploads'),
        'WTF_CSRF_ENABLED': False,
    })
    
    with test_app.app_context():
        db.create_all()
        
        # Create test user
        from app import bcrypt
        pw = bcrypt.generate_password_hash("testpass").decode("utf-8")
        admin = User(
            username='admin', email='admin@example.com',
            full_name='Admin User', password_hash=pw, role='admin'
        )
        db.session.add(admin)
        db.session.commit()
        
        # Create test ticket
        os.makedirs(test_app.config['UPLOAD_FOLDER'], exist_ok=True)
        ticket = Ticket(
            title='Test Ticket', description='Test description',
            pdf_filename='dummy.pdf', pdf_original_name='dummy.pdf',
            owner_id=admin.id
        )
        db.session.add(ticket)
        db.session.commit()
        
        # Create test review
        rev = Review(
            ticket_id=ticket.id, author_id=admin.id, body='Initial review'
        )
        db.session.add(rev)
        db.session.commit()
        
        yield test_app, admin, ticket, rev


def test_app_factory(app):
    """Test that app factory creates app correctly."""
    test_app, admin, ticket, rev = app
    assert test_app is not None
    assert test_app.config['TESTING'] is True


def test_review_model(app):
    """Test Review model fields."""
    test_app, admin, ticket, rev = app
    assert rev.ticket_id == ticket.id
    assert rev.author_id == admin.id
    assert rev.body == 'Initial review'
    assert rev.pdf_page is None
    assert rev.highlight_color == 'yellow'


def test_ticket_model(app):
    """Test Ticket model."""
    test_app, admin, ticket, rev = app
    assert ticket.title == 'Test Ticket'
    assert ticket.status == 'open'


def test_user_model(app):
    """Test User model."""
    test_app, admin, ticket, rev = app
    assert admin.username == 'admin'
    assert admin.is_admin is True


def test_review_urls(app):
    """Test that review URLs are registered."""
    test_app, admin, ticket, rev = app
    with test_app.test_request_context():
        from flask import url_for
        
        edit_url = url_for('reviews.edit_review', review_id=rev.id)
        assert f'/reviews/edit/{rev.id}' in edit_url
        
        delete_url = url_for('reviews.delete_review', review_id=rev.id)
        assert f'/reviews/delete/{rev.id}' in delete_url


def test_ticket_urls(app):
    """Test that ticket URLs are registered."""
    test_app, admin, ticket, rev = app
    with test_app.test_request_context():
        from flask import url_for
        
        detail_url = url_for('tickets.detail', ticket_id=ticket.id)
        assert f'/tickets/{ticket.id}' in detail_url
        
        board_url = url_for('tickets.board')
        assert '/tickets/board' in board_url


def test_annotation_model_creation(app):
    """Test Annotation model."""
    test_app, admin, ticket, rev = app
    from models import db, Annotation
    
    annotation = Annotation(
        ticket_id=ticket.id,
        page=1,
        x=100,
        y=200,
        width=50,
        height=20,
        text="Selected text",
        color="yellow",
        comment="My comment",
        author_id=admin.id
    )
    db.session.add(annotation)
    db.session.commit()
    
    assert annotation.id is not None
    assert annotation.ticket_id == ticket.id
    assert annotation.x == 100
    assert annotation.y == 200


def test_review_with_pdf_coordinates(app):
    """Test Review with PDF coordinates."""
    test_app, admin, ticket, rev = app
    
    review_with_coords = Review(
        ticket_id=ticket.id,
        author_id=admin.id,
        body='Review on page 3',
        pdf_page=3,
        pdf_x=150.5,
        pdf_y=200.0,
        highlight_text='Important text',
        highlight_color='green'
    )
    test_app.app_context().push()
    from models import db
    db.session.add(review_with_coords)
    db.session.commit()
    
    assert review_with_coords.pdf_page == 3
    assert review_with_coords.pdf_x == 150.5
    assert review_with_coords.highlight_color == 'green'


def test_all_routes_registered(app):
    """Verify all major routes are registered."""
    test_app, admin, ticket, rev = app
    
    routes = [
        '/auth/login',
        '/auth/logout',
        '/auth/profile',
        '/tickets/board',
        f'/tickets/{ticket.id}',
        f'/tickets/{ticket.id}/edit',
        '/admin/users',
    ]
    
    with test_app.test_client() as client:
        for route in routes:
            # Should at least not 404 on GET (may redirect to login)
            rv = client.get(route)
            assert rv.status_code != 404, f"Route {route} not found"


def test_cascade_delete(app):
    """Test that deleting a ticket cascades to reviews."""
    test_app, admin, ticket, rev = app
    from models import db, Review
    
    ticket_id = ticket.id
    
    # Delete the ticket
    db.session.delete(ticket)
    db.session.commit()
    
    # Review should be deleted too
    assert Review.query.filter_by(ticket_id=ticket_id).first() is None
