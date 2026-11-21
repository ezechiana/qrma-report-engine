"""Mailbox proofs are unavailable in hosted API responses, including Staging."""
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from app.api import routes_auth
from app.db.models import User
from test_admin_entitlements import auth_engine,auth_app


@pytest.mark.parametrize('environment,debug', [('staging',False),('production',False),('unexpected',False),('development',True)])
def test_mailbox_proofs_are_local_only(auth_app,monkeypatch,environment,debug):
    client,_,engine,ids,_=auth_app
    monkeypatch.setenv('APP_ENV',environment)
    monkeypatch.setattr(routes_auth,'send_password_reset_email',lambda **kwargs:{'sent':True})
    monkeypatch.setattr(routes_auth,'_send_verification_for_user',lambda *args,**kwargs:{'token':'synthetic-verification-proof'})
    with Session(engine) as db:known=db.get(User,ids['owner']).email
    reset=client.post('/auth/forgot-password',json={'email':known})
    unknown=client.post('/auth/forgot-password',json={'email':uuid4().hex+'@example.com'})
    assert reset.status_code==unknown.status_code==200
    assert ('dev_reset_url' in reset.json()) is debug
    if not debug:assert reset.json()==unknown.json()
    email=uuid4().hex+'@example.com';password='SyntheticRegistration2!'
    registration=client.post('/auth/register',json={'email':email,'password':password,
        'confirm_password':password,'first_name':'Synthetic','last_name':'Mailbox'})
    assert registration.status_code==200
    assert ('dev_verification_url' in registration.json()) is debug
    resend=client.post('/auth/resend-verification',json={'email':email})
    assert resend.status_code==200
    assert ('dev_verification_url' in resend.json()) is debug
