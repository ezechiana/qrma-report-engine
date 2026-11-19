"""GO3-274 actual templates/scripts and registry API, isolated synthetic server."""
import os
import socket
import threading
import time
from pathlib import Path
from uuid import uuid4

os.environ.setdefault('DATABASE_URL', 'postgresql://localhost/synthetic_clinic_ui')

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright
from clinic_ui_app import make_app, OWNER, ORG_A, ORG_B, ORG_C

JOURNAL = 'go360.registry.intent.v1:' + str(OWNER)


@pytest.fixture(scope='module')
def ui_server(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    app, engine = make_app(tmp_path_factory.mktemp('clinic-ui') / 'synthetic.sqlite', patch)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(.01)
    assert server.started
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        engine.dispose()
        patch.undo()


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox'], executable_path=os.getenv('GO360_DESIGN_CHROMIUM') or None)
        yield browser
        browser.close()


@pytest.fixture
def page(browser, ui_server):
    context = browser.new_context(viewport={'width':1440,'height':1000}, reduced_motion='reduce')
    context.add_cookies([{'name':'access_token','value':'owner','url':ui_server}])
    page = context.new_page()
    page.request.post(ui_server+'/__test/control', data={'reset':True})
    page.goto(ui_server+f'/app/organisations?organisation={ORG_A}')
    expect(page.locator('#cr-create-clinic')).to_be_enabled()
    yield page
    context.close()


def metrics(page, url):
    return page.request.get(url+'/__test/metrics').json()


def create(page, name):
    page.locator('#cr-create-clinic').click()
    page.locator('#cr-name').fill(name)
    page.locator('#cr-save').click()


def test_real_ui_duplicate_names_validation_keyboard_and_mobile(page, ui_server, tmp_path):
    errors=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    expect(page.locator('.cr-clinic')).to_have_count(2)
    original_ids=page.locator('.cr-clinic h3').evaluate_all('items=>items.map(el=>el.id)')
    assert len(set(page.locator('.cr-clinic .cr-ref').all_text_contents()))==2
    page.locator('#cr-create-clinic').click()
    expect(page.locator('#cr-name')).to_be_focused()
    for _ in range(12):
        page.keyboard.press('Tab')
        assert page.evaluate("document.getElementById('cr-dialog').contains(document.activeElement)")
    page.keyboard.press('Escape')
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    expect(page.locator('#cr-create-clinic')).to_be_focused()
    create(page, '   ')
    expect(page.locator('#cr-field-error')).to_be_visible()
    assert not metrics(page,ui_server)['requests']
    page.locator('#cr-name').fill('Harbour Clinic')
    page.locator('#cr-save').click()
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    expect(page.locator('.cr-clinic')).to_have_count(3)
    page.wait_for_function("previous=>document.activeElement.matches('.cr-clinic h3')&&!previous.includes(document.activeElement.id)",arg=original_ids)
    create(page,'Harbour Clinic')
    expect(page.locator('.cr-clinic')).to_have_count(4)
    requests=metrics(page,ui_server)['requests']
    assert requests[0]['key']!=requests[1]['key']
    for width in (390,320):
        page.set_viewport_size({'width':width,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#cr-create-clinic').click()
        assert page.locator('#cr-dialog').evaluate('el => el.scrollWidth <= el.clientWidth')
        page.keyboard.press('Escape')
    assert not errors


@pytest.mark.parametrize('failure',['before','after','wrong-parent'])
def test_real_api_unknown_outcome_reload_retry_creates_once(page, ui_server, failure):
    page.request.post(ui_server+'/__test/control',data={'failure':failure})
    create(page,'Recoverable clinic')
    expect(page.locator('#cr-save-error')).to_contain_text('couldn’t confirm')
    old=page.evaluate('key => JSON.parse(sessionStorage.getItem(key))',JOURNAL)
    assert old['state']=='unknown'
    assert page.locator('#cr-name').evaluate('el=>el.readOnly')
    page.reload()
    expect(page.locator('#cr-resume')).to_be_visible()
    page.locator('#cr-resume').click()
    page.locator('#cr-save').click()
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    expect(page.locator('.cr-clinic')).to_have_count(3)
    data=metrics(page,ui_server)
    assert len(data['requests'])==2 and data['requests'][0]==data['requests'][1]
    assert data['clinics']==3
    assert page.evaluate('key=>sessionStorage.getItem(key)',JOURNAL) is None


def test_cookie_actor_change_during_submit_clears_payload_without_post(page, ui_server):
    page.request.post(ui_server+'/__test/control',data={'failure':'after'})
    create(page,'Private previous-actor name')
    expect(page.locator('#cr-save-error')).to_be_visible()
    page.context.add_cookies([{'name':'access_token','value':'capable','url':ui_server}])
    page.locator('#cr-save').click()
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    expect(page.locator('#cr-content')).not_to_contain_text('Private previous-actor name')
    assert len(metrics(page,ui_server)['requests'])==1
    assert page.evaluate('key=>sessionStorage.getItem(key)',JOURNAL) is None
    page.reload()
    expect(page.locator('#cr-organisation')).to_contain_text('Capable practice')
    expect(page.locator('.cr-clinic')).to_have_count(0)


def test_storage_write_failure_blocks_post(page, ui_server):
    page.evaluate("""() => { const original=Storage.prototype.setItem; Storage.prototype.setItem=function(k,v){if(k.startsWith('go360.registry'))throw new DOMException('Synthetic quota','QuotaExceededError');return original.call(this,k,v);}; }""")
    create(page,'Not sent')
    expect(page.locator('#cr-save-error')).to_contain_text('storage')
    expect(page.locator('#cr-save')).to_be_disabled()
    assert not metrics(page,ui_server)['requests']


def test_confirmed_save_cleanup_failure_does_not_repeat_post(page, ui_server):
    page.evaluate("""key => { const original=Storage.prototype.removeItem; window.blockCleanup=true; Storage.prototype.removeItem=function(k){if(window.blockCleanup&&k===key)throw new Error('Synthetic cleanup failure');return original.call(this,k);}; }""",JOURNAL)
    create(page,'Confirmed despite cleanup')
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    expect(page.locator('.cr-clinic')).to_have_count(3)
    expect(page.locator('#cr-cleanup')).to_be_visible()
    expect(page.locator('#cr-create-clinic')).to_be_disabled()
    page.evaluate('window.blockCleanup=false')
    page.locator('#cr-cleanup').click()
    expect(page.locator('#cr-create-clinic')).to_be_enabled()
    assert len(metrics(page,ui_server)['requests'])==1


def test_validation_correction_and_real_conflict_require_explicit_new_intent(page, ui_server):
    page.request.post(ui_server+'/__test/control',data={'failure':'validation'})
    create(page,'Correctable name')
    expect(page.locator('#cr-field-error')).to_be_visible()
    expect(page.locator('#cr-name')).to_be_editable()
    page.locator('#cr-cancel').click()
    key=str(uuid4())
    response=page.request.post(ui_server+f'/api/organisations/{ORG_A}/clinics',data={'display_name':'Original'},headers={'Idempotency-Key':key})
    assert response.status==201
    page.evaluate("""([storage,key,actor,parent])=>sessionStorage.setItem(storage,JSON.stringify({version:1,actor_id:actor,kind:'clinic',organisation_id:parent,key,payload:{display_name:'Different'},state:'unknown',created_at:new Date().toISOString()}))""",[JOURNAL,key,str(OWNER),str(ORG_A)])
    page.reload()
    expect(page.locator('#cr-resume')).to_be_visible()
    page.locator('#cr-resume').click()
    page.locator('#cr-save').click()
    expect(page.locator('#cr-save-error')).to_contain_text('conflicts')
    expect(page.locator('#cr-save')).to_be_disabled()
    page.locator('#cr-cancel').click()
    page.locator('#cr-separate').click()
    create(page,'Separate')
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    assert metrics(page,ui_server)['requests'][-1]['key']!=key


def test_copied_tab_retry_and_failed_logout_clear_other_tab_journal(page, ui_server):
    page.request.post(ui_server+'/__test/control',data={'failure':'after'})
    create(page,'Copied pending name')
    expect(page.locator('#cr-save-error')).to_be_visible()
    with page.expect_popup() as popup_info:
        page.evaluate('url=>window.open(url,"_blank")',ui_server+f'/app/organisations?organisation={ORG_A}')
    popup=popup_info.value
    expect(popup.locator('#cr-resume')).to_be_visible()
    assert popup.evaluate('key=>sessionStorage.getItem(key)',JOURNAL)==page.evaluate('key=>sessionStorage.getItem(key)',JOURNAL)
    page.request.post(ui_server+'/__test/control',data={'logout_failure':True})
    page.evaluate('logoutNow()')
    page.wait_for_url('**/login')
    expect(popup.locator('#cr-content')).to_be_empty()
    assert popup.evaluate('key=>sessionStorage.getItem(key)',JOURNAL) is None
    assert len(metrics(page,ui_server)['requests'])==1


def test_late_get_cannot_display_old_clinics_under_new_organisation(page, ui_server):
    page.request.post(ui_server+'/__test/control',data={'slow_parent':str(ORG_A),'delay':1})
    page.locator('#cr-organisation').select_option(str(ORG_B))
    expect(page.locator('#cr-create-clinic')).to_be_enabled()
    page.locator('#cr-organisation').select_option(str(ORG_A))
    page.locator('#cr-organisation').select_option(str(ORG_B))
    expect(page.locator('#cr-content')).to_contain_text('No clinics in this organisation')
    page.wait_for_timeout(1200)
    expect(page.locator('.cr-clinic')).to_have_count(0)
    expect(page.locator('#cr-organisation')).to_have_value(str(ORG_B))


def test_corrupt_journal_and_lost_key_never_auto_resubmit(page, ui_server):
    page.evaluate('key=>sessionStorage.setItem(key,"not-json")',JOURNAL)
    page.reload()
    expect(page.locator('#cr-separate')).to_be_visible()
    expect(page.locator('#cr-create-clinic')).to_be_disabled()
    assert not metrics(page,ui_server)['requests']
    page.locator('#cr-separate').click()
    expect(page.locator('#cr-create-clinic')).to_be_enabled()


def test_capable_client_uses_actual_page_and_revocation_prevents_creation(page, ui_server):
    page.context.add_cookies([{'name':'access_token','value':'capable','url':ui_server}])
    page.goto(ui_server+f'/app/organisations?organisation={ORG_C}')
    expect(page.locator('#cr-create-clinic')).to_be_enabled()
    page.locator('#cr-create-clinic').click()
    page.locator('#cr-name').fill('Will be denied')
    page.request.post(ui_server+'/__test/control',data={'revoke':'capable'})
    page.locator('#cr-save').click()
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    expect(page.locator('#cr-message')).to_contain_text('no longer have access')
    assert not metrics(page,ui_server)['requests']


def test_expired_session_recovers_same_intent_after_same_actor_sign_in(page, ui_server):
    page.request.post(ui_server+'/__test/control',data={'failure':'after'})
    create(page,'Recover after sign in')
    expect(page.locator('#cr-save-error')).to_be_visible()
    original = page.evaluate('key=>sessionStorage.getItem(key)',JOURNAL)
    page.context.clear_cookies()
    page.locator('#cr-save').click()
    page.wait_for_url('**/login?next=*')
    assert page.evaluate('key=>sessionStorage.getItem(key)',JOURNAL)==original
    page.context.add_cookies([{'name':'access_token','value':'owner','url':ui_server}])
    page.goto(ui_server+f'/app/organisations?organisation={ORG_A}')
    page.locator('#cr-resume').click()
    page.locator('#cr-save').click()
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    assert metrics(page,ui_server)['clinics']==3
    requests=metrics(page,ui_server)['requests']
    assert len(requests)==2 and requests[0]==requests[1]


def test_copied_tab_retry_keeps_original_key(page, ui_server):
    page.request.post(ui_server+'/__test/control',data={'failure':'after'})
    create(page,'Copied recovery')
    expect(page.locator('#cr-save-error')).to_be_visible()
    with page.expect_popup() as info:
        page.evaluate('url=>window.open(url,"_blank")',ui_server+f'/app/organisations?organisation={ORG_A}')
    popup=info.value
    popup.locator('#cr-resume').click()
    popup.locator('#cr-save').click()
    expect(popup.locator('#cr-dialog')).not_to_be_visible()
    popup.close()
    page.bring_to_front()
    page.locator('#cr-save').click()
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    data=metrics(page,ui_server)
    assert data['clinics']==3
    assert len(data['requests'])==3 and all(r==data['requests'][0] for r in data['requests'])


def test_pending_resume_waits_for_clinic_list_verification(page, ui_server):
    page.request.post(ui_server+'/__test/control',data={'failure':'after'})
    create(page,'Delayed recovery')
    expect(page.locator('#cr-save-error')).to_be_visible()
    page.request.post(ui_server+'/__test/control',data={'slow_parent':str(ORG_A),'delay':2})
    page.reload()
    expect(page.locator('#cr-resume')).to_be_visible()
    expect(page.locator('#cr-resume')).to_be_disabled()
    expect(page.locator('#cr-resume')).to_be_enabled()
    page.locator('#cr-resume').click()
    expect(page.locator('#cr-dialog')).to_be_visible()


def test_organisation_creation_renders_untrusted_name_as_text(page, ui_server):
    page.locator('#cr-create-org').click()
    name='<img src=x onerror="window.registryXss=true">'
    page.locator('#cr-name').fill(name)
    page.locator('#cr-save').click()
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    expect(page.locator('.cr-org-heading')).to_have_text(name)
    assert page.evaluate('window.registryXss===undefined')
    assert page.locator('.cr-org-heading img').count()==0


def test_confirmed_row_survives_failed_refresh_without_another_post(page, ui_server):
    requests={'gets':0}
    def scoped_get(route):
        requests['gets']+=1
        if requests['gets']==1:
            route.continue_()  # Fresh authority preflight before POST.
        else:
            route.fulfill(status=503,content_type='application/json',body='{}')
    page.route('**/api/organisations/'+str(ORG_A)+'/clinics',lambda route: scoped_get(route) if route.request.method=='GET' else route.continue_())
    create(page,'Confirmed row retained')
    expect(page.locator('#cr-dialog')).not_to_be_visible()
    expect(page.locator('#cr-message')).to_contain_text('couldn’t refresh')
    expect(page.locator('.cr-clinic')).to_have_count(3)
    expect(page.locator('#cr-content')).to_contain_text('Confirmed row retained')
    assert len(metrics(page,ui_server)['requests'])==1
    assert page.evaluate('key=>sessionStorage.getItem(key)',JOURNAL) is None


def test_private_navigation_and_forbidden_parent(page, ui_server):
    page.locator('#account-trigger').click()
    expect(page.locator('#account-clinic-registry-link')).to_be_visible()
    page.locator('#account-clinic-registry-link').click()
    expect(page.locator('#cr-organisation')).to_be_visible()
    page.goto(ui_server+'/app/organisations?organisation='+str(ORG_C))
    expect(page.locator('#cr-message')).to_contain_text('unavailable')
    expect(page.locator('.cr-clinic')).to_have_count(0)
    assert page.locator('#cr-create-clinic').count()==0
    assert not metrics(page,ui_server)['requests']


def test_capture_actual_ui(page, ui_server):
    output=os.getenv('GO360_CLINIC_UI_EVIDENCE')
    if not output:
        return
    folder=Path(output)
    folder.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(folder/'desktop.png'),full_page=True)
    page.locator('#cr-create-clinic').click()
    page.locator('#cr-name').fill('Harbour Clinic')
    page.screenshot(path=str(folder/'creation.png'),full_page=True)
    page.keyboard.press('Escape')
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(folder/'mobile.png'),full_page=True)
