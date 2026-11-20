"""Explicit account linking: browser/session-bound OAuth proof plus password."""
import hashlib
import hmac
import json
from datetime import datetime,timedelta,timezone
from secrets import token_urlsafe
from sqlalchemy import select,delete
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from app.db.models import User,ExternalIdentity,IdentityLinkAttempt,AuditLog
from app.utils.security import verify_password
from app.services import social_auth_service as oauth


def now():return datetime.now(timezone.utc)
def digest(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()
def cookie_name(provider):return 'go360_link_'+provider


def audit(db,actor,provider,outcome):
    db.add(AuditLog(user_id=actor.id,action='identity.link',actor_type='user',outcome=outcome,
                    metadata_json={'provider':provider}))


def deny(db,actor,provider,message,code=403):
    audit(db,actor,provider,'refused');db.commit()
    raise HTTPException(code,message,headers={'Cache-Control':'no-store'})


def authorize(db,actor,provider):
    if not actor.is_active or not actor.email_verified_at:
        deny(db,actor,provider,'Verify your email before managing sign-in methods.')


def prune(db):
    # Remove expired transient proof rows; audit contains no provider subject/email.
    db.execute(delete(IdentityLinkAttempt).where(IdentityLinkAttempt.expires_at<=now()))


def start(db,actor,provider,session_token):
    authorize(db,actor,provider)
    if provider not in oauth.SUPPORTED_SOCIAL_PROVIDERS:
        deny(db,actor,'unknown','Unsupported sign-in provider.',400)
    if not oauth.social_login_enabled() or not oauth.provider_is_configured(provider):
        deny(db,actor,provider,'This sign-in provider is not configured.',503)
    state='link.'+token_urlsafe(32)
    verifier=token_urlsafe(48)
    browser=json.dumps({'nonce':token_urlsafe(32),'verifier':verifier},separators=(',',':'))
    challenge=oauth._b64_encode(hashlib.sha256(verifier.encode()).digest())
    url=oauth.build_oauth_authorization_url(provider,state=state,code_challenge=challenge)
    try:
        db.execute(select(User.id).where(User.id==actor.id).with_for_update(key_share=True)).scalar_one()
        db.refresh(actor);authorize(db,actor,provider)
        prune(db)
        if db.scalar(select(ExternalIdentity.id).where(ExternalIdentity.user_id==actor.id,ExternalIdentity.provider==provider)):
            deny(db,actor,provider,'This provider is already linked.',409)
        db.execute(delete(IdentityLinkAttempt).where(IdentityLinkAttempt.user_id==actor.id,IdentityLinkAttempt.provider==provider))
        db.add(IdentityLinkAttempt(user_id=actor.id,provider=provider,state_hash=digest(state),
            browser_hash=digest(browser),session_hash=digest(session_token),status='pending',expires_at=now()+timedelta(minutes=10)))
        audit(db,actor,provider,'started');db.commit()
        return url,browser
    except Exception:
        db.rollback();raise


def validate(db,actor,row,browser,session_token,phase):
    authorize(db,actor,row.provider if row else 'unknown')
    expires=row.expires_at if row else now()
    if expires.tzinfo is None:expires=expires.replace(tzinfo=timezone.utc)
    if not row or row.user_id!=actor.id or row.status!=phase or expires<=now() or not hmac.compare_digest(row.browser_hash,digest(browser or '')) or not hmac.compare_digest(row.session_hash,digest(session_token or '')):
        deny(db,actor,row.provider if row and row.user_id==actor.id else 'unknown',
            'This linking session expired or your account changed. Start again.')


def callback_attempt(db,actor,provider,state,browser,session_token,*,lock=False):
    query=select(IdentityLinkAttempt).where(IdentityLinkAttempt.state_hash==digest(state))
    if lock:query=query.with_for_update().execution_options(populate_existing=True)
    row=db.scalar(query)
    validate(db,actor,row,browser,session_token,'pending')
    if row.provider!=provider:deny(db,actor,'unknown','Invalid linking session.')
    return row


def record_provider_proof(db,actor,provider,state,browser,session_token,profile):
    try:
        row=callback_attempt(db,actor,provider,state,browser,session_token,lock=True)
        subject=profile.get('provider_subject');email=profile.get('email')
        if profile.get('email_verified') is not True or not subject or not email or len(subject)>255 or len(email)>320:
            row.status='failed';deny(db,actor,provider,'The provider did not verify an account email.')
        row.candidate_subject=subject;row.candidate_email=email;row.status='verified'
        audit(db,actor,provider,'provider_verified');db.commit()
    except Exception:
        db.rollback();raise


def confirm(db,actor,attempt_id,browser,session_token,password):
    try:
        db.execute(select(User.id).where(User.id==actor.id).with_for_update(key_share=True)).scalar_one()
        db.refresh(actor)
        row=db.scalar(select(IdentityLinkAttempt).where(IdentityLinkAttempt.id==attempt_id).with_for_update().execution_options(populate_existing=True))
        validate(db,actor,row,browser,session_token,'verified')
        if not verify_password(password,actor.password_hash):
            row.password_failures+=1
            if row.password_failures>=5:
                row.status='failed';row.candidate_subject=None;row.candidate_email=None
            deny(db,actor,row.provider,'Current password was not accepted. After five attempts, start linking again.',400)
        existing=db.scalar(select(ExternalIdentity).where(ExternalIdentity.provider==row.provider,
                                                       ExternalIdentity.subject==row.candidate_subject))
        own=db.scalar(select(ExternalIdentity).where(ExternalIdentity.user_id==actor.id,ExternalIdentity.provider==row.provider))
        if (existing and existing.user_id!=actor.id) or (own and own.subject!=row.candidate_subject):
            row.status='failed';row.candidate_subject=None;row.candidate_email=None
            deny(db,actor,row.provider,'That provider cannot be linked. No accounts were merged.',409)
        if not existing:
            db.add(ExternalIdentity(user_id=actor.id,provider=row.provider,subject=row.candidate_subject,email=row.candidate_email))
        row.status='complete';row.candidate_subject=None;row.candidate_email=None
        audit(db,actor,row.provider,'success');db.commit()
        return row.provider
    except IntegrityError:
        db.rollback()
        deny(db,actor,'unknown','That provider cannot be linked. No accounts were merged.',409)
    except Exception:
        db.rollback();raise
