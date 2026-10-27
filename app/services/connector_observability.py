"""Locked device telemetry and read-only fleet projections; never reconcile jobs."""
import base64
from datetime import datetime, timedelta, timezone
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from app.db.connector_models import ConnectorDevice, IncomingAssessment
from app.db.models import User, AuditLog
from app.services import connector_audit as audit


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def touch(db, device, observation, heartbeat=None):
    if not audit.enabled():
        return
    # Snapshot before refresh so re-pair cannot replace our identity.
    identity = (device.id, device.owner_id, device.credential_hash)
    row = db.scalar(select(ConnectorDevice).where(ConnectorDevice.id == identity[0])
                    .with_for_update().execution_options(populate_existing=True))
    now = datetime.now(timezone.utc)
    if (not row or row.owner_id != identity[1] or row.credential_hash != identity[2]
            or row.revoked or utc(row.expires_at) <= now):
        raise HTTPException(401, 'Connector session expired or revoked.')
    # Lock serializes timestamps, observation changes, revoke and re-pair.
    now = max(now, utc(row.last_seen_at)) if row.last_seen_at else now
    changed = row.last_ip_address != (observation or {}).get('ip')
    row.last_seen_at = now
    row.last_ip_address = (observation or {}).get('ip')
    if heartbeat:
        changed |= row.connector_version != heartbeat.connector_version or row.platform != heartbeat.platform
        row.connector_version, row.platform = heartbeat.connector_version, heartbeat.platform
        row.last_heartbeat_at = max(now, utc(row.last_heartbeat_at)) if row.last_heartbeat_at else now
    if changed:
        audit.record(db, 'device_observation_changed', actor_id=row.owner_id, owner_id=row.owner_id,
                     connector_id=row.id, observation=observation)
    return now


def status_expression(as_of):
    return case(
        (ConnectorDevice.revoked.is_(True), 'revoked'),
        (ConnectorDevice.expires_at <= as_of, 'expired'),
        (User.is_active.is_not(True), 'owner_inactive'),
        (ConnectorDevice.last_seen_at.is_(None), 'unknown'),
        (ConnectorDevice.last_heartbeat_at >= as_of - timedelta(seconds=180), 'online'),
        else_='stale',
    )


def encode_cursor(stamp, identity):
    return base64.urlsafe_b64encode(json.dumps([utc(stamp).isoformat(), str(identity)]).encode()).decode()


def decode_cursor(value):
    try:
        if len(value) > 256:
            raise ValueError()
        stamp, identity = json.loads(base64.b64decode(value, altchars=b'-_', validate=True))
        if not isinstance(stamp, str) or not isinstance(identity, str):
            raise ValueError()
        stamp = datetime.fromisoformat(stamp)
        if not stamp.tzinfo:
            raise ValueError()
        return stamp, uuid.UUID(identity)
    except (ValueError, TypeError):
        raise HTTPException(422, 'Invalid cursor.') from None


def after_cursor(stamp_column, id_column, value):
    stamp, identity = decode_cursor(value)
    return or_(stamp_column < stamp, and_(stamp_column == stamp, id_column < identity))


def inventory(db, *, limit, cursor=None, owner_id=None, status=None, version=None):
    now = datetime.now(timezone.utc)
    state = status_expression(now)
    query = select(ConnectorDevice, state.label('status')).outerjoin(User, User.id == ConnectorDevice.owner_id)
    if cursor:
        query = query.where(after_cursor(ConnectorDevice.created_at, ConnectorDevice.id, cursor))
    if owner_id:
        query = query.where(ConnectorDevice.owner_id == owner_id)
    if status:
        query = query.where(state == status)
    if version:
        query = query.where(ConnectorDevice.connector_version == version)
    rows = db.execute(query.order_by(ConnectorDevice.created_at.desc(), ConnectorDevice.id.desc()).limit(limit + 1)).all()
    selected = rows[:limit]
    ids = [d.id for d, _ in selected]
    counts = {}
    if ids:
        grouped = db.execute(select(IncomingAssessment.connector_id, IncomingAssessment.state, func.count())
            .join(ConnectorDevice, and_(ConnectorDevice.id == IncomingAssessment.connector_id,
                                       ConnectorDevice.owner_id == IncomingAssessment.owner_id))
            .where(ConnectorDevice.id.in_(ids))
            .group_by(IncomingAssessment.connector_id, IncomingAssessment.state)).all()
        for device_id, job_state, count in grouped:
            counts.setdefault(device_id, {})[job_state] = count
    items = []
    for device, state in selected:
        items.append(dict(connector_id=device.id, owner_id=device.owner_id,
                          created_at=device.created_at, expires_at=device.expires_at, revoked=device.revoked,
                          last_seen_at=device.last_seen_at, last_heartbeat_at=device.last_heartbeat_at,
                          observed_ip=device.last_ip_address, connector_version=device.connector_version,
                          platform=device.platform, status=state, jobs_by_state=counts.get(device.id, {})))
    next_cursor = encode_cursor(selected[-1][0].created_at, selected[-1][0].id) if len(rows) > limit else None
    return dict(as_of=now, items=items, next_cursor=next_cursor)


def stats(db, window_hours):
    now = datetime.now(timezone.utc)
    state = status_expression(now)
    states = {name: 0 for name in ('revoked', 'expired', 'owner_inactive', 'unknown', 'online', 'stale')}
    states.update(dict(db.execute(select(state, func.count()).select_from(ConnectorDevice)
                                 .outerjoin(User, User.id == ConnectorDevice.owner_id).group_by(state)).all()))
    versions = dict(db.execute(select(func.coalesce(ConnectorDevice.connector_version, 'unknown'), func.count())
                              .group_by(ConnectorDevice.connector_version)).all())
    owner_count = db.scalar(select(func.count(func.distinct(ConnectorDevice.owner_id))))
    joined = and_(ConnectorDevice.id == IncomingAssessment.connector_id,
                  ConnectorDevice.owner_id == IncomingAssessment.owner_id)
    window = and_(IncomingAssessment.received_at >= now - timedelta(hours=window_hours),
                  IncomingAssessment.received_at <= now)
    jobs = dict(db.execute(select(IncomingAssessment.state, func.count()).join(ConnectorDevice, joined)
                           .where(window).group_by(IncomingAssessment.state)).all())
    unmatched = db.scalar(select(func.count()).select_from(IncomingAssessment)
                         .outerjoin(ConnectorDevice, joined).where(window, ConnectorDevice.id.is_(None)))
    return dict(as_of=now, window_hours=window_hours, devices_by_status=states,
                versions=versions, distinct_owners=owner_count, received_jobs_by_current_state=jobs,
                unmatched_assessments=unmatched)


def events(db, *, limit, cursor=None, connector_id=None):
    query = select(AuditLog).where(AuditLog.actor_type.is_not(None), AuditLog.action.like('connector.%'))
    if cursor:
        query = query.where(after_cursor(AuditLog.created_at, AuditLog.id, cursor))
    if connector_id:
        query = query.where(AuditLog.connector_id == connector_id)
    rows = db.scalars(query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)).all()
    items = [dict(id=r.id, action=r.action, created_at=r.created_at, actor_id=r.user_id,
                  actor_type=r.actor_type, outcome=r.outcome, connector_id=r.connector_id,
                  job_id=r.job_id, assessment_id=r.assessment_id, case_id=r.case_id,
                  report_version_id=r.report_version_id, request_id=r.request_id,
                  observed_ip=r.ip_address) for r in rows[:limit]]
    return dict(items=items, next_cursor=encode_cursor(rows[limit-1].created_at, rows[limit-1].id)
                if len(rows) > limit else None)
