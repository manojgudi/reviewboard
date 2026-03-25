"""Add last_seen column to users table for online tracking."""
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import db


def run_migration():
    app = create_app()
    with app.app_context():
        # Check if column exists
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('users')]
        
        if 'last_seen' in columns:
            print("✓ Column 'last_seen' already exists in users table")
            return
        
        # Add column
        db.session.execute(db.text(
            "ALTER TABLE users ADD COLUMN last_seen TIMESTAMP NULL"
        ))
        db.session.commit()
        print("✓ Added 'last_seen' column to users table")


if __name__ == "__main__":
    run_migration()
