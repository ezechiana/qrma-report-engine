"""A claimed client can see one unreleased wearable summary per day."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
from app.api.deps import get_current_user, get_db
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


def test_wearable_day_migration_allows_one_summary_per_day():
    sql = Path("app/db/migrations/20261116_wearable_day.sql").read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_source_records_wearable_day" in sql
    assert "payload_json->>'kind' = 'wearable_day'" in sql


def test_claimed_client_sees_unreleased_daily_summaries(monkeypatch):
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
            token="wearable-day-share-token",
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
        note = http.post(
            f"/api/patients/{patient.id}/sources/{sources[0]['source_id']}/records",
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
            f"/api/patients/{patient.id}/sources/{sources[0]['source_id']}/records/{note_id}/release",
            json={"released": True},
        ).status_code == 200
        panel = http.post(
            f"/api/patients/{patient.id}/sources/{sources[1]['source_id']}/panels",
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
            f"/api/patients/{patient.id}/sources/{sources[1]['source_id']}/panels/{panel_id}/release",
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
        missing = http.post(
            f"/api/client/records/{other_record.id}/wearable/days",
            json={"days": [{"recorded_on": "2026-11-02", "steps": 10}]},
        )
        assert missing.status_code == 404
        assert db.query(SourceRecord).filter(SourceRecord.patient_id == other_record.id).count() == 0
        assert http.get(f"/api/client/records/{patient.id}/wearable/days").json() == {"days": []}

        connected = http.post(
            f"/api/client/records/{patient.id}/wearable",
            json={"external_account_ref": "ohs-user-1"},
        )
        assert connected.status_code == 200
        shelf_id = connected.json()["source_id"]

        holder["user"] = practitioner
        clinic_before = http.get(f"/api/patients/{patient.id}/sources").json()
        progress_before = http.get(f"/api/patients/{patient.id}/progress-release").json()
        reports_before = http.get(f"/api/patients/{patient.id}/report-releases").json()
        holder["user"] = client_user
        portal_before = http.get(f"/api/client/records/{patient.id}").json()
        entries_before = http.get(f"/api/client/records/{patient.id}/entries").json()
        release_count = db.query(ContentRelease).count()
        share_count = db.query(ShareLink).count()
        existing_records = {row.id for row in db.query(SourceRecord.id).all()}

        synced = http.post(
            f"/api/client/records/{patient.id}/wearable/days",
            json={
                "days": [
                    {
                        "recorded_on": "2026-11-02",
                        "steps": 8421,
                        "resting_heart_rate": 54,
                        "sleep_duration": 462,
                        "hrv": 61,
                        "glucose": 99,
                        "samples": [1, 2, 3],
                    },
                    {"recorded_on": "2026-11-01", "steps": 84321},
                ]
            },
        )
        assert synced.status_code == 200
        body = synced.json()["days"]
        assert [item["recorded_on"] for item in body] == ["2026-11-02", "2026-11-01"]
        assert set(body[0]) == {
            "record_id",
            "recorded_on",
            "released",
            "steps",
            "resting_heart_rate",
            "sleep_duration",
            "hrv",
        }
        assert body[0]["released"] is False
        assert body[0]["steps"] == {"value": 8421, "unit": "steps"}
        assert body[0]["resting_heart_rate"] == {"value": 54, "unit": "bpm"}
        assert body[0]["sleep_duration"] == {"value": 462, "unit": "min"}
        assert body[0]["hrv"] == {"value": 61, "unit": "ms"}
        assert body[1]["steps"] == {"value": 84321, "unit": "steps"}
        assert body[1]["resting_heart_rate"] is None
        assert body[1]["sleep_duration"] is None
        assert body[1]["hrv"] is None
        assert "glucose" not in synced.text
        assert "samples" not in synced.text
        day_id = body[0]["record_id"]

        listed = http.get(f"/api/client/records/{patient.id}/wearable/days")
        assert listed.status_code == 200
        assert [item["recorded_on"] for item in listed.json()["days"]] == ["2026-11-02", "2026-11-01"]
        assert listed.json()["days"][0]["record_id"] == day_id
        assert listed.json()["days"][0]["released"] is False

        again = http.post(
            f"/api/client/records/{patient.id}/wearable/days",
            json={
                "days": [{
                    "recorded_on": "2026-11-02",
                    "steps": 9000,
                    "resting_heart_rate": 54,
                    "sleep_duration": 462,
                }]
            },
        )
        assert again.status_code == 200
        assert again.json()["days"][0]["record_id"] == day_id
        assert again.json()["days"][0]["steps"] == {"value": 9000, "unit": "steps"}
        assert again.json()["days"][0]["hrv"] is None
        assert again.json()["days"][0]["released"] is False

        duplicated = http.post(
            f"/api/client/records/{patient.id}/wearable/days",
            json={
                "days": [
                    {"recorded_on": "2026-11-03", "steps": 10},
                    {"recorded_on": "2026-11-03", "steps": 20},
                ]
            },
        )
        assert duplicated.status_code == 200
        assert duplicated.json()["days"] == [{
            "record_id": duplicated.json()["days"][0]["record_id"],
            "recorded_on": "2026-11-03",
            "released": False,
            "steps": {"value": 20, "unit": "steps"},
            "resting_heart_rate": None,
            "sleep_duration": None,
            "hrv": None,
        }]

        days = http.get(f"/api/client/records/{patient.id}/wearable/days").json()["days"]
        assert [item["recorded_on"] for item in days] == ["2026-11-03", "2026-11-02", "2026-11-01"]
        by_day = {item["recorded_on"]: item for item in days}
        assert by_day["2026-11-02"]["record_id"] == day_id
        assert by_day["2026-11-02"]["steps"]["value"] == 9000
        assert by_day["2026-11-02"]["hrv"] is None

        shelf = db.query(PatientSource).filter(PatientSource.source_type == "wearable").one()
        assert str(shelf.id) == shelf_id
        assert shelf.external_account_ref == "ohs-user-1"
        stored = db.query(SourceRecord).filter(SourceRecord.patient_source_id == shelf.id).all()
        assert len(stored) == 3
        stored_day = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(day_id)).one()
        assert stored_day.patient_id == patient.id
        assert stored_day.created_by_user_id == client_user.id
        assert stored_day.title == "Daily summary"
        assert stored_day.client_summary is None
        assert stored_day.payload_json == {"kind": "wearable_day"}
        metrics = (
            db.query(SourceObservation)
            .filter(SourceObservation.source_record_id == stored_day.id)
            .all()
        )
        assert {row.code: (int(row.value_numeric), row.unit, row.position) for row in metrics} == {
            "steps": (9000, "steps", 1),
            "resting_heart_rate": (54, "bpm", 2),
            "sleep_duration": (462, "min", 3),
        }
        assert db.query(SourceObservation).filter(SourceObservation.code == "glucose").count() == 0
        assert db.query(ContentRelease).filter(ContentRelease.source_key.like("wearable:%")).count() == 0
        assert db.query(ContentRelease).count() == release_count
        assert db.query(ShareLink).count() == share_count
        assert db.query(ShareLink).one().token == "wearable-day-share-token"
        assert existing_records.isdisjoint({row.id for row in stored})
        opened = http.get(f"/api/client/records/{patient.id}/notes/{day_id}")
        assert opened.status_code == 404
        assert "9000" not in opened.text

        portal = http.get(f"/api/client/records/{patient.id}")
        assert portal.json() == portal_before
        assert portal.json()["trend"]["client_view"]["summary"] == "Progress is ready to review."
        assert [group["display_name"] for group in portal.json()["sources"]] == [
            "Clinic notes",
            "Lab results",
        ]
        assert http.get(f"/api/client/records/{patient.id}/entries").json() == entries_before
        for hidden in ("8421", "9000", "wearable_day", "Daily summary", "Heart rate variability"):
            assert hidden not in portal.text

        day_count = db.query(SourceRecord).filter(SourceRecord.patient_source_id == shelf.id).count()
        rejected = http.post(
            f"/api/client/records/{patient.id}/wearable/days",
            json={"days": [{"recorded_on": "2026-11-05", "steps": -1}]},
        )
        assert rejected.status_code == 422
        assert (
            db.query(SourceRecord).filter(SourceRecord.patient_source_id == shelf.id).count()
            == day_count
        )

        holder["user"] = other_client
        hidden = http.get(f"/api/client/records/{patient.id}/wearable/days")
        assert hidden.status_code == 404
        assert "9000" not in hidden.text
        assert http.post(
            f"/api/client/records/{patient.id}/wearable/days",
            json={"days": [{"recorded_on": "2026-11-05", "steps": 5}]},
        ).status_code == 404
        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{unclaimed.id}/wearable/days",
            json={"days": [{"recorded_on": "2026-11-05", "steps": 5}]},
        ).status_code == 404
        holder["user"] = unverified
        assert http.get(f"/api/client/records/{patient.id}/wearable/days").status_code == 403
        holder["user"] = practitioner
        denied = http.post(
            f"/api/client/records/{patient.id}/wearable/days",
            json={"days": [{"recorded_on": "2026-11-05", "steps": 5}]},
        )
        assert denied.status_code == 403
        assert "9000" not in denied.text
        clinic = http.get(f"/api/patients/{patient.id}/sources")
        assert clinic.status_code == 200
        assert clinic.json() == clinic_before
        assert [item["display_name"] for item in clinic.json()["sources"]] == [
            "Clinic notes",
            "Lab results",
            "From the client",
        ]
        assert "Haemoglobin" in clinic.text
        assert "I slept poorly." in clinic.text
        for hidden in ("8421", "9000", "84321", "wearable_day", "Daily summary", "Wearables"):
            assert hidden not in clinic.text
        assert http.get(f"/api/patients/{patient.id}/progress-release").json() == progress_before
        assert http.get(f"/api/patients/{patient.id}/report-releases").json() == reports_before
        assert (
            db.query(SourceRecord).filter(SourceRecord.patient_source_id == shelf.id).count()
            == day_count
        )
    finally:
        for column, original in originals:
            column.type = original
