"""GO360-017: independent participation, fresh policy and attributable transitions."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func, text, create_engine
from sqlalchemy.orm import Session
from test_admin_entitlements import auth_engine, auth_app
from app.api.routes_clinic_memberships import router
from app.api.routes_clinics import router as registry_router
from app.db.models import Clinic, Organisation, ClinicMembership, ClinicMembershipEvent, User
from app.services import clinic_membership as service


@pytest.fixture
def membership_app(auth_app):
    client,login,engine,ids,report=auth_app
    client.app.include_router(router);client.app.include_router(registry_router)
    with Session(engine) as db:
        org=Organisation(display_name='Synthetic Organisation',created_by_user_id=ids['owner'],request_key=uuid4().hex)
        db.add(org);db.flush()
        clinics=[Clinic(organisation_id=org.id,display_name='Clinic '+name,created_by_user_id=ids['owner'],request_key=uuid4().hex) for name in ['A','B']]
        db.add_all(clinics);db.commit();a,b=[c.id for c in clinics]
    login('owner')
    return client,login,engine,ids,a,b


def headers(ids,actor='owner',key=None):
    return {'X-Expected-Actor':str(ids[actor]),'Idempotency-Key':str(key or uuid4())}


def add(client,ids,clinic,principal='other',key=None):
    response=client.post(f'/api/clinics/{clinic}/memberships',json={'practitioner_user_id':str(ids[principal])},headers=headers(ids,key=key))
    assert response.status_code in {200,201},response.text
    return response.json()


def change(client,ids,clinic,row,status,actor='owner'):
    return client.patch(f'/api/clinics/{clinic}/memberships/{row["id"]}',
        json={'status':status,'revision':row['revision']},headers=headers(ids,actor))


def test_suspend_a_leaves_b_active_and_preserves_identity(membership_app):
    client,login,engine,ids,a,b=membership_app
    row_a=add(client,ids,a);row_b=add(client,ids,b)
    login('other')
    assert client.get(f'/api/clinics/{a}/membership-context').status_code==200
    login('owner');suspended=change(client,ids,a,row_a,'suspended')
    assert suspended.status_code==200
    login('other')
    assert client.get(f'/api/clinics/{a}/membership-context').status_code==403
    allowed=client.get(f'/api/clinics/{b}/membership-context')
    assert allowed.status_code==200 and allowed.json()['clinical_access'] is False
    own=client.get('/api/practitioner/memberships/me').json()['items']
    assert {r['clinic_id']:r['status'] for r in own}=={str(a):'suspended',str(b):'active'}
    with Session(engine) as db:
        assert db.get(User,ids['other']).is_active is True
        audit=db.scalar(select(ClinicMembershipEvent).where(ClinicMembershipEvent.membership_id==row_a['id'],ClinicMembershipEvent.action=='membership.status_changed'))
        assert (audit.prior_status,audit.new_status,audit.revision)==('active','suspended',1)
        assert audit.actor_user_id==ids['owner']
        assert db.get(ClinicMembership,row_b['id']).revision==0


def test_retries_revoke_reactivate_and_stale_revision(membership_app):
    client,_,engine,ids,a,b=membership_app
    key=uuid4();row=add(client,ids,a,key=key)
    assert add(client,ids,a,key=key)['id']==row['id']
    conflict=client.post(f'/api/clinics/{b}/memberships',json={'practitioner_user_id':str(ids['other'])},headers=headers(ids,key=key))
    assert conflict.status_code==409
    suspended=change(client,ids,a,row,'suspended').json()
    assert change(client,ids,a,row,'suspended').status_code==200
    assert change(client,ids,a,row,'revoked').status_code==409
    revoked=change(client,ids,a,suspended,'revoked').json()
    active=change(client,ids,a,revoked,'active').json()
    assert active['id']==row['id'] and active['revision']==3 and active['seat_status']=='unassigned'
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(ClinicMembershipEvent).where(
            ClinicMembershipEvent.membership_id==row['id'],ClinicMembershipEvent.action=='membership.status_changed'))==3


def test_wrong_clinic_actor_field_injection_and_support_denied(membership_app):
    client,login,_,ids,a,b=membership_app
    row=add(client,ids,a)
    assert change(client,ids,b,row,'suspended').status_code==404
    login('other')
    assert client.get(f'/api/clinics/{a}/memberships').status_code==404
    assert change(client,ids,a,row,'suspended',actor='other').status_code==404
    login('owner')
    for extra in [{'clinic_id':str(b)},{'clinical_access':True},{'role':'prescriber'}]:
        assert client.patch(f'/api/clinics/{a}/memberships/{row["id"]}',headers=headers(ids),json={'status':'suspended','revision':0,**extra}).status_code==422
    assert client.patch(f'/api/clinics/{a}/memberships/{row["id"]}',headers=headers(ids,'other'),json={'status':'suspended','revision':0}).status_code==403
    login('admin')
    assert client.get(f'/api/clinics/{a}/membership-context').status_code==403  # no platform bypass
    assert client.post('/auth/impersonate/'+str(ids['owner'])).status_code==200
    assert client.get(f'/api/clinics/{a}/memberships').json()['can_edit'] is False
    assert change(client,ids,a,row,'suspended').status_code==403


def test_client_inactive_and_unverified_targets_refused(membership_app):
    client,login,engine,ids,a,_=membership_app
    assert client.post(f'/api/clinics/{a}/memberships',headers=headers(ids),json={'practitioner_user_id':str(ids['client'])}).status_code==404
    with Session(engine) as db:
        actor=db.get(User,ids['other']);actor.email_verified_at=None;db.commit()
    assert client.post(f'/api/clinics/{a}/memberships',headers=headers(ids),json={'practitioner_user_id':str(ids['other'])}).status_code==404
    login('client');assert client.get('/api/practitioner/memberships/me').status_code==403


def test_atomic_audit_failure(membership_app,monkeypatch):
    client,_,engine,ids,a,_=membership_app
    row=add(client,ids,a)
    def fail(*args,**kwargs):raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(service,'event',fail)
    with pytest.raises(RuntimeError):change(client,ids,a,row,'suspended')
    with pytest.raises(RuntimeError):add(client,ids,a,principal='owner')
    with Session(engine) as db:
        current=db.get(ClinicMembership,row['id']);assert current.status=='active' and current.revision==0
        assert db.scalar(select(ClinicMembership.id).where(ClinicMembership.clinic_id==a,
            ClinicMembership.practitioner_user_id==ids['owner'])) is None


def test_concurrent_adds_and_transitions(membership_app):
    _,_,engine,ids,a,_=membership_app
    key=str(uuid4())
    def create(_):
        with Session(engine) as db:return service.create_membership(db,db.get(User,ids['owner']),a,ids['other'],key)[0]['id']
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(create,range(3)))
    assert len(set(results))==1
    def transition(status):
        with Session(engine) as db:
            try:service.transition(db,db.get(User,ids['owner']),a,results[0],status,0);return 200
            except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(transition,['suspended','revoked']))
    assert sorted(results)==[200,409]


def test_administrators_can_concurrently_add_each_other(membership_app):
    from threading import Barrier
    _,_,engine,ids,a,_=membership_app
    barrier=Barrier(2)
    def create(pair):
        actor_id,target_id=pair
        with Session(engine) as db:
            db.execute(select(User.id).where(User.id==actor_id).with_for_update(key_share=True)).scalar_one()
            barrier.wait(timeout=10)
            return service.create_membership(db,db.get(User,actor_id),a,target_id,str(uuid4()))[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(create,[(ids['owner'],ids['admin']),(ids['admin'],ids['owner'])]))==[True,True]


def test_cached_and_delayed_policy_never_grants_clinical_action(membership_app):
    client,_,engine,ids,a,b=membership_app
    row=add(client,ids,a);add(client,ids,b)
    with Session(engine) as stale:
        # Keep a loaded ORM instance to exercise identity-map staleness as well.
        cached = stale.get(ClinicMembership,row['id'])
        assert service.membership_decision(stale,ids['other'],a).allowed
        assert not service.membership_decision(stale,ids['other'],a,'clinical.prescribe').allowed
        assert change(client,ids,a,row,'revoked').status_code==200
        assert cached.status == 'active'
        assert not service.membership_decision(stale,ids['other'],a).allowed
        assert cached.status == 'revoked'
        assert service.membership_decision(stale,ids['other'],b).allowed


def test_membership_migration_is_additive_and_replay_safe(auth_engine):
    from app.db.base import Base
    from app.db.migrate import run_migrations
    schema='membership_replay_'+uuid4().hex
    with auth_engine.begin() as conn:conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine=create_engine(auth_engine.url,connect_args={'options':f'-csearch_path={schema}'})
    try:
        Base.metadata.create_all(engine);run_migrations(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql('DROP TABLE clinic_membership_events')
            conn.exec_driver_sql('DROP TABLE clinic_memberships')
            conn.execute(text("DELETE FROM schema_migrations WHERE version='20261120_clinic_memberships.sql'"))
            before=list(conn.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')))
        with Session(engine) as db:
            user=User(full_name='Legacy Principal',email='legacy@example.com',password_hash='unused',role='practitioner',is_active=True)
            db.add(user);db.flush()
            org=Organisation(display_name='Legacy Organisation',created_by_user_id=user.id,request_key=uuid4().hex)
            db.add(org);db.flush()
            clinic=Clinic(display_name='Legacy Clinic',organisation_id=org.id,created_by_user_id=user.id,request_key=uuid4().hex)
            db.add(clinic);db.commit();uid,cid,oid=user.id,clinic.id,org.id
        run_migrations(engine)
        with engine.begin() as conn:
            after=list(conn.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')))
            assert [r for r in after if r[0]!='20261120_clinic_memberships.sql']==before
            assert conn.execute(text('SELECT COUNT(*) FROM clinic_memberships')).scalar()==0
            assert conn.execute(text('SELECT id,organisation_id,created_by_user_id FROM clinics')).one()==(cid,oid,uid)
        run_migrations(engine)
        with engine.begin() as conn:
            assert list(conn.execute(text('SELECT version,applied_at FROM schema_migrations ORDER BY version')))==after
    finally:
        engine.dispose()
        with auth_engine.begin() as conn:conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
