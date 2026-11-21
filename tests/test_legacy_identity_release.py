"""Release compatibility gate: audited history backfill, never email matching."""
import os
from uuid import uuid4
from datetime import datetime,timezone
import json
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.exc import IntegrityError
from app.db.base import Base
from app.db import models
from app.db.legacy_identity_release import inspect_coverage,apply_backfill,check_ready,target,ReleaseBlocked


@pytest.fixture
def database():
    url=os.getenv('GO360_CLINIC_TEST_DATABASE_URL')
    if not url:pytest.skip('Disposable PostgreSQL required')
    schema='legacy_identity_'+uuid4().hex;outer=create_engine(url)
    with outer.begin() as conn:conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:conn.exec_driver_sql('CREATE TABLE auth_events(id UUID PRIMARY KEY,user_id UUID,event_type TEXT,status TEXT,meta JSONB)')
    try:yield engine
    finally:
        engine.dispose()
        with outer.begin() as conn:conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        outer.dispose()


def account(conn,*,subject='recorded-google-id',active=True,verified=True):
    uid=uuid4()
    conn.execute(text("INSERT INTO users(id,email,full_name,password_hash,role,is_active,email_verified_at,recommendation_mode_default,timezone) VALUES (:id,:email,'Legacy','unchanged-password-hash','practitioner',:active,:verified,'natural_approaches_clinical','Europe/London')"),
        {'id':uid,'email':uuid4().hex+'@example.com','active':active,'verified':datetime.now(timezone.utc) if verified else None})
    event(conn,uid,subject)
    return uid


def event(conn,uid,subject):
    meta={'method':'social','provider':'google'}
    if subject is not None:meta['provider_subject']=subject
    conn.execute(text("INSERT INTO auth_events VALUES (:id,:uid,'login','success',CAST(:meta AS jsonb))"),{'id':uuid4(),'uid':uid,'meta':json.dumps(meta)})


def test_plan_read_only_backfill_audit_and_idempotency(database):
    with database.begin() as conn:
        uid=account(conn)
        conn.execute(text("INSERT INTO user_capabilities(id,user_id,capability) VALUES (:id,:uid,'admin')"),{'id':uuid4(),'uid':uid})
        before=conn.execute(text('SELECT * FROM users')).all()
        plan=inspect_coverage(conn)
        assert plan.summary()['eligible_backfills']==1 and plan.summary()['unresolved']==0
        with pytest.raises(ReleaseBlocked):check_ready(plan)
        assert conn.execute(text('SELECT COUNT(*) FROM external_identities')).scalar()==0
        assert apply_backfill(conn,plan.fingerprint)['restored']==1
        assert conn.execute(text('SELECT * FROM users')).all()==before
        assert conn.execute(text('SELECT user_id,provider,subject FROM external_identities')).one()==(uid,'google','recorded-google-id')
        audit=conn.execute(text('SELECT action,metadata_json FROM audit_logs')).one()
        assert audit[0]=='identity.legacy_binding_restored'
        assert 'recorded-google-id' not in json.dumps(audit[1])
        check_ready(inspect_coverage(conn))
        assert apply_backfill(conn,plan.fingerprint)=={'restored':0,'already_complete':True}
        assert conn.execute(text('SELECT COUNT(*) FROM audit_logs')).scalar()==1


@pytest.mark.parametrize('kind',['missing','ambiguous','shared_subject','unverified','claimed_elsewhere'])
def test_unresolved_evidence_blocks_all_backfill(database,kind):
    with database.begin() as conn:
        uid=account(conn,subject=None if kind=='missing' else 'same',verified=kind!='unverified')
        if kind=='ambiguous':event(conn,uid,'different')
        if kind=='shared_subject':account(conn,subject='same',active=False)
        if kind=='claimed_elsewhere':
            other=account(conn,subject='other')
            conn.execute(text("INSERT INTO external_identities(id,user_id,provider,subject) VALUES (:id,:uid,'google','same')"),{'id':uuid4(),'uid':other})
        plan=inspect_coverage(conn);assert plan.blockers
        with pytest.raises(ReleaseBlocked):apply_backfill(conn,plan.fingerprint)
        assert conn.execute(text('SELECT COUNT(*) FROM audit_logs')).scalar()==0


def test_stale_plan_refused_and_explicit_link_preserved(database):
    with database.begin() as conn:
        uid=account(conn);plan=inspect_coverage(conn)
        account(conn,subject='new-principal-subject')
        with pytest.raises(ReleaseBlocked):apply_backfill(conn,plan.fingerprint)
        conn.execute(text("INSERT INTO external_identities(id,user_id,provider,subject) VALUES (:id,:uid,'google','explicitly-linked-new-subject')"),{'id':uuid4(),'uid':uid})
        updated=inspect_coverage(conn)
        assert next(e for e in updated.entries if e.user_id==uid).status=='linked'


def test_missing_schema_and_history_fail_closed(database):
    with database.begin() as conn:
        account(conn)
        conn.exec_driver_sql('DROP TABLE identity_link_attempts')
        conn.exec_driver_sql('DROP TABLE external_identities')
        plan=inspect_coverage(conn);assert len(plan.candidates)==1 and not plan.schema_ready
        with pytest.raises(ReleaseBlocked):check_ready(plan)
        with pytest.raises(ReleaseBlocked):apply_backfill(conn,plan.fingerprint)
        conn.exec_driver_sql('DROP TABLE auth_events')
        with pytest.raises(ReleaseBlocked):check_ready(inspect_coverage(conn))


def test_audit_failure_rolls_back_entire_binding_transaction(database):
    with database.begin() as conn:
        account(conn)
        conn.exec_driver_sql("ALTER TABLE audit_logs ADD CONSTRAINT reject_restore CHECK(action <> 'identity.legacy_binding_restored')")
        plan=inspect_coverage(conn)
    with pytest.raises(IntegrityError):
        with database.begin() as conn:apply_backfill(conn,plan.fingerprint)
    with database.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM external_identities')).scalar()==0
        assert conn.execute(text('SELECT COUNT(*) FROM audit_logs')).scalar()==0


def test_environment_and_host_must_match(monkeypatch):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('DATABASE_URL','postgresql://synthetic:synthetic@postgres-ns3m.railway.internal/synthetic')
    with pytest.raises(ReleaseBlocked):target('production','postgres.railway.internal')
    with pytest.raises(ReleaseBlocked):target('staging','postgres-ns3m.railway.internal')


def test_production_startup_refuses_missing_links_then_passes(database,monkeypatch):
    from fastapi.testclient import TestClient
    from app import main as app_main
    # This test isolates the identity readiness gate; clinical roles have their
    # own distinct-login and startup tests in test_clinical_audit.py.
    monkeypatch.setattr('app.db.clinical_audit_roles.check_runtime', lambda conn: None)
    monkeypatch.setattr(app_main,'APP_ENV','production')
    monkeypatch.setattr(app_main,'engine',database)
    with database.begin() as conn:
        account(conn);plan=inspect_coverage(conn)
    with pytest.raises(ReleaseBlocked):
        with TestClient(app_main.create_app()):pass
    with database.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM external_identities')).scalar()==0
    with database.begin() as conn:apply_backfill(conn,plan.fingerprint)
    with TestClient(app_main.create_app()) as client:assert client.get('/health').status_code==200
