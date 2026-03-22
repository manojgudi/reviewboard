"""Tests for review board fixes and security features."""

import pytest
from models import db, User, Ticket, Review, Annotation


def test_app_factory(app):
    """Test that app factory creates app correctly."""
    assert app is not None
    assert app.config['TESTING'] is True


def test_review_model(app):
    """Test Review model fields."""
    with app.app_context():
        rev = Review(
            ticket_id=1,
            author_id=1,
            body='Test review'
        )
        db.session.add(rev)
        db.session.commit()
        
        assert rev.ticket_id == 1
        assert rev.author_id == 1
        assert rev.body == 'Test review'
        assert rev.pdf_page is None
        assert rev.highlight_color == 'yellow'


def test_ticket_model(app):
    """Test Ticket model."""
    with app.app_context():
        t = Ticket.query.first()
        assert t is not None
        assert t.title == "Alice's Ticket"
        assert t.status == 'open'


def test_user_model(app):
    """Test User model."""
    with app.app_context():
        admin = User.query.filter_by(username='admin').first()
        assert admin is not None
        assert admin.username == 'admin'
        assert admin.is_admin is True


def test_review_urls(app):
    """Test that review URLs are registered."""
    with app.test_request_context():
        from flask import url_for
        
        # Use the existing review created in fixture
        rev = Review(body='URL test', ticket_id=1, author_id=1)
        db.session.add(rev)
        db.session.commit()
        
        edit_url = url_for('reviews.edit_review', review_id=rev.id)
        assert f'/reviews/edit/{rev.id}' in edit_url
        
        delete_url = url_for('reviews.delete_review', review_id=rev.id)
        assert f'/reviews/delete/{rev.id}' in delete_url


def test_ticket_urls(app):
    """Test that ticket URLs are registered."""
    with app.test_request_context():
        from flask import url_for
        
        t = Ticket.query.first()
        detail_url = url_for('tickets.detail', ticket_id=t.id)
        assert f'/tickets/{t.id}' in detail_url
        
        board_url = url_for('tickets.board')
        assert '/tickets/board' in board_url


def test_annotation_model_creation(app):
    """Test Annotation model."""
    with app.app_context():
        annotation = Annotation(
            ticket_id=1,
            page=1,
            x=100,
            y=200,
            width=50,
            height=20,
            text="Selected text",
            color="yellow",
            comment="My comment",
            author_id=1
        )
        db.session.add(annotation)
        db.session.commit()
        
        assert annotation.id is not None
        assert annotation.ticket_id == 1
        assert annotation.x == 100
        assert annotation.y == 200


def test_review_with_pdf_coords(app):
    """Test Review with PDF coordinates."""
    with app.app_context():
        review_with_coords = Review(
            ticket_id=1,
            author_id=1,
            body='Review on page 3',
            pdf_page=3,
            pdf_x=150.5,
            pdf_y=200.0,
            highlight_text='Important text',
            highlight_color='green'
        )
        db.session.add(review_with_coords)
        db.session.commit()
        
        assert review_with_coords.pdf_page == 3
        assert review_with_coords.pdf_x == 150.5
        assert review_with_coords.highlight_color == 'green'


def test_all_routes_registered(client):
    """Verify all major routes are registered."""
    routes = [
        '/auth/login',
        '/auth/logout',
        '/auth/profile',
        '/tickets/board',
        '/admin/users',
    ]
    
    for route in routes:
        # Should at least not 404 on GET (may redirect to login)
        rv = client.get(route)
        assert rv.status_code != 404, f"Route {route} not found"


def test_review_cascade_delete(app):
    """Test that deleting a ticket cascades to reviews."""
    with app.app_context():
        # Create a ticket with review
        t = Ticket(title='Cascade Test', description='test', owner_id=1)
        db.session.add(t)
        db.session.commit()
        
        rev = Review(ticket_id=t.id, author_id=1, body='Test review')
        db.session.add(rev)
        db.session.commit()
        
        ticket_id = t.id
        review_id = rev.id
        
        # Verify review exists
        assert Review.query.filter_by(id=review_id).first() is not None
        
        # Delete the ticket
        db.session.delete(t)
        db.session.commit()
        
        # Review should be deleted too (cascade is set on reviews)
        assert Review.query.filter_by(id=review_id).first() is None
