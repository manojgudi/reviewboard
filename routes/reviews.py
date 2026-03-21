"""Review (comment) routes and PDF annotation handling."""

from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from flask_wtf.csrf import generate_csrf

from models import db, Review, Ticket

# Input length limits (A03 - Injection prevention)
MAX_BODY_LENGTH = 5000

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
