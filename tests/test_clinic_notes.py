"""Clinic notes stay on the patient record and off the client portal."""

import inspect
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

from app.api import routes_client_records, routes_content_releases, routes_patient_sources
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
from app.services import patient_source_service
from app.services.content_release_service import set_progress_release


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_clinic_note_routes_stay_off_the_client_portal():
    source = inspect.getsource(patient_source_service)
    assert "ShareLink" not in source
    assert "ContentRelease" not in source
    client = User(role="client")
    assert _client_blocked_from_practitioner_path(client, "/api/patients/1/sources") is True
    page = open("app/templates/patient_detail.html", encoding="utf-8").read()
    assert 'id="sources-section"' in page
    assert "Clinic notes" in page
    assert "Client summary" in page
    assert "Save note" in page
    assert "Release progress view" in page
    assert "/sources/" in page
    assert "record-releases" not in page


def test_practitioner_can_add_a_private_clinic_note(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_patients.get_patient_trends",
        lambda **kwargs: {
            "client_view": {"summary": "Progress is ready to review."},
            "health_index": [],
            "trend_summary": [],
            "trend_intelligence": {},
        },
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    models = (
        User,
        Patient,
        PatientSource,
        SourceRecord,
        Case,
        ReportVersion,
        ShareLink,
        ContentRelease,
    )
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
            password_hash="unused",
            is_active=True,
            role="client",
            email_verified_at=datetime.now(timezone.utc),
        )
        other = User(
            id=uuid.uuid4(),
            email="other@example.com",
            full_name="Other Practitioner",
            password_hash="unused",
            is_active=True,
            role="practitioner",
        )
        db.add_all([practitioner, client_user, other])
        db.commit()
        patient = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
            claimed_by_user_id=client_user.id,
            claimed_at=datetime.now(timezone.utc),
        )
        db.add(patient)
        db.commit()
        case = Case(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            patient_id=patient.id,
            title="Morning scan",
            status="draft",
        )
        db.add(case)
        db.commit()
        report = ReportVersion(
            id=uuid.uuid4(),
            case_id=case.id,
            created_by_user_id=practitioner.id,
            version_number=1,
            status=ReportStatus.ready,
            recommendation_mode="natural_approaches_clinical",
            report_type="assessment",
            is_archived=False,
        )
        db.add(report)
        db.commit()
        link = ShareLink(
            id=uuid.uuid4(),
            report_version_id=report.id,
            token="note-share-token",
            is_active=True,
            share_type="report",
            patient_id=patient.id,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(link)
        db.commit()
        set_progress_release(db, patient, True)
        released_reports = db.query(ContentRelease).count()

        app = FastAPI()
        app.include_router(routes_patient_sources.router)
        app.include_router(routes_client_records.router)
        app.include_router(routes_content_releases.router)
        holder = {"user": practitioner}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        listed = http.get(f"/api/patients/{patient.id}/sources")
        assert listed.status_code == 200
        body = listed.json()
        assert body["sources"][0]["display_name"] == "Clinic notes"
        assert body["sources"][0]["source_type"] == "manual"
        assert body["sources"][0]["records"] == []
        source_id = body["sources"][0]["source_id"]
        again = http.get(f"/api/patients/{patient.id}/sources")
        assert again.json()["sources"][0]["source_id"] == source_id

        empty_summary = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/records",
            json={
                "title": "Morning observation",
                "recorded_on": "2026-11-01",
                "text": "Private raw observation",
                "client_summary": "",
            },
        )
        assert empty_summary.status_code == 200
        assert empty_summary.json()["client_summary"] == ""
        assert empty_summary.json()["text"] == "Private raw observation"
        assert "payload_json" not in empty_summary.text

        saved = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/records",
            json={
                "title": "Follow up",
                "recorded_on": "2026-11-02",
                "text": "Second raw note",
                "client_summary": "A short summary for later",
            },
        )
        assert saved.status_code == 200
        notes = http.get(f"/api/patients/{patient.id}/sources").json()["sources"][0]["records"]
        assert [item["title"] for item in notes] == ["Follow up", "Morning observation"]
        assert notes[1]["client_summary"] == ""
        assert notes[0]["client_summary"] == "A short summary for later"

        missing = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/records",
            json={"title": " ", "recorded_on": "2026-11-03", "text": "x", "client_summary": ""},
        )
        assert missing.status_code == 422

        holder["user"] = other
        denied = http.get(f"/api/patients/{patient.id}/sources")
        assert denied.status_code == 404
        denied_post = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/records",
            json={
                "title": "Nope",
                "recorded_on": "2026-11-04",
                "text": "Hidden",
                "client_summary": "Hidden summary",
            },
        )
        assert denied_post.status_code == 404

        holder["user"] = client_user
        portal = http.get(f"/api/client/records/{patient.id}")
        assert portal.status_code == 200
        portal_text = portal.text
        assert "Private raw observation" not in portal_text
        assert "Morning observation" not in portal_text
        assert "A short summary for later" not in portal_text
        assert "Second raw note" not in portal_text
        assert "sources" not in portal.json()

        holder["user"] = practitioner
        progress = http.get(f"/api/patients/{patient.id}/progress-release")
        assert progress.status_code == 200
        assert progress.json()["released"] is True
        reports = http.get(f"/api/patients/{patient.id}/report-releases")
        assert reports.json()["assessment_reports"][0]["released"] is False
        db.refresh(link)
        assert link.token == "note-share-token"
        assert link.is_active is True
        assert db.query(ShareLink).count() == 1
        assert db.query(ContentRelease).count() == released_reports
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
