"""Loopback synthetic app for the actual registry UI/API. Never mounted in main."""
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Uuid, create_engine, delete, select, text
from sqlalchemy.orm import Session

from app.api import deps, routes_clinics
from app.db.models import Clinic, ClinicRegistryEvent, Organisation, User, UserCapability
from app.services.platform_settings_service import is_platform_admin_user

OWNER = UUID('00000000-0000-4000-8000-000000000001')
CAPABLE = UUID('00000000-0000-4000-8000-000000000002')
OTHER = UUID('00000000-0000-4000-8000-000000000003')
ADMIN = UUID('00000000-0000-4000-8000-000000000004')
CLIENT = UUID('00000000-0000-4000-8000-000000000005')
ORG_A = UUID('7a31b9e2-1dc1-4250-95c3-c22ae0150001')
ORG_B = UUID('91c2d600-4521-4310-9827-c22ae0150002')
ORG_C = UUID('a1c2d600-4521-4310-9827-c22ae0150003')


def make_app(path, monkeypatch):
    monkeypatch.setenv('PLATFORM_ADMIN_EMAILS', '')
    engine = create_engine('sqlite:///' + str(path), connect_args={'check_same_thread': False, 'timeout': 20})
    models = (User, UserCapability, Organisation, Clinic, ClinicRegistryEvent)
    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                monkeypatch.setattr(column, 'type', Uuid(as_uuid=True))
    User.metadata.create_all(engine, tables=[model.__table__ for model in models])
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)')
        conn.execute(text("INSERT INTO app_settings VALUES ('platform_config', :value)"), {'value': '{"admin_emails":""}'})
    actors = {'owner': OWNER, 'capable': CAPABLE, 'other': OTHER, 'admin': ADMIN, 'client': CLIENT}
    state = {'failure': None, 'logout_failure': False, 'requests': [], 'slow_parent': None, 'delay': 0}

    def reset():
        state.update(failure=None, logout_failure=False, requests=[], slow_parent=None, delay=0)
        with Session(engine) as db:
            for model in (ClinicRegistryEvent, Clinic, Organisation, UserCapability, User):
                db.execute(delete(model))
            for name, actor in actors.items():
                db.add(User(id=actor, email=name+'@example.invalid', full_name='Synthetic '+name,
                            role='admin' if name=='admin' else 'client' if name in {'capable','client'} else 'practitioner',
                            password_hash='unused', is_active=True, email_verified_at=datetime.now(timezone.utc)))
            db.flush()
            db.add(UserCapability(user_id=CAPABLE, capability='practitioner'))
            for id_, actor in ((ORG_A,OWNER),(ORG_B,OWNER),(ORG_C,CAPABLE)):
                db.add(Organisation(id=id_, display_name='Harbour Care Group' if actor==OWNER else 'Capable practice',
                    created_by_user_id=actor, request_key=str(id_)))
            db.flush()
            for suffix in (1,2):
                db.add(Clinic(id=UUID(f'3c18e2c{suffix}-0f42-4d41-86ae-06a0d800000{suffix}'), organisation_id=ORG_A,
                    display_name='Harbour Clinic', created_by_user_id=OWNER, request_key='seed'+str(suffix)))
            db.commit()
    reset()

    def db_dependency():
        with Session(engine) as db:
            yield db

    def resolve(db, token):
        if token not in actors:
            raise HTTPException(401, 'Synthetic session ended')
        actor = db.get(User, actors[token])
        if not actor or not actor.is_active:
            raise HTTPException(403, 'Inactive synthetic actor')
        return actor
    monkeypatch.setattr(deps, 'get_current_user_from_token', resolve)
    original = routes_clinics.create_registry_entry

    def create(db, actor, **kwargs):
        state['requests'].append({'actor': str(actor.id), 'name': kwargs['display_name'], 'key': kwargs['request_key'],
                                   'parent': str(kwargs.get('organisation_id')) if kwargs.get('organisation_id') else None})
        failure, state['failure'] = state['failure'], None
        if failure == 'before':
            raise HTTPException(503, 'Synthetic pre-commit failure')
        if failure == 'validation':
            raise HTTPException(422, 'Synthetic validation refusal')
        row, created = original(db, actor, **kwargs)
        if failure == 'after':
            raise HTTPException(503, 'Synthetic response lost after commit')
        if failure == 'wrong-parent':
            return SimpleNamespace(id=row.id, display_name=row.display_name, organisation_id=ORG_B), created
        return row, created
    monkeypatch.setattr(routes_clinics, 'create_registry_entry', create)
    original_list = routes_clinics.list_clinics
    def list_clinics(db, actor, organisation_id):
        result = original_list(db, actor, organisation_id)
        if str(organisation_id) == state['slow_parent']:
            time.sleep(state['delay'])
        return result
    monkeypatch.setattr(routes_clinics, 'list_clinics', list_clinics)
    app = FastAPI()
    app.dependency_overrides[deps.get_db] = db_dependency
    app.include_router(routes_clinics.router)
    app.mount('/static', StaticFiles(directory='app/static'), name='static')

    @app.get('/auth/me')
    def me(db=Depends(deps.get_db), user=Depends(deps.get_current_active_user)):
        return {'user': {'id':str(user.id), 'full_name':user.full_name, 'email':user.email, 'role':user.role,
                         'is_active':user.is_active, 'email_verified_at':user.email_verified_at},
                'capabilities':list(db.scalars(select(UserCapability.capability).where(UserCapability.user_id==user.id)))}

    @app.get('/api/platform-settings/me')
    def platform(db=Depends(deps.get_db), user=Depends(deps.get_current_user)):
        return {'is_admin':is_platform_admin_user(db,user), 'features':{}}

    @app.get('/auth/impersonation-status')
    def support():
        return {'active':False}

    @app.post('/auth/logout')
    def logout(response: Response):
        if state['logout_failure']:
            raise HTTPException(503, 'Synthetic logout failure')
        response.delete_cookie('access_token')
        return {'ok':True}

    @app.get('/login', response_class=HTMLResponse)
    def login():
        return '<h1>Synthetic sign in</h1>'

    @app.post('/__test/control')
    async def control(request: Request):
        data = await request.json()
        if data.get('reset'):
            reset()
        if data.get('revoke') in actors:
            with Session(engine) as db:
                db.execute(delete(UserCapability).where(UserCapability.user_id==actors[data['revoke']]))
                db.commit()
        for key in ('failure', 'logout_failure', 'slow_parent', 'delay'):
            if key in data:
                state[key] = data[key]
        return {'ok':True}

    @app.get('/__test/metrics')
    def metrics():
        with Session(engine) as db:
            return {'requests':state['requests'], 'clinics':len(list(db.scalars(select(Clinic.id))))}
    return app, engine
