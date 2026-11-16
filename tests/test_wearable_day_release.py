"""A claimed client can release or withdraw one wearable day."""

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
from app.services.content_release_service import _as_report_id


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_patient_page_lists_wearable_days_without_clinic_controls():
    page = Path("app/templates/patient_detail.html").read_text(encoding="utf-8")
    assert 'id="wearable-days-section"' in page
    assert ">Wearables<" in page
    assert "This clinic cannot change them here." in page
    assert "renderWearableDays" in page
    assert "wearable-day-release" not in page
    assert "wearable-day-edit" not in page


def test_client_can_release_and_withdraw_one_wearable_day(monkeypatch):
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
            token="wearable-release-share-token",
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
        assert http.post(
            f"/api/client/records/{patient.id}/entries",
            json={
                "title": "Sleep note",
                "recorded_on": "2026-11-02",
                "text": "I slept poorly.",
                "reference": "home-sleep-note.pdf",
            },
        ).status_code == 200
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
        days = {item["recorded_on"]: item for item in synced.json()["days"]}
        day_id = days["2026-11-02"]["record_id"]
        other_day_id = days["2026-11-01"]["record_id"]
        assert days["2026-11-02"]["released"] is False
        assert days["2026-11-01"]["released"] is False

        holder["user"] = practitioner
        clinic_before = http.get(f"/api/patients/{patient.id}/sources").json()
        progress_before = http.get(f"/api/patients/{patient.id}/progress-release").json()
        reports_before = http.get(f"/api/patients/{patient.id}/report-releases").json()
        assert [item["display_name"] for item in clinic_before["sources"]] == [
            "Clinic notes",
            "Lab results",
            "From the client",
        ]
        holder["user"] = client_user
        portal_before = http.get(f"/api/client/records/{patient.id}").json()
        entries_before = http.get(f"/api/client/records/{patient.id}/entries").json()
        clinic_release_count = (
            db.query(ContentRelease).filter(ContentRelease.direction == "clinic_to_client").count()
        )
        share_count = db.query(ShareLink).count()
        stored = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(day_id)).one()
        payload_before = dict(stored.payload_json)
        metrics_before = {
            row.code: (int(row.value_numeric), row.unit)
            for row in db.query(SourceObservation).filter(SourceObservation.source_record_id == stored.id)
        }

        released = http.post(
            f"/api/client/records/{patient.id}/wearable/days/{day_id}/release",
            json={"released": True},
        )
        assert released.status_code == 200
        assert released.json() == {"record_id": day_id, "released": True}
        assert "91001" not in released.text
        own = {item["record_id"]: item for item in http.get(f"/api/client/records/{patient.id}/wearable/days").json()["days"]}
        assert own[day_id]["released"] is True
        assert own[day_id]["steps"] == {"value": 91001, "unit": "steps"}
        assert own[other_day_id]["released"] is False
        assert own[other_day_id]["steps"] == {"value": 92002, "unit": "steps"}

        holder["user"] = practitioner
        clinic = http.get(f"/api/patients/{patient.id}/sources")
        assert clinic.status_code == 200
        clinic_body = clinic.json()
        assert [item["display_name"] for item in clinic_body["sources"]] == [
            "Clinic notes",
            "Lab results",
            "From the client",
            "Wearables",
        ]
        assert clinic_body["sources"][0] == clinic_before["sources"][0]
        assert clinic_body["sources"][1] == clinic_before["sources"][1]
        assert clinic_body["sources"][2] == clinic_before["sources"][2]
        group = clinic_body["sources"][3]
        assert group["source_type"] == "wearable"
        assert group["display_name"] == "Wearables"
        assert [item["record_id"] for item in group["records"]] == [day_id]
        assert set(group["records"][0]) == {
            "record_id",
            "title",
            "recorded_on",
            "steps",
            "resting_heart_rate",
            "sleep_duration",
            "hrv",
        }
        assert "released" not in group["records"][0]
        assert group["records"][0]["title"] == "Daily summary"
        assert group["records"][0]["recorded_on"] == "2026-11-02"
        assert group["records"][0]["steps"] == {"value": 91001, "unit": "steps"}
        assert group["records"][0]["resting_heart_rate"] == {"value": 54, "unit": "bpm"}
        assert group["records"][0]["sleep_duration"] == {"value": 462, "unit": "min"}
        assert group["records"][0]["hrv"] == {"value": 61, "unit": "ms"}
        assert "92002" not in clinic.text
        assert "ohs-user-1" not in clinic.text
        shelf_id = group["source_id"]
        assert http.post(
            f"/api/patients/{patient.id}/sources/{shelf_id}/records",
            json={"title": "Changed", "recorded_on": "2026-11-04", "text": "Edited", "client_summary": ""},
        ).status_code == 404
        assert http.post(
            f"/api/patients/{patient.id}/sources/{shelf_id}/records/{day_id}/release",
            json={"released": False},
        ).status_code == 404

        holder["user"] = client_user
        withdrawn = http.post(
            f"/api/client/records/{patient.id}/wearable/days/{day_id}/release",
            json={"released": False},
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json() == {"record_id": day_id, "released": False}
        assert "91001" not in withdrawn.text
        own = {item["record_id"]: item for item in http.get(f"/api/client/records/{patient.id}/wearable/days").json()["days"]}
        assert own[day_id]["released"] is False
        assert own[day_id]["steps"] == {"value": 91001, "unit": "steps"}
        assert own[day_id]["hrv"] == {"value": 61, "unit": "ms"}
        holder["user"] = practitioner
        after_withdraw = http.get(f"/api/patients/{patient.id}/sources").json()
        assert [item["display_name"] for item in after_withdraw["sources"]] == [
            "Clinic notes",
            "Lab results",
            "From the client",
        ]
        assert "91001" not in http.get(f"/api/patients/{patient.id}/sources").text
        assert after_withdraw["sources"][0] == clinic_before["sources"][0]
        assert after_withdraw["sources"][1] == clinic_before["sources"][1]
        assert after_withdraw["sources"][2] == clinic_before["sources"][2]

        holder["user"] = client_user
        other_released = http.post(
            f"/api/client/records/{patient.id}/wearable/days/{other_day_id}/release",
            json={"released": True},
        )
        assert other_released.status_code == 200
        again = http.post(
            f"/api/client/records/{patient.id}/wearable/days/{day_id}/release",
            json={"released": True},
        )
        assert again.status_code == 200
        assert again.json()["released"] is True
        assert "91001" not in again.text
        holder["user"] = practitioner
        restored = http.get(f"/api/patients/{patient.id}/sources").json()
        restored_days = {item["record_id"]: item for item in restored["sources"][3]["records"]}
        assert set(restored_days) == {day_id, other_day_id}
        assert restored_days[day_id]["steps"] == {"value": 91001, "unit": "steps"}
        assert restored_days[day_id]["resting_heart_rate"] == {"value": 54, "unit": "bpm"}
        assert restored_days[day_id]["sleep_duration"] == {"value": 462, "unit": "min"}
        assert restored_days[day_id]["hrv"] == {"value": 61, "unit": "ms"}
        assert restored_days[other_day_id]["steps"] == {"value": 92002, "unit": "steps"}
        assert restored_days[other_day_id]["resting_heart_rate"] is None
        assert restored_days[other_day_id]["hrv"] is None
        db.refresh(stored)
        assert stored.payload_json == payload_before
        assert {
            row.code: (int(row.value_numeric), row.unit)
            for row in db.query(SourceObservation).filter(SourceObservation.source_record_id == stored.id)
        } == metrics_before
        release_row = db.query(ContentRelease).filter(ContentRelease.source_key == f"wearable:{day_id}").one()
        assert release_row.direction == "client_to_clinic"
        assert release_row.released is True
        assert release_row.practitioner_user_id == practitioner.id
        assert _as_report_id(release_row.source_key) is None
        assert (
            db.query(ContentRelease).filter(ContentRelease.direction == "clinic_to_client").count()
            == clinic_release_count
        )
        assert db.query(ShareLink).count() == share_count
        assert db.query(ShareLink).one().token == "wearable-release-share-token"
        assert db.query(ShareLink).one().is_active is True

        holder["user"] = client_user
        both_off_first = http.post(
            f"/api/client/records/{patient.id}/wearable/days/{day_id}/release",
            json={"released": False},
        )
        both_off_second = http.post(
            f"/api/client/records/{patient.id}/wearable/days/{other_day_id}/release",
            json={"released": False},
        )
        assert both_off_first.status_code == 200
        assert both_off_second.status_code == 200
        listed = http.get(f"/api/client/records/{patient.id}/wearable/days").json()["days"]
        assert {item["record_id"]: item["released"] for item in listed} == {day_id: False, other_day_id: False}
        assert {item["record_id"]: item["steps"]["value"] for item in listed} == {day_id: 91001, other_day_id: 92002}
        holder["user"] = practitioner
        hidden = http.get(f"/api/patients/{patient.id}/sources").json()
        assert [item["display_name"] for item in hidden["sources"]] == [
            "Clinic notes",
            "Lab results",
            "From the client",
        ]
        assert "91001" not in http.get(f"/api/patients/{patient.id}/sources").text
        assert "92002" not in http.get(f"/api/patients/{patient.id}/sources").text

        holder["user"] = client_user
        portal = http.get(f"/api/client/records/{patient.id}")
        assert portal.json() == portal_before
        assert http.get(f"/api/client/records/{patient.id}/entries").json() == entries_before
        for hidden_text in ("91001", "92002", "Wearables", "ohs-user-1"):
            assert hidden_text not in portal.text
        holder["user"] = practitioner
        assert http.get(f"/api/patients/{patient.id}/progress-release").json() == progress_before
        assert http.get(f"/api/patients/{patient.id}/report-releases").json() == reports_before

        holder["user"] = other_client
        hidden_get = http.get(f"/api/client/records/{patient.id}/wearable/days")
        assert hidden_get.status_code == 404
        assert "91001" not in hidden_get.text
        assert http.post(
            f"/api/client/records/{patient.id}/wearable/days/{day_id}/release",
            json={"released": True},
        ).status_code == 404
        holder["user"] = client_user
        assert http.post(
            f"/api/client/records/{unclaimed.id}/wearable/days/{day_id}/release",
            json={"released": True},
        ).status_code == 404
        assert http.post(
            f"/api/client/records/{patient.id}/wearable/days/{note_id}/release",
            json={"released": True},
        ).status_code == 404
        holder["user"] = unverified
        assert http.post(
            f"/api/client/records/{patient.id}/wearable/days/{day_id}/release",
            json={"released": True},
        ).status_code == 403
        holder["user"] = practitioner
        denied = http.post(
            f"/api/client/records/{patient.id}/wearable/days/{day_id}/release",
            json={"released": True},
        )
        assert denied.status_code == 403
        assert "91001" not in denied.text
        db.refresh(release_row)
        assert release_row.released is False
    finally:
        for column, original in originals:
            column.type = original
