"""The clinic sees released wearable days in Sources."""

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


def test_sources_page_places_wearables_after_the_other_groups():
    page = Path("app/templates/patient_detail.html").read_text(encoding="utf-8")
    sources = page.index('id="sources-section"')
    client = page.index('id="client-entries-section"')
    wearable = page.index('id="wearable-days-section"')
    timeline = page.index("Activity timeline")
    assert sources < client < wearable < timeline
    assert "This clinic cannot change them here." in page
    assert "wearable-day-edit" not in page
    assert "wearable-day-release" not in page


def test_clinic_sees_only_released_wearable_days(monkeypatch):
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
        db.add_all([practitioner, client_user])
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
        other = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Grace",
            last_name="Hopper",
            full_name="Grace Hopper",
            claimed_by_user_id=client_user.id,
            claimed_at=datetime.now(timezone.utc),
        )
        db.add_all([patient, other])
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
            token="wearable-clinic-share-token",
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
        assert http.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": True},
        ).status_code == 200

        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{patient.id}/wearable",
            json={"external_account_ref": "ohs-user-1"},
        ).status_code == 200
        synced = http.post(
            f"/api/client/records/{patient.id}/wearable/days",
            json={
                "days": [
                    {
                        "recorded_on": "2026-11-02",
                        "steps": 91001,
                        "resting_heart_rate": 54,
                        "sleep_duration": 462,
                        "hrv": 61,
                    },
                    {"recorded_on": "2026-11-01", "steps": 92002},
                ]
            },
        )
        assert synced.status_code == 200
        by_date = {item["recorded_on"]: item["record_id"] for item in synced.json()["days"]}
        full_id = by_date["2026-11-02"]
        partial_id = by_date["2026-11-01"]

        holder["user"] = practitioner
        before = http.get(f"/api/patients/{patient.id}/sources").json()
        progress_before = http.get(f"/api/patients/{patient.id}/progress-release").json()
        assert [item["display_name"] for item in before["sources"]] == ["Clinic notes", "Lab results"]
        assert "91001" not in http.get(f"/api/patients/{patient.id}/sources").text
        clinic_release_count = (
            db.query(ContentRelease).filter(ContentRelease.direction == "clinic_to_client").count()
        )

        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{patient.id}/wearable/days/{full_id}/release",
            json={"released": True},
        ).status_code == 200
        holder["user"] = practitioner
        released = http.get(f"/api/patients/{patient.id}/sources").json()
        assert [item["display_name"] for item in released["sources"]] == [
            "Clinic notes",
            "Lab results",
            "Wearables",
        ]
        assert released["sources"][0] == before["sources"][0]
        assert released["sources"][1] == before["sources"][1]
        group = released["sources"][2]
        assert group["source_type"] == "wearable"
        assert group["display_name"] == "Wearables"
        assert len(group["records"]) == 1
        day = group["records"][0]
        assert day["record_id"] == full_id
        assert day["title"] == "Daily summary"
        assert day["recorded_on"] == "2026-11-02"
        assert day["steps"] == {"value": 91001, "unit": "steps"}
        assert day["resting_heart_rate"] == {"value": 54, "unit": "bpm"}
        assert day["sleep_duration"] == {"value": 462, "unit": "min"}
        assert day["hrv"] == {"value": 61, "unit": "ms"}
        assert "released" not in day
        assert "92002" not in http.get(f"/api/patients/{patient.id}/sources").text
        assert "ohs-user-1" not in http.get(f"/api/patients/{patient.id}/sources").text
        shelf_id = group["source_id"]

        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{patient.id}/wearable/days/{partial_id}/release",
            json={"released": True},
        ).status_code == 200
        holder["user"] = practitioner
        both = http.get(f"/api/patients/{patient.id}/sources").json()["sources"][2]["records"]
        partial = next(item for item in both if item["record_id"] == partial_id)
        assert partial["title"] == "Daily summary"
        assert partial["recorded_on"] == "2026-11-01"
        assert partial["steps"] == {"value": 92002, "unit": "steps"}
        assert partial["resting_heart_rate"] is None
        assert partial["sleep_duration"] is None
        assert partial["hrv"] is None
        wearable_group = http.get(f"/api/patients/{patient.id}/sources").json()["sources"][2]

        metrics_before = {
            row.code: int(row.value_numeric)
            for row in db.query(SourceObservation).filter(
                SourceObservation.source_record_id == uuid.UUID(full_id)
            )
        }
        assert http.patch(
            f"/api/patients/{patient.id}/sources/{shelf_id}/records/{full_id}",
            json={"title": "Changed", "text": "Edited"},
        ).status_code == 404
        assert http.patch(
            f"/api/patients/{patient.id}/sources/{shelf_id}/panels/{full_id}",
            json={"title": "Changed"},
        ).status_code == 404
        assert http.post(
            f"/api/patients/{patient.id}/sources/{shelf_id}/records/{full_id}/release",
            json={"released": False},
        ).status_code == 404
        assert http.post(
            f"/api/patients/{patient.id}/sources/{shelf_id}/panels/{full_id}/release",
            json={"released": False},
        ).status_code == 404
        assert {
            row.code: int(row.value_numeric)
            for row in db.query(SourceObservation).filter(
                SourceObservation.source_record_id == uuid.UUID(full_id)
            )
        } == metrics_before
        assert db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(full_id)).one().title == "Daily summary"

        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{other.id}/wearable",
            json={"external_account_ref": "ohs-user-2"},
        ).status_code == 200
        other_sync = http.post(
            f"/api/client/records/{other.id}/wearable/days",
            json={"days": [{"recorded_on": "2026-11-03", "steps": 93003}]},
        )
        assert other_sync.status_code == 200
        other_id = other_sync.json()["days"][0]["record_id"]
        assert http.post(
            f"/api/client/records/{other.id}/wearable/days/{other_id}/release",
            json={"released": True},
        ).status_code == 200
        holder["user"] = practitioner
        own_clinic = http.get(f"/api/patients/{patient.id}/sources")
        assert own_clinic.status_code == 200
        assert "93003" not in own_clinic.text
        assert own_clinic.json()["sources"][2] == wearable_group
        other_clinic = http.get(f"/api/patients/{other.id}/sources").json()
        assert [item["display_name"] for item in other_clinic["sources"]] == [
            "Clinic notes",
            "Lab results",
            "Wearables",
        ]
        assert [item["record_id"] for item in other_clinic["sources"][2]["records"]] == [other_id]
        assert other_clinic["sources"][2]["records"][0]["steps"] == {"value": 93003, "unit": "steps"}
        assert "91001" not in http.get(f"/api/patients/{other.id}/sources").text

        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{patient.id}/entries",
            json={
                "title": "Sleep note",
                "recorded_on": "2026-11-02",
                "text": "I slept poorly.",
            },
        ).status_code == 200
        holder["user"] = practitioner
        with_entry = http.get(f"/api/patients/{patient.id}/sources").json()
        assert [item["display_name"] for item in with_entry["sources"]] == [
            "Clinic notes",
            "Lab results",
            "From the client",
            "Wearables",
        ]
        assert with_entry["sources"][0] == before["sources"][0]
        assert with_entry["sources"][1] == before["sources"][1]
        assert with_entry["sources"][3] == wearable_group
        assert with_entry["sources"][2]["records"][0]["text"] == "I slept poorly."

        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{patient.id}/wearable/days/{full_id}/release",
            json={"released": False},
        ).status_code == 200
        assert http.post(
            f"/api/client/records/{patient.id}/wearable/days/{partial_id}/release",
            json={"released": False},
        ).status_code == 200
        holder["user"] = practitioner
        hidden = http.get(f"/api/patients/{patient.id}/sources")
        assert [item["display_name"] for item in hidden.json()["sources"]] == [
            "Clinic notes",
            "Lab results",
            "From the client",
        ]
        assert "91001" not in hidden.text
        assert "92002" not in hidden.text
        assert hidden.json()["sources"][0] == before["sources"][0]
        assert hidden.json()["sources"][1] == before["sources"][1]
        assert http.get(f"/api/patients/{patient.id}/progress-release").json() == progress_before
        assert db.query(ShareLink).one().token == "wearable-clinic-share-token"
        assert db.query(ShareLink).one().is_active is True
        assert (
            db.query(ContentRelease).filter(ContentRelease.direction == "clinic_to_client").count()
            == clinic_release_count
        )
        holder["user"] = client_user
        portal = http.get(f"/api/client/records/{patient.id}")
        assert [group["display_name"] for group in portal.json()["sources"]] == ["Clinic notes"]
        assert "91001" not in portal.text
        assert "Wearables" not in portal.text
    finally:
        for column, original in originals:
            column.type = original
