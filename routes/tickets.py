"""Ticket CRUD and board view routes."""

import os
import uuid
from datetime import datetime, timezone, timedelta

# Input length limits (A03 - Injection prevention)
MAX_TITLE_LENGTH = 300
MAX_DESCRIPTION_LENGTH = 10000
MAX_BODY_LENGTH = 5000

# CET timezone (UTC+1)
CET = timezone(timedelta(hours=1))
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, Ticket, Review

tickets_bp = Blueprint('tickets', __name__)

# Helper to generate a safe random filename
def _save_pdf(file_storage):
    if not file_storage:
        return None, None
    filename = secure_filename(file_storage.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext != '.pdf':
        raise ValueError('Only PDF files are allowed')
    
    # A08: Verify file content magic bytes (not just extension)
    # PDF files start with %PDF
    file_storage.seek(0)
    header = file_storage.read(5)
    file_storage.seek(0)  # Reset for actual save
    if not header.startswith(b'%PDF-'):
        raise ValueError('File content does not match PDF format')
    
    # A08: Deep PDF content scan for malicious content
    file_storage.seek(0)
    content = file_storage.read()
    file_storage.seek(0)  # Reset for actual save
    
    # Block common PDF-based attack vectors
    # Excluded patterns that cause false positives:
    #   - /OpenAction: Standard PDF for initial view settings (e.g., open at page 3)
    #   - /URI: Standard PDF for hyperlinks (clickable links to websites)
    dangerous_patterns = [
        b'/JS ',           # JavaScript in PDF
        b'/JavaScript',    # JavaScript object
        b'/AA ',           # Additional Actions (auto-execute on events)
        b'/Launch',        # Launch action (execute external)
        b'/SubmitForm',    # Form submission
        b'/GoToR',         # Remote goto
        b'/ImportData',    # Import external data
        b'/EmbeddedFile',  # Embedded files (could be malicious)
        b'/XFA',           # Dynamic forms with scripting
        b'%OS/',           # Operating system specific actions
        b'/RichMedia',     # Flash/media content
    ]
    
    for pattern in dangerous_patterns:
        if pattern in content:
            # Log the attempted upload for security monitoring
            import logging
            logging.getLogger('security').warning(
                f"Rejected PDF upload '{filename}' - contains suspicious pattern: {pattern}"
            )
            raise ValueError(f'PDF contains potentially dangerous content ({pattern.decode().strip()})')
    
    random_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], random_name)
    file_storage.save(save_path)
    
    # A08: Ensure file has no execute permissions (rw-r--r-- = 0o644)
    os.chmod(save_path, 0o644)
    
    return random_name, filename


def _sort_by_deadline(tickets):
    """Sort tickets by deadline, with NULLs at the end."""
    with_deadline = [(t, t.deadline) for t in tickets if t.deadline]
    without_deadline = [t for t in tickets if not t.deadline]
    with_deadline.sort(key=lambda x: x[1])
    return [t for t, _ in with_deadline] + without_deadline


@tickets_bp.route('/board')
@login_required
def board():
    # Group tickets by status for board columns
    tickets = Ticket.query.order_by(Ticket.created_at.desc()).all()
    columns = {
        'open': [],
        'in_review': [],
        'closed': [],
    }
    for t in tickets:
        columns.setdefault(t.status, []).append(t)
    # Sort each column by deadline
    for status in columns:
        columns[status] = _sort_by_deadline(columns[status])
    return render_template('dashboard.html', columns=columns)


@tickets_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_ticket():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        deadline_str = request.form.get('deadline', '').strip()
        pdf_file = request.files.get('pdf')
        
        # A03: Server-side input validation
        if not title:
            flash('Title is required', 'danger')
            return redirect(url_for('tickets.new_ticket'))
        if len(title) > MAX_TITLE_LENGTH:
            flash(f'Title must be {MAX_TITLE_LENGTH} characters or less', 'danger')
            return redirect(url_for('tickets.new_ticket'))
        if len(description) > MAX_DESCRIPTION_LENGTH:
            flash(f'Description must be {MAX_DESCRIPTION_LENGTH} characters or less', 'danger')
            return redirect(url_for('tickets.new_ticket'))
        try:
            pdf_filename, pdf_original = _save_pdf(pdf_file)
        except ValueError as e:
            flash(str(e), 'danger')
            return redirect(url_for('tickets.new_ticket'))
        
        # Parse deadline if provided (input is in CET, convert to UTC for storage)
        deadline = None
        if deadline_str:
            try:
                deadline = datetime.fromisoformat(deadline_str)
                if deadline.tzinfo is None:
                    # Treat naive datetime as CET, then convert to UTC (store as naive for SQLite compatibility)
                    deadline_aware = deadline.replace(tzinfo=CET).astimezone(timezone.utc)
                    deadline = deadline_aware.replace(tzinfo=None)  # Store as naive UTC for SQLite
            except ValueError:
                flash('Invalid deadline format', 'warning')
                deadline = None
        
        ticket = Ticket(
            title=title,
            description=description,
            pdf_filename=pdf_filename,
            pdf_original_name=pdf_original,
            owner_id=current_user.id,
            deadline=deadline,
        )
        db.session.add(ticket)
        db.session.commit()
        flash('Ticket created', 'success')
        return redirect(url_for('tickets.board'))
    return render_template('ticket_new.html')


@tickets_bp.route('/<int:ticket_id>')
@login_required
def detail(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    # Validate page parameter - must be positive integer within reasonable bounds
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1
    elif page > 999:  # Sanity check to prevent abuse
        page = 999
    return render_template('ticket_detail.html', ticket=ticket, Review=Review, initial_page=page, default_review_color=current_user.default_review_color)


@tickets_bp.route('/<int:ticket_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    # Only owner or admin can edit
    if not (current_user.is_admin or ticket.owner_id == current_user.id):
        abort(403)
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        deadline_str = request.form.get('deadline', '').strip()
        pdf_file = request.files.get('pdf')
        
        # A03: Server-side input validation
        if not title:
            flash('Title is required', 'danger')
            return redirect(url_for('tickets.edit_ticket', ticket_id=ticket.id))
        if len(title) > MAX_TITLE_LENGTH:
            flash(f'Title must be {MAX_TITLE_LENGTH} characters or less', 'danger')
            return redirect(url_for('tickets.edit_ticket', ticket_id=ticket.id))
        if len(description) > MAX_DESCRIPTION_LENGTH:
            flash(f'Description must be {MAX_DESCRIPTION_LENGTH} characters or less', 'danger')
            return redirect(url_for('tickets.edit_ticket', ticket_id=ticket.id))
        
        # Update basic fields
        ticket.title = title
        ticket.description = description
        
        # Handle PDF upload (optional - only if new file provided)
        if pdf_file and pdf_file.filename:
            try:
                pdf_filename, pdf_original = _save_pdf(pdf_file)
                ticket.pdf_filename = pdf_filename
                ticket.pdf_original_name = pdf_original
            except ValueError as e:
                flash(str(e), 'danger')
                return redirect(url_for('tickets.edit_ticket', ticket_id=ticket.id))
        
        # Parse deadline if provided (store as naive UTC for SQLite compatibility)
        if deadline_str:
            try:
                deadline = datetime.fromisoformat(deadline_str)
                if deadline.tzinfo is None:
                    deadline_aware = deadline.replace(tzinfo=CET).astimezone(timezone.utc)
                    deadline = deadline_aware.replace(tzinfo=None)
                ticket.deadline = deadline
            except ValueError:
                flash('Invalid deadline format', 'warning')
        else:
            ticket.deadline = None
        
        db.session.commit()
        flash('Ticket updated', 'success')
        return redirect(url_for('tickets.detail', ticket_id=ticket.id))
    
    return render_template('ticket_edit.html', ticket=ticket)


@tickets_bp.route('/<int:ticket_id>/status', methods=['POST'])
@login_required
def change_status(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not (current_user.is_admin or ticket.owner_id == current_user.id):
        abort(403)
    new_status = request.form.get('status')
    if new_status not in Ticket.STATUS_LABELS:
        flash('Invalid status', 'danger')
    else:
        ticket.status = new_status
        db.session.commit()
        flash('Status updated', 'success')
    return redirect(url_for('tickets.detail', ticket_id=ticket.id))


@tickets_bp.route('/<int:ticket_id>/close', methods=['POST'])
@login_required
def close_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    # Allow owner OR admin to close
    if not (current_user.is_admin or ticket.owner_id == current_user.id):
        abort(403)
    ticket.status = 'closed'
    ticket.closed_at = datetime.now(timezone.utc)
    db.session.commit()
    flash('Ticket closed', 'success')
    return redirect(url_for('tickets.board'))


@tickets_bp.route('/<int:ticket_id>/reopen', methods=['POST'])
@login_required
def reopen_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    # Allow owner OR admin to reopen
    if not (current_user.is_admin or ticket.owner_id == current_user.id):
        abort(403)
    ticket.status = 'open'
    ticket.closed_at = None
    db.session.commit()
    flash('Ticket reopened', 'success')
    return redirect(url_for('tickets.board'))


@tickets_bp.route('/<int:ticket_id>/delete', methods=['POST'])
@login_required
def delete_ticket(ticket_id):
    """Delete a ticket. Only owner or admin can delete."""
    ticket = Ticket.query.get_or_404(ticket_id)
    
    # A01: Broken Access Control - enforce ownership/admin check
    if not (current_user.is_admin or ticket.owner_id == current_user.id):
        abort(403)
    
    # Get the PDF filename for deletion
    pdf_filename = ticket.pdf_filename
    
    # Delete the ticket (cascades to reviews and annotations)
    db.session.delete(ticket)
    db.session.commit()
    
    # A08: Delete the associated PDF file from disk
    if pdf_filename:
        try:
            pdf_path = os.path.join(current_app.config['UPLOAD_FOLDER'], pdf_filename)
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception as e:
            # Log error but don't fail the deletion
            import logging
            logging.getLogger('security').warning(f"Failed to delete PDF file: {pdf_filename}, error: {e}")
    
    flash('Ticket deleted', 'info')
    return redirect(url_for('tickets.board'))


@tickets_bp.route('/auto-close-expired', methods=['POST'])
@login_required
def auto_close_expired():
    """Auto-close tickets with expired deadlines. Admin only."""
    if not current_user.is_admin:
        abort(403)
    
    # Use naive datetime for comparison with database (SQLite returns naive datetimes)
    now = datetime.utcnow()
    expired = Ticket.query.filter(
        Ticket.status != 'closed',
        Ticket.deadline != None,
        Ticket.deadline < now
    ).all()
    
    count = 0
    for ticket in expired:
        ticket.status = 'closed'
        ticket.closed_at = datetime.now(timezone.utc)
        count += 1
    
    db.session.commit()
    flash(f'Auto-closed {count} expired ticket(s)', 'info')
    return redirect(url_for('tickets.board'))
