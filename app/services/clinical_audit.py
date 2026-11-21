"""Mandatory clinical evidence. Fixed fields only; caller owns transactions."""
import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from app.utils.security import require_access_token, TokenError

logger = logging.getLogger('go360.clinical_audit')


def operational_failure(correlation_id):
    # No exception text, SQL parameters, target, clinical payload or credentials.
    logger.error('clinical_audit_unavailable correlation_id=%s', correlation_id)


def append(db, *, actor_id, action, outcome, correlation_id, target_id=None,
           resulting_version_id=None, reason=None, initiating_actor_id=None):
    if action not in {'report.read', 'assessment_report.regenerated'}:
        raise ValueError('Unsupported clinical action')
    if outcome not in {'allowed', 'denied', 'success'} or reason not in {None, 'not_found_or_not_owned', 'support_mode'}:
        raise ValueError('Unsupported clinical decision')
    event_id = uuid4()
    # INSERT without RETURNING: runtime needs append permission, not SELECT.
    db.execute(text('''INSERT INTO clinical_audit_events
      (id,action,outcome,effective_actor_id,initiating_actor_id,practice_owner_id,
       target_type,target_id,resulting_version_id,correlation_id,reason)
      VALUES (:id,:action,:outcome,:actor,:initiator,:actor,:target_type,:target,:version,:correlation,:reason)'''),
      dict(id=event_id, action=action, outcome=outcome, actor=actor_id,
           initiator=initiating_actor_id, target_type=('case' if resulting_version_id else 'report_version') if target_id else None,
           target=target_id, version=resulting_version_id, correlation=correlation_id, reason=reason))
    return event_id


def read_decision(db, *, actor_id, correlation_id, report_id=None, reason=None, initiator=None):
    try:
        append(db, actor_id=actor_id, action='report.read',
               outcome='allowed' if report_id else 'denied', correlation_id=correlation_id,
               target_id=report_id, reason=reason, initiating_actor_id=initiator)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass  # Failed connection cannot be used to disclose data.
        operational_failure(correlation_id)
        if report_id:
            raise HTTPException(503, 'Clinical audit unavailable. Retry later.') from None


def guard_session(request, db, actor, action, correlation_id):
    """Signed support marker survives cookie removal, bearer use and refresh.

    Pre-cutover sessions must sign in again: an old impersonation token is
    indistinguishable from a normal target token. Refresh cannot upgrade it.
    """
    header = request.headers.get('authorization', '')
    token = header[7:] if header.lower().startswith('bearer ') else request.cookies.get('access_token')
    try:
        claims = require_access_token(token or '')
        if claims.get('sub') != str(actor.id) or claims.get('clinical_session') != 1:
            raise TokenError('Fresh sign-in required')
    except TokenError:
        raise HTTPException(401, 'Please sign in again to use this clinical operation.') from None
    if claims.get('support_actor_id') or request.cookies.get('admin_restore_token'):
        # Signed target token proves support mode even if restore cookie is removed.
        # Cookie alone is never treated as proof of an initiating actor.
        initiator = claims.get('support_actor_id')
        try:
            from uuid import UUID
            initiator = UUID(initiator) if initiator else None
            append(db, actor_id=actor.id, action=action, outcome='denied',
                   correlation_id=correlation_id, reason='support_mode', initiating_actor_id=initiator)
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            operational_failure(correlation_id)
        raise HTTPException(403, 'This clinical operation is unavailable in support mode.')
