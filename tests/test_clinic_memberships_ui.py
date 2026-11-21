"""Actual membership templates/API with authenticated synthetic PostgreSQL users."""
import socket
import threading
import time
import json
from pathlib import Path
import pytest
import uvicorn
from playwright.sync_api import expect
from test_admin_entitlements import auth_engine, auth_app
from test_clinic_memberships import membership_app, add, change
from test_clinic_registry_ui import browser


@pytest.fixture
def member_page(membership_app,browser):
    client,login,engine,ids,a,b=membership_app
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    url=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(client.app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(500):
        if server.started:break
        time.sleep(.01)
    assert server.started
    context=browser.new_context(base_url=url,viewport={'width':390,'height':844})
    context.add_cookies([{'name':c.name,'value':c.value,'url':url} for c in client.cookies.jar])
    page=context.new_page();page.goto(f'/app/clinics/{a}/members')
    expect(page.locator('#cm-add')).to_be_enabled()
    try:yield page,client,login,engine,ids,a,b,url
    finally:
        context.close();server.should_exit=True;thread.join(5);sock.close()


def create_from_ui(page,principal):
    page.get_by_label('Practitioner ID',exact=True).fill(str(principal))
    page.locator('#cm-add').click();expect(page.locator('#cm-confirm')).to_be_visible()
    page.locator('#cm-submit').click();expect(page.locator('#cm-confirm')).not_to_be_visible()
    expect(page.locator('#cm-list')).to_contain_text('Membership: active')


def test_add_suspend_reactivate_mobile_and_keyboard(member_page):
    page,_,_,_,ids,_,_,_=member_page
    page.locator('#cm-practitioner').fill('invalid');page.locator('#cm-add').click()
    expect(page.locator('#cm-error')).to_be_focused()
    create_from_ui(page,ids['other'])
    page.get_by_role('button',name='Suspend',exact=True).click()
    expect(page.locator('#cm-confirm-description')).to_contain_text('Clinic A')
    expect(page.locator('#cm-submit')).to_be_focused()
    page.keyboard.press('Escape');expect(page.locator('#cm-confirm')).not_to_be_visible()
    expect(page.get_by_role('button',name='Suspend',exact=True)).to_be_focused()
    page.get_by_role('button',name='Suspend',exact=True).click();page.keyboard.press('Enter')
    expect(page.locator('#cm-list')).to_contain_text('Membership: suspended')
    expect(page.locator('#cm-reload')).to_be_focused()
    page.get_by_role('button',name='Reactivate',exact=True).click();page.locator('#cm-submit').click()
    expect(page.locator('#cm-list')).to_contain_text('Membership: active')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    Path('artifacts').mkdir(exist_ok=True)
    page.screenshot(path='artifacts/clinic-memberships-mobile.png',full_page=True)


def test_uncertain_post_retry_keeps_creation_key(member_page):
    page,_,_,_,ids,_,_,_=member_page
    keys=[];first=True
    def intercept(route):
        nonlocal first
        if route.request.method!='POST':route.continue_();return
        keys.append(route.request.headers['idempotency-key'])
        if first:
            first=False
            route.fetch()  # Commit succeeded, but the response is lost.
            route.abort()
        else:route.continue_()
    page.route('**/memberships',intercept)
    page.locator('#cm-practitioner').fill(str(ids['other']));page.locator('#cm-add').click();page.locator('#cm-submit').click()
    expect(page.locator('#cm-confirm-error')).to_be_visible()
    page.locator('#cm-submit').click()
    expect(page.locator('#cm-list li')).to_have_count(1)
    expect(page.locator('#cm-list')).to_contain_text('Membership: active')
    assert len(keys)==2 and keys[0]==keys[1]


def test_conflict_recovery_and_actor_change_refusal(member_page):
    page,client,login,_,ids,a,_,url=member_page
    row=add(client,ids,a)
    page.locator('#cm-reload').click();expect(page.locator('#cm-list')).to_contain_text('Synthetic other')
    page.get_by_role('button',name='Suspend',exact=True).click()
    assert change(client,ids,a,row,'revoked').status_code==200
    page.locator('#cm-submit').click();expect(page.locator('#cm-confirm-error')).to_contain_text('Reload')
    expect(page.locator('#cm-submit')).to_be_disabled()
    page.locator('#cm-cancel').click();page.locator('#cm-reload').click()
    expect(page.locator('#cm-list')).to_contain_text('Membership: revoked')
    page.set_viewport_size({'width':1440,'height':1000})
    page.get_by_role('button',name='Reactivate',exact=True).click()
    login('admin')
    page.context.clear_cookies();page.context.add_cookies([{'name':c.name,'value':c.value,'url':url} for c in client.cookies.jar])
    page.locator('#cm-submit').click();expect(page.locator('#cm-confirm-error')).to_contain_text('account changed')
    expect(page.locator('#cm-submit')).to_be_disabled()


def test_read_only_own_and_support_views(member_page):
    page,client,login,_,ids,a,_,url=member_page
    add(client,ids,a)
    login('other');page.context.clear_cookies();page.context.add_cookies([{'name':c.name,'value':c.value,'url':url} for c in client.cookies.jar])
    page.goto('/app/memberships');expect(page.locator('#cm-list')).to_contain_text('Clinic A')
    assert page.locator('[data-change],#cm-add').count()==0
    login('admin');assert client.post('/auth/impersonate/'+str(ids['owner'])).status_code==200
    page.context.clear_cookies();page.context.add_cookies([{'name':c.name,'value':c.value,'url':url} for c in client.cookies.jar])
    page.goto(f'/app/clinics/{a}/members');expect(page.locator('#cm-support')).to_be_visible()
    expect(page.locator('#cm-add')).to_be_disabled()
    assert page.locator('[data-change]').count()==0
