"""T-GO360-012 A-F: real JWT routes, PostgreSQL logins and synthetic data only."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, select, event
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

os.environ.setdefault('DATABASE_URL','postgresql://localhost/synthetic')
from app.api import deps, routes_cases, routes_reports, routes_auth
from app.db.base import Base
from app.db.models import User, Patient, Case, ReportVersion, ShareLink, AuditLog
from app.db.migrate import run_migrations, sql_statements
from app.db.clinical_audit_roles import provision, check_runtime
from app.services import clinical_audit, report_service, storage_service, clinical_artifacts
from app.services.auth_service import build_auth_response, build_impersonation_response, refresh_user_tokens
from app.utils.security import create_access_token, create_refresh_token


@pytest.fixture
def fixture(monkeypatch):
    url=os.getenv('GO360_CLINIC_TEST_DATABASE_URL')
    if not url: pytest.skip('Disposable PostgreSQL required')
    tag=uuid4().hex
    schema='clinical_'+tag
    roles={k:'ca_'+k+'_'+tag for k in ('owner','writer','reviewer','foreign')}
    admin=create_engine(url)
    assert admin.url.host in {'localhost','127.0.0.1'} and admin.url.database=='synthetic' and admin.url.username=='synthetic', 'Disposable local synthetic database required'
    with admin.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA {schema}')
        for kind,role in roles.items():
            c.exec_driver_sql(f"CREATE ROLE {role} {'NOLOGIN' if kind=='owner' else 'LOGIN PASSWORD '+chr(39)+'synthetic'+chr(39)} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    Base.metadata.create_all(engine)
    run_migrations(engine)
    # Existing auth tables are provisioned by the migration/operator identity,
    # never by the restricted runtime. No email is sent.
    from app.services.auth_service import _ensure_auth_events_table, _ensure_password_reset_table
    with Session(engine) as bootstrap:
        _ensure_auth_events_table(bootstrap)
        _ensure_password_reset_table(bootstrap)
    ids={}; cases={}; reports={}
    with Session(engine) as db:
        for key in ('a','b','admin'):
            user=User(id=uuid4(),email=uuid4().hex+'@example.invalid',full_name='Synthetic '+key,
                password_hash='unused',role='admin' if key=='admin' else 'practitioner',is_active=True,email_verified_at=datetime.now(timezone.utc))
            db.add(user);db.flush();ids[key]=user.id
            patient=Patient(id=uuid4(),user_id=user.id,full_name='Synthetic clinical fixture',first_name='Synthetic',last_name='Fixture')
            db.add(patient);db.flush()
            case=Case(id=uuid4(),user_id=user.id,patient_id=patient.id,title='Synthetic',raw_scan_html_path='synthetic/source.htm')
            db.add(case);db.flush();cases[key]=case.id
            report=ReportVersion(id=uuid4(),case_id=case.id,created_by_user_id=user.id,version_number=1,status='ready',report_type='assessment',recommendation_mode='natural_approaches_clinical',report_json={'fixture':'private-'+key},html_path='old/'+key+'.html')
            db.add(report);db.flush();reports[key]=report.id
            db.add(ShareLink(report_version_id=report.id,patient_id=patient.id,token=uuid4().hex,is_active=True))
        db.execute(text("INSERT INTO app_settings(key,value) VALUES ('subscription_config',CAST(:v AS jsonb)) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value"),{'v':json.dumps({'free_features':{'report_generation':True}})})
        db.add(AuditLog(action='synthetic.historical',user_id=ids['a'],metadata_json={'schema_version':1}))
        db.commit()
    with engine.begin() as c:
        c.exec_driver_sql(f'GRANT USAGE ON SCHEMA {schema} TO {roles["writer"]}, {roles["reviewer"]}, {roles["foreign"]}')
        names=c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=:s"),{'s':schema}).scalars().all()
        for n in names:
            if not n.startswith('clinical_audit'):
                c.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON {schema}."{n}" TO {roles["writer"]}')
    provision(engine,schema=schema,owner=roles['owner'],writer=roles['writer'],reviewer=roles['reviewer'])
    # Prove operations provisioning is idempotent; no passwords are in provisioning.
    provision(engine,schema=schema,owner=roles['owner'],writer=roles['writer'],reviewer=roles['reviewer'])
    with engine.begin() as c:
        c.exec_driver_sql(f'GRANT SELECT ON {schema}.clinical_audit_events, {schema}.clinical_audit_reviewer_scopes TO {roles["foreign"]}')
        c.execute(text('INSERT INTO clinical_audit_reviewer_scopes VALUES (:r,:p)'),{'r':roles['reviewer'],'p':ids['a']})
        c.execute(text('INSERT INTO clinical_audit_reviewer_scopes VALUES (:r,:p)'),{'r':roles['foreign'],'p':ids['b']})
    def login_engine(kind):
        return create_engine(admin.url.set(username=roles[kind],password='synthetic'),connect_args={'options':f'-csearch_path={schema}'})
    writer=login_engine('writer'); reviewer=login_engine('reviewer'); foreign=login_engine('foreign')
    app=FastAPI();app.include_router(routes_reports.router);app.include_router(routes_cases.router);app.include_router(routes_auth.router)
    def database():
        with Session(writer) as db:yield db
    app.dependency_overrides[deps.get_db]=database
    def headers(key='a',**kwargs):
        with Session(engine) as db: token=build_auth_response(db.get(User,ids[key]),**kwargs)['access_token']
        return {'Authorization':'Bearer '+token}
    objects={};builds=[]
    class S3:
        def get_public_access_block(self,**kwargs):return {'PublicAccessBlockConfiguration':dict.fromkeys(('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets'),True)}
        def get_bucket_policy_status(self,**kwargs):return {'PolicyStatus':{'IsPublic':False}}
        def put_object(self,**kwargs):objects[kwargs['Key']]=kwargs['Body']
        def get_paginator(self,name):return self
        def paginate(self,**kwargs):return [{'Contents':[{'Key':k} for k in list(objects) if k.startswith(kwargs['Prefix'])]}]
        def delete_object(self,**kwargs):objects.pop(kwargs['Key'],None)
    monkeypatch.setattr(storage_service,'s3',S3())
    monkeypatch.setattr(report_service,'parse_and_enrich_case_html',lambda case:{})
    import app.services.ai_practitioner_intelligence as ai
    monkeypatch.setattr(ai,'apply_practitioner_intelligence',lambda report,**kw:report)
    async def build(*args,**kwargs):
        import asyncio
        await asyncio.sleep(.03)  # Expose lock/event-loop deadlocks in concurrent requests.
        builds.append(kwargs)
        html=storage_service.upload_text(kwargs['html_filename'],'Synthetic only')
        pdf=storage_service.upload_bytes(kwargs['pdf_filename'],b'Synthetic PDF','application/pdf')
        return {'html_path':html,'pdf_path':pdf,'viewer_payload':{'overview':{'overall_summary':'Synthetic'}}}
    monkeypatch.setattr(report_service,'build_report',build)
    with TestClient(app) as client:
        yield SimpleNamespace(admin=admin,engine=engine,writer=writer,reviewer=reviewer,foreign=foreign,
            schema=schema,roles=roles,ids=ids,cases=cases,reports=reports,client=client,headers=headers,
            objects=objects,builds=builds,app=app)
    for e in (writer,reviewer,foreign,engine):e.dispose()
    with admin.begin() as c:
        c.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
        for role in roles.values():c.exec_driver_sql(f'DROP ROLE {role}')
    admin.dispose()


def rows(f):
    with f.engine.connect() as c:return list(c.execute(text('SELECT * FROM clinical_audit_events ORDER BY occurred_at,id')).mappings())


def regenerate(f,key=None):
    return f.client.post(f'/api/cases/{f.cases["a"]}/generate-report',headers={**f.headers(), 'Idempotency-Key':str(key or uuid4())})


def test_A_B_durable_read_and_non_disclosing_denial(fixture,monkeypatch):
    f=fixture;url=f'/api/reports/{f.reports["b"]}/json'
    a=f.client.get(url,headers={**f.headers(),'X-Clinic-ID':str(uuid4()),'X-Correlation-ID':'untrusted'})
    b=f.client.get('/api/reports/'+str(uuid4())+'/json',headers=f.headers())
    assert a.status_code==b.status_code==404
    assert a.json()==b.json()=={'detail':'Report not found'}
    for row in rows(f):
        assert row['effective_actor_id']==f.ids['a'] and row['practice_owner_id']==f.ids['a']
        assert row['clinic_id'] is None and row['organisation_id'] is None and row['target_id'] is None
        assert row['reason']=='not_found_or_not_owned'
    import app.api.routes_reports as routes
    original=routes.JSONResponse
    def verify_durable(*args,**kwargs):
        assert any(r['outcome']=='allowed' and r['target_id']==f.reports['a'] for r in rows(f))
        return original(*args,**kwargs)
    monkeypatch.setattr(routes,'JSONResponse',verify_durable)
    response=f.client.get(f'/api/reports/{f.reports["a"]}/json',headers=f.headers())
    assert response.status_code==200 and response.json()=={'fixture':'private-a'}
    assert response.headers['cache-control']=='no-store'
    assert str(rows(f)[-1]['correlation_id'])==response.headers['x-correlation-id']
    assert not any('private-' in str(r) or 'Synthetic clinical' in str(r) or '@example' in str(r) for r in rows(f))


def test_C_regenerate_replay_and_prior_continuity(fixture):
    f=fixture;key=uuid4();first=regenerate(f,key);second=regenerate(f,key)
    assert first.status_code==second.status_code==200,(first.text,second.text)
    assert first.json()==second.json() and first.json()['version_number']==2
    success=[r for r in rows(f) if r['outcome']=='success']
    assert len(success)==1 and str(success[0]['resulting_version_id'])==first.json()['report_version_id']
    assert success[0]['action']=='assessment_report.regenerated'
    assert len(f.builds)==1 and 'clinical-attempts/' in f.builds[0]['html_filename']
    with Session(f.engine) as db:
        assert db.get(ReportVersion,f.reports['a']).report_json=={'fixture':'private-a'}
        assert db.scalar(select(ShareLink.is_active).where(ShareLink.report_version_id==f.reports['a']))
        assert db.scalar(text("SELECT count(*) FROM assessment_quota_reservations WHERE state='consumed'"))==1
    assert f.client.get(f'/api/reports/{first.json()["report_version_id"]}/json',headers=f.headers()).status_code==200


def test_C_concurrent_replay(fixture):
    f=fixture;key=uuid4()
    def run(_):return regenerate(f,key)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert [r.status_code for r in results]==[200,200],[r.text for r in results]
    assert results[0].json()==results[1].json() and len(f.builds)==1
    assert len([r for r in rows(f) if r['outcome']=='success'])==1


def test_D_actual_writer_and_reviewer_privileges(fixture):
    f=fixture
    assert f.client.get(f'/api/reports/{f.reports["a"]}/json',headers=f.headers()).status_code==200
    with f.writer.connect() as c:check_runtime(c)
    for sql in ('SELECT * FROM clinical_audit_events','UPDATE clinical_audit_events SET outcome=outcome',
                'DELETE FROM clinical_audit_events','TRUNCATE clinical_audit_events',
                'ALTER TABLE clinical_audit_events DISABLE TRIGGER ALL',
                'INSERT INTO clinical_audit_reviewer_scopes VALUES (current_user,gen_random_uuid())',
                f'SET ROLE {f.roles["owner"]}',f'SET ROLE {f.roles["reviewer"]}'):
        with f.writer.begin() as c:
            with pytest.raises(DBAPIError):c.exec_driver_sql(sql)
    with f.reviewer.begin() as c:
        assert len(c.execute(text('SELECT * FROM clinical_audit_events')).all())==1
        c.exec_driver_sql("SET go360.practice_owner_id = 'ffffffff-ffff-ffff-ffff-ffffffffffff'")
        assert len(c.execute(text('SELECT * FROM clinical_audit_events')).all())==1
    with f.foreign.connect() as c:assert c.execute(text('SELECT * FROM clinical_audit_events')).all()==[]
    for role in (f.reviewer,f.foreign):
        with role.begin() as c:
            with pytest.raises(DBAPIError):c.exec_driver_sql('DELETE FROM clinical_audit_events')
    for method in ('get','patch','delete'):
        assert getattr(f.client,method)('/api/clinical-audit-events',headers=f.headers()).status_code==404
    with f.engine.begin() as c:c.exec_driver_sql(f'GRANT SELECT(id) ON clinical_audit_events TO {f.roles["writer"]}')
    with f.writer.connect() as c:
        with pytest.raises(RuntimeError,match='unsafe'):check_runtime(c)
    with f.engine.begin() as c:c.exec_driver_sql(f'REVOKE SELECT(id) ON clinical_audit_events FROM {f.roles["writer"]}')
    with f.writer.begin() as c:
        with pytest.raises(DBAPIError):
            c.execute(text("INSERT INTO clinical_audit_events(id,action,outcome,effective_actor_id,practice_owner_id,target_id,correlation_id) VALUES (:id,'report.read','allowed',:actor,:actor,:target,:id)"),{'id':uuid4(),'actor':f.ids['a'],'target':f.reports['a']})
    assert len(rows(f))==1


@pytest.mark.parametrize('failure',['audit','build','commit','version','cancel'])
def test_E_atomic_failure_and_safe_retry(fixture,monkeypatch,caplog,failure):
    f=fixture;key=uuid4();original_append=clinical_audit.append
    with monkeypatch.context() as m:
        if failure=='audit':
            def fail(db,**kwargs):
                if kwargs['outcome']=='success':raise RuntimeError('secret private-a payload')
                return original_append(db,**kwargs)
            m.setattr(clinical_audit,'append',fail)
        elif failure in ('build','cancel'):
            original=report_service.build_report
            async def fail(*args,**kwargs):
                await original(*args,**kwargs)
                if failure=='cancel':
                    import asyncio
                    raise asyncio.CancelledError()
                raise RuntimeError('secret private-a payload')
            m.setattr(report_service,'build_report',fail)
        elif failure=='version':
            def fail(mapper,connection,target):raise RuntimeError('secret private-a payload')
            event.listen(ReportVersion,'before_insert',fail)
        else:
            original_commit=Session.commit
            def fail(db):
                if db.info.get('clinical_attempts'):raise RuntimeError('secret private-a payload')
                return original_commit(db)
            m.setattr(Session,'commit',fail)
        try:
            if failure=='cancel':
                with pytest.raises(BaseException):regenerate(f,key)
            else:
                result=regenerate(f,key)
                assert result.status_code==503 and 'secret' not in result.text and 'private-a' not in result.text
        finally:
            if failure=='version':event.remove(ReportVersion,'before_insert',fail)
    with f.engine.connect() as c:
        assert c.scalar(text('SELECT count(*) FROM report_versions WHERE case_id=:c'),{'c':f.cases['a']})==1
        assert c.scalar(text('SELECT count(*) FROM assessment_quota_reservations'))==0
    assert rows(f)==[] and f.objects=={}
    assert regenerate(f,key).status_code==200
    assert len([r for r in rows(f) if r['outcome']=='success'])==1
    assert 'secret private-a' not in caplog.text


def test_E_read_audit_failure_withholds_payload_and_preserves_denial(fixture,monkeypatch,caplog):
    f=fixture
    def fail(*a,**kw):raise RuntimeError('secret private-a')
    monkeypatch.setattr(clinical_audit,'append',fail)
    allowed=f.client.get(f'/api/reports/{f.reports["a"]}/json',headers=f.headers())
    denied=f.client.get(f'/api/reports/{f.reports["b"]}/json',headers=f.headers())
    assert allowed.status_code==503 and 'private-a' not in allowed.text
    assert denied.status_code==404 and denied.json()=={'detail':'Report not found'}
    assert rows(f)==[] and 'clinical_audit_unavailable' in caplog.text and 'secret private-a' not in caplog.text


def test_support_bearer_cookie_removal_refresh_and_legacy_sessions(fixture):
    f=fixture
    with Session(f.engine) as db:
        auth=build_impersonation_response(db,db.get(User,f.ids['admin']),str(f.ids['a']))
        refreshed=refresh_user_tokens(db,auth['refresh_token'])
        old=refresh_user_tokens(db,create_refresh_token(str(f.ids['a'])))
    for token in (auth['access_token'],refreshed['access_token']):
        headers={'Authorization':'Bearer '+token}
        for method,url in [('get',f'/api/reports/{f.reports["a"]}/json'),('post',f'/api/cases/{f.cases["a"]}/generate-report')]:
            response=getattr(f.client,method)(url,headers=headers)
            assert response.status_code==403,response.text
    for token in (create_access_token(str(f.ids['a'])),old['access_token']):
        assert f.client.get(f'/api/reports/{f.reports["a"]}/json',headers={'Authorization':'Bearer '+token}).status_code==401
    assert all(r['initiating_actor_id']==f.ids['admin'] and r['reason']=='support_mode' for r in rows(f))
    assert not f.builds


def test_uncertain_commit_keeps_committed_artifacts_and_replay(fixture,monkeypatch):
    f=fixture;key=uuid4();original=Session.commit
    with monkeypatch.context() as m:
        def lost_ack(db):
            attempted=bool(db.info.get('clinical_attempts'))
            original(db)
            if attempted:raise RuntimeError('Lost commit acknowledgement')
        m.setattr(Session,'commit',lost_ack)
        assert regenerate(f,key).status_code==503
    assert len(f.objects)==2
    replay=regenerate(f,key)
    assert replay.status_code==200 and len(f.builds)==1
    assert len([r for r in rows(f) if r['outcome']=='success'])==1
    assert clinical_artifacts.reconcile_attempt(f.writer,f.ids['a'],replay.json()['report_version_id'])=='kept_committed'


def test_storage_public_block_refuses_regeneration(fixture,monkeypatch):
    f=fixture
    monkeypatch.setattr(storage_service.s3,'get_bucket_policy_status',lambda **kw:{'PolicyStatus':{'IsPublic':True}})
    assert regenerate(f).status_code==503
    assert not f.builds and rows(f)==[]

def test_migration_preserves_history_and_second_replay(fixture):
    f=fixture
    assert f.client.get(f'/api/reports/{f.reports["a"]}/json',headers=f.headers()).status_code==200
    prior_events=[dict(r) for r in rows(f)]
    with f.engine.connect() as c:
        old_audit=c.execute(text('SELECT id,action,user_id,created_at,metadata_json FROM audit_logs ORDER BY id')).all()
        ledger=c.execute(text("SELECT version,applied_at FROM schema_migrations WHERE version <> '20261121_clinical_audit.sql' ORDER BY version")).all()
    with f.engine.begin() as c:
        c.execute(text("DELETE FROM schema_migrations WHERE version='20261121_clinical_audit.sql'"))
    run_migrations(f.engine)
    assert [dict(r) for r in rows(f)]==prior_events
    with f.engine.connect() as c:
        assert c.execute(text('SELECT id,action,user_id,created_at,metadata_json FROM audit_logs ORDER BY id')).all()==old_audit
        assert c.execute(text("SELECT version,applied_at FROM schema_migrations WHERE version <> '20261121_clinical_audit.sql' ORDER BY version")).all()==ledger
        first=c.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')).all()
    run_migrations(f.engine)
    with f.engine.connect() as c:assert c.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')).all()==first
    with f.writer.connect() as c:check_runtime(c)


def test_F_restore_versions_roles_scope_and_audit(fixture):
    import hashlib
    import subprocess
    import time
    f=fixture
    source=os.getenv('GO360_CLINICAL_SOURCE_CONTAINER')
    if not source:pytest.skip('Set GO360_CLINICAL_SOURCE_CONTAINER for isolated Docker restore')
    assert regenerate(f).status_code==200
    assert f.client.get(f'/api/reports/{f.reports["b"]}/json',headers=f.headers()).status_code==404
    def canonical(engine):
        with engine.connect() as c:
            return {table:[dict(r) for r in c.execute(text(f'SELECT * FROM {table} ORDER BY 1')).mappings()]
                    for table in ('users','patients','cases','report_versions','share_links','audit_logs','clinical_audit_events',
                                  'clinical_audit_reviewer_scopes','schema_migrations','assessment_quota_reservations')}
    before=canonical(f.engine)
    def docker(*args,input=None):
        result=subprocess.run(['docker',*args],input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if result.returncode:raise RuntimeError('Synthetic restore command failed: '+result.stderr.decode(errors='replace')[:3000])
        return result.stdout
    major=str(f.admin.dialect.server_version_info[0])
    name='go360-clinical-restore-'+uuid4().hex[:12]
    target=None; restored_writer=None; restored_reviewer=None
    started=time.monotonic()
    try:
        dump=docker('exec',source,'pg_dump','-U','synthetic','-d','synthetic','-Fc','--schema',f.schema)
        globals_sql=docker('exec',source,'pg_dumpall','-U','synthetic','--roles-only','--no-role-passwords')
        dumped=time.monotonic()
        docker('run','--detach','--rm','--name',name,'-e','POSTGRES_USER=restore_admin','-e','POSTGRES_PASSWORD=synthetic',
               '-e','POSTGRES_DB=synthetic','-p','127.0.0.1::5432',f'postgres:{major}')
        for attempt in range(100):
            ready=subprocess.run(['docker','exec',name,'pg_isready','-h','127.0.0.1','-U','restore_admin'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if ready.returncode==0:break
            time.sleep(.2)
        else:raise RuntimeError('Synthetic restore target not ready')
        docker('exec','-i',name,'psql','-U','restore_admin','-d','synthetic','-v','ON_ERROR_STOP=1',input=globals_sql)
        docker('exec','-i',name,'pg_restore','-U','restore_admin','-d','synthetic','--exit-on-error',input=dump)
        port=int(docker('port',name,'5432').decode().strip().rsplit(':',1)[1])
        target=create_engine(f.admin.url.set(username='restore_admin',password='synthetic',port=port),connect_args={'options':f'-csearch_path={f.schema}'})
        for role in f.roles.values():
            if role!=f.roles['owner']:
                with target.begin() as c:c.exec_driver_sql(f"ALTER ROLE {role} PASSWORD 'synthetic'")
        restored=time.monotonic()
        assert canonical(target)==before
        restored_writer=create_engine(target.url.set(username=f.roles['writer']),connect_args={'options':f'-csearch_path={f.schema}'})
        restored_reviewer=create_engine(target.url.set(username=f.roles['reviewer']),connect_args={'options':f'-csearch_path={f.schema}'})
        with restored_writer.connect() as c:check_runtime(c)
        with restored_writer.begin() as c:
            with pytest.raises(DBAPIError):c.exec_driver_sql('DELETE FROM clinical_audit_events')
        with restored_reviewer.connect() as c:
            assert all(r==f.ids['a'] for r in c.execute(text('SELECT practice_owner_id FROM clinical_audit_events')).scalars())
        def restored_database():
            with Session(restored_writer) as db:yield db
        original=f.app.dependency_overrides[deps.get_db]
        f.app.dependency_overrides[deps.get_db]=restored_database
        try:
            assert f.client.get(f'/api/reports/{f.reports["a"]}/json',headers=f.headers()).status_code==200
            denied=f.client.get(f'/api/reports/{f.reports["b"]}/json',headers=f.headers())
            assert denied.status_code==404 and denied.json()=={'detail':'Report not found'}
        finally:f.app.dependency_overrides[deps.get_db]=original
        with target.connect() as c:
            samples=[dict(r) for r in c.execute(text('SELECT * FROM clinical_audit_events ORDER BY occurred_at,id')).mappings()]
        Path('artifacts/clinical-audit-samples.json').write_text(json.dumps({'synthetic_only':True,'events':samples},indent=2,default=str)+'\n',encoding='utf-8')
        evidence={'requirement':'G360-08-001','test':'T-GO360-012-F','synthetic_only':True,
            'dump_seconds':round(dumped-started,3),'restore_with_role_recreation_seconds':round(restored-dumped,3),
            'reconciliation_seconds':round(time.monotonic()-restored,3),'observed_missing_or_changed_rows':0,
            'tables_reconciled':len(before),'writer_and_scoped_reviewer_rechecked':True,
            'database_dump_sha256':hashlib.sha256(dump).hexdigest(),
            'limitations':'Synthetic PostgreSQL recovery only; no hosted RTO/RPO/SLO or independent archive guarantee; S3 transport synthetic.'}
        out=Path('artifacts/clinical-audit-restore.json');out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
    finally:
        for e in (restored_writer,restored_reviewer,target):
            if e is not None:e.dispose()
        subprocess.run(['docker','stop',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def test_E_database_audit_refusal_recovers(fixture,caplog):
    f=fixture
    with f.engine.begin() as c:
        c.exec_driver_sql("CREATE FUNCTION synthetic_audit_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic audit unavailable'; END $$")
        c.exec_driver_sql('CREATE TRIGGER synthetic_audit_fault BEFORE INSERT ON clinical_audit_events FOR EACH ROW EXECUTE FUNCTION synthetic_audit_fault()')
    response=f.client.get(f'/api/reports/{f.reports["a"]}/json',headers=f.headers())
    assert response.status_code==503 and 'private-a' not in response.text
    key=uuid4();assert regenerate(f,key).status_code==503
    assert not f.objects and rows(f)==[]
    with f.engine.begin() as c:c.exec_driver_sql('DROP TRIGGER synthetic_audit_fault ON clinical_audit_events')
    assert regenerate(f,key).status_code==200
    assert 'synthetic audit unavailable' not in caplog.text


def test_append_helper_preserves_legacy_commit_contract(fixture):
    from app.services.audit_service import log_action
    f=fixture
    with Session(f.writer) as db:
        committed=log_action(db,'synthetic.committed',user_id=f.ids['a']).id
        db.rollback()
    with Session(f.writer) as db:
        pending=log_action(db,'synthetic.pending',user_id=f.ids['a'],commit=False).id
        db.rollback()
    with Session(f.engine) as db:
        assert db.get(AuditLog,committed) is not None
        assert db.get(AuditLog,pending) is None


def test_hosted_startup_checks_actual_writer(fixture,monkeypatch):
    from app import main
    f=fixture
    monkeypatch.setattr(main,'APP_ENV','staging')
    monkeypatch.setattr(main,'engine',f.engine)
    with pytest.raises(RuntimeError,match='privileged'):
        with TestClient(main.create_app()):pass
    monkeypatch.setattr(main,'engine',f.writer)
    with TestClient(main.create_app()) as client:assert client.get('/health').status_code==200


def test_distinct_keys_have_unique_versions_and_quota_limit(fixture,monkeypatch):
    f=fixture
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:regenerate(f),range(2)))
    assert all(r.status_code==200 for r in results)
    assert {r.json()['version_number'] for r in results}=={2,3}
    for _ in range(3):assert regenerate(f).status_code==200
    assert regenerate(f).status_code==429
    from app.services import assessment_quota
    monkeypatch.setattr(assessment_quota,'assessment_tier',lambda db,user:'pro')
    assert regenerate(f).status_code==200
    assert len([r for r in rows(f) if r['outcome']=='success'])==6


def test_interrupted_cleanup_is_idempotent_and_keeps_unknown_commit(fixture,monkeypatch):
    f=fixture;attempt=uuid4()
    keys=[storage_service.normalise_key(k) for k in clinical_artifacts.attempt_filenames(f.ids['a'],attempt)]
    for key in keys:f.objects[key]=b'synthetic'
    assert clinical_artifacts.reconcile_attempt(f.writer,f.ids['a'],attempt,remove=False)=='would_remove_unreferenced'
    assert len(f.objects)==2
    original=storage_service.s3.delete_object
    def fail(**kwargs):raise RuntimeError('synthetic storage unavailable')
    with monkeypatch.context() as m:
        m.setattr(storage_service.s3,'delete_object',fail)
        with pytest.raises(RuntimeError):clinical_artifacts.reconcile_attempt(f.writer,f.ids['a'],attempt)
    assert len(f.objects)==2
    assert clinical_artifacts.reconcile_attempt(f.writer,f.ids['a'],attempt)=='removed_unreferenced'
    assert clinical_artifacts.reconcile_attempt(f.writer,f.ids['a'],attempt)=='removed_unreferenced'
    assert not f.objects

def test_real_renderer_through_regeneration_route(fixture,monkeypatch):
    """Only AI and object transport are synthetic; parser, renderer and PDF are real."""
    from app.parser.html_parser import parse_html_report
    from app.services.report_builder import build_report
    f=fixture
    parsed=parse_html_report(Path('app/tests/fixtures/qrma/ResultOne.htm').read_bytes().decode('cp1252'))
    parsed.patient.full_name='Synthetic Audit Fixture'
    monkeypatch.setattr(report_service,'parse_and_enrich_case_html',lambda case:parsed)
    monkeypatch.setattr(report_service,'build_report',build_report)
    import time
    started=time.monotonic()
    response=regenerate(f)
    elapsed=time.monotonic()-started
    Path('artifacts/clinical-audit-render.json').write_text(json.dumps({'synthetic_only':True,'endpoint_seconds':round(elapsed,3),'actor_lock_duration_upper_bound_seconds':round(elapsed,3),'capacity_or_slo_target':False}),encoding='utf-8')
    assert response.status_code==200,response.text
    assert len(f.objects)==2
    assert any(value.startswith(b'%PDF-') for value in f.objects.values())
    assert all('/clinical-attempts/' in key for key in f.objects)
    with Session(f.engine) as db:
        from uuid import UUID
        report=db.get(ReportVersion,UUID(response.json()['report_version_id']))
        assert report.status.value=='ready' and report.report_json['viewer']
        assert report.html_path in f.objects and report.pdf_path in f.objects


def test_missing_bucket_policy_is_safe_only_with_public_access_block(fixture,monkeypatch):
    f=fixture
    def missing(**kwargs):raise storage_service.ClientError({'Error':{'Code':'NoSuchBucketPolicy'}},'GetBucketPolicyStatus')
    monkeypatch.setattr(storage_service.s3,'get_bucket_policy_status',missing)
    assert regenerate(f).status_code==200

def test_runtime_password_login_and_recovery_helpers(fixture,caplog):
    from app.utils.security import hash_password
    from app.services.auth_service import create_password_reset_token,create_email_verification_token
    f=fixture;password='SyntheticClinicalAudit230!'
    with Session(f.engine) as db:
        user=db.get(User,f.ids['a']);user.password_hash=hash_password(password);user.email=uuid4().hex+'@example.com';email=user.email;db.commit()
    response=f.client.post('/auth/login',json={'email':email,'password':password})
    assert response.status_code==200,response.text
    with f.engine.connect() as c:assert c.scalar(text("SELECT count(*) FROM auth_events WHERE event_type='login' AND status='success'"))==1
    assert f.client.get(f'/api/reports/{f.reports["a"]}/json').status_code==200
    with Session(f.writer) as db:
        user=db.get(User,f.ids['a'])
        assert create_password_reset_token(db,user)
        assert create_email_verification_token(db,user)
