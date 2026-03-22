#!/usr/bin/env python3
"""Create admin user script."""
import sys
sys.path.insert(0, '/home/miniluv/.picoclaw/workspace/reviewboard')

from app import create_app, bcrypt
from models import db, User

app = create_app()
with app.app_context():
    # Check existing users
    users = User.query.all()
    print(f"Existing users: {len(users)}")
    for u in users:
        print(f"  - {u.username} ({u.role})")
    
    # Create admin if not exists
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            email='admin@example.com',
            full_name='Administrator',
            password_hash=bcrypt.generate_password_hash('admin123456').decode('utf-8'),
            role='admin'
        )
        db.session.add(admin)
        db.session.commit()
        print("\nCreated admin user!")
        print("Username: admin")
        print("Password: admin123456")
    else:
        # Reset password for existing admin
        admin.password_hash = bcrypt.generate_password_hash('admin123456').decode('utf-8')
        admin.role = 'admin'  # Ensure admin role
        db.session.commit()
        print(f"\nAdmin '{admin.username}' exists - password reset to: admin123456")
