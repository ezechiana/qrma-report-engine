"""Isolated SQLite API/audit tests. No application startup or DEV database imports."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import uuid
from unittest.mock import Mock
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from fastapi import HTTPException
from test_connector_server import context, raw, post, install_confirmation_business
from app.db.models import AuditLog, User, Case, Patient, ReportVersion
from app.db.connector_models import ConnectorDevice, IncomingAssessment
from app.api.deps import get_current_user
from app.services import connector_audit as audit, connector_observability as telemetry, connector_limits
from app.schemas.connector_observability import Heartbeat

HEADERS = {'authorization': 'Bearer token'}
PAYLOAD = {'connector_version': '0.5.0', 'platform': 'windows'}


@pytest.fixture
def observed(context, monkeypatch):
    c = context
    with c.engine.begin() as conn:
        conn.execute(text('CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)'))
    monkeypatch.setenv('GO360_CONNECTOR_TELEMETRY_ENABLED', '1')
    connector_limits._buckets.clear()
    from app.api import routes_platform_connectors as platform
    from app.utils import security
    monkeypatch.setattr(security, 'JWT_SECRET_KEY', 'synthetic-test-secret-for-isolated-db')
    c.client.app.include_router(platform.router)
    c.client.app.dependency_overrides[get_current_user] = lambda: c.user
    return c


def beat(c, **kwargs):
    return c.client.post('/api/connector/heartbeat', headers=HEADERS, json=kwargs or PAYLOAD)


def actions(c, action):
    c.db.expire_all()
    return c.db.scalars(select(AuditLog).where(AuditLog.action == 'connector.' + action)).all()


def admin(c):
    c.user.role = 'admin'
    c.db.commit()


def test_heartbeat_idle_observation_no_business_rows(observed):
    c = observed
    expiry = c.device.expires_at
    response = beat(c)
    assert response.status_code == 200, response.text
    assert response.json()['heartbeat_interval_seconds'] == 60
    c.db.refresh(c.device)
    assert c.device.last_seen_at and c.device.last_heartbeat_at
    assert c.device.connector_version == '0.5.0' and c.device.platform == 'windows'
    assert c.device.last_ip_address is None  # TestClient peer is not an IP.
    assert c.device.expires_at == expiry
    assert c.db.query(IncomingAssessment).count() == 0
    for model in (Patient, Case, ReportVersion):
        assert c.db.query(model).count() == 0
    assert len(actions(c, 'device_observation_changed')) == 1
    assert beat(c).status_code == 200
    assert len(actions(c, 'device_observation_changed')) == 1


@pytest.mark.parametrize('payload', [
    {**PAYLOAD, 'owner_id': 'SECRET'}, {**PAYLOAD, 'ip': 'SECRET'},
    {**PAYLOAD, 'connector_version': 'SECRET<unsafe>'},
    {**PAYLOAD, 'platform': 'SECRET'}, {**PAYLOAD, 'connector_version': '1' * 65}, {},
])
def test_bounded_sanitized_schema(observed, payload):
    c = observed
    response = c.client.post('/api/connector/heartbeat', headers=HEADERS, json=payload)
    assert response.status_code == 422
    assert 'SECRET' not in response.text
    assert response.headers['cache-control'] == 'no-store, private'
    assert c.device.last_heartbeat_at is None


def test_oversize_chunked_and_content_type(observed):
    c = observed
    response = c.client.post('/api/connector/heartbeat', content=iter([b'{' + b' ' * 600, b' ' * 600]),
                             headers={**HEADERS, 'content-type': 'application/json'})
    assert response.status_code == 413
    assert c.client.post('/api/connector/heartbeat', headers=HEADERS, content='{}').status_code == 415


@pytest.mark.parametrize('state', ['missing', 'wrong', 'revoked', 'expired', 'inactive'])
def test_heartbeat_auth_rejections(observed, state):
    c = observed
    headers = HEADERS
    if state == 'missing': headers = {}
    if state == 'wrong': headers = {'authorization': 'Bearer SECRET'}
    if state == 'revoked': c.device.revoked = True
    if state == 'expired': c.device.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    if state == 'inactive': c.user.is_active = False
    c.db.commit()
    response = c.client.post('/api/connector/heartbeat', headers=headers, json=PAYLOAD)
    assert response.status_code == 401, response.text
    assert c.device.last_seen_at is None
    rows = actions(c, 'authentication_denied')
    assert len(rows) == 1 and rows[0].user_id is None and rows[0].connector_id is None
    assert 'SECRET' not in json.dumps(rows[0].metadata_json)


def test_device_quota_and_ip_backstop(observed):
    c = observed
    for _ in range(10): assert beat(c).status_code == 200
    response = beat(c)
    assert response.status_code == 429 and response.headers['retry-after'] == '60'
    connector_limits._buckets.clear()
    for _ in range(120):
        assert connector_limits.allow('heartbeat-ip', None, 120)
    assert beat(c).status_code == 429


def test_flags_and_https_fail_closed(observed, monkeypatch):
    c = observed
    for flag, value in [('GO360_CONNECTOR_TELEMETRY_ENABLED', '0'), ('GO360_CONNECTOR_ENABLED', '0'), ('APP_ENV', 'production')]:
        with monkeypatch.context() as m:
            m.setenv(flag, value)
            assert beat(c).status_code == 404
            assert c.client.get('/api/platform/connectors').status_code == 404
    assert c.client.post('http://dev.example.invalid/api/connector/heartbeat', json=PAYLOAD, headers=HEADERS).status_code == 400


@pytest.mark.parametrize('path', ['/api/platform/connectors', '/api/platform/connectors/stats', '/api/platform/connector-events'])
def test_admin_guard_all_endpoints(observed, path):
    c = observed
    assert c.client.get(path).status_code == 403
    c.user.role = 'clinic_admin'; c.db.commit()
    assert c.client.get(path).status_code == 403
    admin(c)
    c.user.is_active = False; c.db.commit()
    assert c.client.get(path).status_code == 403
    c.user.is_active = True; c.db.commit()
    assert c.client.get(path).status_code == 200
    # Remove test identity override: real device credential is not an app JWT.
    c.client.app.dependency_overrides.pop(get_current_user)
    assert c.client.get(path).status_code == 401
    assert c.client.get(path, headers=HEADERS).status_code == 401


def test_allowlisted_admin_and_default_jwt_prerequisite(observed, monkeypatch):
    c = observed
    monkeypatch.setenv('PLATFORM_ADMIN_EMAILS', c.user.email)
    assert c.client.get('/api/platform/connectors').status_code == 200
    from app.utils import security
    monkeypatch.setattr(security, 'JWT_SECRET_KEY', 'change-me-in-production')
    assert c.client.get('/api/platform/connectors').status_code == 503


def test_admin_response_projection_and_no_reconciliation(observed, raw, monkeypatch):
    c = observed
    incoming = post(c, raw).json()
    admin(c)
    from app.services import connector_service
    monkeypatch.setattr(connector_service, 'reconcile', Mock(side_effect=AssertionError('must not reconcile')))
    response = c.client.get('/api/platform/connectors')
    assert response.status_code == 200, response.text
    row = response.json()['items'][0]
    assert row['jobs_by_state'] == {'awaiting_practitioner': 1}
    assert row['status'] == 'stale'  # Legacy upload is contact, not heartbeat.
    forbidden = ['credential_hash', 'source_html', 'parsed_metadata', 'duplicate', 'password_hash']
    for key in forbidden: assert key not in response.text
    assert c.db.get(IncomingAssessment, uuid.UUID(incoming['assessment_id'])).state == 'awaiting_practitioner'
    assert len(actions(c, 'inventory_read')) == 1


def test_previous_owner_jobs_not_transferred(observed, raw):
    c = observed
    post(c, raw)
    c.device.owner_id = c.other.id; c.db.commit()
    admin(c)
    assert c.client.get('/api/platform/connectors').json()['items'][0]['jobs_by_state'] == {}
    result = c.client.get('/api/platform/connectors/stats').json()
    assert result['unmatched_assessments'] == 1
    assert result['received_jobs_by_current_state'] == {}


def test_status_precedence_and_zero_jobs(observed):
    c = observed
    now = datetime.now(timezone.utc)
    for label in ('unknown', 'online', 'stale', 'expired', 'revoked', 'owner_inactive'):
        owner = c.other if label == 'owner_inactive' else c.user
        if label == 'owner_inactive': c.other.is_active = False
        c.db.add(ConnectorDevice(id=uuid.uuid4(), owner_id=owner.id, credential_hash=label,
            expires_at=now + timedelta(days=1) if label not in ('expired', 'revoked') else now - timedelta(days=1),
            revoked=label == 'revoked', last_seen_at=None if label == 'unknown' else now,
            last_heartbeat_at=now if label == 'online' else now - timedelta(seconds=181)))
    c.db.commit(); admin(c)
    result = c.client.get('/api/platform/connectors/stats').json()
    assert result['devices_by_status'] == dict(unknown=2, online=1, stale=1, expired=1, revoked=1, owner_inactive=1)
    assert result['versions'] == {'unknown': 7}
    assert result['received_jobs_by_current_state'] == {}


def test_cursor_ties_filters_and_invalid_bounds(observed):
    c = observed; admin(c)
    for n in range(3):
        c.db.add(ConnectorDevice(id=uuid.uuid4(), owner_id=c.user.id, credential_hash=str(n),
                 expires_at=c.device.expires_at, created_at=c.device.created_at))
    c.db.commit()
    ids=[]; cursor=None
    while True:
        r=c.client.get('/api/platform/connectors', params={'limit': 1, **({'cursor': cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        ids.extend(i['connector_id'] for i in r.json()['items'])
        cursor=r.json()['next_cursor']
        if not cursor: break
    assert len(ids) == len(set(ids)) == 4
    assert c.client.get('/api/platform/connectors', params={'owner_id': str(c.other.id)}).json()['items'] == []
    for params in ({'limit':0}, {'limit':101}, {'cursor':'not base64'}, {'status':'bad'}, {'owner_id':'SECRET'}):
        r=c.client.get('/api/platform/connectors', params=params)
        assert r.status_code == 422 and 'SECRET' not in r.text
    assert c.client.get('/api/platform/connectors/stats?window_hours=2161').status_code == 422


def test_admin_query_failure_and_audit_failure_dont_return_data(observed, monkeypatch):
    c=observed; admin(c)
    with monkeypatch.context() as m:
        m.setattr(telemetry, 'stats', Mock(side_effect=RuntimeError('SECRET')))
        r=c.client.get('/api/platform/connectors/stats')
        assert r.status_code == 503 and 'SECRET' not in r.text
    with monkeypatch.context() as m:
        m.setattr(audit, 'record', Mock(side_effect=RuntimeError('SECRET')))
        r=c.client.get('/api/platform/connectors')
        assert r.status_code == 503 and 'items' not in r.text and 'SECRET' not in r.text


def test_heartbeat_audit_failure_rolls_back(observed, monkeypatch):
    c=observed
    monkeypatch.setattr(audit, 'record', Mock(side_effect=RuntimeError('SECRET')))
    assert beat(c).status_code == 503
    c.db.rollback()  # Real get_db closes/rolls back; shared fixture session needs explicit boundary.
    c.db.refresh(c.device)
    assert c.device.last_seen_at is None and c.device.connector_version is None


def test_ingest_audit_once_and_same_transaction(observed, raw, monkeypatch):
    c=observed; job=uuid.uuid4()
    assert post(c, raw, job).status_code == 200
    assert post(c, raw, job).status_code == 200
    assert len(actions(c, 'assessment_received')) == 1
    assert post(c, raw).json()['status'] == 'needs_attention'
    assert len(actions(c, 'duplicate_held')) == 1
    with monkeypatch.context() as m:
        m.setattr(audit, 'assessment_event', Mock(side_effect=RuntimeError('SECRET')))
        assert post(c, raw+b'\n').status_code == 503
        c.db.rollback()
    assert c.db.query(IncomingAssessment).count() == 2
    assert len(actions(c, 'assessment_received')) == 2


def test_confirmation_events_and_retry(observed, raw, monkeypatch):
    c=observed; install_confirmation_business(c, monkeypatch)
    incoming=post(c,raw).json()
    path='/api/incoming-assessments/'+incoming['assessment_id']+'/confirm'
    payload=dict(patient=dict(first_name='Synthetic',last_name='Test'),case=dict(title='QA'),generate_report=True)
    headers={'origin':'https://dev.example.invalid'}
    result=c.client.post(path,json=payload,headers=headers)
    assert result.status_code == 200, result.text
    assert c.client.post(path,json=payload,headers=headers).status_code == 200
    assert len(actions(c,'case_linked')) == len(actions(c,'report_linked')) == 1
    event=actions(c,'report_linked')[0]
    assert str(event.report_version_id)==result.json()['report_version_id']
    assert event.case_id and event.assessment_id and event.job_id and event.connector_id


def test_case_audit_failure_rolls_back_case_but_keeps_incoming(observed, raw, monkeypatch):
    c=observed; install_confirmation_business(c, monkeypatch)
    incoming=post(c,raw).json()
    monkeypatch.setattr(audit, 'assessment_event', Mock(side_effect=RuntimeError('SECRET')))
    r=c.client.post('/api/incoming-assessments/'+incoming['assessment_id']+'/confirm',
        json=dict(patient=dict(first_name='Synthetic',last_name='QA'),case=dict(title='QA')),
        headers={'origin':'https://dev.example.invalid'})
    assert r.status_code == 503
    c.db.rollback()
    assert c.db.query(Case).count() == c.db.query(Patient).count() == 0
    assert c.db.query(IncomingAssessment).count() == 1


def test_ip_canonicalization_and_a_b_a_changes(observed):
    c=observed
    request=lambda ip: SimpleNamespace(client=SimpleNamespace(host=ip), headers={'x-forwarded-for':'SECRET'})
    assert audit.observed_ip(request('2001:0db8:0:0:0:0:0:1')) == '2001:db8::1'
    assert audit.observed_ip(request('127.0.0.1')) == '127.0.0.1'
    assert audit.observed_ip(request('not-an-ip')) is None
    for ip in ['127.0.0.1','2001:db8::1','127.0.0.1']:
        telemetry.touch(c.db,c.device,audit.context(request(ip)),Heartbeat(**PAYLOAD)); c.db.commit()
    assert len(actions(c,'device_observation_changed')) == 3
    assert all('SECRET' not in json.dumps(r.metadata_json) for r in actions(c,'device_observation_changed'))


def test_touch_rejects_stale_credential_snapshot(observed):
    c=observed
    snapshot=SimpleNamespace(id=c.device.id, owner_id=c.device.owner_id, credential_hash=c.device.credential_hash)
    c.device.credential_hash='repaired'; c.db.commit()
    with pytest.raises(HTTPException) as error:
        telemetry.touch(c.db,snapshot,{'ip':None})
    assert error.value.status_code == 401
    assert c.device.last_seen_at is None


def test_flush_only_writer_and_allowlist(observed):
    c=observed
    audit.record(c.db,'inventory_read',actor_type='admin',actor_id=c.user.id)
    c.db.rollback()
    assert not actions(c,'inventory_read')
    with pytest.raises(ValueError): audit.record(c.db,'SECRET')
    with pytest.raises(ValueError): audit.record(c.db,'inventory_read',reason='SECRET')


def test_events_projection_and_pagination(observed):
    c=observed; admin(c)
    for _ in range(3):
        audit.record(c.db,'device_revoked',actor_id=c.user.id,connector_id=c.device.id)
    c.db.commit()
    response=c.client.get('/api/platform/connector-events',params={'limit':1,'connector_id':str(c.device.id)})
    assert response.status_code == 200
    data=response.json(); assert data['next_cursor']
    next_page=c.client.get('/api/platform/connector-events',params={'limit':1,'connector_id':str(c.device.id),'cursor':data['next_cursor']})
    assert next_page.json()['items'][0]['id'] != data['items'][0]['id']
    assert 'metadata_json' not in response.text and 'event_key' not in response.text
