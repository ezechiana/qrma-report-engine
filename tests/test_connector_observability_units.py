"""Socket-free persistence/handler unit checks; HTTP integration is a separate suite."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_connector_observability import context, observed, raw, PAYLOAD, actions, admin
from app.db.models import AuditLog, User, Patient, Case, ReportVersion
from app.db.connector_models import ConnectorDevice, IncomingAssessment
from app.services import connector_audit as audit, connector_observability as telemetry, connector_service as service
from app.schemas.connector_observability import Heartbeat
from app.api import routes_connector as routes, routes_platform_connectors as platform
from test_connector_server import install_confirmation_business


def immediate(coroutine):
    # These handlers await only our immediately-ready in-memory receive function.
    # Any real suspension fails the test rather than inventing an event loop.
    try:
        value = coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError(f'Unexpected async suspension: {type(value).__name__}')


def request(body=b'', *, ip='2001:0db8:0:0:0:0:0:1', headers=None):
    async def receive():
        return {'type':'http.request','body':body,'more_body':False}
    return Request({'type':'http','method':'POST','path':'/api/connector/heartbeat',
        'scheme':'https','query_string':b'', 'server':('dev.example.invalid',443),
        'client':(ip,1000), 'headers':[(k.lower().encode(),v.encode()) for k,v in
            (headers or {'content-type':'application/json','authorization':'Bearer token'}).items()]},receive)


def heartbeat(c, payload=b'{"connector_version":"0.5.0","platform":"windows"}'):
    return immediate(routes.heartbeat(request(payload),c.device,c.db))


def ingest(c, raw, job=None):
    return service.ingest(c.db,c.device,job or uuid.uuid4(),service.PROFILE,datetime.now(timezone.utc),raw,audit.context(request()))


def test_idle_heartbeat_and_no_redundant_audit(observed):
    c=observed; before=c.device.expires_at
    assert heartbeat(c)['heartbeat_interval_seconds']==60
    c.db.refresh(c.device)
    assert c.device.last_seen_at and c.device.last_heartbeat_at
    assert c.device.last_ip_address=='2001:db8::1'
    assert c.device.expires_at==before and c.device.connector_version=='0.5.0'
    assert c.db.query(IncomingAssessment).count()==0
    heartbeat(c)
    assert len(actions(c,'device_observation_changed'))==1


@pytest.mark.parametrize('body,status', [(b'{' + b' ' * 1024,413),(b'{"secret":"SECRET"}',422),(b'{',422)])
def test_bounded_heartbeat_validation(observed,body,status):
    with pytest.raises(HTTPException) as error: heartbeat(observed,body)
    assert error.value.status_code==status and 'SECRET' not in str(error.value.detail)
    assert observed.device.last_seen_at is None


def test_heartbeat_failure_atomicity(observed,monkeypatch):
    c=observed
    monkeypatch.setattr(audit,'record',Mock(side_effect=RuntimeError('synthetic failure')))
    with pytest.raises(RuntimeError): heartbeat(c)
    c.db.rollback(); c.db.refresh(c.device)
    assert c.device.last_seen_at is None and c.device.connector_version is None


def test_ingestion_replay_duplicate_and_atomicity(observed,raw,monkeypatch):
    c=observed; job=uuid.uuid4()
    a=ingest(c,raw,job); assert ingest(c,raw,job)==a
    assert len(actions(c,'assessment_received'))==1
    assert ingest(c,raw)['status']=='needs_attention'
    assert len(actions(c,'duplicate_held'))==1
    with monkeypatch.context() as m:
        m.setattr(audit,'assessment_event',Mock(side_effect=RuntimeError('synthetic failure')))
        with pytest.raises(RuntimeError): ingest(c,raw+b'\n')
        c.db.rollback()
    assert c.db.query(IncomingAssessment).count()==2
    assert len(actions(c,'assessment_received'))==2


def test_inventory_read_does_not_reconcile_or_leak(observed,raw,monkeypatch):
    c=observed; ingest(c,raw)
    monkeypatch.setattr(service,'reconcile',Mock(side_effect=AssertionError('must not reconcile')))
    data=telemetry.inventory(c.db,limit=50)
    assert data['items'][0]['jobs_by_state']=={'awaiting_practitioner':1}
    assert data['items'][0]['status']=='stale'
    assert 'credential_hash' not in data['items'][0]
    assert 'parsed_metadata' not in data['items'][0]
    assert c.db.query(AuditLog).filter(AuditLog.action=='connector.inventory_read').count()==0


def test_repaired_owner_counts_are_separate(observed,raw):
    c=observed; ingest(c,raw)
    c.device.owner_id=c.other.id; c.db.commit()
    assert telemetry.inventory(c.db,limit=50)['items'][0]['jobs_by_state']=={}
    stats=telemetry.stats(c.db,24)
    assert stats['unmatched_assessments']==1 and stats['received_jobs_by_current_state']=={}


def test_exclusive_status_counts(observed):
    c=observed; now=datetime.now(timezone.utc)
    c.other.is_active=False
    for label in ('online','stale','expired','revoked','owner_inactive'):
        c.db.add(ConnectorDevice(id=uuid.uuid4(),owner_id=c.other.id if label=='owner_inactive' else c.user.id,
            credential_hash=label, expires_at=now + timedelta(days=1) if label not in ('expired','revoked') else now-timedelta(days=1),
            revoked=label=='revoked',last_seen_at=now,
            last_heartbeat_at=now if label=='online' else now-timedelta(seconds=181)))
    c.db.commit()
    assert telemetry.stats(c.db,24)['devices_by_status']==dict(unknown=1,online=1,stale=1,expired=1,revoked=1,owner_inactive=1)


def test_keyset_pagination_ties_and_owner_filters(observed):
    c=observed
    for n in range(3):
        c.db.add(ConnectorDevice(id=uuid.uuid4(),owner_id=c.user.id,credential_hash=str(n),
                 expires_at=c.device.expires_at,created_at=c.device.created_at))
    c.db.commit(); cursor=None; ids=[]
    while True:
        data=telemetry.inventory(c.db,limit=1,cursor=cursor)
        ids.extend(i['connector_id'] for i in data['items'])
        cursor=data['next_cursor']
        if not cursor: break
    assert len(ids)==len(set(ids))==4
    assert not telemetry.inventory(c.db,limit=10,owner_id=c.other.id)['items']
    with pytest.raises(HTTPException): telemetry.inventory(c.db,limit=10,cursor='SECRET')


def test_a_b_a_events_and_monotonic_timestamps(observed):
    c=observed
    for ip in ('127.0.0.1','2001:db8::1','127.0.0.1'):
        telemetry.touch(c.db,c.device,audit.context(request(ip=ip)),Heartbeat(**PAYLOAD)); c.db.commit()
    assert len(actions(c,'device_observation_changed'))==3
    future=datetime.now(timezone.utc)+timedelta(seconds=1)
    c.device.last_seen_at=future; c.db.commit()
    telemetry.touch(c.db,c.device,audit.context(request())); c.db.commit()
    assert telemetry.utc(c.device.last_seen_at)>=future


def test_stale_device_snapshot_cannot_touch(observed):
    c=observed; old=SimpleNamespace(id=c.device.id,owner_id=c.device.owner_id,credential_hash=c.device.credential_hash)
    c.device.credential_hash='rotated'; c.db.commit()
    with pytest.raises(HTTPException): telemetry.touch(c.db,old,audit.context(request()))
    assert c.device.last_seen_at is None


def test_authentication_denial_coalesces_without_identity(observed):
    c=observed
    for _ in range(3):
        with pytest.raises(HTTPException):
            routes.authenticated_device(request(headers={'authorization':'Bearer SECRET'}),c.db)
    rows=actions(c,'authentication_denied')
    assert len(rows)==1 and rows[0].user_id is None and rows[0].connector_id is None
    assert rows[0].metadata_json=={'schema_version':1,'reason':'invalid_credential'}


def test_admin_guard_and_required_access_audit(observed,monkeypatch):
    c=observed
    from app.api.deps import require_platform_admin
    with pytest.raises(HTTPException): require_platform_admin(c.db,c.user)
    c.user.role='admin'; c.db.commit()
    assert platform.administrator(require_platform_admin(c.db,c.user))==c.user
    platform.access(c.db,request(),c.user,'inventory_read')
    assert len(actions(c,'inventory_read'))==1
    c.user.is_active=False
    with pytest.raises(HTTPException): platform.administrator(c.user)
    c.user.is_active=True
    from app.utils import security
    monkeypatch.setattr(security,'JWT_SECRET_KEY','change-me-in-production')
    with pytest.raises(HTTPException): platform.administrator(c.user)


def test_email_admin_uses_authoritative_policy(observed,monkeypatch):
    c=observed
    from app.api.deps import require_platform_admin
    monkeypatch.setenv('PLATFORM_ADMIN_EMAILS',c.user.email)
    assert platform.administrator(require_platform_admin(c.db,c.user))==c.user


def test_flush_only_event_rollback_and_allowlist(observed):
    c=observed
    audit.record(c.db,'inventory_read',actor_id=c.user.id,actor_type='admin'); c.db.rollback()
    assert not actions(c,'inventory_read')
    with pytest.raises(ValueError): audit.record(c.db,'SECRET')
    with pytest.raises(ValueError): audit.record(c.db,'inventory_read',reason='SECRET')


def test_confirmation_event_transaction_and_retry(observed,raw,monkeypatch):
    c=observed; install_confirmation_business(c,monkeypatch)
    incoming=ingest(c,raw)
    from app.services.connector_confirmation import confirm
    payload=routes.ConfirmIncomingRequest(patient=dict(first_name='Synthetic',last_name='QA'),case=dict(title='QA'),generate_report=True)
    a=immediate(confirm(c.engine,c.user.id,uuid.UUID(incoming['assessment_id']),payload))
    b=immediate(confirm(c.engine,c.user.id,uuid.UUID(incoming['assessment_id']),payload))
    assert a['report_version_id']==b['report_version_id']
    assert len(actions(c,'case_linked'))==len(actions(c,'report_linked'))==1


def test_confirmation_case_audit_failure_rolls_back(observed,raw,monkeypatch):
    c=observed; install_confirmation_business(c,monkeypatch)
    incoming=ingest(c,raw)
    from app.services.connector_confirmation import confirm
    payload=routes.ConfirmIncomingRequest(patient=dict(first_name='Synthetic',last_name='QA'),case=dict(title='QA'))
    monkeypatch.setattr(audit,'assessment_event',Mock(side_effect=RuntimeError('synthetic failure')))
    with pytest.raises(RuntimeError): immediate(confirm(c.engine,c.user.id,uuid.UUID(incoming['assessment_id']),payload))
    c.db.rollback()
    assert c.db.query(Case).count()==c.db.query(Patient).count()==0
    assert c.db.query(IncomingAssessment).count()==1


def test_event_projection_pagination(observed):
    c=observed
    for _ in range(3): audit.record(c.db,'device_revoked',actor_id=c.user.id,connector_id=c.device.id)
    c.db.commit()
    a=telemetry.events(c.db,limit=1); b=telemetry.events(c.db,limit=1,cursor=a['next_cursor'])
    assert a['items'][0]['id']!=b['items'][0]['id']
    assert 'metadata_json' not in a['items'][0]


def test_reconcile_only_records_real_transition(observed,raw,monkeypatch):
    c=observed; install_confirmation_business(c,monkeypatch)
    from app.services.connector_confirmation import confirm
    incoming=ingest(c,raw)
    payload=routes.ConfirmIncomingRequest(patient=dict(first_name='Synthetic',last_name='QA'),case=dict(title='QA'),generate_report=True)
    result=immediate(confirm(c.engine,c.user.id,uuid.UUID(incoming['assessment_id']),payload))
    c.db.expire_all()
    row=c.db.get(IncomingAssessment,uuid.UUID(incoming['assessment_id']))
    row.state='processing'; c.db.commit()
    service.reconcile(c.db,row); service.reconcile(c.db,row)
    assert len(actions(c,'report_state_changed'))==1


def test_pairing_replay_owner_switch_and_revoke_idempotency(observed):
    from app.db.connector_models import ConnectorPairing
    c=observed; verifier='a'*64
    c.device.last_ip_address='127.0.0.1'; c.device.connector_version='0.4'
    pairing=ConnectorPairing(connector_id=c.device.id,owner_id=c.other.id,challenge=routes.sha(verifier),
                            code_hash='a'*64,expires_at=datetime.now(timezone.utc)+timedelta(minutes=10),approved=True)
    c.db.add(pairing); c.db.commit()
    payload=routes.PairPoll(pairing_id=pairing.id,verifier=verifier)
    a=routes.poll_pair(payload,request(),c.db)
    assert routes.poll_pair(payload,request(),c.db)==a
    assert len(actions(c,'credential_issued'))==1
    c.db.refresh(c.device)
    assert c.device.owner_id==c.other.id and c.device.last_ip_address is None and c.device.connector_version is None
    event=actions(c,'credential_issued')[0]
    assert event.metadata_json['previous_owner_id']==str(c.user.id)
    req=request(headers={'origin':'https://dev.example.invalid'})
    routes.revoke_device(c.device.id,req,c.other,c.db)
    routes.revoke_device(c.device.id,req,c.other,c.db)
    assert len(actions(c,'device_revoked'))==1


def test_failed_report_event_and_confirmation_retry(observed,raw,monkeypatch):
    c=observed; module=install_confirmation_business(c,monkeypatch)
    from app.db.models import ReportStatus, RecommendationMode
    async def fail(db,case,user,job_id=None):
        report=ReportVersion(case_id=case.id,created_by_user_id=user.id,version_number=1,
            status=ReportStatus.failed,job_id=job_id,recommendation_mode=RecommendationMode.natural_approaches_clinical)
        db.add(report); db.commit(); raise RuntimeError('synthetic failure')
    module.generate_report_version=fail
    incoming=ingest(c,raw)
    from app.services.connector_confirmation import confirm
    payload=routes.ConfirmIncomingRequest(patient=dict(first_name='Synthetic',last_name='QA'),case=dict(title='QA'),generate_report=True)
    a=immediate(confirm(c.engine,c.user.id,uuid.UUID(incoming['assessment_id']),payload))
    b=immediate(confirm(c.engine,c.user.id,uuid.UUID(incoming['assessment_id']),payload))
    assert a['status']==b['status']=='needs_attention'
    assert a['report_version_id']==b['report_version_id']
    assert len(actions(c,'report_failed'))==1 and not actions(c,'report_linked')
    assert c.db.query(Case).count()==c.db.query(ReportVersion).count()==1


def test_second_commit_audit_failure_preserves_case(observed,raw,monkeypatch):
    c=observed; install_confirmation_business(c,monkeypatch)
    incoming=ingest(c,raw)
    from app.services.connector_confirmation import confirm
    payload=routes.ConfirmIncomingRequest(patient=dict(first_name='Synthetic',last_name='QA'),case=dict(title='QA'),generate_report=True)
    original=audit.assessment_event
    def fail_report(db,action,row,**kwargs):
        if action=='report_linked': raise RuntimeError('synthetic audit failure')
        return original(db,action,row,**kwargs)
    monkeypatch.setattr(audit,'assessment_event',fail_report)
    with pytest.raises(RuntimeError): immediate(confirm(c.engine,c.user.id,uuid.UUID(incoming['assessment_id']),payload))
    c.db.rollback()
    assert c.db.query(Case).count()==1 and c.db.query(ReportVersion).count()==0
    assert len(actions(c,'case_linked'))==1 and not actions(c,'report_linked')


def test_telemetry_disabled_legacy_ingest_and_confirmation(context,raw,monkeypatch):
    c=context; install_confirmation_business(c,monkeypatch)
    monkeypatch.setenv('GO360_CONNECTOR_TELEMETRY_ENABLED','0')
    incoming=ingest(c,raw)
    assert c.device.last_seen_at is None
    from app.services.connector_confirmation import confirm
    payload=routes.ConfirmIncomingRequest(patient=dict(first_name='Synthetic',last_name='QA'),case=dict(title='QA'),generate_report=True)
    result=immediate(confirm(c.engine,c.user.id,uuid.UUID(incoming['assessment_id']),payload))
    assert result['status']=='completed'
    assert c.db.query(AuditLog).count()==0


@pytest.mark.parametrize('payload', [['2026-10-27T00:00:00+00:00',1], ['2026-10-27T00:00:00',str(uuid.uuid4())], [None,None], None])
def test_malformed_cursor_types_are_422(payload):
    import base64,json
    cursor=base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(HTTPException) as error: telemetry.decode_cursor(cursor)
    assert error.value.status_code==422
