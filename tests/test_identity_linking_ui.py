"""Actual sign-in methods UI; trusted provider proof is synthetic on loopback."""
import socket,threading,time
from urllib.parse import urlsplit,parse_qs
from pathlib import Path
import pytest,uvicorn
from playwright.sync_api import expect
from test_admin_entitlements import auth_engine,auth_app
from test_identity_linking import link_app,PASSWORD
from test_clinic_registry_ui import browser


@pytest.fixture
def linking_page(link_app,browser,monkeypatch):
    client,login,engine,ids,profile,_=link_app
    sock=socket.socket();sock.bind(('127.0.0.1',0));url=f'http://127.0.0.1:{sock.getsockname()[1]}'
    monkeypatch.setenv('BASE_URL',url);monkeypatch.setenv('APP_ENV','development')
    server=uvicorn.Server(uvicorn.Config(client.app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(500):
        if server.started:break
        time.sleep(.01)
    assert server.started
    context=browser.new_context(base_url=url,viewport={'width':390,'height':844})
    login('admin');context.add_cookies([{'name':c.name,'value':c.value,'url':url} for c in client.cookies.jar])
    page=context.new_page()
    # Redirect only the external authorization navigation to the real callback.
    # The callback and confirmation still exercise the real app/DB/session checks.
    def provider(route):
        query=parse_qs(urlsplit(route.request.url).query)
        route.fulfill(status=302,headers={'Location':url+'/auth/social/google/callback?code=synthetic&state='+query['state'][0]},body='')
    page.route('https://accounts.google.com/**',provider)
    page.goto('/account/sign-in-methods');expect(page.get_by_role('button',name='Link Google',exact=True)).to_be_enabled()
    try:yield page,profile,ids
    finally:
        context.close();server.should_exit=True;thread.join(5);sock.close()


def test_confirm_link_password_error_keyboard_and_mobile(linking_page):
    page,profile,ids=linking_page
    page.get_by_role('button',name='Link Google',exact=True).click()
    password=page.get_by_label('Current GO360 password',exact=True)
    expect(password).to_be_visible();expect(page.locator('[data-provider="google"]')).to_contain_text(profile['email'])
    password.fill('WrongPassword2!');page.get_by_role('button',name='Confirm Google link').click()
    expect(page.locator('#sim-error')).to_be_focused();expect(password).to_have_value('')
    password.fill(PASSWORD);password.press('Enter')
    expect(page.locator('[data-provider="google"]')).to_contain_text('Linked: '+profile['email'])
    expect(page.locator('#sim-status')).to_contain_text('permissions are unchanged')
    assert page.request.get('/auth/me').json()['user']['id']==str(ids['admin'])
    assert page.request.get('/api/subscription/status').json()['tier']=='internal_admin'
    page.reload();expect(page.locator('[data-provider="google"]')).to_contain_text('Linked: '+profile['email'])
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    Path('artifacts').mkdir(exist_ok=True)
    page.screenshot(path='artifacts/sign-in-methods-mobile.png',full_page=True,animations='disabled')


def test_failed_callback_and_disabled_provider_visible(linking_page,monkeypatch):
    page,_,_=linking_page
    monkeypatch.delenv('MICROSOFT_CLIENT_SECRET')
    page.reload();expect(page.get_by_role('button',name='Link Microsoft')).to_be_disabled()
    page.goto('/account/sign-in-methods?link_error=cancelled')
    expect(page.locator('#sim-error')).to_contain_text('cancelled')
    page.set_viewport_size({'width':1440,'height':1000})
    page.get_by_role('button',name='Link Google',exact=True).focus()
    expect(page.get_by_role('button',name='Link Google',exact=True)).to_be_focused()
