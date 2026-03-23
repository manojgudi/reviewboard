"""
Migration: Add AI Review tables (AIReviewJob, AIReviewSection)

Run with: python migrations/add_ai_review.py
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from models import AIReviewJob, AIReviewSection


def run_migration():
    app = create_app()
    
    with app.app_context():
        # Create the new tables
        db.create_all()
        print("✅ Created AI review tables: ai_review_jobs, ai_review_sections")
        
        # Verify tables exist
        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()
        
        if 'ai_review_jobs' in tables and 'ai_review_sections' in tables:
            print("✅ Tables verified successfully")
        else:
            print("❌ Tables not created properly")
            return False
            
    return True


if __name__ == '__main__':
    run_migration()
