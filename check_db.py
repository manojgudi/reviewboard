#!/usr/bin/env python3
import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else 'reviewboard.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print(f'=== Database: {db_path} ===\n')

# Show tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cursor.fetchall()]
print('Tables:', tables)

# Show users
print('\n=== USERS ===')
try:
    cursor.execute('SELECT id, username, email, role FROM users LIMIT 20')
    for row in cursor.fetchall():
        print(f'  ID:{row[0]} | {row[1]} | {row[2]} | {row[3]}')
except Exception as e:
    print(f'  Error: {e}')

# Show tickets
print('\n=== TICKETS (first 10) ===')
try:
    cursor.execute('SELECT id, title, status, created_at FROM tickets ORDER BY id LIMIT 10')
    for row in cursor.fetchall():
        print(f'  ID:{row[0]} | {row[1][:40]} | {row[2]} | {row[3]}')
except Exception as e:
    print(f'  Error: {e}')

conn.close()
