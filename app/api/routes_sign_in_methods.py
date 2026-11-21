import json,os
from uuid import UUID
from urllib.parse import urlsplit
from fastapi import APIRouter,Request,Response,HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from app.api.deps import CurrentActiveUser,CurrentDB
from app.db.models import ExternalIdentity,IdentityLinkAttempt
from app.services import identity_link_service as service,social_auth_service as oauth
from app.services.auth_service import get_current_user_from_token

router=APIRouter(tags=['sign-in-methods'])
templates=Jinja2Templates(directory='app/templates')
PAGE='/account/sign-in-methods'


def token(request):
    auth=request.headers.get('authorization','')
    return auth[7:] if auth.lower().startswith('bearer ') else request.cookies.get('access_token','')


def secure(request):
    return request.url.scheme=='https' or os.getenv('APP_ENV','development').strip().lower() not in {'development','dev','local','test'}


def write_guard(request,db,actor):
    if request.cookies.get('admin_restore_token'):
        service.deny(db,actor,'unknown','Sign-in methods cannot be changed in support mode.')
    expected=urlsplit(os.getenv('BASE_URL') or str(request.base_url))
    origin=request.headers.get('origin','')
    if origin!=f'{expected.scheme}://{expected.netloc}':
        service.deny(db,actor,'unknown','Reload this page before changing sign-in methods.')


def redirect(request,provider,suffix,*,clear=False):
    response=RedirectResponse(PAGE+suffix,status_code=303,
        headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    if clear and provider in oauth.SUPPORTED_SOCIAL_PROVIDERS:
        response.delete_cookie(service.cookie_name(provider),path='/auth',secure=secure(request),httponly=True,samesite='lax')
    return response


@router.get(PAGE)
def page(request:Request):
    # Static shell only: no identity is embedded before authenticated API lookup.
    return templates.TemplateResponse(request=request,name='sign_in_methods.html',
        context={'request':request},headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


@router.get('/auth/sign-in-methods')
def methods(request:Request,response:Response,db:CurrentDB,actor:CurrentActiveUser):
    service.prune(db)
    linked={row.provider:row for row in db.scalars(select(ExternalIdentity).where(ExternalIdentity.user_id==actor.id))}
    pending={}
    for row in db.scalars(select(IdentityLinkAttempt).where(IdentityLinkAttempt.user_id==actor.id,IdentityLinkAttempt.status=='verified')):
        browser=request.cookies.get(service.cookie_name(row.provider),'')
        if service.digest(browser)==row.browser_hash and service.digest(token(request))==row.session_hash:
            pending[row.provider]={'id':str(row.id),'email':row.candidate_email}
    db.commit();response.headers['Cache-Control']='no-store'
    return {'user_id':str(actor.id),'email':actor.email,'return_url':'/client/account' if actor.role=='client' else '/app/profile',
        'read_only':bool(request.cookies.get('admin_restore_token')) or not bool(actor.email_verified_at),
        'read_only_reason':'Return to your own session to manage sign-in methods.' if request.cookies.get('admin_restore_token') else 'Verify your email before linking.' if not actor.email_verified_at else None,
        'providers':[{'provider':p,'configured':oauth.social_login_enabled() and oauth.provider_is_configured(p),
            'linked':p in linked,'email':linked[p].email if p in linked else None,'pending':pending.get(p)} for p in sorted(oauth.SUPPORTED_SOCIAL_PROVIDERS)]}


@router.post('/auth/sign-in-methods/{provider}/link')
def start(provider:str,request:Request,response:Response,db:CurrentDB,actor:CurrentActiveUser):
    write_guard(request,db,actor)
    url,browser=service.start(db,actor,provider,token(request))
    response.set_cookie(service.cookie_name(provider),browser,max_age=600,path='/auth',secure=secure(request),httponly=True,samesite='lax')
    response.headers['Cache-Control']='no-store'
    return {'url':url}


class Confirm(BaseModel):
    model_config=ConfigDict(extra='forbid')
    current_password:str=Field(min_length=1,max_length=128)


@router.post('/auth/sign-in-methods/confirm/{attempt_id}')
def confirm(attempt_id:UUID,command:Confirm,request:Request,response:Response,db:CurrentDB,actor:CurrentActiveUser):
    write_guard(request,db,actor)
    row=db.get(IdentityLinkAttempt,attempt_id)
    browser=request.cookies.get(service.cookie_name(row.provider),'') if row and row.user_id==actor.id else ''
    provider=service.confirm(db,actor,attempt_id,browser,token(request),command.current_password)
    response.delete_cookie(service.cookie_name(provider),path='/auth',secure=secure(request),httponly=True,samesite='lax')
    response.headers['Cache-Control']='no-store'
    return {'ok':True,'provider':provider,'message':'Sign-in method linked. Your GO360 account and permissions are unchanged.'}


def callback(provider,request,db,code,state,error):
    # This branch never falls through to ordinary sign-in or issues new auth tokens.
    try:
        actor=get_current_user_from_token(db,token(request))
        if request.cookies.get('admin_restore_token'):
            service.deny(db,actor,provider,'Linking is unavailable in support mode.')
        browser=request.cookies.get(service.cookie_name(provider),'')
        row=service.callback_attempt(db,actor,provider,state,browser,token(request))
        if error or not code:
            row.status='failed';service.audit(db,actor,provider,'cancelled');db.commit()
            return redirect(request,provider,'?link_error=cancelled',clear=True)
        cookie=json.loads(browser)
        result=oauth.exchange_code_for_token(provider,code,code_verifier=cookie['verifier'])
        access=result.get('access_token')
        if not access:raise ValueError('Provider did not return access proof')
        profile=oauth.normalise_social_profile(provider,oauth.get_provider_user_info(provider,access))
        service.record_provider_proof(db,actor,provider,state,browser,token(request),profile)
        return redirect(request,provider,'?link=confirm')
    except Exception:
        db.rollback()
        return redirect(request,provider,'?link_error=failed',clear=True)
