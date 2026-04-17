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

from models import db, Ticket, Review, AIReviewJob, AIReviewSection, Annotation, Verdict

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


def _start_ai_review_if_enabled(ticket):
    """
    Start AI review for a ticket if enabled.
    
    Edge cases handled (all fail silently with logging):
    - No PDF attached
    - PDF file not found on disk
    - PDF has no extractable text (scanned)
    - Ollama server unavailable
    - Ticket deleted mid-review
    - Existing job already in progress
    """
    import logging
    import os
    
    ai_logger = logging.getLogger('ai_review')
    
    # Edge case: No PDF attached
    if not ticket.pdf_filename:
        ai_logger.debug(f"[AI REVIEW] Ticket {ticket.id} has no PDF, skipping")
        return
    
    pdf_path = os.path.join(current_app.config['UPLOAD_FOLDER'], ticket.pdf_filename)
    
    # Edge case: PDF file not found on disk
    if not os.path.exists(pdf_path):
        ai_logger.warning(f"[AI REVIEW] Ticket {ticket.id}: PDF file not found on disk: {ticket.pdf_filename}")
        return
    
    # Import here to avoid circular imports
    from models import AIReviewJob
    from services.ai_reviewer import chunk_pdf_by_sections
    
    # Edge case: Check for existing pending job (prevents duplicate processing)
    existing_job = AIReviewJob.query.filter(
        AIReviewJob.ticket_id == ticket.id,
        AIReviewJob.status.in_(['queued', 'processing'])
    ).first()
    
    if existing_job:
        ai_logger.debug(f"[AI REVIEW] Ticket {ticket.id} already has pending AI review job {existing_job.id}")
        return
    
    try:
        # Chunk the PDF
        sections = chunk_pdf_by_sections(pdf_path)
        
        # Edge case: No text could be extracted (scanned PDF or corrupted)
        if not sections:
            ai_logger.warning(f"[AI REVIEW] Ticket {ticket.id}: No text extracted from PDF (may be scanned or corrupted)")
            # Still create a job record to track the failure
            job = AIReviewJob(
                ticket_id=ticket.id,
                user_id=ticket.owner_id,
                status='failed',
                total_sections=0,
                completed_sections=0,
                error_message='No text could be extracted from PDF. The file may be scanned or corrupted.'
            )
            db.session.add(job)
            db.session.commit()
            return
        
        # Create job record
        job = AIReviewJob(
            ticket_id=ticket.id,
            user_id=ticket.owner_id,
            status='queued',
            total_sections=len(sections),
            completed_sections=0
        )
        db.session.add(job)
        db.session.commit()
        
        ai_logger.info(f"[AI REVIEW] Created job {job.id} for ticket {ticket.id} ({len(sections)} sections)")
        
        # Queue for background processing
        try:
            from worker import queue_ai_review_job
            rq_job = queue_ai_review_job(job.id)
            job.job_id = rq_job.id
            job.status = 'processing'
            db.session.commit()
            ai_logger.info(f"[AI REVIEW] Queued job {job.id} to Redis (RQ job {rq_job.id})")
        except Exception as e:
            # If Redis/RQ not available, process synchronously
            ai_logger.warning(f"[AI REVIEW] Redis/RQ not available, processing synchronously: {e}")
            job.status = 'processing'
            db.session.commit()
            
            # Process directly (blocking)
            _process_ai_review_job_sync(job.id, sections)
            
    except Exception as e:
        ai_logger.error(f"[AI REVIEW] Failed to start AI review for ticket {ticket.id}: {e}")


def _process_ai_review_job_sync(job_id: int, sections):
    """
    Process AI review job synchronously (fallback when Redis not available).
    
    Edge cases handled (all fail silently with logging):
    - Job deleted from database
    - Ticket deleted mid-review
    - No sections to process
    - Ollama failures (partial or complete)
    """
    import logging
    
    ai_logger = logging.getLogger('ai_review')
    
    job = db.session.get(AIReviewJob, job_id)
    
    # Edge case: Job was deleted
    if not job:
        ai_logger.warning(f"[AI REVIEW] Job {job_id} not found in database, skipping")
        return
    
    # Edge case: Ticket was deleted mid-review
    from models import Ticket
    ticket = db.session.get(Ticket, job.ticket_id)
    if not ticket:
        ai_logger.warning(f"[AI REVIEW] Ticket {job.ticket_id} was deleted, cancelling job {job_id}")
        job.status = 'cancelled'
        job.error_message = 'Ticket was deleted during review'
        db.session.commit()
        return
    
    # Edge case: No sections to process
    if not sections:
        ai_logger.warning(f"[AI REVIEW] Job {job_id}: No sections provided")
        job.status = 'failed'
        job.error_message = 'No sections extracted from PDF'
        db.session.commit()
        return
    
    try:
        from services.ai_reviewer import process_pdf_sections
        
        # Pass ticket_id and db_session for ticket existence check
        results = process_pdf_sections(sections, job_id=job.job_id, 
                                        ticket_id=job.ticket_id, db_session=db.session)
        
        # Check if results were skipped due to ticket deletion
        if not results and job.status == 'cancelled':
            ai_logger.warning(f"[AI REVIEW] Job {job_id} cancelled: ticket deleted during review")
            return
        
        # Store results
        for result in results:
            section = sections[result.section_index]
            
            # Create AIReviewSection record
            from models import AIReviewSection
            section_record = AIReviewSection(
                job_id=job.id,
                section_index=result.section_index,
                section_title=section.title,
                section_content_hash=section.content_hash,
                review=result.review if result.success else None,
                success=result.success,
                error_message=result.error if not result.success else None
            )
            db.session.add(section_record)
            
            job.completed_sections += 1
            
            # Create review comment for successful sections
            if result.success and result.review:
                review = Review(
                    ticket_id=job.ticket_id,
                    author_id=job.user_id,
                    body=f"**🤖 AI Review** ({section.title})\n\n{result.review}",
                    highlight_color='lightblue',
                    pdf_page=section.page_start
                )
                db.session.add(review)
        
        # Determine final status
        success_count = sum(1 for r in results if r.success)
        if success_count == len(results):
            job.status = 'completed'
            ai_logger.info(f"[AI REVIEW] Job {job_id} completed successfully ({success_count}/{len(results)} sections)")
        elif success_count > 0:
            job.status = 'partial'
            job.error_message = f'{success_count}/{len(results)} sections reviewed'
            ai_logger.warning(f"[AI REVIEW] Job {job_id} partially completed ({success_count}/{len(results)} sections)")
        else:
            job.status = 'failed'
            job.error_message = 'All sections failed to review'
            ai_logger.error(f"[AI REVIEW] Job {job_id} failed: all {len(results)} sections failed")
        
        job.completed_at = db.func.now()
        db.session.commit()
        
    except Exception as e:
        ai_logger.error(f"[AI REVIEW] Job {job_id} failed with exception: {e}")
        job.status = 'failed'
        job.error_message = str(e)
        job.completed_at = db.func.now()
        db.session.commit()


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
        request_ai_review = request.form.get('request_ai_review') == 'on'
        
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
            request_ai_review=request_ai_review,
        )
        db.session.add(ticket)
        db.session.commit()
        
        # Auto-start AI review if requested and PDF was uploaded
        if request_ai_review and pdf_filename:
            _start_ai_review_if_enabled(ticket)
        
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

    # Get all reviews for this ticket to pass to template for highlighting
    from models import Review
    reviews = Review.query.filter_by(ticket_id=ticket_id).all()
    reviews_json = [
        {
            'id': r.id,
            'body': r.body,
            'pdf_page': r.pdf_page,
            'highlight_x': r.highlight_x,
            'highlight_y': r.highlight_y,
            'highlight_width': r.highlight_width,
            'highlight_height': r.highlight_height,
            'highlight_color': r.highlight_color,
        }
        for r in reviews if r.pdf_page is not None
    ]

    return render_template('ticket_detail.html', ticket=ticket, Review=Review, initial_page=page, current_page=page, default_review_color=current_user.default_review_color, reviews_json=reviews_json)


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

    # ── Clean up children with NO ACTION FKs (must delete before parent) ──
    # 1. AIReviewSections + AIReviewJobs (use ORM to avoid FK violations)
    for job in ticket.ai_review_jobs.all():
        AIReviewSection.query.filter_by(job_id=job.id).delete(synchronize_session=False)
        db.session.delete(job)
    # 2. Verdicts (blocked by verdicts.ticket_id → NO ACTION)
    db.session.execute(db.text('DELETE FROM verdicts WHERE ticket_id = :tid'),
                       [{'tid': ticket_id}])
    # 3. Annotations (blocked by annotations.ticket_id → NO ACTION)
    db.session.execute(db.text('DELETE FROM annotations WHERE ticket_id = :tid'),
                       [{'tid': ticket_id}])

    # Delete the ticket (Review cascade works because Review.ticket has cascade="all, delete-orphan")
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
