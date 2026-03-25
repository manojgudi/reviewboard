#!/usr/bin/env python3
"""
Test script for AI Review functionality using isolated test database.
DO NOT modify this script to touch the production database.
"""

import os
import sys

# CRITICAL: Ensure we're using the TEST database
TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_reviewboard.db")
PROD_DB_PATH = os.path.join(os.path.dirname(__file__), "reviewboard.db")

# Force testing mode
os.environ["TESTING"] = "1"

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app, db
from models import Ticket, User

def main():
    # Create app with TESTING mode (uses test_reviewboard.db)
    app = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{TEST_DB_PATH}"
    })
    
    with app.app_context():
        # Get ticket 1's PDF from PRODUCTION database (READ-ONLY)
        import sqlite3
        prod_conn = sqlite3.connect(PROD_DB_PATH)
        prod_cursor = prod_conn.cursor()
        prod_cursor.execute("SELECT id, title, pdf_filename FROM tickets WHERE id = 1")
        ticket_info = prod_cursor.fetchone()
        prod_conn.close()
        
        if not ticket_info:
            print("❌ Could not find ticket 1 in production database")
            return 1
        
        prod_ticket_id, prod_title, pdf_filename = ticket_info
        print(f"📄 Source ticket: {prod_ticket_id} - {prod_title}")
        print(f"📄 PDF file: {pdf_filename}")
        
        # Verify PDF exists
        pdf_path = os.path.join(app.config["UPLOAD_FOLDER"], pdf_filename)
        if not os.path.exists(pdf_path):
            print(f"❌ PDF not found: {pdf_path}")
            return 1
        
        pdf_size = os.path.getsize(pdf_path)
        print(f"✅ PDF file exists: {pdf_path} ({pdf_size:,} bytes)")
        
        # Check if test ticket already exists (cleanup from previous run)
        existing_test = Ticket.query.filter(
            Ticket.title.like("[TEST]%")
        ).first()
        
        if existing_test:
            print(f"🧹 Cleaning up previous test ticket ID: {existing_test.id}")
            db.session.delete(existing_test)
            db.session.commit()
        
        # Get admin user for test ticket
        admin_user = User.query.filter_by(role="admin").first()
        if not admin_user:
            print("❌ No admin user found in database")
            return 1
        print(f"✅ Using admin user: {admin_user.username} (ID: {admin_user.id})")
        
        # Create a test ticket in the TEST database
        from datetime import datetime
        test_ticket = Ticket(
            title=f"[TEST] AI Review Test - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            description="This is a test ticket created by test_ai_review.py. Safe to delete.",
            status="open",
            pdf_filename=pdf_filename,  # Use the same PDF
            owner_id=admin_user.id
        )
        
        db.session.add(test_ticket)
        db.session.commit()
        
        print(f"✅ Created test ticket ID: {test_ticket.id}")
        
        # Queue the AI review job
        print(f"📤 Queuing AI review job for ticket {test_ticket.id}...")
        
        from worker import queue_ai_review_job
        
        try:
            rq_job = queue_ai_review_job(test_ticket.id)
            print(f"✅ Job enqueued! RQ Job ID: {rq_job.id}")
            print(f"⏳ Check the RQ worker output to see processing...")
            print(f"📝 Worker logs: tail -f ai_review.log")
            print(f"📝 Job status: rq info")
        except Exception as e:
            print(f"❌ Failed to enqueue job: {e}")
            return 1
        
        print("\n" + "="*60)
        print("TEST SETUP COMPLETE")
        print("="*60)
        print(f"Test Ticket ID: {test_ticket.id}")
        print(f"RQ Job ID: {rq_job.id}")
        print(f"Test DB: {TEST_DB_PATH}")
        print("\nWatch worker logs for processing:")
        print("  tail -f /home/miniluv/.picoclaw/workspace/reviewboard/ai_review.log")
        print("\nOr check queue status:")
        print("  cd /home/miniluv/.picoclaw/workspace/reviewboard && .venv/bin/rq info")
        print("="*60)
        
        return 0

if __name__ == "__main__":
    sys.exit(main())
