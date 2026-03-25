#!/usr/bin/env python3
"""Reset AI review status for a ticket.

Usage:
    python reset_ai_review.py <ticket_id>           # Reset only
    python reset_ai_review.py <ticket_id> --retry   # Reset and restart review
    python reset_ai_review.py <ticket_id> --status # Show status only
    python reset_ai_review.py --list-stuck         # List stuck jobs
    python reset_ai_review.py --fix-stuck           # Fix all stuck jobs
"""

import sys
import os
os.environ['SECRET_KEY'] = 'dev-secret-key-for-reset-script'
sys.path.insert(0, '/home/miniluv/.picoclaw/workspace/reviewboard')

from app import create_app, db
from models import Ticket, AIReviewJob, AIReviewSection, Review
from datetime import datetime, timedelta, timezone

app = create_app()


def reset_ticket(ticket_id, retry=False):
    """Reset AI review state for a single ticket."""
    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        if not ticket:
            print(f"❌ Ticket #{ticket_id} not found")
            return False
        
        print(f"\n{'='*60}")
        print(f"🔄  Resetting AI Review for Ticket #{ticket_id}")
        print(f"    Title: {ticket.title}")
        print(f"{'='*60}")
        
        deleted_jobs = []
        deleted_sections = 0
        
        # Get all jobs for this ticket
        jobs = AIReviewJob.query.filter_by(ticket_id=ticket_id).all()
        for job in jobs:
            deleted_jobs.append({
                'id': job.id,
                'status': job.status,
                'rq_job_id': job.job_id
            })
            
            # Cancel RQ job if exists
            if job.job_id:
                try:
                    import redis
                    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
                    r = redis.from_url(REDIS_URL)
                    rq_key = f"rq:job:{job.job_id}"
                    if r.exists(rq_key):
                        r.delete(rq_key)
                        print(f"   ✅ Cancelled RQ job: {job.job_id}")
                except Exception as e:
                    print(f"   ⚠️  Could not cancel RQ job {job.job_id}: {e}")
            
            # Count and delete sections
            sections = AIReviewSection.query.filter_by(job_id=job.id).all()
            deleted_sections += len(sections)
            for section in sections:
                db.session.delete(section)
            
            # Delete job
            db.session.delete(job)
            print(f"   🗑️  Deleted job #{job.id} ({job.status})")
        
        # Delete AI-generated review comments
        ai_reviews = Review.query.filter(
            Review.ticket_id == ticket_id,
            Review.body.like('%🤖 AI Review%')
        ).all()
        for review in ai_reviews:
            db.session.delete(review)
        
        db.session.commit()
        
        print(f"\n   📊 Summary:")
        print(f"      - Jobs deleted: {len(deleted_jobs)}")
        print(f"      - Sections deleted: {deleted_sections}")
        print(f"      - AI reviews deleted: {len(ai_reviews)}")
        
        # Optionally retry
        if retry:
            if not ticket.pdf_filename:
                print(f"\n   ⚠️  No PDF attached - cannot retry")
            else:
                print(f"\n   🚀 Starting new AI review...")
                try:
                    from services.ai_reviewer import chunk_pdf_by_sections
                    from worker import queue_ai_review_job
                    
                    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], ticket.pdf_filename)
                    if not os.path.exists(pdf_path):
                        print(f"   ❌ PDF file not found: {ticket.pdf_filename}")
                        return True  # Still a success for reset
                    
                    sections = chunk_pdf_by_sections(pdf_path)
                    if not sections:
                        print(f"   ❌ Could not extract text from PDF")
                        return True
                    
                    # Create new job
                    new_job = AIReviewJob(
                        ticket_id=ticket_id,
                        user_id=ticket.owner_id,
                        status='queued',
                        total_sections=len(sections),
                        completed_sections=0
                    )
                    db.session.add(new_job)
                    db.session.commit()
                    
                    # Queue it
                    rq_job = queue_ai_review_job(new_job.id)
                    new_job.job_id = rq_job.id
                    new_job.status = 'processing'
                    db.session.commit()
                    
                    print(f"   ✅ New job #{new_job.id} queued (RQ: {rq_job.id})")
                    print(f"      - Sections: {len(sections)}")
                    
                except Exception as e:
                    print(f"   ❌ Failed to start review: {e}")
                    import traceback
                    traceback.print_exc()
        
        print(f"\n✅ Reset complete! Ticket #{ticket_id} is ready for a fresh AI review.")
        return True


def show_status(ticket_id):
    """Show AI review status for a ticket."""
    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        if not ticket:
            print(f"❌ Ticket #{ticket_id} not found")
            return
        
        print(f"\n{'='*60}")
        print(f"📋  AI Review Status for Ticket #{ticket_id}")
        print(f"    Title: {ticket.title}")
        print(f"{'='*60}")
        
        jobs = AIReviewJob.query.filter_by(ticket_id=ticket_id).order_by(AIReviewJob.created_at.desc()).all()
        
        if not jobs:
            print("\n   No AI review jobs found.")
            return
        
        for job in jobs:
            age = ""
            if job.created_at:
                age = f" ({int((datetime.now(timezone.utc) - job.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 60)} min ago)"
            
            print(f"\n   Job #{job.id} - {job.status}{age}")
            print(f"      Sections: {job.completed_sections}/{job.total_sections} ({job.progress_percent}%)")
            print(f"      RQ Job ID: {job.job_id or 'N/A'}")
            
            if job.error_message:
                print(f"      Error: {job.error_message[:100]}...")
            
            # Check RQ status
            if job.job_id:
                try:
                    import redis
                    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
                    r = redis.from_url(REDIS_URL)
                    rq_key = f"rq:job:{job.job_id}"
                    if r.exists(rq_key):
                        print(f"      RQ Status: ✅ Active in Redis")
                    else:
                        print(f"      RQ Status: ⚠️  Not in Redis (may be stale)")
                except Exception as e:
                    print(f"      RQ Status: ⚠️  Could not check: {e}")
        
        # AI reviews count
        ai_reviews = Review.query.filter(
            Review.ticket_id == ticket_id,
            Review.body.like('%🤖 AI Review%')
        ).count()
        print(f"\n   AI-generated reviews: {ai_reviews}")


def list_stuck_jobs():
    """List all stuck jobs."""
    with app.app_context():
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        
        stuck_jobs = AIReviewJob.query.filter(
            AIReviewJob.status == 'processing',
            AIReviewJob.created_at < cutoff
        ).all()
        
        print(f"\n{'='*60}")
        print(f"⚠️  Stuck Jobs (processing > 10 min)")
        print(f"{'='*60}")
        
        if not stuck_jobs:
            print("\n   ✅ No stuck jobs found!")
            return
        
        for job in stuck_jobs:
            age = int((datetime.now(timezone.utc) - job.created_at).total_seconds() / 60) if job.created_at else 0
            ticket_title = job.ticket.title if job.ticket else "[DELETED]"
            print(f"\n   Ticket #{job.ticket_id}: {ticket_title}")
            print(f"   Job #{job.id} - {age} minutes old")
            print(f"   RQ Job ID: {job.job_id or 'N/A'}")
            print(f"   Progress: {job.completed_sections}/{job.total_sections}")
        
        print(f"\n   Total: {len(stuck_jobs)} stuck job(s)")
        print(f"\n   To fix: python reset_ai_review.py --fix-stuck")


def fix_stuck_jobs():
    """Reset all stuck jobs."""
    with app.app_context():
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        
        stuck_jobs = AIReviewJob.query.filter(
            AIReviewJob.status == 'processing',
            AIReviewJob.created_at < cutoff
        ).all()
        
        print(f"\n{'='*60}")
        print(f"🔧  Fixing {len(stuck_jobs)} stuck job(s)")
        print(f"{'='*60}")
        
        for job in stuck_jobs:
            print(f"\n   Resetting Job #{job.id} (Ticket #{job.ticket_id})...")
            reset_ticket(job.ticket_id, retry=True)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Reset AI review state')
    parser.add_argument('ticket_id', nargs='?', type=int, help='Ticket ID to reset')
    parser.add_argument('--retry', action='store_true', help='Restart review after reset')
    parser.add_argument('--status', action='store_true', help='Show status only')
    parser.add_argument('--list-stuck', action='store_true', help='List stuck jobs')
    parser.add_argument('--fix-stuck', action='store_true', help='Fix all stuck jobs')
    
    args = parser.parse_args()
    
    if args.list_stuck:
        list_stuck_jobs()
    elif args.fix_stuck:
        fix_stuck_jobs()
    elif args.ticket_id:
        if args.status:
            show_status(args.ticket_id)
        else:
            reset_ticket(args.ticket_id, retry=args.retry)
    else:
        parser.print_help()
        print(f"\nExamples:")
        print(f"  python reset_ai_review.py 5              # Reset ticket #5")
        print(f"  python reset_ai_review.py 5 --retry      # Reset and restart")
        print(f"  python reset_ai_review.py 5 --status     # Show status")
        print(f"  python reset_ai_review.py --list-stuck   # Show stuck jobs")
        print(f"  python reset_ai_review.py --fix-stuck     # Fix all stuck jobs")
