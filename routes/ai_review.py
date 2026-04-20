"""
AI Review API routes - manage AI review jobs for tickets.

Endpoints:
- POST /api/ai-review/<ticket_id> - Start an AI review job
- GET /api/ai-review/<ticket_id>/status - Get job status
- GET /api/ai-review/<ticket_id>/results - Get completed review results
- DELETE /api/ai-review/<ticket_id> - Cancel a pending job
"""

import logging
import os
from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user

from models import db, Ticket, AIReviewJob, AIReviewSection, Review, User

logger = logging.getLogger(__name__)

ai_review_bp = Blueprint('ai_review', __name__)


def get_ollama_config():
    """Get Ollama configuration from environment or settings.
    
    IMPORTANT: Must use /v1/chat/completions endpoint (OpenAI-compatible).
    The /api/chat endpoint returns streaming NDJSON which is incompatible.
    """
    return {
        'endpoint': os.getenv('OLLAMA_ENDPOINT', 'http://10.51.5.169:11434/v1/chat/completions'),
        'model': os.getenv('OLLAMA_MODEL', 'gemma4:26b'),
        'timeout': int(os.getenv('OLLAMA_TIMEOUT', '120')),
        'max_retries': int(os.getenv('OLLAMA_MAX_RETRIES', '3')),
        'max_concurrent': int(os.getenv('OLLAMA_MAX_CONCURRENT', '10')),
    }


def check_ollama_available():
    """Check if Ollama is reachable."""
    import requests
    config = get_ollama_config()
    try:
        # Use /v1/models to check availability (OpenAI compat endpoint)
        check_url = config['endpoint'].replace('/v1/chat/completions', '/v1/models')
        response = requests.get(check_url, timeout=5)
        return response.status_code == 200
    except:
        return False


@ai_review_bp.route("/api/ai-review/<int:ticket_id>", methods=["POST"])
@login_required
def start_ai_review(ticket_id):
    """
    Start an AI review job for a ticket.
    
    Requires:
    - Ticket must exist and have a PDF attached
    - User must be owner of the ticket or admin
    
    Returns:
        JSON with job_id and initial status
    """
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    # Check PDF exists
    if not ticket.pdf_filename:
        return jsonify({'error': 'No PDF attached to this ticket'}), 400
    
    pdf_path = os.path.join(current_app.config['UPLOAD_FOLDER'], ticket.pdf_filename)
    if not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF file not found'}), 404
    
    # Check authorization - only ticket owner or admin can start AI review
    if ticket.owner_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'Only the ticket creator can start an AI review'}), 403
    
    # Check if there's already a pending/processing job
    existing_job = AIReviewJob.query.filter(
        AIReviewJob.ticket_id == ticket_id,
        AIReviewJob.status.in_(['queued', 'processing'])
    ).first()
    
    if existing_job:
        return jsonify({
            'error': 'A review is already in progress',
            'job_id': existing_job.id,
            'status': existing_job.status
        }), 409
    
    # Check if Ollama is configured and available
    config = get_ollama_config()
    
    # Create job record first (total_sections will be set by worker)
    job = AIReviewJob(
        ticket_id=ticket_id,
        user_id=current_user.id,
        status='queued',
        total_sections=0,  # Will be updated by worker when it chunks the PDF
        completed_sections=0
    )
    db.session.add(job)
    db.session.commit()
    
    # Queue the job for background processing
    try:
        from worker import queue_ai_review_job
        rq_job = queue_ai_review_job(job.id)
        job.job_id = rq_job.id
        job.status = 'processing'
        db.session.commit()
        
        return jsonify({
            'job_id': job.id,
            'status': job.status,
            'total_sections': job.total_sections,
            'completed_sections': job.completed_sections
        }), 202
        
    except Exception as e:
        # If Redis/RQ not available, process synchronously
        logger.warning(f"RQ not available, processing synchronously: {e}")
        job.status = 'processing'
        db.session.commit()
        
        # Process directly (blocking, but works)
        _process_job_sync(job.id, config, pdf_path)


def _process_job_sync(job_id: int, config, pdf_path):
    """
    Process AI review job synchronously (fallback when Redis not available).
    """
    from services.ai_reviewer import chunk_pdf_by_sections, process_pdf_sections
    
    job = db.session.get(AIReviewJob, job_id)
    if not job:
        return
    
    job.status = 'processing'
    db.session.commit()
    
    try:
        # Chunk the PDF (same as worker does)
        sections = chunk_pdf_by_sections(pdf_path)
        
        if not sections:
            job.status = 'failed'
            job.error_message = 'Could not extract text from PDF'
            db.session.commit()
            return
        
        # Update job with correct section count
        job.total_sections = len(sections)
        db.session.commit()
        
        results = process_pdf_sections(
            sections,
            job_id=job.job_id
        )
        
        # Store successful reviews
        for result in results:
            section = sections[result.section_index]
            
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
        
        # Create review comments for successful sections
        for result in results:
            if result.success and result.review:
                section = sections[result.section_index]
                
                # Create a review comment for this section
                review = Review(
                    ticket_id=job.ticket_id,
                    author_id=job.user_id,  # Attributed to requester
                    body=f"**🤖 AI Review** ({section.title})\n\n{result.review}",
                    highlight_color='lightblue',
                    pdf_page=section.page_start
                )
                db.session.add(review)
        
        job.status = 'completed'
        job.completed_at = db.func.now()
        db.session.commit()
        
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        job.status = 'failed'
        job.error_message = str(e)
        db.session.commit()


@ai_review_bp.route("/api/ai-review/<int:ticket_id>/status", methods=["GET"])
@login_required
def get_ai_review_status(ticket_id):
    """
    Get the status of the latest AI review job for a ticket.
    """
    job = AIReviewJob.query.filter(
        AIReviewJob.ticket_id == ticket_id
    ).order_by(AIReviewJob.created_at.desc()).first()
    
    if not job:
        return jsonify({'status': 'not_started', 'job_id': None}), 200
    
    return jsonify({
        'job_id': job.id,
        'status': job.status,
        'total_sections': job.total_sections,
        'completed_sections': job.completed_sections,
        'progress_percent': job.progress_percent,
        'error_message': job.error_message,
        'created_at': job.created_at.isoformat() if job.created_at else None,
        'completed_at': job.completed_at.isoformat() if job.completed_at else None
    }), 200


@ai_review_bp.route("/api/ai-review/<int:ticket_id>/results", methods=["GET"])
@login_required
def get_ai_review_results(ticket_id):
    """
    Get the results of a completed AI review.
    """
    job = AIReviewJob.query.filter(
        AIReviewJob.ticket_id == ticket_id,
        AIReviewJob.status == 'completed'
    ).order_by(AIReviewJob.created_at.desc()).first()
    
    if not job:
        return jsonify({'error': 'No completed review found'}), 404
    
    sections = AIReviewSection.query.filter_by(job_id=job.id).order_by(AIReviewSection.section_index).all()
    
    return jsonify({
        'job_id': job.id,
        'status': job.status,
        'total_sections': job.total_sections,
        'completed_sections': sum(1 for s in sections if s.success),
        'sections': [
            {
                'index': s.section_index,
                'title': s.section_title,
                'review': s.review,
                'success': s.success,
                'error': s.error_message,
                'created_at': s.created_at.isoformat() if s.created_at else None
            }
            for s in sections
        ]
    }), 200


@ai_review_bp.route("/api/ai-review/<int:ticket_id>", methods=["DELETE"])
@login_required
def cancel_ai_review(ticket_id):
    """
    Cancel a pending/processing AI review job.
    """
    job = AIReviewJob.query.filter(
        AIReviewJob.ticket_id == ticket_id,
        AIReviewJob.status.in_(['queued', 'processing'])
    ).first()
    
    if not job:
        return jsonify({'error': 'No active job found'}), 404
    
    # Check ownership or admin
    if job.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        # Try to cancel RQ job if exists
        if job.job_id:
            from worker import get_worker
            try:
                rq_job = get_worker().active_job(job.job_id)
                if rq_job:
                    rq_job.cancel()
            except:
                pass
    except:
        pass
    
    job.status = 'failed'
    job.error_message = 'Cancelled by user'
    db.session.commit()
    
    return jsonify({'message': 'Job cancelled'}), 200


@ai_review_bp.route("/api/ai-review/<int:ticket_id>/reset", methods=["POST"])
@login_required
def reset_ai_review(ticket_id):
    """
    Reset AI review state for a ticket.
    
    This deletes:
    - All AIReviewJob records for this ticket
    - All AIReviewSection records
    - All AI-generated reviews (marked with 🤖)
    
    Then resets ticket status to 'open' if it was 'in_review'.
    
    Only ticket owner or admin can reset.
    """
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    # Check authorization
    if ticket.owner_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    results = {
        'deleted_jobs': 0,
        'deleted_sections': 0,
        'deleted_reviews': 0,
        'status_reset': False
    }
    
    # 1. Get all jobs for this ticket
    jobs = AIReviewJob.query.filter_by(ticket_id=ticket_id).all()
    results['deleted_jobs'] = len(jobs)
    
    # 2. Delete sections for each job
    for job in jobs:
        sections = AIReviewSection.query.filter_by(job_id=job.id).all()
        results['deleted_sections'] += len(sections)
        for section in sections:
            db.session.delete(section)
        db.session.delete(job)
    
    # 3. Delete AI-generated reviews (marked with 🤖)
    ai_reviews = Review.query.filter(
        Review.ticket_id == ticket_id,
        Review.body.like('%🤖 AI Review%')
    ).all()
    results['deleted_reviews'] = len(ai_reviews)
    for review in ai_reviews:
        db.session.delete(review)
    
    # 4. Reset ticket status if it was in_review
    if ticket.status == 'in_review':
        ticket.status = 'open'
        results['status_reset'] = True
    
    # 5. Reset request_ai_review flag
    ticket.request_ai_review = False
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Reset complete. Deleted {results["deleted_jobs"]} jobs, {results["deleted_sections"]} sections, {results["deleted_reviews"]} reviews.',
        'details': results
    }), 200


@ai_review_bp.route("/api/ai-review/config", methods=["GET"])
@login_required
def get_ai_config():
    """Get current AI configuration (admin only)."""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin only'}), 403
    
    config = get_ollama_config()
    config['available'] = check_ollama_available()
    
    return jsonify(config), 200


@ai_review_bp.route("/api/ai-review/config", methods=["POST"])
@login_required
def update_ai_config():
    """Update AI configuration (admin only)."""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin only'}), 403
    
    data = request.get_json() or {}
    
    # Update environment variables (in-memory for current session)
    if 'endpoint' in data:
        os.environ['OLLAMA_ENDPOINT'] = data['endpoint']
    if 'model' in data:
        os.environ['OLLAMA_MODEL'] = data['model']
    if 'timeout' in data:
        os.environ['OLLAMA_TIMEOUT'] = str(data['timeout'])
    if 'max_retries' in data:
        os.environ['OLLAMA_MAX_RETRIES'] = str(data['max_retries'])
    if 'max_concurrent' in data:
        os.environ['OLLAMA_MAX_CONCURRENT'] = str(data['max_concurrent'])
    
    # For production, you'd want to persist this in a config file or database
    
    return jsonify({'message': 'Configuration updated'}), 200
