"""
Redis Queue worker for AI review jobs.

Run with: rq worker ai-review-queue
"""

import logging
import os

import redis
from rq import Queue, Worker

from app import create_app
from models import db, AIReviewJob, AIReviewSection, Review

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
    """
    from services.ai_reviewer import (
        chunk_pdf_by_sections,
        process_pdf_sections,
        get_ollama_config
    )
    
    app = create_app()
    
    with app.app_context():
        # Load job from database
        job = db.session.get(AIReviewJob, job_id)
        
        if not job:
            logger.error(f"AIReviewJob {job_id} not found")
            return
        
        # Get ticket and PDF path
        ticket = job.ticket
        if not ticket or not ticket.pdf_filename:
            job.status = 'failed'
            job.error_message = 'Ticket or PDF not found'
            db.session.commit()
            return
        
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], ticket.pdf_filename)
        if not os.path.exists(pdf_path):
            job.status = 'failed'
            job.error_message = 'PDF file not found'
            db.session.commit()
            return
        
        # Get Ollama config
        config = get_ollama_config()
        
        try:
            job.status = 'processing'
            db.session.commit()
            
            # Chunk the PDF
            logger.info(f"Starting AI review for job {job_id}, ticket {job.ticket_id}")
            sections = chunk_pdf_by_sections(pdf_path)
            
            if not sections:
                job.status = 'failed'
                job.error_message = 'Could not extract text from PDF'
                db.session.commit()
                return
            
            # Update job with correct section count
            job.total_sections = len(sections)
            db.session.commit()
            
            # Process sections concurrently
            results = process_pdf_sections(
                sections,
                job_id=job.job_id
            )
            
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
                    review = Review(
                        ticket_id=job.ticket_id,
                        author_id=job.user_id,  # Attributed to the user who requested
                        body=f"**🤖 AI Review** ({section.title})\n\n{result.review}",
                        highlight_color='lightblue',
                        pdf_page=section.page_start
                    )
                    db.session.add(review)
                    db.session.commit()
            
            # Mark job complete
            job.status = 'completed'
            job.completed_at = db.func.now()
            db.session.commit()
            
            logger.info(f"Completed AI review job {job_id}")
            
        except Exception as e:
            logger.error(f"AI review job {job_id} failed: {e}")
            job.status = 'failed'
            job.error_message = str(e)
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
