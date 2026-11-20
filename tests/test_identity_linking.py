"""Real authenticated linking APIs; only external OAuth transport is synthetic."""
import hashlib,json
from urllib.parse import urlsplit,parse_qs
from datetime import timedelta
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from test_admin_entitlements import auth_engine,auth_app
from app.api import routes_sign_in_methods,routes_auth
from app.db.models import ExternalIdentity,IdentityLinkAttempt,User,AuditLog
from app.services import social_auth_service as oauth,identity_link_service as service

PASSWORD='SyntheticPassword230!'
ORIGIN={'Origin':'https://testserver'}


@pytest.fixture
def link_app(auth_app,monkeypatch):
    client,login,engine,ids,_=auth_app
    client.base_url=httpx.URL('https://testserver')
    client.app.include_router(routes_sign_in_methods.router)
    for key,value in [('BASE_URL','https://testserver'),('GOOGLE_CLIENT_ID','synthetic'),('GOOGLE_CLIENT_SECRET','synthetic'),
        ('MICROSOFT_CLIENT_ID','synthetic'),('MICROSOFT_CLIENT_SECRET','synthetic'),('SOCIAL_LOGIN_ENABLED','true')]:monkeypatch.setenv(key,value)
    profile={'sub':uuid4().hex,'email':'provider-'+uuid4().hex+'@example.com','email_verified':True,'name':'Provider name'}
    exchange=[]
    def token_exchange(provider,code,**kwargs):
        exchange.append(kwargs)
        return {'access_token':'synthetic-provider-token'}
    monkeypatch.setattr(oauth,'exchange_code_for_token',token_exchange)
    monkeypatch.setattr(oauth,'get_provider_user_info',lambda *args:profile)
    monkeypatch.setattr(routes_auth,'exchange_code_for_token',token_exchange)
    monkeypatch.setattr(routes_auth,'get_provider_user_info',lambda *args:profile)
    return client,login,engine,ids,profile,exchange


def begin(client,provider='google'):
    response=client.post('/auth/sign-in-methods/'+provider+'/link',headers=ORIGIN,json={})
    assert response.status_code==200,response.text
    query=parse_qs(urlsplit(response.json()['url']).query)
    assert query['code_challenge_method']==['S256']
    from http.cookies import SimpleCookie
    cookies=SimpleCookie();cookies.load(service.cookie_name(provider)+'='+client.cookies.get(service.cookie_name(provider)))
    cookie=json.loads(cookies[service.cookie_name(provider)].value)
    assert query['code_challenge']==[oauth._b64_encode(hashlib.sha256(cookie['verifier'].encode()).digest())]
    return query['state'][0]


def verified(client,provider='google'):
    state=begin(client,provider)
    response=client.get('/auth/social/'+provider+'/callback',params={'state':state,'code':'synthetic-code'},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/account/sign-in-methods?link=confirm'
    row=next(p for p in client.get('/auth/sign-in-methods').json()['providers'] if p['provider']==provider)
    return row['pending']['id'],state


@pytest.mark.parametrize('actor',['admin','allowlisted','owner','client'])
def test_linking_retains_existing_identity_email_and_authority(link_app,actor):
    client,login,engine,ids,profile,exchange=link_app;login(actor)
    original=client.get('/auth/me').json()['user']
    attempt,_=verified(client)
    assert exchange[-1]['code_verifier']
    with Session(engine) as db:assert db.scalar(select(func.count()).select_from(ExternalIdentity).where(ExternalIdentity.user_id==ids[actor]))==0
    response=client.post('/auth/sign-in-methods/confirm/'+attempt,headers=ORIGIN,json={'current_password':PASSWORD})
    assert response.status_code==200
    methods=client.get('/auth/sign-in-methods').json()
    assert methods['user_id']==str(ids[actor])
    assert next(p for p in methods['providers'] if p['provider']=='google')['linked']
    with Session(engine) as db:
        row=db.scalar(select(ExternalIdentity).where(ExternalIdentity.user_id==ids[actor]));assert row.user_id==ids[actor] and row.email==profile['email']
        pending=db.get(IdentityLinkAttempt,attempt);assert pending.status=='complete' and pending.candidate_subject is None
        event=db.scalar(select(AuditLog).where(AuditLog.user_id==ids[actor],AuditLog.action=='identity.link',AuditLog.outcome=='success'))
        assert event.metadata_json=={'provider':'google'}
    client.post('/auth/logout')
    state=oauth.build_social_state(provider='google')
    assert client.get('/auth/social/google/callback',params={'code':'synthetic','state':state},follow_redirects=False).status_code==303
    after=client.get('/auth/me').json()['user'];assert after['id']==original['id'] and after['email']==original['email']
    if actor in {'admin','allowlisted'}:assert client.get('/api/subscription/status').json()['tier']=='internal_admin'
    if actor=='client':assert client.get('/api/reports').status_code==403


def test_no_auto_link_csrf_replay_and_changed_session(link_app):
    client,login,engine,ids,profile,_=link_app;login('owner')
    assert client.post('/auth/sign-in-methods/google/link',headers={'Origin':'https://attacker.invalid'},json={}).status_code==403
    attempt,state=verified(client)
    assert 'link_error=' in client.get('/auth/social/google/callback',params={'state':state,'code':'again'},follow_redirects=False).headers['location']
    # Replay failure clears the browser proof, so confirmation cannot reuse it.
    assert client.post('/auth/sign-in-methods/confirm/'+attempt,headers=ORIGIN,json={'current_password':PASSWORD}).status_code==403
    attempt,_=verified(client)
    login('admin')
    assert client.post('/auth/sign-in-methods/confirm/'+attempt,headers=ORIGIN,json={'current_password':PASSWORD}).status_code==403
    with Session(engine) as db:assert db.scalar(select(func.count()).select_from(ExternalIdentity).where(ExternalIdentity.user_id==ids['owner']))==0


def test_wrong_password_limit_and_other_users_provider_refused(link_app):
    client,login,engine,ids,profile,_=link_app;login('owner')
    attempt,_=verified(client)
    for _ in range(5):assert client.post('/auth/sign-in-methods/confirm/'+attempt,headers=ORIGIN,json={'current_password':'Wrong!'}).status_code==400
    assert client.post('/auth/sign-in-methods/confirm/'+attempt,headers=ORIGIN,json={'current_password':PASSWORD}).status_code==403
    with Session(engine) as db:
        row=db.get(IdentityLinkAttempt,attempt);assert row.status=='failed' and row.candidate_email is None
        db.add(ExternalIdentity(user_id=ids['other'],provider='google',subject=profile['sub'],email=profile['email']));db.commit()
    attempt,_=verified(client)
    assert client.post('/auth/sign-in-methods/confirm/'+attempt,headers=ORIGIN,json={'current_password':PASSWORD}).status_code==409
    with Session(engine) as db:assert db.scalar(select(ExternalIdentity).where(ExternalIdentity.subject==profile['sub'])).user_id==ids['other']


def test_support_unverified_cancel_expiry_and_disabled_provider(link_app,monkeypatch):
    client,login,engine,ids,_,_=link_app;login('admin')
    assert client.post('/auth/impersonate/'+str(ids['owner'])).status_code==200
    assert client.get('/auth/sign-in-methods').json()['read_only'] is True
    assert client.post('/auth/sign-in-methods/google/link',headers=ORIGIN,json={}).status_code==403
    client.post('/auth/impersonate/exit');login('owner')
    state=begin(client)
    cancelled=client.get('/auth/social/google/callback',params={'state':state,'error':'access_denied'},follow_redirects=False)
    assert 'link_error=cancelled' in cancelled.headers['location']
    state=begin(client)
    with Session(engine) as db:
        row=db.scalar(select(IdentityLinkAttempt).where(IdentityLinkAttempt.state_hash==service.digest(state)))
        row.expires_at=service.now()-timedelta(seconds=1);db.commit()
    assert 'link_error=' in client.get('/auth/social/google/callback',params={'state':state,'code':'synthetic'},follow_redirects=False).headers['location']
    monkeypatch.setenv('SOCIAL_LOGIN_ENABLED','false')
    assert client.post('/auth/sign-in-methods/google/link',headers=ORIGIN,json={}).status_code==503


def test_link_and_audit_are_atomic(link_app,monkeypatch):
    client,login,engine,ids,_,_=link_app;login('owner');attempt,_=verified(client)
    def fail(*args,**kwargs):raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(service,'audit',fail)
    with pytest.raises(RuntimeError):client.post('/auth/sign-in-methods/confirm/'+attempt,headers=ORIGIN,json={'current_password':PASSWORD})
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(ExternalIdentity).where(ExternalIdentity.user_id==ids['owner']))==0
        assert db.get(IdentityLinkAttempt,attempt).status=='verified'


def test_microsoft_link_and_callback_binding(link_app):
    client,login,_,ids,_,_=link_app;login('owner')
    attempt,_=verified(client,'microsoft')
    assert client.post('/auth/sign-in-methods/confirm/'+attempt,headers=ORIGIN,json={'current_password':PASSWORD}).status_code==200
    assert client.get('/auth/me').json()['user']['id']==str(ids['owner'])


def test_unverified_proof_and_missing_browser_cookie_refused(link_app):
    client,login,engine,ids,profile,_=link_app;login('owner')
    state=begin(client);profile['email_verified']=False
    assert 'link_error=' in client.get('/auth/social/google/callback',params={'state':state,'code':'synthetic'},follow_redirects=False).headers['location']
    profile['email_verified']=True;state=begin(client)
    client.cookies.delete(service.cookie_name('google'))
    assert 'link_error=' in client.get('/auth/social/google/callback',params={'state':state,'code':'synthetic'},follow_redirects=False).headers['location']
    with Session(engine) as db:assert db.scalar(select(ExternalIdentity.id).where(ExternalIdentity.user_id==ids['owner'])) is None


def test_concurrent_accounts_cannot_claim_same_provider(link_app):
    from fastapi.testclient import TestClient
    from concurrent.futures import ThreadPoolExecutor
    client,login,engine,ids,profile,_=link_app;login('owner');first,_=verified(client)
    with Session(engine) as db:email=db.get(User,ids['other']).email
    with TestClient(client.app,base_url='https://testserver') as second:
        assert second.post('/auth/login',json={'email':email,'password':PASSWORD}).status_code==200
        other,_=verified(second)
        def confirm(pair):
            c,attempt=pair
            return c.post('/auth/sign-in-methods/confirm/'+attempt,headers=ORIGIN,json={'current_password':PASSWORD}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(confirm,[(client,first),(second,other)]))==[200,409]
    with Session(engine) as db:
        rows=list(db.scalars(select(ExternalIdentity).where(ExternalIdentity.subject==profile['sub'])))
        assert len(rows)==1 and rows[0].user_id in {ids['owner'],ids['other']}


def test_linking_migration_preserves_existing_bindings_and_history(auth_engine):
    from app.db.base import Base
    from app.db.migrate import run_migrations,ordered_migrations
    from sqlalchemy import create_engine,text
    schema='link_replay_'+uuid4().hex
    with auth_engine.begin() as conn:conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine=create_engine(auth_engine.url,connect_args={'options':f'-csearch_path={schema}'})
    try:
        names=[p.name for p in ordered_migrations()]
        assert names.index('20261120_provider_identity_linking.sql')>names.index('20261120_practitioner_identity.sql')
        Base.metadata.create_all(engine);run_migrations(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql('DROP TABLE identity_link_attempts')
            conn.exec_driver_sql('ALTER TABLE external_identities DROP CONSTRAINT uq_external_identity_user_provider')
            conn.exec_driver_sql('ALTER TABLE external_identities DROP COLUMN email')
            conn.execute(text("DELETE FROM schema_migrations WHERE version='20261120_provider_identity_linking.sql'"))
            before=list(conn.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')))
        uid=uuid4();binding=uuid4()
        with Session(engine) as db:
            db.add(User(id=uid,email='legacy@example.com',full_name='Legacy Identity',password_hash='unused',role='admin',is_active=True));db.flush()
            db.execute(text("INSERT INTO external_identities(id,user_id,provider,subject) VALUES (:id,:user,'google','legacy-subject')"),{'id':binding,'user':uid});db.commit()
        run_migrations(engine)
        with engine.connect() as conn:
            after=list(conn.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')))
            assert [r for r in after if r[0]!='20261120_provider_identity_linking.sql']==before
            assert conn.execute(text('SELECT id,user_id,subject,email FROM external_identities')).one()==(binding,uid,'legacy-subject',None)
            assert conn.execute(text('SELECT COUNT(*) FROM identity_link_attempts')).scalar()==0
        run_migrations(engine)
        with engine.connect() as conn:assert list(conn.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')))==after
    finally:
        engine.dispose()
        with auth_engine.begin() as conn:conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
