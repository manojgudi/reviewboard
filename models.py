"""SQLAlchemy models for the Review Board application."""

from datetime import datetime, timezone, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(200), nullable=False, default="")
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="reviewer")  # admin | reviewer
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    # User preferences
    icon_color = db.Column(db.String(7), nullable=False, default="#0052CC")  # Hex color for avatar
    default_review_color = db.Column(db.String(20), nullable=False, default="yellow")  # Default highlight color

    tickets = db.relationship("Ticket", backref="owner", lazy="dynamic")
    reviews = db.relationship("Review", backref="author", lazy="dynamic")

    @property
    def is_admin(self):
        return self.role == "admin"

    def __repr__(self):
        return f"<User {self.username}>"


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    pdf_filename = db.Column(db.String(300), nullable=True)
    pdf_original_name = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="open")  # open | in_review | closed
    deadline = db.Column(db.DateTime, nullable=True)  # Optional deadline
    closed_at = db.Column(db.DateTime, nullable=True)  # When ticket was closed
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    reviews = db.relationship("Review", backref="ticket", lazy="dynamic",
                              cascade="all, delete-orphan")

    STATUS_LABELS = {
        "open": "Open",
        "in_review": "In Review",
        "closed": "Closed",
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def is_urgent(self):
        """Check if deadline is within 24 hours."""
        if not self.deadline:
            return False
        from datetime import timezone as tz, timedelta
        now = datetime.now(tz.utc)
        deadline = self.deadline
        if deadline.tzinfo is None:
            # Naive datetime is stored as UTC (after CET->UTC conversion)
            deadline = deadline.replace(tzinfo=tz.utc)
        return deadline <= now + timedelta(hours=24)

    def __repr__(self):
        return f"<Ticket #{self.id} {self.title[:40]}>"


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    pdf_page = db.Column(db.Integer, nullable=True)
    pdf_x = db.Column(db.Float, nullable=True)
    pdf_y = db.Column(db.Float, nullable=True)
    highlight_text = db.Column(db.Text, nullable=True)
    highlight_color = db.Column(db.String(20), nullable=False, default="yellow")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Review #{self.id} on Ticket #{self.ticket_id}>"


class Annotation(db.Model):
    """PDF/Document annotations with highlighting and comments."""
    __tablename__ = "annotations"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    page = db.Column(db.Integer, nullable=False, default=1)
    x = db.Column(db.Float, nullable=False, default=0)  # X position
    y = db.Column(db.Float, nullable=False, default=0)  # Y position
    width = db.Column(db.Float, nullable=False, default=0)  # Width of highlight
    height = db.Column(db.Float, nullable=False, default=0)  # Height of highlight
    text = db.Column(db.Text, nullable=True)  # Original selected text
    color = db.Column(db.String(20), nullable=False, default="yellow")  # Highlight color
    comment = db.Column(db.Text, nullable=True)  # User's comment
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    author = db.relationship("User", backref=db.backref("annotations", lazy="dynamic"))
    ticket = db.relationship("Ticket", backref=db.backref("annotations", lazy="dynamic"))

    def __repr__(self):
        return f"<Annotation #{self.id} on Ticket #{self.ticket_id}>"
