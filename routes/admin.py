"""Admin routes for user management (admin only)."""

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, current_app
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, SubmitField
from wtforms.validators import DataRequired, Length, Email
import os

from models import db, User, Ticket, Review, Annotation

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


class CreateUserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(3, 80)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    full_name = StringField("Full Name", validators=[Length(max=200)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6)])
    role = SelectField("Role", choices=[("reviewer", "Reviewer"), ("admin", "Admin")], 
                       validators=[DataRequired()])
    submit = SubmitField("Create User")


def _get_bcrypt():
    """Import bcrypt from app module to avoid circular imports."""
    from app import bcrypt
    return bcrypt


@admin_bp.route('/users')
@login_required
@admin_required
def users():
    users = User.query.order_by(User.username).all()
    return render_template('admin/users.html', users=users)


@admin_bp.route('/users/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    form = CreateUserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash("Username already taken.", "danger")
            return render_template('admin/user_create.html', form=form)
        if User.query.filter_by(email=form.email.data).first():
            flash("Email already registered.", "danger")
            return render_template('admin/user_create.html', form=form)
        
        pw_hash = _get_bcrypt().generate_password_hash(form.password.data).decode("utf-8")
        user = User(
            username=form.username.data,
            email=form.email.data,
            full_name=form.full_name.data or "",
            password_hash=pw_hash,
            role=form.role.data,
        )
        db.session.add(user)
        db.session.commit()
        flash(f"User '{user.username}' created successfully.", "success")
        return redirect(url_for('admin.users'))
    return render_template('admin/user_create.html', form=form)


@admin_bp.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    is_self = user.id == current_user.id
    
    if request.method == 'POST':
        user.full_name = request.form.get('full_name', user.full_name)
        user.email = request.form.get('email', user.email)
        
        if not is_self:
            role = request.form.get('role')
            if role in ('admin', 'reviewer'):
                user.role = role
        
        db.session.commit()
        flash('User updated', 'success')
        return redirect(url_for('admin.users'))
    
    return render_template('admin/user_edit.html', user=user, is_self=is_self)


@admin_bp.route('/users/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Delete a user and all their associated data."""
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        flash('You cannot delete yourself', 'danger')
        return redirect(url_for('admin.users'))
    
    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    
    # Get all user's tickets
    tickets = Ticket.query.filter_by(owner_id=user.id).all()
    
    for ticket in tickets:
        # Delete associated PDF file from disk
        if ticket.pdf_filename:
            pdf_path = os.path.join(upload_folder, ticket.pdf_filename)
            if os.path.isfile(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass
    
    # Delete all user's annotations (Annotation uses author_id, not user_id)
    Annotation.query.filter_by(author_id=user.id).delete(synchronize_session=False)
    
    # Delete all user's reviews (Review uses author_id, not user_id)
    Review.query.filter_by(author_id=user.id).delete(synchronize_session=False)
    
    # Delete all tickets (cascades to reviews and annotations via FK)
    Ticket.query.filter_by(owner_id=user.id).delete(synchronize_session=False)
    
    # Delete user
    db.session.delete(user)
    
    db.session.commit()
    
    flash(f"User '{user.username}' and all associated data deleted", 'success')
    return redirect(url_for('admin.users'))
