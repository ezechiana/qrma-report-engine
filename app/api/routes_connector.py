"""Explicitly gated device approval and incoming assessment API."""
import hashlib
import html
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request, Form, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_active_user
from app.db.models import User
from app.db.connector_models import ConnectorDevice, ConnectorPairing, IncomingAssessment
from app.services import connector_service as service
from app.services import connector_audit as audit, connector_observability as telemetry
from app.services.connector_limits import allow, require
from app.services import connector_policy as policy
from app.schemas.connector_observability import Heartbeat
from app.services.connector_privacy import ConnectorRoute, install_access_filter
install_access_filter()


def enabled(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    if not policy.enabled():
        raise HTTPException(404)
    if request.url.scheme != 'https':
        raise HTTPException(400, 'HTTPS is required.')
    if policy.release_environment() and str(request.base_url).rstrip('/') != origin():
        raise HTTPException(403, 'Connector host rejected.')
    if policy.release_environment():
        require('request-all', audit.observed_ip(request), 240)




# Self-contained approval UI; CSP pins the exact inline style and success-page script.
_APPROVAL_STYLE = 'body{margin:0;background:#f3f6fb;color:#15243d;font:16px Segoe UI,Arial,sans-serif}main{max-width:520px;margin:9vh auto;background:white;border:1px solid #dce4ef;border-radius:18px;padding:32px;box-shadow:0 10px 40px #0c25401a}.brand{color:#1478e8;font-size:29px;font-weight:800;letter-spacing:-1px}.brand span{color:#d4a422}h1{font-size:24px;margin:24px 0 12px}p{line-height:1.5;color:#40516b}label{display:block;margin:20px 0 8px;font-weight:600}input{font:inherit;padding:12px;width:100%;box-sizing:border-box;border:1px solid #afbdd0;border-radius:8px}button{background:#176de5;border:0;color:white;border-radius:8px;padding:12px 18px;font:600 15px Segoe UI,Arial,sans-serif;cursor:pointer;margin-top:18px}.small{font-size:13px}.success{color:#147c58}'
_APPROVAL_CSS_CSP = "'sha256-zMfHMySWF23KJSEMlCMjaOpkREPyi6mEYkAVUfY0SWU='"
_SUCCESS_SCRIPT = "window.addEventListener('load',function(){setTimeout(function(){window.close()},1200)});"
_SUCCESS_JS_CSP = "'sha256-OgAoBZmzPNPM+apy3lGBGZ2F4QsYA1rIjlhyCkVqdlE='"

def approval_html(content, *, close=False):
    script_tag = '<script>' + _SUCCESS_SCRIPT + '</script>' if close else ''
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>GO360 Desktop Connection</title><style>' + _APPROVAL_STYLE + '</style></head>'
            '<body><main><div class="brand">go<span>●</span>360</div>' + content + '</main>'
            + script_tag + '</body></html>')

router = APIRouter(dependencies=[Depends(enabled)], tags=['desktop-connector'], route_class=ConnectorRoute)
limits = defaultdict(deque)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def origin():
    value = os.environ.get('GO360_CONNECTOR_ORIGIN', '').rstrip('/')
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.path not in ('', '/') or parts.query or parts.fragment:
        raise HTTPException(503, 'Configure an HTTPS connector origin.')
    return value


def rate(request, kind, count):
    if policy.release_environment():
        require('request-' + kind, audit.observed_ip(request), count)
        return
    key = (kind, request.client.host if request.client else 'unknown')
    stamp = time.monotonic()
    for old in list(limits):
        if not limits[old] or limits[old][-1] < stamp - 60:
            del limits[old]
    if len(limits) > 10000:
        raise HTTPException(429, 'Try again later.')
    q = limits[key]
    while q and q[0] < stamp - 60:
        q.popleft()
    if len(q) >= count:
        raise HTTPException(429, 'Try again later.')
    q.append(stamp)


def same_origin(request):
    if request.headers.get('origin') != origin():
        raise HTTPException(403, 'Origin rejected.')


def browser_user(request: Request, user=Depends(get_current_active_user)):
    if request.method != 'GET':
        same_origin(request)
    return user


def authentication_denied(db, request, reason):
    # No asserted actor/device identity; no tokens, hashes or payload copied.
    if audit.enabled() and allow('auth-denial-audit', audit.observed_ip(request), 1):
        with Session(bind=db.get_bind()) as tx:
            audit.record(tx, 'authentication_denied', actor_type='anonymous', outcome='denied',
                         reason=reason, observation=audit.context(request))
            tx.commit()


def authenticated_device(request: Request, db=Depends(get_db)):
    value = request.headers.get('authorization', '')
    if not value.startswith('Bearer ') or len(value) > 256:
        authentication_denied(db, request, 'missing_credential')
        raise HTTPException(401, 'Connector sign-in required.')
    device = db.scalar(select(ConnectorDevice).where(ConnectorDevice.credential_hash == sha(value[7:])))
    if not device or device.revoked or utc(device.expires_at) <= datetime.now(timezone.utc):
        authentication_denied(db, request, 'invalid_credential')
        raise HTTPException(401, 'Connector session expired or revoked.')
    user = db.get(User, device.owner_id)
    if not user or not user.is_active:
        authentication_denied(db, request, 'account_unavailable')
        raise HTTPException(401, 'Account unavailable.')
    return device


class PairStart(BaseModel):
    connector_id: uuid.UUID
    challenge: str = Field(pattern=r'^[a-f0-9]{64}$')


class PairPoll(BaseModel):
    pairing_id: uuid.UUID
    verifier: str = Field(min_length=43, max_length=128)


@router.post('/api/connector/pairings')
def start_pair(payload: PairStart, request: Request, db=Depends(get_db)):
    rate(request, 'start', 8)
    code = secrets.token_hex(5).upper()
    row = ConnectorPairing(connector_id=payload.connector_id, challenge=payload.challenge,
                           code_hash=sha(code), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    db.add(row)
    db.commit()
    return dict(pairing_id=str(row.id), user_code=code, interval=5,
                verification_uri=origin() + '/app/connector/approve?pairing_id=' + str(row.id))


@router.get('/app/connector/approve', response_class=HTMLResponse)
def approval_page(request: Request, pairing_id: uuid.UUID, user=Depends(browser_user), db=Depends(get_db)):
    row = db.get(ConnectorPairing, pairing_id)
    if not row or row.consumed or utc(row.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(410, 'Approval expired. Sign in from the connector again.')
    nonce = secrets.token_urlsafe(32)
    content = ('<h1>Connect GO360 Desktop</h1>'
        '<p>Approve only a connector you started. Enter the code displayed in its window.</p>'
        '<p class="small">Signed in as ' + html.escape(user.email) + '</p>'
        '<form method="post"><input type="hidden" name="pairing_id" value="' + str(row.id) + '">'
        '<input type="hidden" name="csrf" value="' + nonce + '">'
        '<label for="connector-code">Connector code</label>'
        '<input id="connector-code" name="code" required maxlength="10" autocomplete="off" '
        'autocapitalize="characters" spellcheck="false">'
        '<button type="submit">Approve this connector</button></form>')
    response = HTMLResponse(approval_html(content))
    response.set_cookie('connector_approval', nonce, secure=True, httponly=True, samesite='strict', max_age=600, path='/app/connector/approve')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'none'; style-src " + _APPROVAL_CSS_CSP + "; form-action 'self'; frame-ancestors 'none'"
    return response


@router.post('/app/connector/approve', response_class=HTMLResponse)
def approve(request: Request, pairing_id: uuid.UUID = Form(...), code: str = Form(...), csrf: str = Form(...),
            user=Depends(browser_user), db=Depends(get_db)):
    rate(request, 'approve', 10)
    if not secrets.compare_digest(csrf, request.cookies.get('connector_approval', '')) or not csrf:
        raise HTTPException(403, 'Approval expired.')
    row = db.scalar(select(ConnectorPairing).where(ConnectorPairing.id == pairing_id).with_for_update())
    if not row or row.approved or row.consumed or row.attempts >= 5 or utc(row.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(410, 'Approval expired.')
    row.attempts += 1
    if not secrets.compare_digest(row.code_hash, sha(code.strip().upper())):
        db.commit()
        raise HTTPException(400, 'Connector code did not match.')
    row.owner_id, row.approved = user.id, True
    audit.record(db, 'pair_approved', actor_type='user', actor_id=user.id, owner_id=user.id,
                 connector_id=row.connector_id, event_key=f'pair:{row.id}:approved', observation=audit.context(request))
    db.commit()
    content = ('<h1 class="success">GO360 Desktop Connected</h1>'
               '<p>Connection approved. Return to the GO360 Desktop Connector.</p>'
               '<p class="small">You may close this browser tab if it does not close automatically.</p>')
    return HTMLResponse(approval_html(content, close=True), headers={
        'Cache-Control':'no-store',
        'Content-Security-Policy': "default-src 'none'; style-src " + _APPROVAL_CSS_CSP
            + "; script-src " + _SUCCESS_JS_CSP + "; frame-ancestors 'none'",
    })


@router.post('/api/connector/pairings/token')
def poll_pair(payload: PairPoll, request: Request, db=Depends(get_db)):
    rate(request, 'poll', 30)
    row = db.scalar(select(ConnectorPairing).where(ConnectorPairing.id == payload.pairing_id).with_for_update())
    if not row or utc(row.expires_at) <= datetime.now(timezone.utc) or not secrets.compare_digest(row.challenge, sha(payload.verifier)):
        raise HTTPException(401, 'Invalid pairing.')
    if not row.approved:
        raise HTTPException(428, 'Awaiting browser approval.')
    user = db.get(User, row.owner_id)
    if not user or not user.is_active:
        raise HTTPException(401)
    credential = sha('go360-connector-v1:' + str(row.id) + ':' + payload.verifier)
    # PostgreSQL transaction lock covers first creation across different owners.
    # UUID is installation identity, not authorization; approved account switching remains supported.
    if audit.enabled() and db.get_bind().dialect.name == 'postgresql':
        from sqlalchemy import text
        key = row.connector_id.int % (2**63)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
    device = db.scalar(select(ConnectorDevice).where(ConnectorDevice.id == row.connector_id)
                       .with_for_update().execution_options(populate_existing=True))
    if row.consumed:
        if not device or device.revoked or device.credential_hash != sha(credential):
            raise HTTPException(401, 'Pair again.')
    else:
        if device is None:
            device = ConnectorDevice(id=row.connector_id, owner_id=row.owner_id)
            db.add(device)
        previous_owner = device.owner_id
        if audit.enabled() and previous_owner != row.owner_id:
            # A new owner must not see former owner's network/version observations.
            device.last_seen_at = device.last_heartbeat_at = None
            device.last_ip_address = device.connector_version = device.platform = None
        device.owner_id, device.credential_hash = row.owner_id, sha(credential)
        device.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        device.revoked, row.consumed = False, True
        audit.record(db, 'credential_issued', actor_type='user', actor_id=user.id, owner_id=user.id,
                     connector_id=device.id, previous_owner_id=previous_owner,
                     event_key=f'pair:{row.id}:issued', observation=audit.context(request))
        db.commit()
    return dict(access_token=credential, account_id=str(user.id), display_name=user.full_name,
                expires_at=utc(device.expires_at).isoformat())


@router.delete('/api/connector/session')
def logout(request: Request, device=Depends(authenticated_device), db=Depends(get_db)):
    if audit.enabled():
        telemetry.touch(db, device, audit.context(request))
    device.revoked = True
    audit.record(db, 'session_signed_out', actor_id=device.owner_id, owner_id=device.owner_id,
                 connector_id=device.id, observation=audit.context(request))
    db.commit()
    return {'signed_out': True}


@router.post('/api/connector/jobs/{job_id}')
async def upload(job_id: uuid.UUID, request: Request, device=Depends(authenticated_device), db=Depends(get_db)):
    rate(request, 'upload', 30)
    origin()  # Reject an unconfigured/non-HTTPS server origin.
    from app.services.subscription_service import require_subscription_feature
    require_subscription_feature(db, db.get(User, device.owner_id), 'report_generation')
    if request.headers.get('content-type', '').split(';')[0] != 'text/html':
        raise HTTPException(415, 'Expected text/html.')
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > service.MAX_HTML:
            raise HTTPException(413, 'Assessment exceeds 8 MiB.')
    try:
        captured = datetime.fromisoformat(request.headers.get('x-captured-at', '').replace('Z', '+00:00'))
        if captured.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, 'Expected capture timestamp with timezone.')
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(service.ingest, db, device, job_id,
           request.headers.get('x-source-profile', ''), captured, bytes(raw), audit.context(request))


@router.get('/api/connector/jobs/{job_id}')
def status(job_id: uuid.UUID, request: Request, device=Depends(authenticated_device), db=Depends(get_db)):
    row = db.scalar(select(IncomingAssessment).where(IncomingAssessment.owner_id == device.owner_id,
                    IncomingAssessment.connector_id == device.id, IncomingAssessment.job_id == job_id))
    if not row:
        raise HTTPException(404, 'Job not found.')
    return service.reconcile(db, row, device=device, observation=audit.context(request))


@router.get('/api/incoming-assessments')
def incoming(user=Depends(browser_user), db=Depends(get_db), offset: int = 0):
    offset = max(0, min(offset, 100000))
    rows = db.scalars(select(IncomingAssessment).where(IncomingAssessment.owner_id == user.id)
                      .order_by(IncomingAssessment.received_at.desc()).offset(offset).limit(100)).all()
    return [service.result(row, True) for row in rows]


@router.get('/api/incoming-assessments/{assessment_id}')
def incoming_one(assessment_id: uuid.UUID, user=Depends(browser_user), db=Depends(get_db)):
    return service.result(service.owned(db, user.id, assessment_id), True)


@router.get('/app/cases/incoming', response_class=HTMLResponse)
def incoming_page(request: Request, user=Depends(browser_user)):
    return Jinja2Templates(directory='app/templates').TemplateResponse(
        request=request, name='incoming_assessments.html', context={'request':request, 'current_user':user})

# Browser-only confirmation; credentials never bypass patient confirmation.
from app.schemas.cases import CreateFromImportRequest


class ConfirmIncomingRequest(CreateFromImportRequest):
    temporary_upload_id: str = ''
    generate_report: bool = False


@router.post('/api/incoming-assessments/{assessment_id}/confirm')
async def confirm_incoming(assessment_id: uuid.UUID, payload: ConfirmIncomingRequest, request: Request,
                           user=Depends(browser_user), db=Depends(get_db)):
    from app.services.connector_confirmation import confirm
    from app.services.subscription_service import require_subscription_feature
    require_subscription_feature(db, user, 'report_generation')
    return await confirm(db.get_bind(), user.id, assessment_id, payload, observation=audit.context(request))


@router.get('/api/connector-devices')
def devices(user=Depends(browser_user), db=Depends(get_db)):
    rows = db.scalars(select(ConnectorDevice).where(ConnectorDevice.owner_id == user.id)).all()
    return [dict(connector_id=str(row.id), revoked=row.revoked, expires_at=utc(row.expires_at).isoformat()) for row in rows]


@router.delete('/api/connector-devices/{connector_id}')
def revoke_device(connector_id: uuid.UUID, request: Request, user=Depends(browser_user), db=Depends(get_db)):
    row = db.scalar(select(ConnectorDevice).where(ConnectorDevice.id == connector_id, ConnectorDevice.owner_id == user.id).with_for_update().execution_options(populate_existing=True))
    if not row:
        raise HTTPException(404)
    if not row.revoked:
        row.revoked = True
        audit.record(db, 'device_revoked', actor_type='user', actor_id=user.id, owner_id=user.id,
                     connector_id=row.id, observation=audit.context(request))
    db.commit()
    return {'revoked': True}


def heartbeat_guard(request: Request):
    if not audit.enabled():
        raise HTTPException(404)
    require('heartbeat-ip', audit.observed_ip(request), 120)


@router.post('/api/connector/heartbeat', dependencies=[Depends(heartbeat_guard)])
async def heartbeat(request: Request, device=Depends(authenticated_device), db=Depends(get_db)):
    require('heartbeat-device', str(device.id), 10)
    if request.headers.get('content-type', '').split(';')[0] != 'application/json':
        raise HTTPException(415, 'Expected application/json.')
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 1024:
            raise HTTPException(413, 'Heartbeat exceeds 1 KiB.')
    try:
        payload = Heartbeat.model_validate_json(bytes(raw))
    except ValueError:
        raise HTTPException(422, 'Invalid heartbeat.') from None
    stamp = telemetry.touch(db, device, audit.context(request), heartbeat=payload)
    db.commit()
    return {'server_time': stamp.isoformat(), 'heartbeat_interval_seconds': 60}
