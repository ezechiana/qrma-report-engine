"""A new lab panel stays in the clinic record and off the client portal."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
from app.services.content_release_service import set_progress_release


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_practitioner_can_add_a_private_lab_panel(monkeypatch):
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
            token="lab-share-token",
            is_active=True,
            share_type="report",
            patient_id=patient.id,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(link)
        db.commit()
        set_progress_release(db, patient, True)
        release_count = db.query(ContentRelease).count()

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
        sources = listed.json()["sources"]
        assert sources[0]["source_type"] == "manual"
        assert sources[0]["display_name"] == "Clinic notes"
        assert sources[1]["source_type"] == "laboratory"
        assert sources[1]["display_name"] == "Lab results"
        assert sources[1]["records"] == []
        source_id = sources[1]["source_id"]
        again = http.get(f"/api/patients/{patient.id}/sources")
        assert again.json()["sources"][1]["source_id"] == source_id

        created = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "June bloods",
                "recorded_on": "2026-06-12",
                "specimen": "Blood",
                "laboratory_name": "North Lab",
                "client_summary": "The blood count is ready to discuss.",
                "observations": [
                    {
                        "name": "Haemoglobin",
                        "value_numeric": 140,
                        "value_text": "140",
                        "unit": "g/L",
                        "reference_low": 115,
                        "reference_high": 150,
                        "interpretation": "normal",
                    },
                    {
                        "name": "H. pylori antigen",
                        "value_text": "Positive",
                        "reference_text": "Negative",
                    },
                ],
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["released"] is False
        assert body["title"] == "June bloods"
        assert body["specimen"] == "Blood"
        assert body["laboratory_name"] == "North Lab"
        assert body["observations"][0]["name"] == "Haemoglobin"
        assert body["observations"][0]["value_numeric"] == 140
        assert body["observations"][0]["unit"] == "g/L"
        assert body["observations"][0]["reference_low"] == 115
        assert body["observations"][0]["reference_high"] == 150
        assert body["observations"][1]["value_text"] == "Positive"
        assert "payload_json" not in body
        record_id = body["record_id"]

        stored = db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).all()
        assert len(stored) == 2
        haemoglobin = next(row for row in stored if row.name == "Haemoglobin")
        assert haemoglobin.value_numeric == Decimal("140")
        assert haemoglobin.unit == "g/L"
        assert db.query(ContentRelease).filter(ContentRelease.source_key == f"record:{record_id}").count() == 0
        original_ids = {row.id for row in stored}
        panel_url = f"/api/patients/{patient.id}/sources/{source_id}/panels/{record_id}"

        changed_summary = http.patch(panel_url, json={"client_summary": "Updated wording for the client"})
        assert changed_summary.status_code == 200
        assert changed_summary.json()["client_summary"] == "Updated wording for the client"
        assert changed_summary.json()["released"] is False
        assert [item["name"] for item in changed_summary.json()["observations"]] == [
            "Haemoglobin",
            "H. pylori antigen",
        ]
        kept = db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).all()
        assert {row.id for row in kept} == original_ids

        replaced = http.patch(panel_url, json={
            "observations": [{
                "name": "ALT",
                "value_numeric": 42,
                "value_text": "42",
                "unit": "U/L",
                "reference_low": 7,
                "reference_high": 56,
            }],
        })
        assert replaced.status_code == 200
        assert replaced.json()["client_summary"] == "Updated wording for the client"
        assert replaced.json()["released"] is False
        assert replaced.json()["title"] == "June bloods"
        assert replaced.json()["observations"][0]["name"] == "ALT"
        assert replaced.json()["observations"][0]["value_numeric"] == 42
        new_rows = db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).all()
        assert len(new_rows) == 1
        assert {row.id for row in new_rows}.isdisjoint(original_ids)

        details = http.patch(panel_url, json={
            "title": "June bloods revised",
            "recorded_on": "2026-07-01",
            "specimen": "Urine",
            "laboratory_name": "South Lab",
        })
        assert details.status_code == 200
        assert details.json()["title"] == "June bloods revised"
        assert details.json()["recorded_on"] == "2026-07-01"
        assert details.json()["specimen"] == "Urine"
        assert details.json()["laboratory_name"] == "South Lab"
        assert details.json()["client_summary"] == "Updated wording for the client"
        assert details.json()["observations"][0]["value_numeric"] == 42
        assert details.json()["released"] is False

        cleared_lab = http.patch(panel_url, json={"laboratory_name": ""})
        assert cleared_lab.status_code == 200
        assert cleared_lab.json()["laboratory_name"] == ""
        assert cleared_lab.json()["specimen"] == "Urine"
        assert cleared_lab.json()["observations"][0]["name"] == "ALT"

        assert http.patch(panel_url, json={}).status_code == 422
        assert http.patch(panel_url, json={"title": " "}).status_code == 422
        assert http.patch(panel_url, json={"observations": []}).status_code == 422
        assert http.patch(panel_url, json={
            "observations": [{
                "name": "ALT",
                "value_numeric": 20,
                "reference_low": 40,
                "reference_high": 10,
            }],
        }).status_code == 422
        assert http.patch(panel_url, json={"observations": [{"name": "ALT"}]}).status_code == 422
        still = db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).one()
        assert still.name == "ALT"
        assert float(still.value_numeric) == 42

        note_source = sources[0]["source_id"]
        assert http.patch(
            f"/api/patients/{patient.id}/sources/{note_source}/panels/{record_id}",
            json={"client_summary": "No"},
        ).status_code == 404
        assert http.patch(
            f"/api/patients/{patient.id}/sources/{source_id}/panels/{uuid.uuid4()}",
            json={"title": "Missing"},
        ).status_code == 404
        wrong_source = http.post(
            f"/api/patients/{patient.id}/sources/{note_source}/panels",
            json={
                "title": "Nope",
                "recorded_on": "2026-06-12",
                "observations": [{"name": "Haemoglobin", "value_text": "140"}],
            },
        )
        assert wrong_source.status_code == 404
        blank = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Empty",
                "recorded_on": "2026-06-12",
                "observations": [{"name": " ", "value_text": "1"}],
            },
        )
        assert blank.status_code == 422
        missing_value = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "No value",
                "recorded_on": "2026-06-12",
                "observations": [{"name": "ALT"}],
            },
        )
        assert missing_value.status_code == 422
        backwards = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Range",
                "recorded_on": "2026-06-12",
                "observations": [{
                    "name": "ALT",
                    "value_numeric": 20,
                    "reference_low": 40,
                    "reference_high": 10,
                }],
            },
        )
        assert backwards.status_code == 422
        too_many = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Too many",
                "recorded_on": "2026-06-12",
                "observations": [{"name": f"Marker {index}", "value_numeric": index} for index in range(101)],
            },
        )
        assert too_many.status_code == 422

        holder["user"] = other
        assert http.patch(panel_url, json={"client_summary": "Hidden"}).status_code == 404
        assert http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Hidden",
                "recorded_on": "2026-06-12",
                "observations": [{"name": "Haemoglobin", "value_text": "1"}],
            },
        ).status_code == 404

        holder["user"] = client_user
        portal = http.get(f"/api/client/records/{patient.id}")
        assert portal.status_code == 200
        assert portal.json()["sources"] == []
        for hidden in (
            "Haemoglobin",
            "140",
            "g/L",
            "H. pylori antigen",
            "Positive",
            "June bloods",
            "The blood count is ready to discuss.",
            "Updated wording for the client",
            "North Lab",
            "South Lab",
            "ALT",
            "Urine",
        ):
            assert hidden not in portal.text

        holder["user"] = practitioner
        assert http.get(f"/api/patients/{patient.id}/progress-release").json()["released"] is True
        reports = http.get(f"/api/patients/{patient.id}/report-releases")
        assert reports.json()["assessment_reports"][0]["released"] is False
        db.refresh(link)
        assert link.token == "lab-share-token"
        assert link.is_active is True
        assert db.query(ShareLink).count() == 1
        assert db.query(ContentRelease).count() == release_count
        released_at = datetime.now(timezone.utc)
        release_row = ContentRelease(
            id=uuid.uuid4(),
            patient_id=patient.id,
            practitioner_user_id=practitioner.id,
            source_key=f"record:{record_id}",
            direction="clinic_to_client",
            released=True,
            released_at=released_at,
            created_at=released_at,
        )
        db.add(release_row)
        db.commit()
        kept_marker_id = still.id
        after_release = http.patch(panel_url, json={"observations": [{
            "name": "Ferritin",
            "value_text": "Low",
        }]})
        assert after_release.status_code == 200
        assert after_release.json()["released"] is True
        assert after_release.json()["client_summary"] == "Updated wording for the client"
        assert after_release.json()["observations"][0]["value_text"] == "Low"
        db.refresh(release_row)
        assert release_row.released is True
        assert release_row.withdrawn_at is None
        marker = db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).one()
        assert marker.id != kept_marker_id
        assert marker.name == "Ferritin"
        summary_only = http.patch(panel_url, json={"client_summary": "Wording after the flag"})
        assert summary_only.status_code == 200
        assert summary_only.json()["released"] is True
        assert summary_only.json()["observations"][0]["name"] == "Ferritin"
        assert db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).one().id == marker.id
        db.refresh(release_row)
        assert release_row.released is True
        assert db.query(ShareLink).count() == 1
        assert http.get(f"/api/patients/{patient.id}/progress-release").json()["released"] is True
        assert http.get(f"/api/patients/{patient.id}/report-releases").json()["assessment_reports"][0]["released"] is False
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
