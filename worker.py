"""
Redis Queue worker for AI review jobs.

Run with: rq worker ai-review-queue
"""

import logging
import os

import redis
from rq import Queue, Worker

from app import create_app
from models import db, AIReviewJob, AIReviewSection, Review, Ticket

logger = logging.getLogger(__name__)

# Redis connection
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Create Redis connection
redis_conn = redis.from_url(REDIS_URL)

# Queue name
AI_REVIEW_QUEUE = 'ai-review-queue'

# Create queue
ai_review_queue = Queue(AI_REVIEW_QUEUE, connection=redis_conn)


def get_worker():
    """Get RQ worker instance."""
    return Worker(AI_REVIEW_QUEUE, connection=redis_conn)


def queue_ai_review_job(job_id: int):
    """
    Queue an AI review job for background processing.
    
    Args:
        job_id: The AIReviewJob ID in the database
        
    Returns:
        The RQ Job object
    """
    job = ai_review_queue.enqueue(
        'worker.process_ai_review',
        job_id,
        job_timeout='30m',  # 30 minute timeout
        result_ttl=86400  # Keep results for 24 hours
    )
    
    logger.info(f"Queued AI review job {job_id} as RQ job {job.id}")
    return job


def process_ai_review(job_id: int):
    """
    Background task to process AI review for a PDF.
    
    This function runs in a separate worker process.
    
    Edge cases handled (all fail silently with logging):
    - Job deleted from database
    - Ticket deleted mid-review
    - PDF file missing
    - PDF has no extractable text
    - Ollama failures (partial or complete)
    """
    import logging
    
    ai_logger = logging.getLogger('ai_review')
    
    from services.ai_reviewer import (
        chunk_pdf_by_sections,
        process_pdf_sections
    )
    
    app = create_app()
    
    with app.app_context():
        # Load job from database
        job = db.session.get(AIReviewJob, job_id)
        
        # Edge case: Job was deleted
        if not job:
            ai_logger.warning(f"[AI REVIEW] Worker: Job {job_id} not found in database")
            return
        
        # Get ticket
        ticket = job.ticket
        
        # Edge case: Ticket was deleted
        if not ticket:
            ai_logger.warning(f"[AI REVIEW] Worker: Ticket {job.ticket_id} was deleted, cancelling job {job_id}")
            job.status = 'cancelled'
            job.error_message = 'Ticket was deleted during review'
            db.session.commit()
            return
        
        # Edge case: No PDF attached
        if not ticket.pdf_filename:
            ai_logger.warning(f"[AI REVIEW] Worker: Ticket {job.ticket_id} has no PDF")
            job.status = 'failed'
            job.error_message = 'No PDF attached to ticket'
            db.session.commit()
            return
        
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], ticket.pdf_filename)
        
        # Edge case: PDF file missing
        if not os.path.exists(pdf_path):
            ai_logger.warning(f"[AI REVIEW] Worker: PDF file not found: {ticket.pdf_filename}")
            job.status = 'failed'
            job.error_message = 'PDF file not found on disk'
            db.session.commit()
            return
        
        try:
            job.status = 'processing'
            db.session.commit()
            
            ai_logger.info(f"[AI REVIEW] Worker: Starting job {job_id} for ticket {job.ticket_id}")
            
            # Chunk the PDF
            sections = chunk_pdf_by_sections(pdf_path)
            
            # Edge case: No text extracted (scanned PDF or corrupted)
            if not sections:
                ai_logger.warning(f"[AI REVIEW] Worker: No text extracted from PDF for job {job_id}")
                job.status = 'failed'
                job.error_message = 'No text could be extracted from PDF. The file may be scanned or corrupted.'
                db.session.commit()
                return
            
            # Update job with correct section count
            job.total_sections = len(sections)
            db.session.commit()
            
            ai_logger.info(f"[AI REVIEW] Worker: Processing {len(sections)} sections for job {job_id}")
            
            # Process sections concurrently - pass ticket_id and db_session for ticket existence check
            results = process_pdf_sections(
                sections,
                job_id=job.job_id,
                ticket_id=job.ticket_id,
                db_session=db.session
            )
            
            # Check if cancelled due to ticket deletion during processing
            job = db.session.get(AIReviewJob, job_id)
            if not job or job.status == 'cancelled':
                ai_logger.warning(f"[AI REVIEW] Worker: Job {job_id} was cancelled (ticket deleted)")
                return
            
            # Store results
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
                
                # Update job progress
                job.completed_sections = AIReviewSection.query.filter_by(
                    job_id=job.id
                ).count()
                db.session.commit()
                
                # Create review comment for successful sections
                if result.success and result.review:
                    # Check ticket still exists before creating review
                    ticket = db.session.get(Ticket, job.ticket_id)
                    if ticket:
                        review = Review(
                            ticket_id=job.ticket_id,
                            author_id=job.user_id,  # Attributed to the user who requested
                            body=f"**🤖 AI Review** ({section.title})\n\n{result.review}",
                            highlight_color='lightblue',
                            pdf_page=section.page_start
                        )
                        db.session.add(review)
                        db.session.commit()
                    else:
                        ai_logger.warning(f"[AI REVIEW] Worker: Ticket deleted, skipping review creation for section {result.section_index}")
            
            # Reload job to get final state
            job = db.session.get(AIReviewJob, job_id)
            
            # Determine final status
            success_count = sum(1 for r in results if r.success)
            if success_count == len(results):
                job.status = 'completed'
                ai_logger.info(f"[AI REVIEW] Worker: Job {job_id} completed successfully ({success_count}/{len(results)} sections)")
            elif success_count > 0:
                job.status = 'partial'
                job.error_message = f'{success_count}/{len(results)} sections reviewed'
                ai_logger.warning(f"[AI REVIEW] Worker: Job {job_id} partially completed ({success_count}/{len(results)} sections)")
            else:
                job.status = 'failed'
                job.error_message = 'All sections failed to review'
                ai_logger.error(f"[AI REVIEW] Worker: Job {job_id} failed: all {len(results)} sections failed")
            
            job.completed_at = db.func.now()
            db.session.commit()
            
        except Exception as e:
            ai_logger.error(f"[AI REVIEW] Worker: Job {job_id} failed with exception: {e}")
            # Reload job in case session was invalidated
            job = db.session.get(AIReviewJob, job_id)
            if job:
                job.status = 'failed'
                job.error_message = str(e)
                job.completed_at = db.func.now()
                db.session.commit()
            raise


if __name__ == '__main__':
    # Run worker directly for testing
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    print(f"Starting RQ worker on queue: {AI_REVIEW_QUEUE}")
    print(f"Redis: {REDIS_URL}")
    
    worker = Worker([AI_REVIEW_QUEUE], connection=redis_conn)
    worker.work()
