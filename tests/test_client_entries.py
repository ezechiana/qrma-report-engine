"""A claimed client can send a plain-text entry to the clinic."""

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
    SourceObservation,
    SourceRecord,
    User,
)
from app.services.content_release_service import _as_report_id


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_claimed_client_can_send_an_entry_to_the_clinic(monkeypatch):
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
        SourceObservation,
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
        other_client = User(
            id=uuid.uuid4(),
            email="grace@example.com",
            full_name="Grace Hopper",
            password_hash="unused",
            is_active=True,
            role="client",
            email_verified_at=datetime.now(timezone.utc),
        )
        unverified = User(
            id=uuid.uuid4(),
            email="new@example.com",
            full_name="New Client",
            password_hash="unused",
            is_active=True,
            role="client",
        )
        db.add_all([practitioner, client_user, other_client, unverified])
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
        unclaimed = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Other",
            last_name="Person",
            full_name="Other Person",
        )
        db.add_all([patient, unclaimed])
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
            token="entry-share-token",
            is_active=True,
            share_type="report",
            patient_id=patient.id,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(link)
        db.commit()

        app = FastAPI()
        app.include_router(routes_patient_sources.router)
        app.include_router(routes_content_releases.router)
        app.include_router(routes_client_records.router)
        holder = {"user": practitioner}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        sources = http.get(f"/api/patients/{patient.id}/sources").json()["sources"]
        note_source = sources[0]["source_id"]
        lab_source = sources[1]["source_id"]
        note = http.post(
            f"/api/patients/{patient.id}/sources/{note_source}/records",
            json={
                "title": "Visit note",
                "recorded_on": "2026-11-01",
                "text": "Private clinic wording",
                "client_summary": "A short note for the client",
            },
        )
        assert note.status_code == 200
        note_id = note.json()["record_id"]
        assert http.post(
            f"/api/patients/{patient.id}/sources/{note_source}/records/{note_id}/release",
            json={"released": True},
        ).status_code == 200
        panel = http.post(
            f"/api/patients/{patient.id}/sources/{lab_source}/panels",
            json={
                "title": "June bloods",
                "recorded_on": "2026-06-12",
                "observations": [{"name": "Haemoglobin", "value_numeric": 140, "unit": "g/L"}],
            },
        )
        assert panel.status_code == 200
        assert http.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": True},
        ).status_code == 200
        clinic_sources = http.get(f"/api/patients/{patient.id}/sources").json()
        assert [item["display_name"] for item in clinic_sources["sources"]] == ["Clinic notes", "Lab results"]
        progress = http.get(f"/api/patients/{patient.id}/progress-release").json()
        reports = http.get(f"/api/patients/{patient.id}/report-releases").json()
        clinic_release_count = (
            db.query(ContentRelease)
            .filter(ContentRelease.direction == "clinic_to_client")
            .count()
        )

        holder["user"] = client_user
        sent = http.post(
            f"/api/client/records/{patient.id}/entries",
            json={
                "title": "  Sleep note  ",
                "recorded_on": "2026-11-02",
                "text": "  I slept poorly.  ",
                "reference": "  home-sleep-note.pdf  ",
            },
        )
        assert sent.status_code == 200
        body = sent.json()
        assert set(body) == {"record_id", "title", "recorded_on", "released"}
        assert body["title"] == "Sleep note"
        assert body["recorded_on"] == "2026-11-02"
        assert body["released"] is True
        assert "I slept poorly." not in sent.text
        assert "home-sleep-note.pdf" not in sent.text
        record_id = body["record_id"]

        earlier = http.post(
            f"/api/client/records/{patient.id}/entries",
            json={
                "title": "Earlier day",
                "recorded_on": "2026-10-01",
                "text": "No reference here",
            },
        )
        assert earlier.status_code == 200
        assert earlier.json()["released"] is True

        listed = http.get(f"/api/client/records/{patient.id}/entries")
        assert listed.status_code == 200
        entries = listed.json()["entries"]
        assert [item["title"] for item in entries] == ["Sleep note", "Earlier day"]
        assert set(entries[0]) == {"record_id", "title", "recorded_on", "text", "reference", "released"}
        assert entries[0]["record_id"] == record_id
        assert entries[0]["text"] == "I slept poorly."
        assert entries[0]["reference"] == "home-sleep-note.pdf"
        assert entries[0]["released"] is True
        assert entries[1]["reference"] == ""
        assert entries[1]["text"] == "No reference here"
        for hidden in ("Haemoglobin", "Private clinic wording", "payload_json", "observations", "g/L"):
            assert hidden not in listed.text

        portal = http.get(f"/api/client/records/{patient.id}")
        assert portal.status_code == 200
        assert portal.json()["trend"]["client_view"]["summary"] == "Progress is ready to review."
        assert [group["display_name"] for group in portal.json()["sources"]] == ["Clinic notes"]
        assert portal.json()["sources"][0]["records"][0]["title"] == "Visit note"
        assert portal.json()["reports"]["assessment_reports"] == []
        opened = http.get(f"/api/client/records/{patient.id}/notes/{record_id}")
        assert opened.status_code == 404
        for hidden in (
            "I slept poorly.",
            "home-sleep-note.pdf",
            "No reference here",
            "From the client",
            "Haemoglobin",
            "Private clinic wording",
            "payload_json",
            "client_entry",
            "observations",
        ):
            assert hidden not in portal.text
            assert hidden not in opened.text

        stored = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(record_id)).one()
        assert stored.client_summary is None
        assert stored.created_by_user_id == client_user.id
        assert stored.payload_json == {
            "kind": "client_entry",
            "text": "I slept poorly.",
            "reference": "home-sleep-note.pdf",
        }
        assert (
            db.query(SourceObservation)
            .filter(SourceObservation.source_record_id == stored.id)
            .count()
            == 0
        )
        release = db.query(ContentRelease).filter(ContentRelease.source_key == f"client:{record_id}").one()
        assert release.direction == "client_to_clinic"
        assert release.released is True
        assert release.practitioner_user_id == practitioner.id
        assert release.patient_id == patient.id
        assert _as_report_id(release.source_key) is None
        plain = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(earlier.json()["record_id"])).one()
        assert plain.payload_json == {"kind": "client_entry", "text": "No reference here"}
        shelf = db.query(PatientSource).filter(PatientSource.source_type == "client").one()
        assert shelf.display_name == "From the client"
        assert shelf.patient_id == patient.id

        record_count = db.query(SourceRecord).count()
        rejected = http.post(
            f"/api/client/records/{patient.id}/entries",
            json={"title": "   ", "recorded_on": "2026-11-03", "text": "Hello"},
        )
        assert rejected.status_code == 422
        assert db.query(SourceRecord).count() == record_count
        holder["user"] = other_client
        assert http.get(f"/api/client/records/{patient.id}/entries").status_code == 404
        assert http.post(
            f"/api/client/records/{patient.id}/entries",
            json={"title": "Nope", "recorded_on": "2026-11-03", "text": "Hidden"},
        ).status_code == 404
        assert "I slept poorly." not in http.get(f"/api/client/records/{patient.id}/entries").text
        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{unclaimed.id}/entries",
            json={"title": "Nope", "recorded_on": "2026-11-03", "text": "Hidden"},
        ).status_code == 404
        holder["user"] = unverified
        assert http.get(f"/api/client/records/{patient.id}/entries").status_code == 403
        holder["user"] = practitioner
        denied = http.post(
            f"/api/client/records/{patient.id}/entries",
            json={"title": "Nope", "recorded_on": "2026-11-03", "text": "Hidden"},
        )
        assert denied.status_code == 403
        assert db.query(SourceRecord).count() == record_count

        clinic = http.get(f"/api/patients/{patient.id}/sources")
        assert clinic.status_code == 200
        clinic_body = clinic.json()
        assert [item["display_name"] for item in clinic_body["sources"]] == [
            "Clinic notes",
            "Lab results",
            "From the client",
        ]
        assert clinic_body["sources"][0] == clinic_sources["sources"][0]
        assert clinic_body["sources"][1] == clinic_sources["sources"][1]
        group = clinic_body["sources"][2]
        assert group["source_type"] == "client"
        assert group["display_name"] == "From the client"
        assert [item["title"] for item in group["records"]] == ["Sleep note", "Earlier day"]
        assert set(group["records"][0]) == {"record_id", "title", "recorded_on", "text", "reference"}
        assert group["records"][0]["record_id"] == record_id
        assert group["records"][0]["recorded_on"] == "2026-11-02"
        assert group["records"][0]["text"] == "I slept poorly."
        assert group["records"][0]["reference"] == "home-sleep-note.pdf"
        assert set(group["records"][1]) == {"record_id", "title", "recorded_on", "text"}
        assert group["records"][1]["text"] == "No reference here"
        source_id = group["source_id"]
        assert http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/records",
            json={"title": "Changed", "recorded_on": "2026-11-04", "text": "Edited", "client_summary": ""},
        ).status_code == 404
        assert http.patch(
            f"/api/patients/{patient.id}/sources/{source_id}/records/{record_id}",
            json={"title": "Changed"},
        ).status_code == 404
        assert http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/records/{record_id}/release",
            json={"released": False},
        ).status_code == 404
        assert http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Changed",
                "recorded_on": "2026-11-04",
                "observations": [{"name": "ALT", "value_text": "High"}],
            },
        ).status_code == 404
        holder["user"] = client_user
        assert _client_blocked_from_practitioner_path(client_user, f"/api/patients/{patient.id}/sources") is True
        blocked = http.get(f"/api/patients/{patient.id}/sources")
        assert blocked.status_code == 404
        assert "I slept poorly." not in blocked.text
        holder["user"] = practitioner
        for row in db.query(ContentRelease).filter(ContentRelease.direction == "client_to_clinic"):
            row.released = False
        db.commit()
        hidden = http.get(f"/api/patients/{patient.id}/sources")
        assert [item["display_name"] for item in hidden.json()["sources"]] == ["Clinic notes", "Lab results"]
        assert "I slept poorly." not in hidden.text
        assert "home-sleep-note.pdf" not in hidden.text
        assert "No reference here" not in hidden.text
        holder["user"] = client_user
        own = http.get(f"/api/client/records/{patient.id}/entries")
        assert own.status_code == 200
        assert own.json()["entries"][0]["text"] == "I slept poorly."
        assert own.json()["entries"][0]["released"] is False
        holder["user"] = practitioner
        assert http.get(f"/api/patients/{patient.id}/progress-release").json()["released"] is True
        assert progress["released"] is True
        assert http.get(f"/api/patients/{patient.id}/report-releases").json() == reports
        assert reports["assessment_reports"][0]["released"] is False
        db.refresh(link)
        assert link.token == "entry-share-token"
        assert link.is_active is True
        assert db.query(ShareLink).count() == 1
        assert (
            db.query(ContentRelease)
            .filter(ContentRelease.direction == "clinic_to_client")
            .count()
            == clinic_release_count
        )
        assert db.query(ContentRelease).filter(ContentRelease.direction == "client_to_clinic").count() == 2
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
