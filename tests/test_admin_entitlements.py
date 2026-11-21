"""UAT regressions using PostgreSQL, real password login and JWT cookies."""
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

os.environ.setdefault('DATABASE_URL','postgresql://localhost/synthetic')

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.api import deps, routes_auth, routes_ui, routes_subscriptions, routes_share_links, routes_reports, routes_cases, routes_client_records, routes_share, routes_content_releases
from fastapi.staticfiles import StaticFiles
from app.api.share_helpers import _enforce_share_payment_or_402
from app.db.models import User, Patient, Case, ReportVersion, ShareLink
from app.db.base import Base
from app.db.migrate import run_migrations
from app.services.subscription_service import effective_access, require_subscription_feature, PRO_FEATURES
from app.services.assessment_quota import assessment_tier
from app.utils.security import hash_password


@pytest.fixture(scope='module')
def auth_engine():
    url=os.getenv('GO360_CLINIC_TEST_DATABASE_URL')
    if not url: pytest.skip('Disposable PostgreSQL required')
    schema='uat_'+uuid4().hex
    outer=create_engine(url)
    with outer.begin() as c:c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    Base.metadata.create_all(engine)
    run_migrations(engine)
    yield engine
    engine.dispose()
    with outer.begin() as c:c.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    outer.dispose()


@pytest.fixture
def auth_app(auth_engine,monkeypatch):
    monkeypatch.setenv('PLATFORM_ADMIN_EMAILS','')
    monkeypatch.setenv('APP_ENV','staging')
    ids={};emails={};password='SyntheticPassword230!'
    with Session(auth_engine) as db:
        for name,role in [('owner','practitioner'),('other','practitioner'),('admin','admin'),('allowlisted','practitioner'),('client','client')]:
            u=User(id=uuid4(),email=uuid4().hex+'@example.com',full_name='Synthetic '+name,
                   password_hash=hash_password(password),role=role,is_active=True,email_verified_at=datetime.now(timezone.utc))
            db.add(u);ids[name]=u.id;emails[name]=u.email
        db.flush()
        for key,value in [('platform_config',{'admin_emails':emails['allowlisted']}),('subscription_config',{'free_features':dict.fromkeys(PRO_FEATURES,False)}),('feature_flags_config',{'paid_shares_enabled':True})]:
            db.execute(text('INSERT INTO app_settings(key,value) VALUES (:key,CAST(:value AS jsonb)) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value'),{'key':key,'value':json.dumps(value)})
        patient=Patient(id=uuid4(),user_id=ids['owner'],full_name='Synthetic patient',first_name='Synthetic',last_name='Patient')
        db.add(patient);db.flush()
        case=Case(id=uuid4(),user_id=ids['owner'],patient_id=patient.id,title='Synthetic scan',raw_scan_html_path='synthetic/source.htm')
        db.add(case);db.flush()
        report=ReportVersion(id=uuid4(),case_id=case.id,created_by_user_id=ids['owner'],version_number=1,status='ready',recommendation_mode='natural_approaches_clinical',report_json={})
        db.add(report);db.commit();report_id=report.id
    app=FastAPI()
    for router in [routes_auth.router,routes_ui.router,routes_subscriptions.router,routes_share_links.router,routes_reports.router,routes_cases.router,routes_client_records.router,routes_share.router,routes_content_releases.router]:app.include_router(router)
    app.mount('/static',StaticFiles(directory='app/static'),name='static')
    def database():
        with Session(auth_engine) as db:yield db
    app.dependency_overrides[deps.get_db]=database
    with TestClient(app) as client:
        def login(name):
            response=client.post('/auth/login',json={'email':emails[name],'password':password})
            assert response.status_code==200
        yield client,login,auth_engine,ids,report_id


@pytest.mark.parametrize('name',['admin','allowlisted'])
def test_admin_override_preserves_billing_and_real_payment_readiness(auth_app,name):
    client,login,engine,ids,_=auth_app
    with engine.begin() as c:
        c.execute(text("INSERT INTO subscriptions(id,user_id,plan_code,status,current_period_end,cancel_at_period_end) VALUES (:id,:user,'actual-paid-plan','canceled',:end,false)"),{'id':uuid4(),'user':ids[name],'end':datetime.now(timezone.utc)-timedelta(days=3)})
    login(name)
    state=client.get('/api/subscription/status').json()
    assert state['tier']=='internal_admin' and set(state['features'])==PRO_FEATURES
    assert state['billing_status']=='canceled' and state['billing_plan_code']=='actual-paid-plan'
    assert state['display_plan_name']=='Platform Admin' and state['upgrade_nudge'] is None
    assert state['stripe_connect']['ready_for_direct_payouts'] is False
    with Session(engine) as db:
        actor=db.get(User,ids[name])
        assert assessment_tier(db,actor)=='internal_admin'
        for feature in PRO_FEATURES: require_subscription_feature(db,actor,feature)
        assert db.execute(text('SELECT status FROM subscriptions WHERE user_id=:id'),{'id':ids[name]}).scalar()=='canceled'


def test_impersonation_uses_target_access_and_restore_recovers_admin(auth_app):
    client,login,_,ids,_=auth_app
    login('admin')
    assert client.post('/auth/impersonate/'+str(ids['owner'])).status_code==200
    state=client.get('/api/subscription/status').json()
    assert state['tier']=='free' and state['is_platform_admin'] is False
    assert 'sell_shares' not in state['features']
    assert client.post('/auth/impersonate/exit').status_code==200
    assert client.get('/api/subscription/status').json()['tier']=='internal_admin'


def test_client_landing_and_account_are_real_pages(auth_app):
    client,login,_,_,_=auth_app
    login('client')
    for path in ['/client','/client/account']:
        response=client.get(path)
        assert response.status_code==200 and 'text/html' in response.headers['content-type']
    assert client.get('/api/reports').status_code==403


def test_paid_share_refusal_ownership_and_atomic_payment(auth_app,monkeypatch):
    client,login,engine,ids,report_id=auth_app
    path='/api/reports/'+str(report_id)+'/share-links'
    login('owner')
    assert client.post(path,json={'price_amount':1234}).status_code==402
    with engine.begin() as c:
        c.execute(text("UPDATE app_settings SET value='{}'::jsonb WHERE key='subscription_config'"))
    login('other')
    assert client.post(path,json={'price_amount':1234}).status_code==404
    login('owner')
    response=client.post(path,json={'price_amount':1234,'expires_in_days':14})
    assert response.status_code==200
    with Session(engine) as db:
        link=db.scalar(select(ShareLink).where(ShareLink.token==response.json()['token']))
        payment=db.execute(text('SELECT price_amount,requires_payment,payment_status FROM share_links WHERE id=:id'),{'id':link.id}).one()
        assert tuple(payment)==(1234,True,'unpaid') and link.price_pence==1234
        assert link.expires_at>datetime.now(timezone.utc)+timedelta(days=13)
        with pytest.raises(HTTPException) as refused:_enforce_share_payment_or_402(db,link)
        assert refused.value.status_code==402
    def fail(*args,**kwargs):raise RuntimeError('Synthetic payment write failure')
    monkeypatch.setattr(routes_share_links,'_set_share_payment_config',fail)
    with pytest.raises(RuntimeError,match='Synthetic payment'):
        client.post(path,json={'price_amount':999})
    with Session(engine) as db:
        assert len(list(db.scalars(select(ShareLink).where(ShareLink.report_version_id==report_id))))==1


def test_admin_does_not_override_disabled_paid_sharing(auth_app):
    client,login,engine,_,report_id=auth_app
    with engine.begin() as c:
        c.execute(text('UPDATE app_settings SET value=CAST(:value AS jsonb) WHERE key=:key'),{'value':json.dumps({'paid_shares_enabled':False}),'key':'feature_flags_config'})
    login('admin')
    state=client.get('/api/subscription/status').json()
    assert 'sell_shares' in state['features'] and state['paid_shares_enabled'] is False
    assert client.post('/api/reports/'+str(report_id)+'/share-links',json={'price_amount':100}).status_code==403


def test_new_version_endpoint_retains_ownership_and_entitlement_checks(auth_app,monkeypatch):
    client,login,engine,ids,report_id=auth_app
    with Session(engine) as db:case_id=db.get(ReportVersion,report_id).case_id
    path='/api/cases/'+str(case_id)+'/generate-report'
    login('owner');assert client.post(path).status_code==402
    login('admin');assert client.post(path).status_code==404
    with Session(engine) as db:
        db.get(Case,case_id).user_id=ids['admin'];db.commit()
    called=[]
    async def generate(db,case,user,request_key=None,*,caller_owned=False):
        assert caller_owned is True
        from types import SimpleNamespace
        called.append((user.id,request_key))
        assert assessment_tier(db,user)=='internal_admin'
        return SimpleNamespace(id=uuid4(),version_number=2,status='ready')
    monkeypatch.setattr(routes_cases,'generate_report_version',generate)
    key=str(uuid4())
    response=client.post(path,headers={'Idempotency-Key':key})
    assert response.status_code==200 and response.json()['version_number']==2
    assert called==[(ids['admin'],'browser:'+key)]


@pytest.mark.parametrize('payload',[{'price_amount':-1},{'price_amount':1.23},{'price_amount':100000000},{'price_amount':100,'price_currency':'not-a-currency'}])
def test_pricing_validation_rejects_invalid_values(auth_app,payload):
    client,login,_,_,report=auth_app
    login('owner')
    assert client.post('/api/reports/'+str(report)+'/share-links',json=payload).status_code==422


@pytest.mark.parametrize('password',[None,'SyntheticSharePassword!'])
def test_paid_assessment_cannot_bypass_paywall_or_password(auth_app,monkeypatch,password):
    client,login,engine,_,report=auth_app
    with engine.begin() as c:c.execute(text("UPDATE app_settings SET value='{}'::jsonb WHERE key='subscription_config'"))
    login('owner')
    response=client.post('/api/reports/'+str(report)+'/share-links',json={'price_amount':1234,'password':password})
    assert response.status_code==200
    token=response.json()['token'];path='/share/'+token
    from unittest.mock import Mock
    payload=Mock(return_value={'synthetic':'not-secret'})
    monkeypatch.setattr(routes_share,'_public_report_payload',payload)
    monkeypatch.setattr(routes_share,'_tenant_theme_from_report',lambda *args:{})
    monkeypatch.setattr(routes_share,'_track_share_event',lambda *args:None)
    if password:
        assert client.post(path+'/verify-password',data={'password':'wrong'}).status_code==401
    response=client.post(path+'/verify-password',data={'password':password or 'unused'})
    assert response.status_code==200 and 'Unlock your assessment report' in response.text
    assert 'Continue to secure payment' in response.text
    assert 'Unlock your assessment report' in client.get(path+'?preview=1').text
    for suffix in ['/data','/html','/pdf']:
        assert client.get(path+suffix).status_code==402
    payload.assert_not_called()
    # A completed but unpaid Stripe session must not unlock access.
    monkeypatch.setattr(routes_share,'_stripe_api_request',lambda *args:{'client_reference_id':token,'status':'complete','payment_status':'unpaid'})
    assert client.get(path+'/payment-success?session_id=synthetic').status_code==402
    with engine.begin() as c:c.execute(text("UPDATE share_links SET payment_status='paid' WHERE token=:token"),{'token':token})
    assert client.get(path+'/data').status_code==200
    assert client.post(path+'/checkout',follow_redirects=False).headers['location']==path


def test_assessment_checkout_returns_to_assessment_not_trend(auth_app,monkeypatch):
    client,login,engine,_,report=auth_app
    with engine.begin() as c:c.execute(text("UPDATE app_settings SET value='{}'::jsonb WHERE key='subscription_config'"))
    login('owner')
    token=client.post('/api/reports/'+str(report)+'/share-links',json={'price_amount':1200}).json()['token']
    sent=[]
    monkeypatch.setattr(routes_share,'_connect_destination_for_report',lambda *args:{'mode':'platform'})
    def stripe(method,path,payload=None,**kwargs):
        sent.append(payload or kwargs.get('data'))
        return {'id':'synthetic','url':'https://checkout.stripe.com/synthetic'}
    monkeypatch.setattr(routes_share,'_stripe_api_request',stripe)
    response=client.post('/share/'+token+'/checkout',follow_redirects=False)
    assert response.status_code==303
    assert sent[0]['cancel_url'].endswith('/share/'+token)
