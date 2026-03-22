"""Migration: Add verdicts table for final reviewer verdicts."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from models import Verdict

def run_migration():
    app = create_app()
    with app.app_context():
        # Create the verdicts table
        db.create_all()
        print("✓ Verdicts table created successfully!")

if __name__ == '__main__':
    run_migration()
