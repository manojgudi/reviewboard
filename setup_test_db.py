#!/usr/bin/env python3
"""
Seed the test database with production users (read-only from production).
Then run the test.
"""

import os
import sys
import sqlite3
import shutil

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_reviewboard.db")
PROD_DB_PATH = os.path.join(os.path.dirname(__file__), "reviewboard.db")

# Re-copy the database to ensure it's fresh
if os.path.exists(TEST_DB_PATH):
    os.remove(TEST_DB_PATH)

shutil.copy2(PROD_DB_PATH, TEST_DB_PATH)
print(f"✅ Copied production DB to test DB: {TEST_DB_PATH}")

# Verify users exist in test DB
conn = sqlite3.connect(TEST_DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT id, username, role FROM users")
users = cursor.fetchall()
conn.close()

if not users:
    print("❌ No users in production database!")
    sys.exit(1)

print(f"✅ Found {len(users)} users in test database:")
for u in users:
    print(f"   - ID {u[0]}: {u[1]} (role: {u[2]})")
