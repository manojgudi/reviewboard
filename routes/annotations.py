"""Annotation API routes for PDF/document annotations."""

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from models import db

# Create blueprint
annotations_bp = Blueprint('annotations', __name__, url_prefix='/api/annotation')


@annotations_bp.route('/<int:ticket_id>', methods=['GET'])
@login_required
def get_annotations(ticket_id):
    """Get all annotations for a ticket."""
    from models import Annotation
    
    annotations = Annotation.query.filter_by(ticket_id=ticket_id).all()
    
    return jsonify([{
        'id': a.id,
        'page': a.page,
        'x': a.x,
        'y': a.y,
        'width': a.width,
        'height': a.height,
        'text': a.text or '',
        'color': a.color,
        'comment': a.comment or '',
        'author': a.author.username if a.author else 'Unknown',
        'created_at': a.created_at.isoformat() if a.created_at else None
    } for a in annotations])


@annotations_bp.route('/save', methods=['POST'])
@login_required
def save_annotation():
    """Save a new or update an existing annotation."""
    from models import Annotation, Ticket
    
    data = request.get_json()
    
    annotation_id = data.get('annotation_id')
    ticket_id = data.get('ticket_id')
    
    # A01: Broken Access Control - Verify ticket exists and user has access
    if ticket_id:
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return jsonify({'success': False, 'error': 'Ticket not found'}), 404
        # Only owner or admin can add annotations to closed tickets
        if ticket.status == 'closed' and ticket.owner_id != current_user.id and not current_user.is_admin:
            return jsonify({'success': False, 'error': 'Cannot annotate closed tickets'}), 403
    
    if annotation_id:
        # Update existing
        annotation = Annotation.query.get(annotation_id)
        if annotation:
            # A01: Verify ownership before updating
            if annotation.author_id != current_user.id and not current_user.is_admin:
                return jsonify({'success': False, 'error': 'Unauthorized'}), 403
            if data.get('comment'):
                annotation.comment = data['comment']
            if data.get('color'):
                annotation.color = data['color']
            annotation.updated_at = db.func.now()
    else:
        # Create new
        annotation = Annotation(
            ticket_id=ticket_id,
            page=data.get('page', 1),
            x=data.get('x', 0),
            y=data.get('y', 0),
            width=data.get('width', 0),
            height=data.get('height', 0),
            text=data.get('text', ''),
            color=data.get('color', 'yellow'),
            comment=data.get('comment', ''),
            author_id=current_user.id
        )
        db.session.add(annotation)
    
    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'id': annotation.id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@annotations_bp.route('/delete/<int:annotation_id>', methods=['DELETE'])
@login_required
def delete_annotation(annotation_id):
    """Delete an annotation."""
    from models import Annotation
    
    # A01: Broken Access Control - explicit ownership check with admin override
    annotation = Annotation.query.get(annotation_id)
    
    if not annotation:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    
    if annotation.author_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    try:
        db.session.delete(annotation)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@annotations_bp.route('/update/<int:annotation_id>', methods=['PUT'])
@login_required
def update_annotation(annotation_id):
    """Update an annotation's comment."""
    from models import Annotation
    
    annotation = Annotation.query.get(annotation_id)
    
    if not annotation:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    
    if annotation.author_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    
    if 'comment' in data:
        annotation.comment = data['comment']
    if 'color' in data:
        annotation.color = data['color']
    
    annotation.updated_at = db.func.now()
    
    try:
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
