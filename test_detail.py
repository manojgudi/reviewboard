import os
import sys
# Ensure using the virtualenv's site-packages
venv_path = os.path.join(os.path.dirname(__file__), '.venv', 'lib', f'python{sys.version_info.major}.{sys.version_info.minor}', 'site-packages')
if venv_path not in sys.path:
    sys.path.insert(0, venv_path)

from app import create_app
from models import db, User, Ticket, Review
from flask import url_for

app = create_app()
app.testing = True
with app.app_context():
    db.drop_all()
    db.create_all()
    # create user
    user = User(username='test', email='test@example.com', full_name='Test User', password_hash='dummy', role='admin')
    db.session.add(user)
    db.session.commit()
    # create ticket
    ticket = Ticket(title='Sample Ticket', description='Desc', owner_id=user.id)
    db.session.add(ticket)
    db.session.commit()
    # create review
    review = Review(ticket_id=ticket.id, author_id=user.id, body='Nice work')
    db.session.add(review)
    db.session.commit()
    # use test client
    client = app.test_client()
    # login user via session hack
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
    resp = client.get(f'/tickets/{ticket.id}')
    print('Status:', resp.status_code)
    # print part of response
    data = resp.get_data(as_text=True)
    print('Snippet:', data[:500])
