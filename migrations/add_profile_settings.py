#!/usr/bin/env python3
"""
Migration: Add profile settings columns to users table
Run this ONCE to add icon_color and default_review_color to existing databases.
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
    
    # Check if columns already exist
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    
    changes_made = 0
    
    # Add icon_color column if not exists
    if 'icon_color' not in columns:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN icon_color VARCHAR(7) NOT NULL DEFAULT '#0052CC'")
            conn.commit()
            print("✅ Added 'icon_color' column to users table.")
            changes_made += 1
        except sqlite3.Error as e:
            print(f"❌ Error adding icon_color column: {e}")
            conn.close()
            return False
    else:
        print("ℹ️ Column 'icon_color' already exists in users table.")
    
    # Add default_review_color column if not exists
    if 'default_review_color' not in columns:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN default_review_color VARCHAR(20) NOT NULL DEFAULT 'yellow'")
            conn.commit()
            print("✅ Added 'default_review_color' column to users table.")
            changes_made += 1
        except sqlite3.Error as e:
            print(f"❌ Error adding default_review_color column: {e}")
            conn.close()
            return False
    else:
        print("ℹ️ Column 'default_review_color' already exists in users table.")
    
    conn.close()
    
    if changes_made > 0:
        print(f"\n✅ Migration complete! Added {changes_made} column(s).")
    else:
        print("\nℹ️ No changes needed - columns already exist.")
    
    return True

if __name__ == '__main__':
    success = migrate()
    sys.exit(0 if success else 1)
