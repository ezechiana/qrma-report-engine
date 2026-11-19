"""T-GO360-008: real PostgreSQL API, concurrency and additive migration rehearsal."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/synthetic")

from app.api.deps import get_current_user, get_db
from app.api.routes_clinics import router
from app.db.migrate import sql_statements
from app.db import migrate as migration_runner
from app.db.models import Clinic, ClinicRegistryEvent, Organisation, PractitionerSettings, User, UserCapability
from app.services import clinic_registry

MIGRATION = Path("app/db/migrations/20261119_tenant_organisations_clinics.sql")


@pytest.fixture
def registry(monkeypatch):
    url = os.getenv("GO360_CLINIC_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set GO360_CLINIC_TEST_DATABASE_URL to a disposable PostgreSQL database")
    schema = "clinic_test_" + uuid4().hex
    admin_engine = create_engine(url)
    with admin_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    User.metadata.create_all(engine, tables=[model.__table__ for model in (User, UserCapability, PractitionerSettings)])
    with Session(engine) as db:
        actors = [User(id=uuid4(), email=f"actor{i}@example.invalid", full_name=f"Synthetic {i}",
                       password_hash="unused", role="client" if i == 2 else "practitioner",
                       clinic_name="Same practice", email_verified_at=datetime.now(timezone.utc), is_active=True)
                  for i in range(3)]
        db.add_all(actors)
        db.commit()
        ids = [u.id for u in actors]
    monkeypatch.setattr(clinic_registry, "is_platform_admin_user", lambda db, actor: actor.role == "admin")
    try:
        yield engine, ids
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


def migrate(engine):
    with engine.begin() as conn:
        for statement in sql_statements(MIGRATION.read_text()):
            conn.exec_driver_sql(statement)


def client_for(engine, actor_id):
    app = FastAPI()
    app.include_router(router)
    def db_dependency():
        with Session(engine) as db:
            yield db
    def actor_dependency():
        with Session(engine) as db:
            return db.get(User, actor_id)
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = actor_dependency
    return TestClient(app)


def create_org(client, key=None, name="Synthetic organisation"):
    return client.post("/api/organisations", json={"display_name": name},
                       headers={"Idempotency-Key": str(key or uuid4())})


def test_same_names_distinct_ids_retry_and_changed_payload(registry):
    engine, ids = registry
    migrate(engine)
    with client_for(engine, ids[0]) as client:
        org = create_org(client).json()["id"]
        path = f"/api/organisations/{org}/clinics"
        key = str(uuid4())
        first = client.post(path, json={"display_name": "Same clinic"}, headers={"Idempotency-Key": key})
        retry = client.post(path, json={"display_name": "Same clinic"}, headers={"Idempotency-Key": key})
        second = client.post(path, json={"display_name": "Same clinic"}, headers={"Idempotency-Key": str(uuid4())})
        assert (first.status_code, retry.status_code, second.status_code) == (201, 200, 201)
        assert first.json() == retry.json()
        assert first.json()["id"] != second.json()["id"]
        assert client.post(path, json={"display_name": "Changed"}, headers={"Idempotency-Key": key}).status_code == 409
        assert len(client.get(path).json()) == 2
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(ClinicRegistryEvent).where(
            ClinicRegistryEvent.action == "clinic.create", ClinicRegistryEvent.outcome == "denied")) == 1


def test_organisation_retry_and_actor_key_scope(registry):
    engine, ids = registry
    migrate(engine)
    key = uuid4()
    with client_for(engine, ids[0]) as a, client_for(engine, ids[1]) as b:
        original = create_org(a, key)
        assert original.status_code == 201
        assert create_org(a, key).json() == original.json()
        assert create_org(b, key).json()["id"] != original.json()["id"]
        assert create_org(a, key, "Changed").status_code == 409


def test_cross_organisation_and_invalid_reference_have_no_partial_clinic(registry):
    engine, ids = registry
    migrate(engine)
    with client_for(engine, ids[0]) as owner, client_for(engine, ids[1]) as outsider:
        org = create_org(owner).json()["id"]
        for organisation in (org, str(uuid4())):
            path = f"/api/organisations/{organisation}/clinics"
            assert outsider.get(path).status_code == 404
            assert outsider.post(path, json={"display_name": "Forbidden"},
                                  headers={"Idempotency-Key": str(uuid4())}).status_code == 404
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Clinic)) == 2  # legacy mappings only


def test_discovery_scoped_to_owner_and_explicit_platform_admin(registry):
    engine, ids = registry
    migrate(engine)
    with client_for(engine, ids[0]) as a, client_for(engine, ids[1]) as b:
        own = a.get("/api/organisations").json()
        other = b.get("/api/organisations").json()
        assert len(own) == len(other) == 1
        assert own[0]["id"] != other[0]["id"]
        with Session(engine) as db:
            db.get(User, ids[1]).role = "admin"
            db.commit()
        assert len(b.get("/api/organisations").json()) == 2
        assert b.post(f'/api/organisations/{own[0]["id"]}/clinics', json={"display_name": "Admin provisioned"},
                      headers={"Idempotency-Key": str(uuid4())}).status_code == 201


def test_unauthenticated_request_rejected(registry):
    engine, ids = registry
    migrate(engine)
    with client_for(engine, ids[0]) as client:
        client.app.dependency_overrides.pop(get_current_user)
        assert create_org(client).status_code == 401


@pytest.mark.parametrize("condition", ["client", "inactive", "unverified"])
def test_ineligible_actor_denied(registry, condition):
    engine, ids = registry
    migrate(engine)
    actor_id = ids[2] if condition == "client" else ids[0]
    with Session(engine) as db:
        actor = db.get(User, actor_id)
        if condition == "inactive": actor.is_active = False
        if condition == "unverified": actor.email_verified_at = None
        db.commit()
    with client_for(engine, actor_id) as client:
        assert create_org(client).status_code == 403


def test_input_validation_and_immutable_identity(registry):
    engine, ids = registry
    migrate(engine)
    with client_for(engine, ids[0]) as client:
        assert create_org(client, name="   ").status_code == 422
        assert client.post("/api/organisations", json={"display_name": "Clinic"}).status_code == 422
        assert client.post("/api/organisations", json={"display_name": "Clinic", "id": str(uuid4())},
                           headers={"Idempotency-Key": str(uuid4())}).status_code == 422
    for table in ("organisations", "clinics"):
        with pytest.raises(DBAPIError, match="immutable"):
            with engine.begin() as conn:
                conn.exec_driver_sql(f"UPDATE {table} SET id = gen_random_uuid()")


def test_concurrent_retry_is_one_clinic_and_atomic_audit(registry):
    engine, ids = registry
    migrate(engine)
    with Session(engine) as db:
        org = db.scalar(select(Organisation).where(Organisation.legacy_owner_user_id == ids[0])).id
    key = str(uuid4())
    def create(_):
        with Session(engine) as db:
            row, created = clinic_registry.create_registry_entry(db, db.get(User, ids[0]),
                display_name="Concurrent", request_key=key, organisation_id=org)
            return row.id, created
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert results[0][0] == results[1][0]
    assert sum(created for _, created in results) == 1
    with Session(engine) as db:
        outcomes = list(db.scalars(select(ClinicRegistryEvent.outcome).where(ClinicRegistryEvent.clinic_id == results[0][0])))
        assert sorted(outcomes) == ["created", "replayed"]


def test_audit_failure_rolls_back_creation_and_safe_retry(registry, monkeypatch):
    engine, ids = registry
    migrate(engine)
    real_event = clinic_registry.event
    def fail(*args, **kwargs): raise RuntimeError("Synthetic audit failure")
    monkeypatch.setattr(clinic_registry, "event", fail)
    key = str(uuid4())
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="audit failure"):
            clinic_registry.create_registry_entry(db, db.get(User, ids[0]), display_name="Atomic", request_key=key)
        assert db.scalar(select(Organisation).where(Organisation.request_key == key)) is None
        monkeypatch.setattr(clinic_registry, "event", real_event)
        _, created = clinic_registry.create_registry_entry(db, db.get(User, ids[0]), display_name="Atomic", request_key=key)
        assert created


def test_migration_replay_and_transactional_rollback(registry):
    engine, ids = registry
    # First rehearsal rolls back schema and mapping together.
    with engine.connect() as conn:
        tx = conn.begin()
        for statement in sql_statements(MIGRATION.read_text()): conn.exec_driver_sql(statement)
        assert conn.scalar(text("SELECT count(*) FROM clinics")) == 2
        tx.rollback()
        assert conn.scalar(text("SELECT to_regclass('clinics')")) is None
    migrate(engine)
    with engine.connect() as conn:
        before = conn.execute(text("SELECT id, organisation_id, legacy_owner_user_id, created_at FROM clinics ORDER BY id")).all()
    migrate(engine)
    with engine.connect() as conn:
        after = conn.execute(text("SELECT id, organisation_id, legacy_owner_user_id, created_at FROM clinics ORDER BY id")).all()
        assert before == after
        assert {row.legacy_owner_user_id for row in after} == set(ids[:2])
        assert conn.scalar(text("SELECT count(*) FROM clinic_registry_events")) == 2
        assert conn.scalar(text("SELECT count(DISTINCT display_name) FROM clinics")) == 1


def test_existing_ledger_preserved_and_new_migration_recorded_once(registry, monkeypatch, tmp_path):
    engine, _ = registry
    (tmp_path / MIGRATION.name).write_text(MIGRATION.read_text())
    monkeypatch.setattr(migration_runner, "MIGRATIONS_DIR", tmp_path)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())")
        conn.exec_driver_sql("INSERT INTO schema_migrations (version) VALUES ('20261118_clinic_associations.sql')")
        original = conn.execute(text("SELECT version, applied_at FROM schema_migrations")).one()
    migration_runner.run_migrations(engine)
    migration_runner.run_migrations(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version, applied_at FROM schema_migrations WHERE version = '20261118_clinic_associations.sql'")).one() == original
        assert conn.scalar(text("SELECT count(*) FROM schema_migrations")) == 2
        assert conn.scalar(text("SELECT count(*) FROM clinics")) == 2
