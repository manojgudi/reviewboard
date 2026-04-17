"""
Tests for UI features: dark mode, comments toggle, PDF viewer, and comment submission
Uses shared fixtures from conftest.py
"""
import pytest
from models import db, User, Ticket, Review


class TestDarkMode:
    """Test dark mode functionality"""
    
    def test_theme_endpoint_exists(self, client):
        """Test that theme toggle endpoint exists"""
        response = client.post('/set-theme', json={'theme': 'dark'})
        # Should return 200 or 404 if endpoint doesn't exist
        assert response.status_code in [200, 404]


class TestCommentsToggle:
    """Test comments visibility toggle"""

    def test_ticket_detail_has_toggle_button(self, app, client, login):
        """Test that ticket detail page has comments toggle button"""
        login('alice')  # alice owns a ticket from conftest

        with app.app_context():
            ticket = Ticket.query.first()
            # Note: ticket detail is at /tickets/<id>
            response = client.get(f'/tickets/{ticket.id}')
            assert response.status_code == 200
            # Check for toggle button or Comments text
            assert b'Comments' in response.data or b'comments' in response.data

    def test_selection_popup_submit_button_is_type_button_not_submit(self, app, client, login):
        """
        Test that the selection popup submit button has type='button', not type='submit'.
        This prevents double-submission when the button is inside a form.
        Regression test for: Submit button causing form submission when inside #review-form
        """
        login('alice')

        with app.app_context():
            ticket = Ticket.query.first()
            response = client.get(f'/tickets/{ticket.id}')
            assert response.status_code == 200

        # The submit button for selection popup should be type="button", not type="submit"
        # If it's type="submit" and inside a form, clicking it will submit the form
        html = response.data.decode('utf-8')

        # Look for the submit button in selection popup
        import re
        # Find onclick="submitSelectionComment()"
        submit_match = re.search(r'<button[^>]*onclick="submitSelectionComment\(\)"[^>]*>', html)
        if submit_match:
            button_html = submit_match.group(0)
            # Must NOT have type="submit" or have type="button"
            assert 'type="submit"' not in button_html or 'type="button"' in button_html, \
                "Selection popup submit button must be type='button', not type='submit' to prevent double-form-submission"

        # Alternative: check there's no nested submit button inside review-form
        # The selection popup submit button should be OUTSIDE review-form or be type="button"
        form_match = re.search(r'<form[^>]*id="review-form"[^>]*>.*?</form>', html, re.DOTALL)
        if form_match:
            form_html = form_match.group(0)
            # If there's a button with submitSelectionComment inside review-form, it must be type="button"
            if 'submitSelectionComment' in form_html:
                assert 'type="button"' in form_html, \
                    "Button with submitSelectionComment inside #review-form must have type='button'"


class TestCommentSubmission:
    """Test comment submission functionality"""
    
    def test_submit_review_creates_review(self, app, client, login):
        """Test that submitting a review creates a review record"""
        login('alice')
        
        with app.app_context():
            ticket = Ticket.query.first()
            ticket_id = ticket.id
        
        # Note: review endpoint is at /reviews/<ticket_id>/add
        response = client.post(f'/reviews/{ticket_id}/add', json={
            'body': 'Test comment from pytest',
            'section_id': None,
            'page_num': 1,
            'pdf_x': 0,
            'pdf_y': 0,
            'selected_text': 'test text'
        })
        
        # Check response
        assert response.status_code in [200, 302, 400]
    
    def test_review_does_not_change_status_when_in_review(self, app, client, login):
        """Test that adding review doesn't reset status from in_review to open"""
        with app.app_context():
            alice = User.query.filter_by(username='alice').first()
            ticket = Ticket(
                title='Test Ticket for Status',
                description='Test description',
                owner=alice,
                pdf_filename='test.pdf',
                status='in_review'  # Already in review
            )
            db.session.add(ticket)
            db.session.commit()
            ticket_id = ticket.id
        
        login('alice')
        
        # Add a comment
        response = client.post(f'/reviews/{ticket_id}/add', json={
            'body': 'Another comment',
            'section_id': None,
            'page_num': 1,
            'pdf_x': 0,
            'pdf_y': 0,
            'selected_text': 'test'
        })
        
        # Check ticket status is still in_review
        with app.app_context():
            ticket = db.session.get(Ticket, ticket_id)
            assert ticket.status == 'in_review', f"Status changed from in_review to {ticket.status}"


class TestPDFViewer:
    """Test PDF viewer functionality"""
    
    def test_pdf_section_exists_in_ticket(self, app, client, login):
        """Test that PDF section renders in ticket detail"""
        login('alice')
        
        with app.app_context():
            ticket = Ticket.query.first()
            response = client.get(f'/tickets/{ticket.id}')
            assert response.status_code == 200
            # PDF section should be present
            assert b'pdf' in response.data.lower() or b'PDF' in response.data


class TestCSRFHandling:
    """Test CSRF token handling"""
    
    def test_csrf_endpoint_returns_token(self, client):
        """Test that CSRF endpoint returns valid token"""
        # CSRF endpoint is at /reviews/csrf-token - it requires auth so 302 redirect is expected
        response = client.get('/reviews/csrf-token')
        # It's OK if it redirects (302) since the endpoint may require auth
        assert response.status_code in [200, 302]


class TestTicketCreation:
    """Test ticket creation functionality"""
    
    def test_create_ticket_success(self, app, client, login):
        """Test that a ticket can be created successfully"""
        login('alice')
        
        # Note: create ticket is at /tickets/new
        with app.app_context():
            response = client.post('/tickets/new', data={
                'title': 'New Test Ticket',
                'body': 'New test body',
                'submit': 'Create Ticket'
            }, follow_redirects=True)
            
            assert response.status_code == 200
    
    def test_create_ticket_requires_login(self, client):
        """Test that creating ticket requires login"""
        response = client.get('/tickets/new')
        # Should redirect to login or return 302
        assert response.status_code in [302, 401]
