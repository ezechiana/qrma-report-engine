"""Real report template and entitlement UI, with synthetic PostgreSQL accounts."""
import json
import os
from pathlib import Path
import socket
import threading
import time
from uuid import uuid4

import pytest
import uvicorn
from playwright.sync_api import expect
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from app.db.models import Case,ReportVersion,Patient
from test_admin_entitlements import auth_engine,auth_app
from test_clinic_registry_ui import browser


@pytest.fixture
def workspace(auth_app,browser):
    client,login,engine,ids,report=auth_app
    # The admin uses an explicitly owned synthetic chart; admin is not a chart bypass.
    with Session(engine) as db:
        item=db.get(ReportVersion,report);case=db.get(Case,item.case_id)
        case.user_id=ids['admin'];item.created_by_user_id=ids['admin'];db.get(Patient,case.patient_id).user_id=ids['admin'];db.commit()
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    url=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(client.app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(500):
        if server.started:break
        time.sleep(.01)
    assert server.started
    context=browser.new_context(base_url=url,viewport={'width':1440,'height':1000})
    login('admin')
    context.add_cookies([{'name':c.name,'value':c.value,'url':url} for c in client.cookies.jar])
    assert context.request.get('/auth/me').status==200
    page=context.new_page();page.goto('/app/reports/'+str(report))
    try:expect(page.locator('#share-price')).to_be_visible()
    except AssertionError:
        print('Workspace URL:',page.url,'body:',page.locator('body').inner_text()[:700])
        raise
    expect(page.locator('#create-report-version-btn')).to_be_enabled()
    try:yield page,client,engine,ids,report
    finally:
        context.close();server.should_exit=True;thread.join(5);sock.close()


def test_admin_price_creates_real_paid_link_and_validates_decimals(workspace):
    page,client,_,_,_=workspace
    page.locator('#share-price').fill('12.345')
    page.locator('#create-share-link-btn').click()
    expect(page.locator('#share-feedback')).to_contain_text('two decimal')
    page.locator('#share-price').fill('12.34')
    with page.expect_response('**/share-links') as info:
        page.locator('#create-share-link-btn').click()
    response=info.value
    assert response.status==200
    payload=json.loads(response.request.post_data)
    assert payload['price_amount']==1234 and payload['price_currency']=='gbp'
    expect(page.locator('#share-result')).to_contain_text('Share link ready')
    if os.getenv('GO360_UAT_EVIDENCE'):
        output=Path(os.environ['GO360_UAT_EVIDENCE']);output.mkdir(parents=True,exist_ok=True)
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(output/'report-controls-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_function("document.getElementById('app-sidebar').getBoundingClientRect().right<=0")
        page.screenshot(path=str(output/'report-controls-mobile.png'))


def test_new_version_retry_preserves_intent_and_navigates_to_new_report(workspace):
    page,_,_,_,_=workspace
    sent=[];new=str(uuid4())
    def generate(route):
        sent.append(route.request.headers.get('idempotency-key'))
        if len(sent)==1:route.fulfill(status=503,content_type='application/json',body='{}')
        else:route.fulfill(status=200,content_type='application/json',body=json.dumps({'report_version_id':new,'version_number':2}))
    page.route('**/generate-report',generate)
    page.locator('#create-report-version-btn').click()
    expect(page.locator('#new-version-status')).to_contain_text('same request')
    page.locator('#create-report-version-btn').click()
    page.wait_for_url('**/app/reports/'+new)
    assert len(sent)==2 and sent[0] and sent[0]==sent[1]


def test_target_user_permissions_disable_controls_during_impersonation(workspace):
    page,client,engine,ids,report=workspace
    with Session(engine) as db:
        item=db.get(ReportVersion,report);db.get(Case,item.case_id).user_id=ids['owner'];db.commit()
    response=page.request.post('/auth/impersonate/'+str(ids['owner']))
    assert response.status==200
    page.reload()
    expect(page.locator('#share-price')).not_to_be_visible()
    expect(page.locator('#create-report-version-btn')).to_be_disabled()
    expect(page.locator('#create-share-link-btn')).to_be_disabled()


def test_client_route_browser_loads_records_and_account(workspace):
    page,_,_,ids,_=workspace
    assert page.request.post('/auth/impersonate/'+str(ids['client'])).status==200
    page.goto('/client')
    expect(page.locator('#client-card h1')).to_have_text('Today')
    page.get_by_role('link',name='Account',exact=True).click()
    expect(page.locator('#account-name')).to_have_value('Synthetic client')
