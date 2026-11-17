"""A claimed client can connect one Wearables shelf."""

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


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_claimed_client_can_connect_one_wearable_shelf(monkeypatch):
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
        other_record = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Second",
            last_name="Record",
            full_name="Second Record",
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
        db.add_all([patient, other_record, unclaimed])
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
            token="wearable-share-token",
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
                "client_summary": "Blood results are ready",
                "observations": [{"name": "Haemoglobin", "value_numeric": 140, "unit": "g/L"}],
            },
        )
        assert panel.status_code == 200
        panel_id = panel.json()["record_id"]
        assert http.post(
            f"/api/patients/{patient.id}/sources/{lab_source}/panels/{panel_id}/release",
            json={"released": True},
        ).status_code == 200
        assert http.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": True},
        ).status_code == 200

        holder["user"] = client_user
        sent = http.post(
            f"/api/client/records/{patient.id}/entries",
            json={
                "title": "Sleep note",
                "recorded_on": "2026-11-02",
                "text": "I slept poorly.",
                "reference": "home-sleep-note.pdf",
            },
        )
        assert sent.status_code == 200

        before_sources = http.get(f"/api/client/records/{patient.id}").json()
        before_entries = http.get(f"/api/client/records/{patient.id}/entries").json()
        absent = http.get(f"/api/client/records/{patient.id}/wearable")
        assert absent.status_code == 200
        assert absent.json() == {"connected": False}
        assert "ohs-user" not in absent.text
        assert db.query(PatientSource).filter(PatientSource.source_type == "wearable").count() == 0

        holder["user"] = practitioner
        clinic_before = http.get(f"/api/patients/{patient.id}/sources").json()
        progress_before = http.get(f"/api/patients/{patient.id}/progress-release").json()
        reports_before = http.get(f"/api/patients/{patient.id}/report-releases").json()
        record_ids = {row.id for row in db.query(SourceRecord.id).all()}
        observation_count = db.query(SourceObservation).count()
        release_count = db.query(ContentRelease).count()
        clinic_release_count = (
            db.query(ContentRelease).filter(ContentRelease.direction == "clinic_to_client").count()
        )
        client_release_count = (
            db.query(ContentRelease).filter(ContentRelease.direction == "client_to_clinic").count()
        )
        share_count = db.query(ShareLink).count()

        holder["user"] = client_user
        connected = http.post(
            f"/api/client/records/{patient.id}/wearable",
            json={"external_account_ref": "  ohs-user-1  "},
        )
        assert connected.status_code == 200
        body = connected.json()
        assert set(body) == {"connected", "source_id", "display_name", "external_account_ref"}
        assert body["connected"] is True
        assert body["display_name"] == "Wearables"
        assert body["external_account_ref"] == "ohs-user-1"
        source_id = body["source_id"]

        again = http.post(
            f"/api/client/records/{patient.id}/wearable",
            json={"external_account_ref": "ohs-user-2"},
        )
        assert again.status_code == 200
        assert again.json()["source_id"] == source_id
        assert again.json()["external_account_ref"] == "ohs-user-2"
        assert again.json()["display_name"] == "Wearables"

        current = http.get(f"/api/client/records/{patient.id}/wearable")
        assert current.status_code == 200
        assert current.json() == again.json()

        shelves = db.query(PatientSource).filter(PatientSource.source_type == "wearable").all()
        assert len(shelves) == 1
        shelf = shelves[0]
        assert str(shelf.id) == source_id
        assert shelf.patient_id == patient.id
        assert shelf.practitioner_user_id == practitioner.id
        assert shelf.display_name == "Wearables"
        assert shelf.status == "active"
        assert shelf.external_account_ref == "ohs-user-2"
        assert db.query(SourceRecord).filter(SourceRecord.patient_source_id == shelf.id).count() == 0
        assert {row.id for row in db.query(SourceRecord.id).all()} == record_ids
        assert db.query(SourceObservation).count() == observation_count
        assert db.query(ContentRelease).count() == release_count
        assert (
            db.query(ContentRelease).filter(ContentRelease.direction == "clinic_to_client").count()
            == clinic_release_count
        )
        assert (
            db.query(ContentRelease).filter(ContentRelease.direction == "client_to_clinic").count()
            == client_release_count
        )
        assert db.query(ShareLink).count() == share_count
        assert db.query(ShareLink).one().token == "wearable-share-token"
        assert db.query(ShareLink).one().is_active is True

        portal = http.get(f"/api/client/records/{patient.id}")
        assert portal.status_code == 200
        assert portal.json() == before_sources
        assert portal.json()["trend"]["client_view"]["summary"] == "Progress is ready to review."
        assert [group["display_name"] for group in portal.json()["sources"]] == [
            "Clinic notes",
            "Lab results",
        ]
        assert http.get(f"/api/client/records/{patient.id}/entries").json() == before_entries
        for hidden in ("ohs-user-1", "ohs-user-2", "Wearables", "external_account_ref"):
            assert hidden not in portal.text

        other = http.post(
            f"/api/client/records/{other_record.id}/wearable",
            json={"external_account_ref": "ohs-user-other"},
        )
        assert other.status_code == 200
        assert other.json()["source_id"] != source_id
        assert db.query(PatientSource).filter(PatientSource.source_type == "wearable").count() == 2
        db.refresh(shelf)
        assert shelf.external_account_ref == "ohs-user-2"

        record_count = db.query(PatientSource).filter(PatientSource.source_type == "wearable").count()
        rejected = http.post(
            f"/api/client/records/{patient.id}/wearable",
            json={"external_account_ref": "   "},
        )
        assert rejected.status_code == 422
        too_long = http.post(
            f"/api/client/records/{patient.id}/wearable",
            json={"external_account_ref": "x" * 256},
        )
        assert too_long.status_code == 422
        db.refresh(shelf)
        assert shelf.external_account_ref == "ohs-user-2"
        assert (
            db.query(PatientSource).filter(PatientSource.source_type == "wearable").count()
            == record_count
        )

        holder["user"] = other_client
        hidden = http.get(f"/api/client/records/{patient.id}/wearable")
        assert hidden.status_code == 404
        assert "ohs-user-2" not in hidden.text
        assert http.post(
            f"/api/client/records/{patient.id}/wearable",
            json={"external_account_ref": "ohs-user-stolen"},
        ).status_code == 404
        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{unclaimed.id}/wearable",
            json={"external_account_ref": "ohs-user-unclaimed"},
        ).status_code == 404
        holder["user"] = unverified
        assert http.get(f"/api/client/records/{patient.id}/wearable").status_code == 403
        assert http.post(
            f"/api/client/records/{patient.id}/wearable",
            json={"external_account_ref": "ohs-user-unverified"},
        ).status_code == 403
        holder["user"] = practitioner
        denied = http.post(
            f"/api/client/records/{patient.id}/wearable",
            json={"external_account_ref": "ohs-user-clinic"},
        )
        assert denied.status_code == 403
        assert "ohs-user-2" not in denied.text
        db.refresh(shelf)
        assert shelf.external_account_ref == "ohs-user-2"
        assert (
            db.query(PatientSource).filter(PatientSource.source_type == "wearable").count()
            == record_count
        )

        clinic = http.get(f"/api/patients/{patient.id}/sources")
        assert clinic.status_code == 200
        assert clinic.json() == clinic_before
        assert [item["display_name"] for item in clinic.json()["sources"]] == [
            "Clinic notes",
            "Lab results",
            "From the client",
        ]
        assert "wearable" not in clinic.text
        assert "ohs-user-2" not in clinic.text
        assert "Wearables" not in clinic.text
        assert http.get(f"/api/patients/{patient.id}/progress-release").json() == progress_before
        assert http.get(f"/api/patients/{patient.id}/report-releases").json() == reports_before
        holder["user"] = client_user
        assert _client_blocked_from_practitioner_path(client_user, f"/api/patients/{patient.id}/sources") is True
    finally:
        for column, original in originals:
            column.type = original
