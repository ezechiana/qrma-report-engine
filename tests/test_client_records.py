"""A signed-in client can return only to records they have claimed."""

import os
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_client_records, routes_share
from app.api.deps import _client_blocked_from_practitioner_path, get_current_user, get_db
from app.db.models import (
    Case,
    ContentRelease,
    Patient,
    PatientSource,
    ReportStatus,
    ReportVersion,
    ShareLink,
    SourceRecord,
    User,
)


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_client_record_route_is_not_a_practitioner_route():
    assert _client_blocked_from_practitioner_path(User(role="client"), "/api/patients")
    assert _client_blocked_from_practitioner_path(User(role="client"), "/api/client/records") is False
    paths = [route.path for route in routes_share.router.routes]
    assert "/api/client/records" not in paths
    assert "/share/{token}/trend-data" in paths


def test_signed_in_client_sees_only_the_claimed_client_safe_record(monkeypatch):
    calls = []

    def fake_trends(**kwargs):
        calls.append(kwargs)
        return {
            "view_mode": "client",
            "client_view": {
                "summary": "Progress is ready to review.",
                "progress_cards": [{"label": "Overall trend", "value": "Stable", "detail": "Holding steady."}],
                "top_priorities": [],
                "recommendations": [{"name": "Discussion item", "summary": "Talk with your practitioner."}],
                "review_note": "Client-safe summary.",
            },
            "health_index": [{"value": 70}],
            "trend_summary": [
                {"label": "Energy", "is_meaningful": True, "delta": 1},
                {"label": "Hidden noise", "is_meaningful": False, "delta": 0},
            ],
            "trend_intelligence": {
                "confidence": {"label": "Moderate"},
                "insights": [{"title": "Steady"}],
                "clinical_narrative": {"headline": "Progress is ready to review."},
            },
            "trend_recommendations": {"products": [{"name": "Practitioner control"}]},
            "markers": {"secret_marker": []},
            "systems": {"secret_system": []},
        }

    monkeypatch.setattr("app.api.routes_patients.get_patient_trends", fake_trends)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    models = (User, Patient, PatientSource, SourceRecord, Case, ReportVersion, ShareLink, ContentRelease)
    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
    try:
        for model in models:
            model.__table__.create(engine)
        db = Session(engine)
        practitioner = User(
            id=uuid.uuid4(),
            email="clinic@example.com",
            full_name="Clinic Practitioner",
            clinic_name="North Clinic",
            password_hash="unused",
            is_active=True,
            role="practitioner",
        )
        client_user = User(
            id=uuid.uuid4(),
            email="ada@example.com",
            full_name="Ada Lovelace",
            clinic_name=None,
            password_hash="unused",
            is_active=True,
            role="client",
            email_verified_at=datetime.now(timezone.utc),
        )
        other_client = User(
            id=uuid.uuid4(),
            email="grace@example.com",
            full_name="Grace Hopper",
            password_hash="unused",
            is_active=True,
            role="client",
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add_all([practitioner, client_user, other_client])
        db.commit()
        claimed = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
            notes="Private practitioner note",
            claimed_by_user_id=client_user.id,
            claimed_at=datetime.now(timezone.utc),
        )
        other = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Grace",
            last_name="Hopper",
            full_name="Grace Hopper",
            notes="Another private note",
            claimed_by_user_id=other_client.id,
            claimed_at=datetime.now(timezone.utc),
        )
        db.add_all([claimed, other])
        db.commit()
        released_case = Case(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            patient_id=claimed.id,
            title="Released scan",
            status="draft",
        )
        hidden_case = Case(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            patient_id=claimed.id,
            title="Unreleased scan",
            status="draft",
        )
        db.add_all([released_case, hidden_case])
        db.commit()
        released = ReportVersion(
            id=uuid.uuid4(),
            case_id=released_case.id,
            created_by_user_id=practitioner.id,
            version_number=1,
            status=ReportStatus.ready,
            recommendation_mode="natural_approaches_clinical",
            report_type="assessment",
            is_archived=False,
        )
        hidden = ReportVersion(
            id=uuid.uuid4(),
            case_id=hidden_case.id,
            created_by_user_id=practitioner.id,
            version_number=1,
            status=ReportStatus.ready,
            recommendation_mode="natural_approaches_clinical",
            report_type="assessment",
            is_archived=False,
        )
        db.add_all([released, hidden])
        db.commit()
        db.add(ShareLink(
            id=uuid.uuid4(),
            report_version_id=released.id,
            patient_id=claimed.id,
            token="released-share",
            is_active=True,
            share_type="report",
            expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        ))
        db.add(ShareLink(
            id=uuid.uuid4(),
            report_version_id=hidden.id,
            patient_id=claimed.id,
            token="revoked-share",
            is_active=False,
            share_type="report",
            expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        ))
        db.commit()

        app = FastAPI()
        app.include_router(routes_client_records.router)
        holder = {"user": client_user}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        listed = http.get("/api/client/records")
        assert listed.status_code == 200
        assert listed.json() == {
            "records": [{
                "patient_id": str(claimed.id),
                "display_name": "Ada Lovelace",
                "clinic_name": "North Clinic",
            }]
        }
        assert "Grace Hopper" not in listed.text
        assert "Private practitioner note" not in listed.text

        held = http.get(f"/api/client/records/{claimed.id}")
        assert held.status_code == 200
        assert held.json()["trend"]["client_view"]["summary"] == (
            "Your practitioner has not released a progress view yet."
        )
        assert calls == []
        assert "released-share" not in held.text
        assert "Private practitioner note" not in held.text

        db.add(ContentRelease(
            id=uuid.uuid4(),
            patient_id=claimed.id,
            practitioner_user_id=practitioner.id,
            source_key="progress_view",
            direction="clinic_to_client",
            released=True,
            released_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()

        opened = http.get(f"/api/client/records/{claimed.id}")
        assert opened.status_code == 200
        body = opened.json()
        assert body["viewer_mode"] == "patient"
        assert body["patient"] == {"id": str(claimed.id), "display_name": "Ada Lovelace"}
        assert body["tenant"] == {"tenant_name": "North Clinic"}
        assert body["trend"]["view_mode"] == "client"
        assert body["trend"]["client_view"]["summary"] == "Progress is ready to review."
        assert body["trend"]["trend_summary"] == [{"label": "Energy", "is_meaningful": True, "delta": 1}]
        assert "trend_recommendations" not in body["trend"]
        assert "markers" not in body["trend"]
        assert "recommendation_mode" not in opened.text
        assert "payment" not in opened.text
        assert "Private practitioner note" not in opened.text
        assert calls[0]["view_mode"] == "client"
        assert calls[0]["current_user"].id == practitioner.id
        assert calls[0]["source_report_ids"] is None

        withdrawn = db.query(ContentRelease).one()
        withdrawn.released = False
        withdrawn.withdrawn_at = datetime.now(timezone.utc)
        db.commit()
        hidden_again = http.get(f"/api/client/records/{claimed.id}")
        assert hidden_again.status_code == 200
        assert hidden_again.json()["trend"]["client_view"]["summary"] == (
            "Your practitioner has not released a progress view yet."
        )
        assert len(calls) == 1

        denied = http.get(f"/api/client/records/{other.id}")
        assert denied.status_code == 404

        holder["user"] = practitioner
        blocked = http.get("/api/client/records")
        assert blocked.status_code == 403

        holder["user"] = client_user
        returned = http.get(f"/api/client/records/{claimed.id}")
        assert returned.status_code == 200
        assert returned.json()["patient"]["id"] == str(claimed.id)
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
