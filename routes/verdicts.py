"""Verdict routes - allow reviewers to give final verdicts on tickets."""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from models import db, Verdict, Ticket, VERDICT_CHOICES

verdicts_bp = Blueprint('verdicts', __name__)

VALID_VERDICTS = [choice[0] for choice in VERDICT_CHOICES]


@verdicts_bp.route('/api/tickets/<int:ticket_id>/verdict', methods=['GET'])
@login_required
def get_verdict(ticket_id):
    """Get current user's verdict for a ticket."""
    try:
        ticket = Ticket.query.get_or_404(ticket_id)
    except Exception:
        # Return default for existing tickets
        return jsonify({
            'verdict': 'no_verdict',
            'created_at': None,
            'updated_at': None,
        })
    
    try:
        verdict = Verdict.query.filter_by(
            ticket_id=ticket_id,
            user_id=current_user.id
        ).first()
        
        return jsonify({
            'verdict': verdict.verdict if verdict else 'no_verdict',
            'created_at': verdict.created_at.isoformat() if verdict else None,
            'updated_at': verdict.updated_at.isoformat() if verdict else None,
        })
    except Exception:
        # Table might not exist yet (existing tickets before feature was added)
        return jsonify({
            'verdict': 'no_verdict',
            'created_at': None,
            'updated_at': None,
        })


@verdicts_bp.route('/api/tickets/<int:ticket_id>/verdict', methods=['POST'])
@login_required
def save_verdict(ticket_id):
    """Save or update the current user's verdict for a ticket."""
    try:
        ticket = Ticket.query.get_or_404(ticket_id)
    except Exception:
        return jsonify({'error': 'Ticket not found'}), 404
    
    # A01: Creators cannot give verdicts on their own work
    if ticket.owner_id == current_user.id:
        return jsonify({'error': 'You cannot give a verdict on your own ticket'}), 403
    
    data = request.get_json()
    if data is None:
        return jsonify({'error': 'Invalid JSON in request body'}), 400
    
    verdict_value = data.get('verdict', 'no_verdict')
    
    # Validate verdict value
    if verdict_value not in VALID_VERDICTS:
        return jsonify({'error': f'Invalid verdict value: {verdict_value}'}), 400
    
    try:
        # Find existing verdict or create new one
        verdict = Verdict.query.filter_by(
            ticket_id=ticket_id,
            user_id=current_user.id
        ).first()
        
        if verdict:
            verdict.verdict = verdict_value
        else:
            verdict = Verdict(
                ticket_id=ticket_id,
                user_id=current_user.id,
                verdict=verdict_value
            )
            db.session.add(verdict)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'verdict': verdict_value,
            'updated_at': verdict.updated_at.isoformat(),
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to save verdict'}), 500


@verdicts_bp.route('/api/tickets/<int:ticket_id>/verdicts', methods=['GET'])
@login_required
def get_all_verdicts(ticket_id):
    """Get all verdicts for a ticket (for ticket owner to see reviewer verdicts)."""
    try:
        ticket = Ticket.query.get_or_404(ticket_id)
    except Exception:
        # Return empty list for existing tickets that don't exist
        return jsonify({'verdicts': []})
    
    # Only ticket owner can see all verdicts
    if ticket.owner_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        verdicts = Verdict.query.filter(
            Verdict.ticket_id == ticket_id,
            Verdict.verdict != 'no_verdict'  # Don't show "No Verdict" entries
        ).all()
        
        return jsonify({
            'verdicts': [
                {
                    'user_id': v.user_id,
                    'username': v.user.username,
                    'verdict': v.verdict,
                    'updated_at': v.updated_at.isoformat(),
                }
                for v in verdicts
            ]
        })
    except Exception:
        # Table might not exist yet (existing tickets before feature was added)
        return jsonify({'verdicts': []})
