#!/usr/bin/env python3
"""Migration: Add request_ai_review column to tickets table."""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from models import db

def run_migration():
    with app.app_context():
        # Check if column exists
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('tickets')]
        
        if 'request_ai_review' in columns:
            print("✓ Column 'request_ai_review' already exists in tickets table")
            return
        
        # Add column
        try:
            db.session.execute(db.text(
                "ALTER TABLE tickets ADD COLUMN request_ai_review BOOLEAN NOT NULL DEFAULT 0"
            ))
            db.session.commit()
            print("✓ Added 'request_ai_review' column to tickets table")
        except Exception as e:
            print(f"✗ Error adding column: {e}")
            db.session.rollback()
            # Try SQLite-specific approach
            try:
                # SQLite doesn't support ALTER TABLE ADD COLUMN with NOT NULL
                # Need to recreate table or use a workaround
                db.session.execute(db.text(
                    "ALTER TABLE tickets ADD COLUMN request_ai_review BOOLEAN DEFAULT 0"
                ))
                db.session.commit()
                print("✓ Added 'request_ai_review' column (nullable)")
                
                # Update existing rows to have False
                db.session.execute(db.text(
                    "UPDATE tickets SET request_ai_review = 0 WHERE request_ai_review IS NULL"
                ))
                db.session.commit()
                
                # Now try to make it NOT NULL
                # SQLite will respect this at the application level
                print("✓ Set default value for existing rows")
            except Exception as e2:
                print(f"✗ Error: {e2}")
                db.session.rollback()

if __name__ == "__main__":
    run_migration()
