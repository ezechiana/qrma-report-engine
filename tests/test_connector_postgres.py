"""Opt-in PostgreSQL race check; NEVER targets the DEV application database.

Create an empty disposable database named go360_connector_test_<suffix> on
localhost, then set GO360_CONNECTOR_TEST_DATABASE_URL to its connection URL.
No URL is printed. This test creates and drops only its named test schema.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import os
from pathlib import Path
import uuid
import pytest
from sqlalchemy import create_engine,text,select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def test_postgres_transport_race_and_migration(monkeypatch):
    url=os.environ.get('GO360_CONNECTOR_TEST_DATABASE_URL')
    if not url:pytest.skip('Dedicated PostgreSQL test database not configured')
    parsed=make_url(url)
    if parsed.host not in {'localhost','127.0.0.1','::1'} or not (parsed.database or '').startswith('go360_connector_test_'):
        pytest.fail('Only a local disposable go360_connector_test_ database is permitted')
    from app.db.base import Base
    from app.db.models import User,Patient,Case,ReportVersion
    from app.db.connector_models import ConnectorDevice,ConnectorPairing,IncomingAssessment
    from app.services import connector_service as service
    schema='connector_qa_'+uuid.uuid4().hex
    admin=create_engine(url)
    engine=admin.execution_options(schema_translate_map={None:schema})
    with admin.begin() as connection:
        connection.execute(text('CREATE SCHEMA '+schema))
    try:
        # Existing baseline table shapes; SQL migration owns the new tables.
        # Set search_path on each physical connection before use so unqualified
        # foreign keys in the reviewed migration resolve into this test schema.
        from sqlalchemy import event
        @event.listens_for(admin,'connect')
        def search_path(connection,record):
            with connection.cursor() as cursor:cursor.execute('SET search_path TO '+schema)
            connection.commit()
        # Discard initial pool connections created before the hook.
        admin.dispose()
        for model in (User,Patient,Case,ReportVersion):model.__table__.create(engine,checkfirst=True)
        migration=Path(__file__).parents[1]/'app/db/migrations/20261026_connector_v04.sql'
        with engine.begin() as connection:
            connection.exec_driver_sql(migration.read_text(encoding='utf-8-sig'))
        with Session(engine) as db:
            user=User(id=uuid.uuid4(),email='qa@example.invalid',full_name='Synthetic',password_hash='unused',is_active=True)
            db.add(user);db.commit();owner=user.id
        device=type('Device',(),{'id':uuid.uuid4(),'owner_id':owner})()
        # Focus on database concurrency; actual parser/fingerprint equivalence is
        # covered by the offline endpoint and unchanged baseline suites.
        monkeypatch.setattr(service,'validate_html',lambda raw:({'source_patient_data':{}},'v1:'+('a'*64)))
        job=uuid.uuid4();raw=b'<html>synthetic</html>'
        def send(_):
            with Session(engine) as db:return service.ingest(db,device,job,service.PROFILE,datetime.now(timezone.utc),raw)
        with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(send,range(2)))
        assert results[0]['assessment_id']==results[1]['assessment_id']
        with Session(engine) as db:
            assert db.query(IncomingAssessment).count()==1
        with engine.begin() as connection:
            connection.exec_driver_sql(migration.read_text(encoding='utf-8-sig'))
    finally:
        engine.dispose()
        with admin.begin() as connection:connection.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
