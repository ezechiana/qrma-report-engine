"""Opt-in PostgreSQL observability checks. Never use the application database.

Not executed as part of the socket-free checkpoint validation.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
from types import SimpleNamespace
import uuid
import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


@pytest.fixture
def postgres(monkeypatch):
    url=os.environ.get('GO360_CONNECTOR_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Dedicated PostgreSQL test database not configured')
    parsed=make_url(url)
    if parsed.host not in {'localhost','127.0.0.1','::1'} or not (parsed.database or '').startswith('go360_connector_test_'):
        pytest.fail('Only a local disposable go360_connector_test_ database is permitted')
    from app.db.models import User,Patient,Case,ReportVersion
    schema='connector_qa_'+uuid.uuid4().hex
    engine=create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA '+schema))
    @event.listens_for(engine,'connect')
    def search_path(connection,record):
        with connection.cursor() as cursor: cursor.execute('SET search_path TO '+schema)
        connection.commit()
    engine.dispose()
    try:
        for model in (User,Patient,Case,ReportVersion): model.__table__.create(engine, checkfirst=True)
        # Old audit shape deliberately has no new columns.
        with engine.begin() as conn:
            conn.exec_driver_sql('''CREATE TABLE audit_logs (
              id UUID PRIMARY KEY, user_id UUID REFERENCES users(id), case_id UUID REFERENCES cases(id),
              report_version_id UUID REFERENCES report_versions(id), action VARCHAR(120) NOT NULL,
              metadata_json JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
            conn.exec_driver_sql((Path(__file__).parents[1]/'app/db/migrations/20261026_connector_v04.sql').read_text(encoding='utf-8-sig'))
        monkeypatch.setenv('APP_ENV','test'); monkeypatch.setenv('GO360_CONNECTOR_ENABLED','1')
        monkeypatch.setenv('GO360_CONNECTOR_TELEMETRY_ENABLED','1')
        yield engine
    finally:
        engine.dispose()
        # Explicit generated schema only, guarded database above.
        with engine.begin() as conn: conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        engine.dispose()


def migrate(engine):
    sql=(Path(__file__).parents[1]/'app/db/migrations/20261027_connector_v050_observability.sql').read_text(encoding='utf-8-sig')
    with engine.begin() as conn: conn.exec_driver_sql(sql)


def test_additive_migration_repeat_and_legacy_rows(postgres):
    engine=postgres
    old_id=uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO audit_logs(id,action) VALUES (:id,'legacy')"),{'id':old_id})
    migrate(engine); migrate(engine)
    columns={c['name'] for c in inspect(engine).get_columns('connector_devices')}
    assert {'last_seen_at','last_heartbeat_at','last_ip_address','connector_version','platform'} <= columns
    with engine.connect() as conn:
        row=conn.execute(text('SELECT action,connector_id,actor_type FROM audit_logs WHERE id=:id'),{'id':old_id}).one()
        assert tuple(row)==('legacy',None,None)


def test_concurrent_ingest_one_received_event(postgres,monkeypatch):
    from app.db.models import User,AuditLog
    from app.db.connector_models import ConnectorDevice,IncomingAssessment
    from app.services import connector_service as service
    engine=postgres; migrate(engine)
    with Session(engine) as db:
        user=User(id=uuid.uuid4(),email='synthetic@example.invalid',full_name='Synthetic',password_hash='unused',is_active=True)
        db.add(user); db.flush()
        device=ConnectorDevice(id=uuid.uuid4(),owner_id=user.id,credential_hash='a'*64,
                               expires_at=datetime.now(timezone.utc)+timedelta(days=1))
        db.add(device); db.commit(); device_id=device.id; owner=user.id
    monkeypatch.setattr(service,'validate_html',lambda raw: ({'source_patient_data':{}},'v1:'+'a'*64))
    job=uuid.uuid4()
    def send(_):
        with Session(engine) as db:
            device=db.get(ConnectorDevice,device_id)
            return service.ingest(db,device,job,service.PROFILE,datetime.now(timezone.utc),b'<html>synthetic</html>',{'ip':'127.0.0.1'})
    with ThreadPoolExecutor(max_workers=2) as executor: results=list(executor.map(send,range(2)))
    assert results[0]['assessment_id']==results[1]['assessment_id']
    with Session(engine) as db:
        assert db.query(IncomingAssessment).count()==1
        assert db.query(AuditLog).filter(AuditLog.action=='connector.assessment_received').count()==1


def test_revocation_prevents_stale_touch(postgres):
    from app.db.models import User
    from app.db.connector_models import ConnectorDevice
    from app.services import connector_observability as service
    from fastapi import HTTPException
    engine=postgres; migrate(engine)
    with Session(engine) as db:
        user=User(id=uuid.uuid4(),email='revoke@example.invalid',full_name='Synthetic',password_hash='unused',is_active=True)
        db.add(user); db.flush()
        device=ConnectorDevice(id=uuid.uuid4(),owner_id=user.id,credential_hash='b'*64,
                               expires_at=datetime.now(timezone.utc)+timedelta(days=1))
        db.add(device); db.commit()
        snapshot=SimpleNamespace(id=device.id,owner_id=user.id,credential_hash=device.credential_hash)
    with Session(engine) as revoke:
        device=revoke.scalar(select(ConnectorDevice).where(ConnectorDevice.id==snapshot.id).with_for_update())
        device.revoked=True; revoke.commit()
    with Session(engine) as heartbeat:
        with pytest.raises(HTTPException): service.touch(heartbeat,snapshot,{'ip':'127.0.0.1'})
    with Session(engine) as db:
        assert db.get(ConnectorDevice,snapshot.id).last_seen_at is None

