"""Reproducible synthetic fixtures for the S0 baseline."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/s0_synthetic")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_content_releases, routes_reports
from app.api.deps import get_current_user, get_db
from app.db.models import Case, ContentRelease, Patient, ReportStatus, ReportVersion, ShareLink, User

SCENARIO = json.loads(Path(__file__).with_name("scenario.json").read_text())
FIXED_TIME = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)


def fixture_id(key):
    return uuid5(NAMESPACE_URL, "https://example.invalid/go360/s0/v1/" + key)


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return self.impl.python_type(str(value)) if value is not None else None


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def two_practices(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models = (User, Patient, Case, ReportVersion, ContentRelease, ShareLink)
    originals = []
    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
    db = Session(engine)
    try:
        for model in models:
            model.__table__.create(engine)
        actors = {}
        for key in ("owner_a", "owner_b", "shared_practitioner", "outsider", "client_a", "client_b"):
            actors[key] = User(
                id=fixture_id(key), email=key + "@example.invalid", full_name=key,
                password_hash="synthetic-unused", role="client" if key.startswith("client") else "practitioner",
                is_active=True, email_verified_at=FIXED_TIME,
            )
            db.add(actors[key])
        db.flush()
        charts, reports, shares = {}, {}, {}
        for clinic in SCENARIO["clinics"]:
            key = clinic["key"]
            suffix = key[-1]
            owner = actors[clinic["owner"]]
            owner.clinic_name = clinic["name"]
            patient = Patient(
                id=fixture_id(clinic["patient"]), user_id=owner.id,
                full_name=clinic["patient_name"], first_name=clinic["patient_name"].split()[0],
                last_name="Morgan", claimed_by_user_id=actors["client_" + suffix].id,
                claimed_at=FIXED_TIME,
            )
            case = Case(
                id=fixture_id(key + "_case"), user_id=owner.id, patient_id=patient.id,
                title=key + " QRMA", status="draft", scan_datetime=FIXED_TIME,
            )
            report = ReportVersion(
                id=fixture_id(key + "_report"), case_id=case.id, created_by_user_id=owner.id,
                version_number=1, status=ReportStatus.ready, report_type="assessment",
                recommendation_mode="natural_approaches_clinical", is_archived=False,
                generated_at=FIXED_TIME, html_path=key + "/report.html", pdf_path=key + "/report.pdf",
                report_json={"fixture": key}, build_version="s0-fixture-v1",
            )
            share = ShareLink(
                id=fixture_id(key + "_share"), report_version_id=report.id, patient_id=patient.id,
                token="synthetic-share-" + key, share_type="report", is_active=True,
            )
            db.add_all([patient, case, report, share])
            charts[key], reports[key], shares[key] = patient, report, share
        db.commit()
        app = FastAPI()
        app.include_router(routes_reports.router)
        app.include_router(routes_content_releases.router)
        actor = {"user": actors["owner_a"]}
        @app.middleware("http")
        async def synthetic_session(request, call_next):
            from app.services.auth_service import build_auth_response
            token = build_auth_response(actor["user"])["access_token"]
            request.scope["headers"] = [(k,v) for k,v in request.scope["headers"] if k.lower() != b"authorization"] + [(b"authorization", ("Bearer " + token).encode())]
            return await call_next(request)
        app.dependency_overrides[get_current_user] = lambda: actor["user"]
        app.dependency_overrides[get_db] = lambda: db
        monkeypatch.setattr(routes_reports, "object_exists", lambda key: True)
        monkeypatch.setattr(routes_reports, "generate_presigned_url", lambda key: "https://example.invalid/" + key)
        with TestClient(app) as http:
            yield SimpleNamespace(
                db=db, actors=actors, charts=charts, reports=reports, shares=shares,
                actor=actor, http=http, scenario=SCENARIO,
            )
    finally:
        db.close()
        for column, original in originals:
            column.type = original
        engine.dispose()
