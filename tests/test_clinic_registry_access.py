"""GO3-188: registry page/API parity without broadening clinical page access."""
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/synthetic_registry_access")

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine, delete, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.routes_clinics import router
from app.db.models import Clinic, ClinicRegistryEvent, Organisation, User, UserCapability


@compiles(JSONB, "sqlite")
def compile_json(type_, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def access_app(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", "")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models = (User, UserCapability, Organisation, Clinic, ClinicRegistryEvent)
    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                monkeypatch.setattr(column, "type", Uuid(as_uuid=True))
    User.metadata.create_all(engine, tables=[m.__table__ for m in models])
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    actors = {}
    with Session(engine) as db:
        for name, role in (("client", "client"), ("capable", "client"), ("practitioner", "practitioner"),
                           ("admin", "admin"), ("allowlisted", "client"), ("unverified", "practitioner"),
                           ("inactive", "practitioner")):
            actor = User(id=uuid4(), email=name+"@example.invalid", full_name=name, password_hash="unused",
                         role=role, is_active=name!="inactive",
                         email_verified_at=None if name=="unverified" else datetime.now(timezone.utc))
            db.add(actor)
            actors[name] = actor.id
        db.flush()
        db.add(UserCapability(user_id=actors["capable"], capability="practitioner"))
        db.execute(text("INSERT INTO app_settings VALUES ('platform_config', :value)"),
                   {"value": json.dumps({"admin_emails": "allowlisted@example.invalid"})})
        db.commit()
    holder = {"actor": "capable"}
    app = FastAPI()
    app.include_router(router)

    def db_dependency():
        with Session(engine) as db:
            yield db

    def resolve(db, token):
        if token != "synthetic-access":
            raise HTTPException(401, "Invalid synthetic token")
        return db.get(User, actors[holder["actor"]])

    # The normal get_current_user dependency and registry authority execute.
    # Only token decoding is replaced, so this is not a sign-in/JWT test.
    monkeypatch.setattr(deps, "get_current_user_from_token", resolve)
    app.dependency_overrides[deps.get_db] = db_dependency

    def protected(actor=Depends(deps.get_current_user)):
        return {"actor": str(actor.id)}

    for path in ("/app/organisations", "/app/organisations/other", "/app/organisations-extra",
                 "/app/patients", "/api/patients", "/api/reports", "/api/share-links", "/app/settings"):
        app.add_api_route(path, protected, methods=["GET", "HEAD", "POST"])
    try:
        with TestClient(app) as client:
            yield client, holder, engine, actors
    finally:
        engine.dispose()


@pytest.mark.parametrize("actor, expected", [("client",403),("capable",200),("practitioner",200),
    ("admin",200),("allowlisted",200),("unverified",403),("inactive",403)])
def test_page_api_use_the_same_live_authority(access_app, actor, expected):
    client, holder, _, _ = access_app
    holder["actor"] = actor
    headers = {"Authorization": "Bearer synthetic-access"}
    assert client.get("/app/organisations", headers=headers).status_code == expected
    assert client.get("/api/organisations", headers=headers).status_code == expected


@pytest.mark.parametrize("path", ["/app/organisations/other", "/app/organisations-extra", "/app/patients",
    "/api/patients", "/api/reports", "/api/share-links", "/app/settings"])
def test_capable_client_does_not_gain_other_practitioner_paths(access_app, path):
    client, _, _, _ = access_app
    assert client.get(path, headers={"Authorization": "Bearer synthetic-access"}).status_code == 403


def test_only_safe_page_methods_are_excepted(access_app):
    client, _, _, _ = access_app
    headers = {"Authorization": "Bearer synthetic-access"}
    assert client.head("/app/organisations", headers=headers).status_code == 200
    assert client.post("/app/organisations", headers=headers).status_code == 403


def test_capability_revocation_is_rechecked(access_app):
    client, _, engine, actors = access_app
    headers = {"Authorization": "Bearer synthetic-access"}
    assert client.get("/app/organisations", headers=headers).status_code == 200
    with Session(engine) as db:
        db.execute(delete(UserCapability).where(UserCapability.user_id == actors["capable"]))
        db.commit()
    assert client.get("/app/organisations", headers=headers).status_code == 403
    assert client.get("/api/organisations", headers=headers).status_code == 403


def test_effective_actor_not_original_admin_determines_access(access_app):
    client, holder, _, actors = access_app
    headers = {"Authorization": "Bearer synthetic-access", "X-Original-Admin": str(actors["admin"])}
    holder["actor"] = "client"
    assert client.get("/app/organisations", headers=headers).status_code == 403
    holder["actor"] = "capable"
    assert client.get("/app/organisations", headers=headers).status_code == 200


def test_anonymous_and_invalid_tokens_remain_rejected(access_app):
    client, _, _, _ = access_app
    assert client.get("/app/organisations").status_code == 401
    assert client.get("/app/organisations", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_cookie_session_uses_the_same_gate(access_app):
    client, holder, _, _ = access_app
    client.cookies.set("access_token", "synthetic-access")
    assert client.get("/app/organisations").status_code == 200
    holder["actor"] = "client"
    assert client.get("/app/organisations").status_code == 403


def test_saved_allowlist_revocation_is_rechecked(access_app):
    client, holder, engine, _ = access_app
    holder["actor"] = "allowlisted"
    headers = {"Authorization": "Bearer synthetic-access"}
    assert client.get("/app/organisations", headers=headers).status_code == 200
    with engine.begin() as conn:
        conn.execute(text("UPDATE app_settings SET value = :value"), {"value": '{"admin_emails": "other@example.invalid"}'})
    assert client.get("/app/organisations", headers=headers).status_code == 403
    assert client.get("/api/organisations", headers=headers).status_code == 403


def test_page_selection_does_not_grant_organisation_authority(access_app):
    client, _, engine, actors = access_app
    with Session(engine) as db:
        org = Organisation(display_name="Other practice", created_by_user_id=actors["practitioner"], request_key=str(uuid4()))
        db.add(org)
        db.commit()
        org_id = org.id
    headers = {"Authorization": "Bearer synthetic-access"}
    assert client.get(f"/app/organisations?organisation={org_id}", headers=headers).status_code == 200
    assert client.get(f"/api/organisations/{org_id}/clinics", headers=headers).status_code == 404
