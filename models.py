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


# Verdict constants
VERDICT_CHOICES = [
    ("no_verdict", "No Verdict"),
    ("strong_accept", "Strong Accept"),
    ("weak_accept", "Weak Accept"),
    ("weak_reject", "Weak Reject"),
    ("strong_reject", "Strong Reject"),
]

VERDICT_LABELS = {
    "strong_accept": "👍 Strong Accept",
    "weak_accept": "👌 Weak Accept",
    "weak_reject": "👎 Weak Reject",
    "strong_reject": "👎👎 Strong Reject",
    "no_verdict": "No Verdict",
}

VERDICT_COLORS = {
    "strong_accept": "success",
    "weak_accept": "info",
    "weak_reject": "warning",
    "strong_reject": "danger",
    "no_verdict": "secondary",
}


class Verdict(db.Model):
    """Final verdict for a ticket by a reviewer."""
    __tablename__ = "verdicts"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    verdict = db.Column(db.String(20), nullable=False, default="no_verdict")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user = db.relationship("User", backref=db.backref("verdicts", lazy="dynamic"))
    ticket = db.relationship("Ticket", backref=db.backref("verdicts", lazy="dynamic"))

    # Unique constraint: one verdict per user per ticket
    __table_args__ = (
        db.UniqueConstraint('ticket_id', 'user_id', name='unique_verdict_per_user_ticket'),
    )

    @property
    def verdict_label(self):
        return VERDICT_LABELS.get(self.verdict, self.verdict)

    @property
    def verdict_color_class(self):
        return VERDICT_COLORS.get(self.verdict, "secondary")

    def __repr__(self):
        return f"<Verdict #{self.id} {self.verdict} by User #{self.user_id} on Ticket #{self.ticket_id}>"


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


class AIReviewJob(db.Model):
    """Background job for AI-powered PDF review using Ollama."""
    __tablename__ = "ai_review_jobs"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)  # Who requested
    status = db.Column(db.String(20), nullable=False, default="queued")  # queued | processing | completed | failed
    total_sections = db.Column(db.Integer, nullable=False, default=0)
    completed_sections = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.Text, nullable=True)  # Summary error if failed
    job_id = db.Column(db.String(100), nullable=True)  # RQ job ID for tracking
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    ticket = db.relationship("Ticket", backref=db.backref("ai_review_jobs", lazy="dynamic"))
    user = db.relationship("User", backref=db.backref("ai_review_jobs", lazy="dynamic"))

    @property
    def progress_percent(self):
        if self.total_sections == 0:
            return 0
        return int((self.completed_sections / self.total_sections) * 100)

    @property
    def is_complete(self):
        return self.status == "completed"

    @property
    def is_failed(self):
        return self.status == "failed"

    def __repr__(self):
        return f"<AIReviewJob #{self.id} ticket={self.ticket_id} status={self.status}>"


class AIReviewSection(db.Model):
    """Individual section review from AI (stored as a comment/review)."""
    __tablename__ = "ai_review_sections"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("ai_review_jobs.id"), nullable=False, index=True)
    section_index = db.Column(db.Integer, nullable=False)  # 0-based index of section
    section_title = db.Column(db.String(500), nullable=True)  # Heading/title of section
    section_content_hash = db.Column(db.String(64), nullable=True)  # Hash to avoid duplicate work
    review = db.Column(db.Text, nullable=True)  # The AI's review text
    success = db.Column(db.Boolean, nullable=False, default=False)  # Did AI successfully review?
    error_message = db.Column(db.Text, nullable=True)  # Error if failed
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationship
    job = db.relationship("AIReviewJob", backref=db.backref("sections", lazy="dynamic"))

    def __repr__(self):
        return f"<AIReviewSection #{self.id} job={self.job_id} section={self.section_index}>"
