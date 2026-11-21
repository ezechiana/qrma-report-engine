"""Actual profile UI/API with real synthetic authentication."""
import socket
import threading
import time
import pytest
import uvicorn
from playwright.sync_api import expect
from test_admin_entitlements import auth_engine, auth_app
from test_practitioner_identity import profile_app
from test_clinic_registry_ui import browser


@pytest.fixture
def profile_page(profile_app,browser):
    client,login,engine,ids,_=profile_app
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    url=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(client.app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(500):
        if server.started:break
        time.sleep(.01)
    assert server.started
    context=browser.new_context(base_url=url,viewport={'width':390,'height':844})
    login('owner')
    context.add_cookies([{'name':c.name,'value':c.value,'url':url} for c in client.cookies.jar])
    page=context.new_page();page.goto('/app/profile')
    expect(page.locator('#profile-name')).to_be_enabled()
    try:yield page,client,login
    finally:
        context.close();server.should_exit=True;thread.join(5);sock.close()


def test_profile_save_cancel_and_narrow_layout(profile_page):
    page,_,_=profile_page
    expect(page.get_by_role('button',name='Save profile',exact=True)).to_be_disabled()
    expect(page.locator('#profile-reload')).to_be_hidden()
    name=page.get_by_label('Full name',exact=True)
    original=name.input_value()
    name.fill('Cancel Me');page.get_by_role('button',name='Cancel changes').click()
    expect(name).to_have_value(original)
    name.fill('<script>synthetic</script>')
    page.get_by_role('button',name='Save profile',exact=True).click()
    expect(page.locator('#profile-status')).to_have_text('Profile saved.')
    page.reload();expect(name).to_have_value('<script>synthetic</script>')
    assert page.locator('script').filter(has_text='synthetic').count()==0
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    from pathlib import Path
    Path('artifacts').mkdir(exist_ok=True)
    page.screenshot(path='artifacts/practitioner-profile-mobile.png',full_page=True)


def test_validation_conflict_and_network_retry(profile_page):
    page,client,_=profile_page
    page.get_by_label('Timezone',exact=True).fill('Wrong/Zone')
    page.locator('#profile-save').click()
    expect(page.locator('#profile-error')).to_be_focused()
    expect(page.locator('#profile-zone')).to_have_attribute('aria-invalid','true')
    page.get_by_label('Timezone',exact=True).fill('UTC')
    initial=client.get('/api/practitioner/profile/me').json()
    client.patch('/api/practitioner/profile/me',json=dict(revision=initial['revision'],full_name='Other Tab',phone=None,timezone='UTC'))
    page.locator('#profile-save').click()
    expect(page.locator('#profile-error')).to_contain_text('Reload')
    expect(page.locator('#profile-save')).to_be_disabled()
    page.locator('#profile-reload').click();expect(page.locator('#profile-name')).to_have_value('Other Tab')
    page.route('**/api/practitioner/profile/me',lambda route:route.abort() if route.request.method=='PATCH' else route.continue_())
    page.locator('#profile-name').fill('Retain Input');page.locator('#profile-save').click()
    expect(page.locator('#profile-error')).to_be_visible();expect(page.locator('#profile-name')).to_have_value('Retain Input')
    page.unroute('**/api/practitioner/profile/me');page.locator('#profile-save').click()
    expect(page.locator('#profile-status')).to_have_text('Profile saved.')


def test_support_profile_controls_disabled(profile_page):
    page,client,login=profile_page
    target=client.get('/api/practitioner/profile/me').json()['id']
    login('admin');assert client.post('/auth/impersonate/'+target).status_code==200
    page.context.clear_cookies()
    page.evaluate('localStorage.clear()')
    page.context.add_cookies([{'name':c.name,'value':c.value,'url':page.url.split('/app/')[0]} for c in client.cookies.jar])
    page.reload();expect(page.locator('#profile-support')).to_be_visible()
    expect(page.locator('#profile-name')).to_be_disabled()


def test_desktop_keyboard_save_and_read_only_identifiers(profile_page):
    page,_,_=profile_page
    page.set_viewport_size({'width':1440,'height':1000})
    name=page.get_by_label('Full name',exact=True)
    name.fill('Keyboard Person');name.focus()
    page.keyboard.press('Tab');expect(page.locator('#profile-phone')).to_be_focused()
    page.keyboard.press('Tab');expect(page.locator('#profile-zone')).to_be_focused()
    page.keyboard.press('Tab');expect(page.locator('#profile-save')).to_be_focused()
    page.keyboard.press('Enter');expect(page.locator('#profile-status')).to_have_text('Profile saved.')
    assert page.locator('input[name="id"],input[name="email"]').count()==0
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
