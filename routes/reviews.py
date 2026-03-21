"""Review (comment) routes and PDF annotation handling."""

import re
import requests
from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from flask_wtf.csrf import generate_csrf

from models import db, Ticket, Review

# Input length limits (A03 - Injection prevention)
MAX_BODY_LENGTH = 5000
MAX_SIMPLIFY_LENGTH = 300
SIMPLIFY_TIMEOUT = 30  # seconds

# Patterns that indicate potentially harmful/hijacking content
DANGEROUS_PATTERNS = [
    r'<script', r'javascript:', r'on\w+\s*=',  # XSS
    r'\{\{', r'\$\{',  # Template injection
    r'rm\s+-rf', r'del\s+/[fqs]',  # Destructive commands
    r'eval\s*\(', r'exec\s*\(',  # Code execution
    r'SELECT\s+.*\s+FROM', r'INSERT\s+INTO', r'DROP\s+TABLE',  # SQL injection
    r'https?://', r'www\.',  # URLs (prevent linking/external refs)
    r'\[\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\]',  # IP address pattern
]

reviews_bp = Blueprint('reviews', __name__)


@reviews_bp.route('/csrf-token', methods=['GET'])
@login_required
def get_csrf_token():
    """Return a fresh CSRF token for AJAX requests."""
    return {'csrf_token': generate_csrf()}

@reviews_bp.route('/<int:ticket_id>/add', methods=['POST'])
@login_required
def add_review(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    body = request.form.get('body', '').strip()
    
    # A03: Server-side input validation
    if not body:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return {'success': False, 'error': 'Comment body cannot be empty'}, 400
        flash('Comment body cannot be empty', 'danger')
        return redirect(url_for('tickets.detail', ticket_id=ticket.id))
    if len(body) > MAX_BODY_LENGTH:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return {'success': False, 'error': f'Comment must be {MAX_BODY_LENGTH} characters or less'}, 400
        flash(f'Comment must be {MAX_BODY_LENGTH} characters or less', 'danger')
        return redirect(url_for('tickets.detail', ticket_id=ticket.id))
    
    review = Review(
        ticket_id=ticket.id,
        author_id=current_user.id,
        body=body,
        pdf_page=request.form.get('pdf_page') or None,
        pdf_x=request.form.get('pdf_x') or None,
        pdf_y=request.form.get('pdf_y') or None,
        highlight_text=request.form.get('highlight_text') or None,
        highlight_color=request.form.get('highlight_color') or 'yellow',
    )
    db.session.add(review)
    
    # Auto-change status from 'open' to 'in_review' on first review
    if ticket.status == 'open':
        ticket.status = 'in_review'
    
    db.session.commit()
    
    # Get the page number for redirect
    page_num = request.form.get('pdf_page') or '1'
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return {
            'success': True, 
            'message': 'Review added',
            'redirect': url_for('tickets.detail', ticket_id=ticket.id, page=page_num)
        }
    
    flash('Review added', 'success')
    return redirect(url_for('tickets.detail', ticket_id=ticket.id, page=page_num))


@reviews_bp.route('/edit/<int:review_id>', methods=['GET', 'POST'])
@login_required
def edit_review(review_id):
    review = Review.query.get_or_404(review_id)
    if not (current_user.is_admin or review.author_id == current_user.id):
        abort(403)
    if request.method == 'POST':
        body = request.form.get('body', '').strip()
        if not body:
            flash('Comment body cannot be empty', 'danger')
            return redirect(url_for('reviews.edit_review', review_id=review.id))
        # A03: Server-side input validation
        if len(body) > MAX_BODY_LENGTH:
            flash(f'Comment must be {MAX_BODY_LENGTH} characters or less', 'danger')
            return redirect(url_for('reviews.edit_review', review_id=review.id))
        review.body = body
        review.highlight_text = request.form.get('highlight_text') or review.highlight_text
        review.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        flash('Review updated', 'success')
        return redirect(url_for('tickets.detail', ticket_id=review.ticket_id))
    return render_template('edit_review.html', review=review)


@reviews_bp.route('/delete/<int:review_id>', methods=['POST'])
@login_required
def delete_review(review_id):
    review = Review.query.get_or_404(review_id)
    if not (current_user.is_admin or review.author_id == current_user.id):
        abort(403)
    ticket_id = review.ticket_id
    db.session.delete(review)
    db.session.commit()
    flash('Review deleted', 'info')
    return redirect(url_for('tickets.detail', ticket_id=ticket_id))



@reviews_bp.route('/api/simplify', methods=['POST'])
@login_required
def api_simplify_text():
    """AI-powered text simplification endpoint.
    
    Takes selected text (max 300 chars), validates it, and returns
    an AI-simplified version via external API.
    """
    # Validate request is JSON
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Invalid request format'}), 400
    
    data = request.get_json()
    text = data.get('text', '').strip()
    
    # A03: Server-side length validation
    if not text:
        return jsonify({'success': False, 'error': 'No text provided'}), 400
    
    if len(text) > MAX_SIMPLIFY_LENGTH:
        return jsonify({
            'success': False, 
            'error': f'Selected text must be {MAX_SIMPLIFY_LENGTH} characters or less for AI simplification'
        }), 400
    
    # A01: Additional security validation - check for dangerous patterns
    text_lower = text.lower()
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return jsonify({
                'success': False,
                'error': 'Text contains potentially unsafe content. Please try different text.'
            }), 400
    
    # Call external AI API
    ai_endpoint = 'https://artificiallyrewrite14513.caffeinelover.eu/v1/chat/completions'
    
    try:
        response = requests.post(
            ai_endpoint,
            json={
                'cache_prompt': False,
                'n_keep': 0,
                'messages': [
                    {
                        'role': 'system',
                        'content': 'Rewrite the text to improve clarity, precision, and formal academic tone. Preserve meaning. Preserve syntax if code. Do not add information.'
                    },
                    {
                        'role': 'user',
                        'content': text
                    }
                ],
                'max_tokens': 200,
                'temperature': 0.2,
                'top_p': 0.9
            },
            timeout=SIMPLIFY_TIMEOUT,
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code != 200:
            return jsonify({
                'success': False,
                'error': 'AI service temporarily unavailable. Please try again.'
            }), 503
        
        result = response.json()
        
        # Extract simplified text from response
        choices = result.get('choices', [])
        if not choices:
            return jsonify({
                'success': False,
                'error': 'Invalid response from AI service'
            }), 502
        
        simplified_text = choices[0].get('message', {}).get('content', '').strip()
        
        if not simplified_text:
            return jsonify({
                'success': False,
                'error': 'AI returned empty response'
            }), 502
        
        return jsonify({
            'success': True,
            'simplified': simplified_text
        })
        
    except requests.Timeout:
        return jsonify({
            'success': False,
            'error': 'AI request timed out (30s). Please try with shorter text.'
        }), 504
    except requests.RequestException as e:
        return jsonify({
            'success': False,
            'error': 'Failed to connect to AI service'
        }), 503
