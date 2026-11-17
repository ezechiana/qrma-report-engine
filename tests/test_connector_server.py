"""Real SQLite persistence + FastAPI boundaries, offline storage mocked."""
import asyncio
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
import types
import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select, Uuid
from sqlalchemy.orm import Session
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

class FlexibleUuid(TypeDecorator):
    impl=Uuid
    cache_ok=True
    def process_bind_param(self,value,dialect):
        return uuid.UUID(value) if isinstance(value,str) else value

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault('DATABASE_URL','sqlite:///connector-test-unused.db')
from app.db.base import Base
from app.db.models import User, Case, Patient, ReportVersion, AuditLog
from app.db.connector_models import ConnectorDevice, ConnectorPairing, IncomingAssessment
from app.services import connector_service as service

@compiles(JSONB,'sqlite')
def sqlite_json(type_,compiler,**kw):return 'JSON'

@pytest.fixture
def context(monkeypatch,tmp_path):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    originals=[]
    for model in (User,Patient,Case,ReportVersion,AuditLog):
        for column in model.__table__.columns:
            if isinstance(column.type,Uuid):
                originals.append((column,column.type));column.type=FlexibleUuid()
    for model in (User,Patient,Case,ReportVersion,ConnectorDevice,ConnectorPairing,IncomingAssessment,AuditLog):
        model.__table__.create(engine)
    db=Session(engine)
    user=User(id=uuid.uuid4(),email='dev@example.invalid',full_name='DEV Practitioner',password_hash='unused',is_active=True)
    other=User(id=uuid.uuid4(),email='other@example.invalid',full_name='Other',password_hash='unused',is_active=True)
    db.add_all([user,other]);db.commit()
    monkeypatch.setenv('TEMP_UPLOAD_DIR',str(tmp_path/'uploads'))
    storage=types.ModuleType('app.services.storage_service')
    storage.upload_bytes=Mock(side_effect=lambda key,*args:key);storage.download_bytes=Mock()
    monkeypatch.setitem(sys.modules,'app.services.storage_service',storage)
    sys.modules.pop('app.services.scan_import_service',None)
    from app.api import routes_connector as routes
    monkeypatch.setenv('GO360_CONNECTOR_ENABLED','1');monkeypatch.setenv('APP_ENV','test')
    monkeypatch.setenv('GO360_CONNECTOR_ORIGIN','https://dev.example.invalid')
    routes.limits.clear()
    subscription=types.ModuleType('app.services.subscription_service')
    subscription.require_subscription_feature=Mock()
    monkeypatch.setitem(sys.modules,'app.services.subscription_service',subscription)
    app=FastAPI();app.include_router(routes.router)
    def get_db():yield db
    app.dependency_overrides[routes.get_db]=get_db
    app.dependency_overrides[routes.get_current_active_user]=lambda:user
    client=TestClient(app,base_url='https://dev.example.invalid')
    device=ConnectorDevice(id=uuid.uuid4(),owner_id=user.id,credential_hash=routes.sha('token'),
                           expires_at=datetime(2099,1,1,tzinfo=timezone.utc))
    db.add(device);db.commit()
    yield types.SimpleNamespace(db=db,engine=engine,user=user,other=other,device=device,client=client,routes=routes,storage=storage)
    db.close();engine.dispose()
    for column,kind in originals:column.type=kind

@pytest.fixture(scope='module')
def raw():return (Path(__file__).parents[1]/'app/tests/fixtures/qrma/ResultOne.htm').read_bytes()


def post(c,raw,job=None,token='token',profile=service.PROFILE):
    return c.client.post('/api/connector/jobs/'+str(job or uuid.uuid4()),content=raw,
        headers={'authorization':'Bearer '+token,'content-type':'text/html','x-source-profile':profile,
                 'x-captured-at':datetime.now(timezone.utc).isoformat()})


def test_atomic_replay_conflict_no_business_creation(context,raw):
    c=context;job=uuid.uuid4()
    first=post(c,raw,job);assert first.status_code==200,first.text
    again=post(c,raw,job);assert again.status_code==200
    assert first.json()==again.json()
    assert post(c,raw+b'\n',job).status_code==409
    assert len(c.db.scalars(select(IncomingAssessment)).all())==1
    for model in (Patient,Case,ReportVersion):assert c.db.query(model).count()==0
    c.storage.upload_bytes.assert_not_called()


def test_duplicate_holds_and_changed_scan(context,raw):
    c=context
    first=post(c,raw).json()
    duplicate=post(c,raw).json()
    assert duplicate['status']=='needs_attention'
    assert duplicate['duplicate']['existing_assessment_id']==first['assessment_id']
    changed=raw.replace(b'59.21',b'59.22');assert changed!=raw
    assert post(c,changed).json()['status']=='awaiting_practitioner'


def test_unauthorized_cross_account_revocation(context,raw):
    c=context
    assert post(c,raw,token='wrong').status_code==401
    value=post(c,raw).json()
    c.client.app.dependency_overrides[c.routes.get_current_active_user]=lambda:c.other
    assert c.client.get('/api/incoming-assessments/'+value['assessment_id']).status_code==404
    assert c.client.get('/api/incoming-assessments').json()==[]
    c.device.revoked=True;c.db.commit()
    assert post(c,raw).status_code==401
    assert c.client.get('/api/connector/jobs/'+value['job_id'],headers={'authorization':'Bearer token'}).status_code==401


def test_browser_origin_and_device_approval(context):
    c=context;verifier='a'*64
    started=c.client.post('/api/connector/pairings',json={'connector_id':str(uuid.uuid4()),'challenge':hashlib.sha256(verifier.encode()).hexdigest()}).json()
    poll={'pairing_id':started['pairing_id'],'verifier':verifier}
    assert c.client.post('/api/connector/pairings/token',json=poll).status_code==428
    assert c.client.post('/api/connector/pairings/token',json={**poll,'verifier':'b'*64}).status_code==401
    page=c.client.get('/app/connector/approve?pairing_id='+started['pairing_id'])
    assert page.status_code==200
    import re
    nonce=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
    form={'pairing_id':started['pairing_id'],'code':started['user_code'],'csrf':nonce}
    assert c.client.post('/app/connector/approve',data=form).status_code==403
    approved=c.client.post('/app/connector/approve',data=form,headers={'origin':'https://dev.example.invalid'})
    assert approved.status_code==200,approved.text
    token=c.client.post('/api/connector/pairings/token',json=poll)
    assert token.status_code==200,token.text
    assert token.json()['account_id']==str(c.user.id)
    assert c.client.post('/api/connector/pairings/token',json=poll).json()==token.json()
    assert c.client.delete('/api/connector/session',headers={'authorization':'Bearer '+token.json()['access_token']}).status_code==200
    assert c.client.post('/api/connector/pairings/token',json=poll).status_code==401


def test_limits_profile_and_dev_gate(context,raw,monkeypatch):
    c=context
    assert post(c,b'not html').status_code==422
    assert post(c,b'x'*(service.MAX_HTML+1)).status_code==413
    assert post(c,raw,profile='unvalidated').status_code==422
    monkeypatch.setenv('APP_ENV','production')
    assert post(c,raw).status_code==404
    monkeypatch.setenv('APP_ENV','test')
    assert c.client.get('http://dev.example.invalid/api/incoming-assessments').status_code==400

def install_confirmation_business(c,monkeypatch):
    """Execute the actual import route body with real patient/case/storage services.

    Only unrelated report/PDF/subscription machinery is replaced. This keeps
    the test independent of live storage, the full app and document rendering.
    """
    import ast
    from datetime import date
    from fastapi import Depends
    from app.schemas import cases as schemas
    from app.schemas.patients import PatientCreate
    from app.services.patient_service import create_patient
    from app.services.case_service import create_case
    import importlib
    scan=importlib.import_module('app.services.scan_import_service')
    from app.db.models import CaseStatus
    namespace=dict(vars(schemas))
    namespace.update(UUID=uuid.UUID,Session=Session,User=User,Patient=Patient,Case=Case,
        ReportVersion=ReportVersion,CaseStatus=CaseStatus,PatientCreate=PatientCreate,
        Depends=lambda *a,**k:None,get_db=lambda:None,get_current_user=lambda:None,
        create_patient=create_patient,create_case=create_case,HTTPException=HTTPException,
        require_subscription_feature=Mock(),log_action=Mock(),date=date,datetime=datetime)
    namespace.update({name:getattr(scan,name) for name in ['load_temp_html','save_case_html','parse_qrma_html','DuplicateScanError']})
    source=Path(__file__).parents[1]/'app/api/routes_cases.py'
    names={'create_from_import','_normalise_sex','_enum_value','_get_owned_case','_get_owned_patient','make_json_safe'}
    tree=ast.parse(source.read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:node.decorator_list=[]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(source),'exec'),namespace)
    module=types.ModuleType('app.api.routes_cases');module.create_from_import=namespace['create_from_import']
    monkeypatch.setitem(sys.modules,'app.api.routes_cases',module)
    from app.db.models import ReportStatus,RecommendationMode
    report_module=types.ModuleType('app.services.report_service')
    async def generate(db,case,user,job_id=None):
        report=ReportVersion(case_id=case.id,created_by_user_id=user.id,version_number=1,
           status=ReportStatus.ready,job_id=job_id,
           recommendation_mode=RecommendationMode.natural_approaches_clinical)
        db.add(report);db.commit();return report
    report_module.generate_report_version=generate
    monkeypatch.setitem(sys.modules,'app.services.report_service',report_module)
    return report_module


def test_confirmation_creates_case_once_and_report_only_on_action(context,raw,monkeypatch):
    c=context;install_confirmation_business(c,monkeypatch)
    incoming=post(c,raw).json()
    payload=dict(temporary_upload_id='ignored-client-value',patient=dict(first_name='Jane',last_name='Bloggs'),
                 case=dict(title='DEV test'),generate_report=False)
    path='/api/incoming-assessments/'+incoming['assessment_id']+'/confirm'
    assert c.client.post(path,json=payload).status_code==403
    headers={'origin':'https://dev.example.invalid'}
    created=c.client.post(path,json=payload,headers=headers)
    assert created.status_code==200,created.text
    assert created.json()['case_id']
    assert c.db.query(Patient).count()==1 and c.db.query(Case).count()==1
    assert c.db.query(ReportVersion).count()==0
    retried=c.client.post(path,json=payload,headers=headers)
    assert retried.json()['case_id']==created.json()['case_id']
    assert c.db.query(Patient).count()==1 and c.db.query(Case).count()==1
    generated=c.client.post(path,json={**payload,'generate_report':True},headers=headers)
    assert generated.status_code==200,generated.text
    assert generated.json()['report_version_id']
    assert c.db.query(ReportVersion).count()==1
    assert c.client.post(path,json={**payload,'generate_report':True},headers=headers).json()['report_version_id']==generated.json()['report_version_id']
    assert c.db.query(ReportVersion).count()==1


def test_duplicate_confirmation_rolls_back_and_override_links_existing(context,raw,monkeypatch):
    c=context;install_confirmation_business(c,monkeypatch)
    a=post(c,raw).json();b=post(c,raw).json()
    payload=dict(temporary_upload_id='ignored',patient=dict(first_name='Jane',last_name='Bloggs'),case=dict(title='DEV'),generate_report=False)
    headers={'origin':'https://dev.example.invalid'}
    path='/api/incoming-assessments/'+b['assessment_id']+'/confirm'
    assert c.client.post(path,json=payload,headers=headers).status_code==409
    assert c.db.query(Patient).count()==0 and c.db.query(Case).count()==0
    existing=Patient(user_id=c.user.id,first_name='Jane',last_name='Bloggs',full_name='Jane Bloggs')
    c.db.add(existing);c.db.commit()
    accepted=c.client.post(path,json={**payload,'existing_patient_id':str(existing.id),'allow_duplicate':True},headers=headers)
    assert accepted.status_code==200,accepted.text
    assert accepted.json()['patient_id']==str(existing.id)
    assert c.db.query(Patient).count()==1 and c.db.query(Case).count()==1

def test_cross_format_hold_and_connector_owner_scope(context,raw):
    from bs4 import BeautifulSoup
    c=context
    first=post(c,raw).json()
    exported=str(BeautifulSoup(raw.decode('cp1252'),'lxml')).encode()
    assert exported!=raw
    duplicate=post(c,exported).json()
    assert duplicate['status']=='needs_attention'
    assert duplicate['duplicate']['match_type']=='assessment'
    second=ConnectorDevice(id=uuid.uuid4(),owner_id=c.other.id,credential_hash=c.routes.sha('other-token'),
                           expires_at=datetime(2099,1,1,tzinfo=timezone.utc))
    c.db.add(second);c.db.commit()
    assert c.client.get('/api/connector/jobs/'+first['job_id'],headers={'authorization':'Bearer other-token'}).status_code==404
    assert post(c,raw,token='other-token').json()['status']=='awaiting_practitioner'


def test_failed_report_is_associated_and_confirmation_retry_does_not_duplicate(context,raw,monkeypatch):
    from app.db.models import ReportStatus,RecommendationMode
    c=context;report_module=install_confirmation_business(c,monkeypatch)
    async def fail(db,case,user,job_id=None):
        report=ReportVersion(case_id=case.id,created_by_user_id=user.id,version_number=1,
            status=ReportStatus.failed,job_id=job_id,recommendation_mode=RecommendationMode.natural_approaches_clinical)
        db.add(report);db.commit();raise RuntimeError('synthetic generation failure')
    report_module.generate_report_version=fail
    incoming=post(c,raw).json()
    path='/api/incoming-assessments/'+incoming['assessment_id']+'/confirm'
    payload=dict(patient=dict(first_name='Jane',last_name='Bloggs'),case=dict(title='DEV'),generate_report=True)
    headers={'origin':'https://dev.example.invalid'}
    first=c.client.post(path,json=payload,headers=headers)
    assert first.status_code==200,first.text
    assert first.json()['status']=='needs_attention' and first.json()['report_version_id']
    again=c.client.post(path,json=payload,headers=headers)
    assert again.json()['report_version_id']==first.json()['report_version_id']
    assert c.db.query(Patient).count()==1 and c.db.query(Case).count()==1 and c.db.query(ReportVersion).count()==1

def test_sensitive_error_and_access_log_redaction(context,raw,monkeypatch,caplog):
    import logging
    from app.services.connector_privacy import ConnectorAccessFilter
    c=context
    def fail(*args,**kwargs):raise RuntimeError('SYNTHETIC_PATIENT_SECRET')
    monkeypatch.setattr(service,'ingest',fail)
    with caplog.at_level(logging.WARNING):
        response=post(c,raw)
    assert response.status_code==503
    assert 'SYNTHETIC_PATIENT_SECRET' not in response.text+caplog.text
    record=logging.LogRecord('uvicorn.access',20,'',0,'%s - "%s %s HTTP/%s" %d',
              ('client','GET','/api/connector/jobs/private-job?secret=value','1.1',200),None)
    ConnectorAccessFilter().filter(record)
    assert 'private-job' not in record.getMessage() and 'secret=value' not in record.getMessage()
