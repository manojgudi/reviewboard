#!/usr/bin/env python3
"""
Migration: Add closed_at column to tickets table
Run this ONCE to add the closed_at column to existing databases.
"""

import sqlite3
import sys
import os

def migrate():
    db_path = os.path.join(os.path.dirname(__file__), '..', 'reviewboard.db')
    db_path = os.path.abspath(db_path)
    
    print(f"Using database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if column already exists
    cursor.execute("PRAGMA table_info(tickets)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'closed_at' in columns:
        print("✅ Column 'closed_at' already exists in tickets table.")
        conn.close()
        return True
    
    # Add the column
    try:
        cursor.execute("ALTER TABLE tickets ADD COLUMN closed_at TIMESTAMP")
        conn.commit()
        print("✅ Added 'closed_at' column to tickets table.")
        conn.close()
        return True
    except sqlite3.Error as e:
        print(f"❌ Error adding column: {e}")
        conn.close()
        return False

if __name__ == '__main__':
    success = migrate()
    sys.exit(0 if success else 1)
