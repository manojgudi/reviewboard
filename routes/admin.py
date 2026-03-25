"""Admin routes for user management (admin only)."""

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, current_app, jsonify
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, SubmitField
from wtforms.validators import DataRequired, Length, Email
import os

from models import db, User, Ticket, Review, AIReviewJob, AIReviewSection, Annotation, Verdict

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


class CreateUserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(3, 80)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    full_name = StringField("Full Name", validators=[Length(max=200)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6)])
    role = SelectField("Role", choices=[("reviewer", "Reviewer"), ("admin", "Admin")], 
                       validators=[DataRequired()])
    submit = SubmitField("Create User")


def _get_bcrypt():
    """Import bcrypt from app module to avoid circular imports."""
    from app import bcrypt
    return bcrypt


@admin_bp.route('/users')
@login_required
@admin_required
def users():
    users = User.query.order_by(User.username).all()
    return render_template('admin/users.html', users=users)


@admin_bp.route('/users/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    form = CreateUserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash("Username already taken.", "danger")
            return render_template('admin/user_create.html', form=form)
        if User.query.filter_by(email=form.email.data).first():
            flash("Email already registered.", "danger")
            return render_template('admin/user_create.html', form=form)
        
        pw_hash = _get_bcrypt().generate_password_hash(form.password.data).decode("utf-8")
        user = User(
            username=form.username.data,
            email=form.email.data,
            full_name=form.full_name.data or "",
            password_hash=pw_hash,
            role=form.role.data,
        )
        db.session.add(user)
        db.session.commit()
        flash(f"User '{user.username}' created successfully.", "success")
        return redirect(url_for('admin.users'))
    return render_template('admin/user_create.html', form=form)


@admin_bp.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    is_self = user.id == current_user.id
    
    if request.method == 'POST':
        user.full_name = request.form.get('full_name', user.full_name)
        user.email = request.form.get('email', user.email)
        
        if not is_self:
            role = request.form.get('role')
            if role in ('admin', 'reviewer'):
                user.role = role
        
        db.session.commit()
        flash('User updated', 'success')
        return redirect(url_for('admin.users'))
    
    return render_template('admin/user_edit.html', user=user, is_self=is_self)


@admin_bp.route('/users/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Delete a user and all their associated data."""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('You cannot delete yourself', 'danger')
        return redirect(url_for('admin.users'))

    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')

    # ── Get user's tickets (needed for PDF cleanup + AI job cleanup below) ──
    tickets = Ticket.query.filter_by(owner_id=user.id).all()

    # ── 1. Verdicts authored by this user (blocked by verdicts.user_id → NO ACTION) ──
    Verdict.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    # ── 2. AIReviewJobs where this user is the requester ──
    # Sections blocked by job_id → NO ACTION; use ORM to cascade properly
    user_jobs = AIReviewJob.query.filter_by(user_id=user.id).all()
    for job in user_jobs:
        # Delete sections for each job (avoids FK NO ACTION on job_id)
        AIReviewSection.query.filter_by(job_id=job.id).delete(synchronize_session=False)
        db.session.delete(job)

    # ── 3. AIReviewJobs on user's tickets (blocked by ai_review_jobs.ticket_id → NO ACTION) ──
    for ticket in tickets:
        ticket_jobs = AIReviewJob.query.filter_by(ticket_id=ticket.id).all()
        for job in ticket_jobs:
            AIReviewSection.query.filter_by(job_id=job.id).delete(synchronize_session=False)
            db.session.delete(job)

    # ── 4. PDF files on disk ──
    for ticket in tickets:
        if ticket.pdf_filename:
            pdf_path = os.path.join(upload_folder, ticket.pdf_filename)
            if os.path.isfile(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass

    # ── 5. Annotations authored by this user (blocked by annotations.author_id → NO ACTION) ──
    Annotation.query.filter_by(author_id=user.id).delete(synchronize_session=False)

    # ── 6. Reviews authored by this user (blocked by reviews.author_id → NO ACTION) ──
    Review.query.filter_by(author_id=user.id).delete(synchronize_session=False)

    # ── 7. Delete all tickets (cascades to reviews via Review.ticket cascade="all,delete-orphan") ──
    # Verdicts/Annotations on these tickets are now orphaned; NO ACTION means no DB error,
    # but explicit cleanup is done by delete_ticket in normal usage.
    Ticket.query.filter_by(owner_id=user.id).delete(synchronize_session=False)

    # ── 8. Delete user ──
    db.session.delete(user)

    db.session.commit()

    flash(f"User '{user.username}' and all associated data deleted", 'success')
    return redirect(url_for('admin.users'))


# =============================================================================
# AI Review Management Routes
# =============================================================================

@admin_bp.route('/ai-review/<int:ticket_id>/reset', methods=['POST'])
@login_required
@admin_required
def reset_ai_review(ticket_id):
    """
    Reset AI review state for a ticket.
    
    This endpoint:
    1. Cancels any active RQ jobs
    2. Deletes all AIReviewJob and AIReviewSection records
    3. Deletes AI-generated review comments (marked with 🤖)
    4. Optionally re-queues a new job
    
    Query params:
        - retry=1: Automatically restart the review after reset
    
    Returns:
        JSON with reset details
    """
    import json
    
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    reset_details = {
        'ticket_id': ticket_id,
        'ticket_title': ticket.title,
        'deleted_jobs': [],
        'deleted_sections': 0,
        'deleted_reviews': 0,
        'rq_jobs_cancelled': [],
    }
    
    # 1. Cancel any RQ jobs in Redis
    jobs = AIReviewJob.query.filter_by(ticket_id=ticket_id).all()
    for job in jobs:
        if job.job_id:
            try:
                from worker import get_worker
                rq_worker = get_worker()
                try:
                    rq_job = rq_worker.active_job(job.job_id)
                    if rq_job:
                        rq_job.cancel()
                        reset_details['rq_jobs_cancelled'].append(job.job_id)
                except:
                    # Try to cancel via Redis directly
                    from worker import redis_conn
                    rq_key = f"rq:job:{job.job_id}"
                    if redis_conn.exists(rq_key):
                        redis_conn.delete(rq_key)
                        reset_details['rq_jobs_cancelled'].append(job.job_id)
            except Exception as e:
                current_app.logger.warning(f"Could not cancel RQ job {job.job_id}: {e}")
        
        reset_details['deleted_jobs'].append({
            'id': job.id,
            'status': job.status,
            'created_at': job.created_at.isoformat() if job.created_at else None
        })
        
        # Count sections before deletion
        sections = AIReviewSection.query.filter_by(job_id=job.id).all()
        reset_details['deleted_sections'] += len(sections)
        
        # Delete sections
        for section in sections:
            db.session.delete(section)
        
        # Delete job
        db.session.delete(job)
    
    # 2. Delete AI-generated review comments
    ai_reviews = Review.query.filter(
        Review.ticket_id == ticket_id,
        Review.body.like('%🤖 AI Review%')
    ).all()
    reset_details['deleted_reviews'] = len(ai_reviews)
    for review in ai_reviews:
        db.session.delete(review)
    
    db.session.commit()
    
    # 3. Optionally re-queue a new job
    should_retry = request.args.get('retry', '0') == '1'
    new_job_id = None
    
    if should_retry:
        if not ticket.pdf_filename:
            reset_details['warning'] = 'No PDF attached, cannot retry'
        else:
            try:
                from routes.ai_review import start_ai_review
                # We need to call this without the request context
                # Instead, do it directly
                from services.ai_reviewer import chunk_pdf_by_sections
                import os
                
                pdf_path = os.path.join(current_app.config['UPLOAD_FOLDER'], ticket.pdf_filename)
                if os.path.exists(pdf_path):
                    sections = chunk_pdf_by_sections(pdf_path)
                    if sections:
                        new_job = AIReviewJob(
                            ticket_id=ticket_id,
                            user_id=current_user.id,
                            status='queued',
                            total_sections=len(sections),
                            completed_sections=0
                        )
                        db.session.add(new_job)
                        db.session.commit()
                        
                        # Queue the job
                        from worker import queue_ai_review_job
                        rq_job = queue_ai_review_job(new_job.id)
                        new_job.job_id = rq_job.id
                        new_job.status = 'processing'
                        db.session.commit()
                        
                        new_job_id = new_job.id
                        reset_details['new_job_id'] = new_job_id
                        reset_details['status'] = 'retry_started'
                    else:
                        reset_details['warning'] = 'Could not extract sections from PDF'
                else:
                    reset_details['warning'] = 'PDF file not found on disk'
            except Exception as e:
                reset_details['error'] = f'Failed to start retry: {str(e)}'
                current_app.logger.error(f"Failed to retry AI review for ticket {ticket_id}: {e}")
    else:
        reset_details['status'] = 'reset_complete'
    
    return jsonify(reset_details), 200


@admin_bp.route('/ai-review/<int:ticket_id>/status', methods=['GET'])
@login_required
@admin_required
def ai_review_status(ticket_id):
    """Get detailed AI review status for admin debugging."""
    import json
    
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    jobs = AIReviewJob.query.filter_by(ticket_id=ticket_id).order_by(AIReviewJob.created_at.desc()).all()
    ai_reviews = Review.query.filter(
        Review.ticket_id == ticket_id,
        Review.body.like('%🤖 AI Review%')
    ).all()
    
    # Check RQ status for active jobs
    rq_status = []
    for job in jobs:
        if job.job_id:
            try:
                from worker import redis_conn
                rq_key = f"rq:job:{job.job_id}"
                exists = redis_conn.exists(rq_key)
                status = 'active' if exists else 'not_found'
                
                # Get job data
                if exists:
                    job_data = redis_conn.hgetall(rq_key)
                    rq_status.append({
                        'job_id': job.job_id,
                        'rq_exists': True,
                        'status': job_data.get(b'status', b'unknown').decode() if job_data else 'unknown',
                    })
                else:
                    rq_status.append({
                        'job_id': job.job_id,
                        'rq_exists': False,
                        'status': 'removed'
                    })
            except Exception as e:
                rq_status.append({
                    'job_id': job.job_id,
                    'rq_exists': 'unknown',
                    'error': str(e)
                })
    
    return jsonify({
        'ticket_id': ticket_id,
        'ticket_title': ticket.title,
        'ticket_status': ticket.status,
        'jobs': [
            {
                'id': j.id,
                'status': j.status,
                'total_sections': j.total_sections,
                'completed_sections': j.completed_sections,
                'progress_percent': j.progress_percent,
                'error_message': j.error_message,
                'job_id': j.job_id,
                'created_at': j.created_at.isoformat() if j.created_at else None,
                'completed_at': j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in jobs
        ],
        'ai_reviews_count': len(ai_reviews),
        'rq_jobs': rq_status
    }), 200


# =============================================================================
# Utility endpoint - list all stuck jobs across all tickets
# =============================================================================

@admin_bp.route('/ai-review/stuck-jobs', methods=['GET'])
@login_required
@admin_required
def list_stuck_jobs():
    """List all stuck/processing jobs that might need attention."""
    import json
    
    # Find jobs stuck in 'processing' for more than 10 minutes
    from datetime import datetime, timedelta, timezone
    
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    
    stuck_jobs = AIReviewJob.query.filter(
        AIReviewJob.status == 'processing',
        AIReviewJob.created_at < cutoff
    ).all()
    
    return jsonify({
        'stuck_jobs': [
            {
                'job_id': j.id,
                'ticket_id': j.ticket_id,
                'ticket_title': j.ticket.title if j.ticket else 'DELETED',
                'status': j.status,
                'created_at': j.created_at.isoformat() if j.created_at else None,
                'rq_job_id': j.job_id,
                'age_minutes': int((datetime.now(timezone.utc) - j.created_at).total_seconds() / 60) if j.created_at else 0
            }
            for j in stuck_jobs
        ],
        'total_stuck': len(stuck_jobs)
    }), 200


# =============================================================================
# Online Users Route
# =============================================================================

@admin_bp.route('/online-users')
@login_required
@admin_required
def online_users():
    """Show currently active users (active within last 5 minutes)."""
    from datetime import datetime, timezone, timedelta
    
    # Get all users
    all_users = User.query.order_by(User.username).all()
    
    # Calculate online status
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=5)
    
    online_users = []
    offline_users = []
    
    for user in all_users:
        is_online = False
        if user.last_seen:
            # Ensure timezone-aware comparison
            last_seen = user.last_seen
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            is_online = last_seen >= cutoff
        
        user_info = {
            'user': user,
            'is_online': is_online,
            'last_seen': user.last_seen,
            'minutes_ago': None
        }
        
        if is_online:
            minutes_ago = int((now - last_seen).total_seconds() / 60)
            user_info['minutes_ago'] = minutes_ago if minutes_ago >= 1 else 'just now'
            online_users.append(user_info)
        else:
            if user.last_seen:
                minutes_ago = int((now - last_seen).total_seconds() / 60)
                if minutes_ago >= 60:
                    user_info['minutes_ago'] = f"{minutes_ago // 60}h ago"
                else:
                    user_info['minutes_ago'] = f"{minutes_ago}m ago"
            offline_users.append(user_info)
    
    return render_template('admin/online_users.html', 
                         online_users=online_users,
                         offline_users=offline_users,
                         online_count=len(online_users),
                         total_count=len(all_users))
