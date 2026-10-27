"""Connector event allow-list. Caller owns transaction and commit."""
from datetime import datetime, timezone
import ipaddress
import os
import uuid
from app.db.models import AuditLog

ACTIONS = {
    'pair_approved', 'credential_issued', 'session_signed_out', 'device_revoked',
    'assessment_received', 'duplicate_held', 'duplicate_override', 'case_linked',
    'report_linked', 'report_failed', 'report_state_changed', 'device_observation_changed',
    'inventory_read', 'stats_read', 'events_read', 'authentication_denied',
}


def enabled():
    from app.services.connector_policy import telemetry_enabled
    return telemetry_enabled()


def observed_ip(request):
    # ASGI client is normalized by the server's trusted-proxy handling.
    # Never independently parse caller-controlled forwarding headers.
    try:
        return str(ipaddress.ip_address(request.client.host)) if request and request.client else None
    except ValueError:
        return None


def context(request):
    return {'ip': observed_ip(request), 'request_id': uuid.uuid4()}


def record(db, action, *, actor_id=None, owner_id=None, actor_type='device',
           connector_id=None, assessment=None, outcome='success', event_key=None,
           observation=None, previous_owner_id=None, reason=None):
    if not enabled():
        return
    if action not in ACTIONS or actor_type not in {'device', 'user', 'admin', 'anonymous'}:
        raise ValueError('Unsupported connector audit event')
    if outcome not in {'success', 'denied', 'failed'}:
        raise ValueError('Unsupported connector audit outcome')
    if reason not in {None, 'missing_credential', 'invalid_credential', 'account_unavailable'}:
        raise ValueError('Unsupported connector audit reason')
    metadata = {'schema_version': 1}
    if owner_id is not None:
        metadata['owner_id'] = str(owner_id)
    if previous_owner_id is not None:
        metadata['previous_owner_id'] = str(previous_owner_id)
    if reason:
        metadata['reason'] = reason
    entry = AuditLog(
        action='connector.' + action, user_id=actor_id, actor_type=actor_type,
        created_at=datetime.now(timezone.utc),
        outcome=outcome, connector_id=connector_id,
        assessment_id=assessment.id if assessment else None,
        job_id=assessment.job_id if assessment else None,
        case_id=assessment.case_id if assessment else None,
        report_version_id=assessment.report_id if assessment else None,
        metadata_json=metadata, event_key=event_key,
        ip_address=(observation or {}).get('ip'),
        request_id=(observation or {}).get('request_id', uuid.uuid4()),
    )
    db.add(entry)
    db.flush()  # No implicit commit: failed audit rolls back its mutation.
    return entry


def assessment_event(db, action, row, *, actor_type='device', observation=None, suffix=''):
    return record(db, action, actor_id=row.owner_id, owner_id=row.owner_id,
                  actor_type=actor_type, connector_id=row.connector_id, assessment=row,
                  outcome='failed' if action == 'report_failed' else 'success',
                  event_key=f'connector:{row.id}:{action}{suffix}', observation=observation)
