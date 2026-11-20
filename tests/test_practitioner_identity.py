"""Real JWT/profile APIs and PostgreSQL identity constraints, synthetic data only."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from test_admin_entitlements import auth_engine, auth_app
from app.api.routes_practitioner_profile import router
from app.db.models import User, ExternalIdentity, AuditLog, Patient, UserCapability
from app.services.auth_service import get_or_create_social_user
from app.services.social_auth_service import normalise_social_profile


def social(db, subject, email):
    return get_or_create_social_user(db, provider='google', provider_subject=subject,
                                    email=email, full_name='Synthetic Practitioner')


def test_callbacks_preserve_identity_across_email_change(auth_engine):
    subject = uuid4().hex
    with Session(auth_engine) as db:
        original = social(db, subject, subject+'@example.com').id
        assert social(db, subject, 'changed-'+subject+'@example.com').id == original
        assert db.get(User, original).email == subject+'@example.com'
        assert db.scalar(select(func.count()).select_from(ExternalIdentity).where(ExternalIdentity.subject==subject)) == 1
        assert db.scalar(select(func.count()).select_from(Patient).where(Patient.subject_user_id==original)) == 1
        with pytest.raises(HTTPException) as denied:
            social(db, uuid4().hex, subject+'@example.com')
        assert denied.value.status_code == 409
        user = db.get(User, original); user.is_active=False; db.commit()
        with pytest.raises(HTTPException) as inactive:
            social(db, subject, subject+'@example.com')
        assert inactive.value.status_code == 403


def test_concurrent_callbacks_converge(auth_engine):
    subject = uuid4().hex
    def callback(_):
        with Session(auth_engine) as db:
            return social(db, subject, subject+'@example.com').id
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(callback, range(3)))
    assert len(set(ids)) == 1


def test_invalid_subject_and_unverified_provider_proof(auth_engine):
    with Session(auth_engine) as db:
        with pytest.raises(HTTPException): social(db, '', 'nobody@example.com')
    assert not normalise_social_profile('google', {'email':'a@example.com','sub':'s'})['email_verified']
    assert not normalise_social_profile('google', {'email_verified':'false'})['email_verified']


def test_signed_callback_replay_and_unverified_refusal(auth_app,monkeypatch):
    from app.api import routes_auth
    from app.services.social_auth_service import build_social_state
    client,_,engine,_,_=auth_app
    subject=uuid4().hex
    info={'sub':subject,'email':subject+'@example.com','email_verified':True,'name':'Callback Person'}
    monkeypatch.setattr(routes_auth,'exchange_code_for_token',lambda *a:{'access_token':'synthetic-provider-token'})
    monkeypatch.setattr(routes_auth,'get_provider_user_info',lambda *a:info)
    url='/auth/social/google/callback'
    args={'code':'synthetic-code','state':build_social_state(provider='google')}
    assert client.get(url,params=args,follow_redirects=False).status_code==303
    first=client.get('/auth/me').json()['user']['id']
    info['email']='changed-'+subject+'@example.com'
    assert client.get(url,params=args,follow_redirects=False).status_code==303
    assert client.get('/auth/me').json()['user']['id']==first
    info['email_verified']=False
    denied=client.get(url,params=args,follow_redirects=False)
    assert 'social_error=google_failed' in denied.headers['location']


def test_concurrent_profile_updates_have_one_winner(auth_engine):
    from app.services.practitioner_profile import update_profile
    from app.api.routes_practitioner_profile import ProfileUpdate
    with Session(auth_engine) as db:
        user=social(db,uuid4().hex,uuid4().hex+'@example.com');uid=user.id
    def update_name(name):
        with Session(auth_engine) as db:
            try:
                update_profile(db,db.get(User,uid),ProfileUpdate(revision=0,full_name=name,phone=None,timezone='UTC'))
                return 200
            except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update_name,['Concurrent A','Concurrent B']))==[200,409]


def test_atomic_signup_failure(auth_engine, monkeypatch):
    import app.services.external_identity_service as service
    subject=uuid4().hex
    def fail(*args, **kwargs): raise RuntimeError('synthetic capability failure')
    monkeypatch.setattr(service, 'grant_capability', fail)
    with Session(auth_engine) as db:
        with pytest.raises(RuntimeError): social(db, subject, subject+'@example.com')
        assert db.scalar(select(User.id).where(User.email==subject+'@example.com')) is None
        assert db.scalar(select(ExternalIdentity.id).where(ExternalIdentity.subject==subject)) is None


@pytest.fixture
def profile_app(auth_app):
    client,login,engine,ids,report_id=auth_app
    client.app.include_router(router)
    return auth_app


def test_profile_scoping_revision_validation_and_audit(profile_app):
    client,login,engine,ids,_=profile_app
    path='/api/practitioner/profile/me'
    assert client.get(path).status_code == 401
    login('owner')
    initial=client.get(path).json()
    data=dict(revision=initial['revision'],full_name='Updated Person',phone='123',timezone='Europe/London')
    for field,value in [('id',str(ids['other'])),('email','other@example.com'),('role','admin')]:
        assert client.patch(path,json={**data,field:value}).status_code==422
    assert client.patch(path,json={**data,'timezone':'invalid/zone'}).status_code==422
    assert client.patch(path,json={**data,'full_name':'  '}).status_code==422
    response=client.patch(path,json=data)
    assert response.status_code==200 and response.json()['id']==str(ids['owner'])
    assert client.patch(path,json=data).status_code==200  # safe uncertain-response retry
    assert client.patch(path,json={**data,'full_name':'Stale tab'}).status_code==409
    with Session(engine) as db:
        assert db.get(User,ids['other']).full_name!='Updated Person'
        audit=db.scalar(select(AuditLog).where(AuditLog.user_id==ids['owner'],AuditLog.action=='profile.updated'))
        assert audit.metadata_json=={'fields':['full_name','phone','timezone'],'revision':initial['revision']+1}
    login('client')
    assert client.get(path).status_code==403


def test_support_mode_is_read_only(profile_app):
    client,login,engine,ids,_=profile_app
    login('admin')
    assert client.post('/auth/impersonate/'+str(ids['owner'])).status_code==200
    path='/api/practitioner/profile/me'
    profile=client.get(path).json()
    assert profile['read_only'] is True
    data={k:profile[k] for k in ['revision','full_name','phone','timezone']}
    assert client.patch(path,json=data).status_code==403


def test_profile_audit_failure_rolls_back(profile_app,monkeypatch):
    client,login,engine,ids,_=profile_app
    login('owner'); path='/api/practitioner/profile/me'; before=client.get(path).json()
    import app.services.practitioner_profile as service
    def fail(*args,**kwargs):raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(service,'event',fail)
    with pytest.raises(RuntimeError):
        client.patch(path,json=dict(revision=before['revision'],full_name='Must Roll Back',phone=None,timezone='UTC'))
    with Session(engine) as db:
        assert db.get(User,ids['owner']).full_name==before['full_name']
        assert db.get(User,ids['owner']).profile_revision==before['revision']


def test_additive_migration_preserves_existing_identity_and_ledger(auth_engine):
    from app.db.migrate import run_migrations
    # A separate schema models the previous schema and ledger, containing an old user.
    from app.db.base import Base
    from sqlalchemy import create_engine
    schema='identity_replay_'+uuid4().hex
    with auth_engine.begin() as conn:conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine=create_engine(auth_engine.url,connect_args={'options':f'-csearch_path={schema}'})
    try:
        Base.metadata.create_all(engine);run_migrations(engine)
        principal=uuid4()
        with engine.begin() as conn:
            conn.exec_driver_sql('DROP TABLE external_identities')
            conn.exec_driver_sql('ALTER TABLE users DROP COLUMN profile_revision')
            conn.execute(text("DELETE FROM schema_migrations WHERE version='20261120_practitioner_identity.sql'"))
            conn.execute(text("INSERT INTO users(id,email,full_name,password_hash,role,is_active,recommendation_mode_default,timezone) VALUES (:id,'legacy@example.com','Legacy Name','unused','practitioner',true,'natural_approaches_clinical','Europe/London')"),{'id':principal})
            before=list(conn.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')))
        run_migrations(engine)
        with engine.begin() as conn:
            after=list(conn.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')))
            assert [r for r in after if r[0]!='20261120_practitioner_identity.sql']==before
            assert conn.execute(text('SELECT id,full_name,profile_revision FROM users')).one()==(principal,'Legacy Name',0)
            assert conn.execute(text('SELECT COUNT(*) FROM external_identities')).scalar()==0
        run_migrations(engine)
        with engine.begin() as conn:
            assert list(conn.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')))==after
    finally:
        engine.dispose()
        with auth_engine.begin() as conn:conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
