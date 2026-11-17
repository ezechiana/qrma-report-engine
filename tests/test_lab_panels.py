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
from app.services.content_release_service import _as_report_id, set_progress_release


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


def test_practitioner_can_release_and_withdraw_one_lab_panel(monkeypatch):
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
            token="panel-release-token",
            is_active=True,
            share_type="report",
            patient_id=patient.id,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(link)
        db.commit()

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

        sources = http.get(f"/api/patients/{patient.id}/sources").json()["sources"]
        source_id = sources[1]["source_id"]
        created = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "June bloods",
                "recorded_on": "2026-06-12",
                "specimen": "Blood",
                "laboratory_name": "North Lab",
                "client_summary": "The blood count is ready to discuss.",
                "observations": [{
                    "name": "Haemoglobin",
                    "value_numeric": 140,
                    "value_text": "140",
                    "unit": "g/L",
                    "reference_low": 115,
                    "reference_high": 150,
                }],
            },
        )
        assert created.status_code == 200
        assert created.json()["released"] is False
        record_id = created.json()["record_id"]
        blank = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Internal bloods",
                "recorded_on": "2026-06-13",
                "client_summary": "",
                "observations": [{"name": "ALT", "value_text": "42"}],
            },
        )
        assert blank.status_code == 200
        assert blank.json()["released"] is False
        blank_id = blank.json()["record_id"]
        release_url = f"/api/patients/{patient.id}/sources/{source_id}/panels/{record_id}/release"

        denied = http.post(release_url, json={"released": True})
        assert denied.status_code == 409
        assert db.query(ContentRelease).count() == 0

        patient.claimed_by_user_id = client_user.id
        patient.claimed_at = datetime.now(timezone.utc)
        db.commit()
        set_progress_release(db, patient, True)
        progress_count = db.query(ContentRelease).count()

        empty = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels/{blank_id}/release",
            json={"released": True},
        )
        assert empty.status_code == 422
        assert db.query(ContentRelease).filter(ContentRelease.source_key == f"record:{blank_id}").count() == 0

        released = http.post(release_url, json={"released": True})
        assert released.status_code == 200
        body = released.json()
        assert body["released"] is True
        assert body["record_id"] == record_id
        assert "observations" not in body
        assert "client_summary" not in body
        assert "Haemoglobin" not in released.text
        assert "140" not in released.text
        panel_key = f"record:{record_id}"
        row = db.query(ContentRelease).filter(ContentRelease.source_key == panel_key).one()
        assert row.released is True
        assert row.direction == "clinic_to_client"
        assert _as_report_id(row.source_key) is None
        marker = db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).one()
        assert db.query(ContentRelease).filter(ContentRelease.source_key == str(marker.id)).count() == 0

        note_source = sources[0]["source_id"]
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
        unreleased = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Unreleased bloods",
                "recorded_on": "2026-06-20",
                "client_summary": "Not ready for the client",
                "observations": [{"name": "Ferritin", "value_text": "Low"}],
            },
        )
        assert unreleased.status_code == 200
        unreleased_id = unreleased.json()["record_id"]

        listed = http.get(f"/api/patients/{patient.id}/sources").json()["sources"][1]["records"]
        by_id = {item["record_id"]: item for item in listed}
        assert by_id[record_id]["released"] is True
        assert by_id[blank_id]["released"] is False
        assert by_id[record_id]["observations"][0]["value_numeric"] == 140
        assert by_id[record_id]["observations"][0]["unit"] == "g/L"

        holder["user"] = client_user
        portal = http.get(f"/api/client/records/{patient.id}")
        assert portal.status_code == 200
        assert portal.json()["trend"]["client_view"]["summary"] == "Progress is ready to review."
        assert portal.json()["reports"]["assessment_reports"] == []
        assert portal.json()["sources"] == [
            {
                "display_name": "Clinic notes",
                "records": [{
                    "record_id": note_id,
                    "title": "Visit note",
                    "recorded_on": "2026-11-01",
                }],
            },
            {
                "display_name": "Lab results",
                "records": [{
                    "record_id": record_id,
                    "title": "June bloods",
                    "recorded_on": "2026-06-12",
                }],
            },
        ]
        for item in portal.json()["sources"][1]["records"]:
            assert set(item) == {"record_id", "title", "recorded_on"}
        opened = http.get(f"/api/client/records/{patient.id}/notes/{record_id}")
        assert opened.status_code == 200
        panel = opened.json()
        assert set(panel) == {"record_id", "title", "recorded_on", "client_summary", "source_name"}
        assert panel["title"] == "June bloods"
        assert panel["recorded_on"] == "2026-06-12"
        assert panel["client_summary"] == "The blood count is ready to discuss."
        assert panel["source_name"] == "Lab results"
        assert http.get(f"/api/client/records/{patient.id}/notes/{blank_id}").status_code == 404
        assert http.get(f"/api/client/records/{patient.id}/notes/{unreleased_id}").status_code == 404
        for hidden in (
            "Haemoglobin",
            "g/L",
            "115",
            "150",
            "North Lab",
            "Blood",
            "ALT",
            "Internal bloods",
            "Ferritin",
            "Low",
            "Unreleased bloods",
            "Not ready for the client",
            "Private clinic wording",
            "observations",
            "value_numeric",
            "payload_json",
            "interpretation",
        ):
            assert hidden not in portal.text
            assert hidden not in opened.text
        assert "The blood count is ready to discuss." not in portal.text
        record_page = open("app/templates/client_record.html", encoding="utf-8").read()
        note_page = open("app/templates/client_note.html", encoding="utf-8").read()
        assert "Lab results" not in record_page
        assert "value_numeric" not in record_page
        assert "payload_json" not in record_page
        assert "client_summary" in note_page
        assert "value_numeric" not in note_page
        assert "payload_json" not in note_page

        holder["user"] = practitioner
        withdrawn = http.post(release_url, json={"released": False})
        assert withdrawn.status_code == 200
        assert withdrawn.json()["released"] is False
        assert "Haemoglobin" not in withdrawn.text
        db.refresh(row)
        assert row.released is False
        assert row.withdrawn_at is not None
        stored = db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).one()
        assert stored.name == "Haemoglobin"
        assert float(stored.value_numeric) == 140
        assert db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(record_id)).one().client_summary == (
            "The blood count is ready to discuss."
        )

        assert http.get(f"/api/patients/{patient.id}/progress-release").json()["released"] is True
        reports = http.get(f"/api/patients/{patient.id}/report-releases")
        assert reports.json()["assessment_reports"][0]["released"] is False
        db.refresh(link)
        assert link.token == "panel-release-token"
        assert link.is_active is True
        assert db.query(ShareLink).count() == 1
        assert db.query(ContentRelease).count() == progress_count + 2
        holder["user"] = client_user
        after = http.get(f"/api/client/records/{patient.id}")
        assert after.json()["sources"] == [{
            "display_name": "Clinic notes",
            "records": [{
                "record_id": note_id,
                "title": "Visit note",
                "recorded_on": "2026-11-01",
            }],
        }]
        assert "June bloods" not in after.text
        assert "Haemoglobin" not in after.text
        assert after.json()["trend"]["client_view"]["summary"] == "Progress is ready to review."
        assert http.get(f"/api/client/records/{patient.id}/notes/{record_id}").status_code == 404
        holder["user"] = practitioner
        assert http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/records/{record_id}/release",
            json={"released": True},
        ).status_code == 404

        holder["user"] = other
        assert http.post(release_url, json={"released": True}).status_code == 404
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()


def test_practitioner_can_release_and_withdraw_marker_values_for_one_panel():
    assert _client_blocked_from_practitioner_path(
        User(role="client"),
        "/api/patients/1/sources/2/panels/3/marker-release",
    ) is True
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
            token="marker-release-token",
            is_active=True,
            share_type="report",
            patient_id=patient.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(link)
        db.commit()

        app = FastAPI()
        app.include_router(routes_patient_sources.router)
        app.include_router(routes_client_records.router)
        holder = {"user": practitioner}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        sources = http.get(f"/api/patients/{patient.id}/sources").json()["sources"]
        note_source = sources[0]["source_id"]
        source_id = sources[1]["source_id"]
        created = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "June bloods",
                "recorded_on": "2026-06-12",
                "client_summary": "The blood count is ready to discuss.",
                "observations": [{
                    "name": "Haemoglobin",
                    "value_numeric": 140,
                    "value_text": "140",
                    "unit": "g/L",
                    "reference_low": 115,
                    "reference_high": 150,
                }],
            },
        )
        assert created.status_code == 200
        assert created.json()["released"] is False
        assert created.json()["markers_released"] is False
        record_id = created.json()["record_id"]
        summary_only = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Summary only bloods",
                "recorded_on": "2026-06-13",
                "client_summary": "Discuss at the next visit.",
                "observations": [{"name": "Ferritin", "value_text": "Low"}],
            },
        )
        assert summary_only.status_code == 200
        summary_id = summary_only.json()["record_id"]
        marker_url = f"/api/patients/{patient.id}/sources/{source_id}/panels/{record_id}/marker-release"
        summary_url = f"/api/patients/{patient.id}/sources/{source_id}/panels/{record_id}/release"

        patient.claimed_by_user_id = None
        patient.claimed_at = None
        db.commit()
        closed = http.post(marker_url, json={"released": True})
        assert closed.status_code == 409
        assert db.query(ContentRelease).count() == 0
        patient.claimed_by_user_id = client_user.id
        patient.claimed_at = datetime.now(timezone.utc)
        db.commit()

        early = http.post(marker_url, json={"released": True})
        assert early.status_code == 409
        assert early.json()["detail"] == "Release the panel before releasing marker values."
        assert db.query(ContentRelease).filter(ContentRelease.source_key == f"markers:{record_id}").count() == 0

        note = http.post(
            f"/api/patients/{patient.id}/sources/{note_source}/records",
            json={
                "title": "Visit note",
                "recorded_on": "2026-11-01",
                "text": "Private clinic wording",
                "client_summary": "A short note for the client",
            },
        )
        note_id = note.json()["record_id"]
        assert http.post(
            f"/api/patients/{patient.id}/sources/{note_source}/panels/{note_id}/marker-release",
            json={"released": True},
        ).status_code == 404

        released_summary = http.post(summary_url, json={"released": True})
        assert released_summary.status_code == 200
        http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels/{summary_id}/release",
            json={"released": True},
        )
        listed = http.get(f"/api/patients/{patient.id}/sources").json()["sources"][1]["records"]
        by_id = {item["record_id"]: item for item in listed}
        assert by_id[record_id]["released"] is True
        assert by_id[record_id]["markers_released"] is False
        assert by_id[summary_id]["markers_released"] is False
        assert by_id[record_id]["observations"][0]["name"] == "Haemoglobin"

        turned_on = http.post(marker_url, json={"released": True})
        assert turned_on.status_code == 200
        assert set(turned_on.json()) == {"patient_id", "record_id", "released", "available"}
        assert turned_on.json()["released"] is True
        assert turned_on.json()["record_id"] == record_id
        for hidden in ("Haemoglobin", "140", "g/L", "observations", "client_summary", "value_numeric"):
            assert hidden not in turned_on.text
        marker_key = f"markers:{record_id}"
        marker_row = db.query(ContentRelease).filter(ContentRelease.source_key == marker_key).one()
        summary_row = db.query(ContentRelease).filter(ContentRelease.source_key == f"record:{record_id}").one()
        assert marker_row.released is True
        assert marker_row.direction == "clinic_to_client"
        assert summary_row.released is True
        assert _as_report_id(marker_row.source_key) is None
        stored_link = db.query(ShareLink).one()
        assert stored_link.token == "marker-release-token"
        assert stored_link.is_active is True
        listed = http.get(f"/api/patients/{patient.id}/sources").json()["sources"][1]["records"]
        released_panel = {item["record_id"]: item for item in listed}[record_id]
        assert released_panel["markers_released"] is True
        assert released_panel["observations"][0]["value_numeric"] == 140

        holder["user"] = client_user
        opened = http.get(f"/api/client/records/{patient.id}/notes/{record_id}")
        assert opened.status_code == 200
        assert opened.json()["observations"] == [{
            "name": "Haemoglobin",
            "value": "140",
            "unit": "g/L",
            "reference_range": "115–150",
        }]

        holder["user"] = practitioner
        turned_off = http.post(marker_url, json={"released": False})
        assert turned_off.status_code == 200
        assert turned_off.json()["released"] is False
        db.refresh(marker_row)
        db.refresh(summary_row)
        assert marker_row.released is False
        assert marker_row.withdrawn_at is not None
        assert summary_row.released is True
        stored = db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).one()
        assert stored.name == "Haemoglobin"
        assert float(stored.value_numeric) == 140
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()


def test_withdrawing_the_summary_also_withdraws_marker_values():
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
            token="summary-withdraw-token",
            is_active=True,
            share_type="report",
            patient_id=patient.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(link)
        db.commit()

        app = FastAPI()
        app.include_router(routes_patient_sources.router)
        app.include_router(routes_client_records.router)
        holder = {"user": practitioner}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        sources = http.get(f"/api/patients/{patient.id}/sources").json()["sources"]
        source_id = sources[1]["source_id"]
        created = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "June bloods",
                "recorded_on": "2026-06-12",
                "client_summary": "The blood count is ready to discuss.",
                "observations": [{
                    "name": "Haemoglobin",
                    "value_numeric": 140,
                    "value_text": "140",
                    "unit": "g/L",
                }],
            },
        )
        assert created.status_code == 200
        record_id = created.json()["record_id"]
        other = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Other bloods",
                "recorded_on": "2026-06-13",
                "client_summary": "The other panel stays available.",
                "observations": [{"name": "Platelets", "value_text": "250"}],
            },
        )
        assert other.status_code == 200
        other_id = other.json()["record_id"]
        summary_url = f"/api/patients/{patient.id}/sources/{source_id}/panels/{record_id}/release"
        marker_url = f"/api/patients/{patient.id}/sources/{source_id}/panels/{record_id}/marker-release"
        other_summary = f"/api/patients/{patient.id}/sources/{source_id}/panels/{other_id}/release"
        other_marker = f"/api/patients/{patient.id}/sources/{source_id}/panels/{other_id}/marker-release"

        assert http.post(summary_url, json={"released": True}).status_code == 200
        assert http.post(other_summary, json={"released": True}).status_code == 200
        assert http.post(marker_url, json={"released": True}).status_code == 200
        assert http.post(other_marker, json={"released": True}).status_code == 200

        withdrawn = http.post(summary_url, json={"released": False})
        assert withdrawn.status_code == 200
        assert set(withdrawn.json()) == {"patient_id", "record_id", "released", "available"}
        assert withdrawn.json()["released"] is False
        for hidden in ("Haemoglobin", "140", "Platelets", "250", "observations", "client_summary"):
            assert hidden not in withdrawn.text
        summary_row = db.query(ContentRelease).filter(ContentRelease.source_key == f"record:{record_id}").one()
        marker_row = db.query(ContentRelease).filter(ContentRelease.source_key == f"markers:{record_id}").one()
        other_summary_row = db.query(ContentRelease).filter(ContentRelease.source_key == f"record:{other_id}").one()
        other_marker_row = db.query(ContentRelease).filter(ContentRelease.source_key == f"markers:{other_id}").one()
        assert summary_row.released is False
        assert summary_row.withdrawn_at is not None
        assert marker_row.released is False
        assert marker_row.withdrawn_at is not None
        assert other_summary_row.released is True
        assert other_marker_row.released is True
        stored_link = db.query(ShareLink).one()
        assert stored_link.token == "summary-withdraw-token"
        assert stored_link.is_active is True

        restored = http.post(summary_url, json={"released": True})
        assert restored.status_code == 200
        assert restored.json()["released"] is True
        db.refresh(summary_row)
        db.refresh(marker_row)
        db.refresh(other_marker_row)
        assert summary_row.released is True
        assert marker_row.released is False
        assert other_marker_row.released is True
        listed = http.get(f"/api/patients/{patient.id}/sources").json()["sources"][1]["records"]
        by_id = {item["record_id"]: item for item in listed}
        assert by_id[record_id]["released"] is True
        assert by_id[record_id]["markers_released"] is False
        assert by_id[other_id]["markers_released"] is True

        assert http.post(marker_url, json={"released": True}).status_code == 200
        cleared = http.patch(
            f"/api/patients/{patient.id}/sources/{source_id}/panels/{record_id}",
            json={"client_summary": "   "},
        )
        assert cleared.status_code == 200
        db.refresh(summary_row)
        db.refresh(marker_row)
        assert summary_row.released is True
        assert marker_row.released is True
        stored = db.query(SourceObservation).filter(SourceObservation.source_record_id == uuid.UUID(record_id)).one()
        assert stored.name == "Haemoglobin"
        assert float(stored.value_numeric) == 140

        holder["user"] = client_user
        portal = http.get(f"/api/client/records/{patient.id}")
        assert portal.status_code == 200
        titles = [item["title"] for group in portal.json()["sources"] for item in group["records"]]
        assert titles == ["Other bloods"]
        hidden_panel = http.get(f"/api/client/records/{patient.id}/notes/{record_id}")
        assert hidden_panel.status_code == 404
        assert hidden_panel.json()["detail"] == "Note not found"
        for hidden in ("Haemoglobin", "140", "Platelets", "250", "observations", "June bloods"):
            assert hidden not in hidden_panel.text
            assert hidden not in portal.text
        opened_other = http.get(f"/api/client/records/{patient.id}/notes/{other_id}")
        assert opened_other.status_code == 200
        assert opened_other.json()["observations"] == [{
            "name": "Platelets",
            "value": "250",
            "unit": "",
            "reference_range": "",
        }]
        db.refresh(stored_link)
        assert stored_link.token == "summary-withdraw-token"
        assert stored_link.is_active is True
        assert db.query(ShareLink).count() == 1
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()


def test_client_sees_marker_values_only_when_that_release_is_on():
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
        stranger = User(
            id=uuid.uuid4(),
            email="other@example.com",
            full_name="Other Client",
            password_hash="unused",
            is_active=True,
            role="client",
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add_all([practitioner, client_user, stranger])
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
            token="marker-display-token",
            is_active=True,
            share_type="report",
            patient_id=patient.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(link)
        db.commit()

        app = FastAPI()
        app.include_router(routes_patient_sources.router)
        app.include_router(routes_client_records.router)
        holder = {"user": practitioner}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        sources = http.get(f"/api/patients/{patient.id}/sources").json()["sources"]
        note_source = sources[0]["source_id"]
        source_id = sources[1]["source_id"]
        created = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "June bloods",
                "recorded_on": "2026-06-12",
                "specimen": "SpecimenSecret",
                "laboratory_name": "LaboratorySecret",
                "client_summary": "The blood count is ready to discuss.",
                "observations": [
                    {
                        "name": "Ferritin",
                        "value_text": "Low",
                        "value_numeric": 9,
                        "unit": "ug/L",
                        "reference_text": "30 to 400",
                        "code": "SHOULD-NOT-LEAK",
                        "interpretation": "abnormal",
                    },
                    {
                        "name": "Haemoglobin",
                        "value_numeric": 140.5,
                        "unit": "g/L",
                        "reference_low": 115,
                        "reference_high": 150,
                    },
                ],
            },
        )
        assert created.status_code == 200
        record_id = created.json()["record_id"]
        quiet = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Quiet bloods",
                "recorded_on": "2026-06-13",
                "client_summary": "Summary only for this panel.",
                "observations": [{
                    "name": "HiddenMarkerZZ",
                    "value_text": "secret-99",
                    "code": "QUIET-CODE",
                    "interpretation": "abnormal",
                }],
            },
        )
        assert quiet.status_code == 200
        quiet_id = quiet.json()["record_id"]
        blank = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels",
            json={
                "title": "Empty bloods",
                "recorded_on": "2026-06-14",
                "client_summary": "No markers were stored.",
                "observations": [{"name": "Placeholder", "value_text": "temp"}],
            },
        )
        assert blank.status_code == 200
        blank_id = blank.json()["record_id"]
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
        for panel_id in (record_id, quiet_id, blank_id):
            assert http.post(
                f"/api/patients/{patient.id}/sources/{source_id}/panels/{panel_id}/release",
                json={"released": True},
            ).status_code == 200
        assert http.post(
            f"/api/patients/{patient.id}/sources/{note_source}/records/{note_id}/release",
            json={"released": True},
        ).status_code == 200

        hidden = (
            "HiddenMarkerZZ",
            "secret-99",
            "QUIET-CODE",
            "SHOULD-NOT-LEAK",
            "abnormal",
            "SpecimenSecret",
            "LaboratorySecret",
            "value_numeric",
            "reference_low",
            "reference_text",
            "payload_json",
            "Private clinic wording",
        )

        def assert_no_marker_leak(response, note=False):
            items = hidden + (("interpretation",) if note else ())
            for item in items:
                assert item not in response.text

        holder["user"] = client_user
        closed = http.get(f"/api/client/records/{patient.id}/notes/{record_id}")
        assert closed.status_code == 200
        assert set(closed.json()) == {"record_id", "title", "recorded_on", "client_summary", "source_name"}
        assert "observations" not in closed.json()
        assert "Ferritin" not in closed.text
        assert "140.5" not in closed.text
        assert_no_marker_leak(closed, note=True)
        quiet_closed = http.get(f"/api/client/records/{patient.id}/notes/{quiet_id}")
        assert quiet_closed.status_code == 200
        assert "observations" not in quiet_closed.json()
        assert_no_marker_leak(quiet_closed, note=True)
        note_opened = http.get(f"/api/client/records/{patient.id}/notes/{note_id}")
        assert note_opened.status_code == 200
        assert "observations" not in note_opened.json()
        assert "Private clinic wording" not in note_opened.text

        portal = http.get(f"/api/client/records/{patient.id}")
        listed = [item for group in portal.json()["sources"] for item in group["records"]]
        assert {item["title"] for item in listed} == {"June bloods", "Quiet bloods", "Empty bloods", "Visit note"}
        assert all(set(item) == {"record_id", "title", "recorded_on"} for item in listed)
        assert_no_marker_leak(portal)
        assert "Ferritin" not in portal.text
        assert "140.5" not in portal.text
        today = http.get("/api/client/today")
        assert today.status_code == 200
        assert {item["title"] for item in today.json()["from_clinic"]} >= {"June bloods", "Quiet bloods"}
        assert all(set(item) == {"title", "recorded_on", "patient_id"} for item in today.json()["from_clinic"])
        assert_no_marker_leak(today)
        assert "Ferritin" not in today.text
        assert "observations" not in today.text

        holder["user"] = practitioner
        assert http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels/{record_id}/marker-release",
            json={"released": True},
        ).status_code == 200
        assert http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels/{blank_id}/marker-release",
            json={"released": True},
        ).status_code == 200
        db.query(SourceObservation).filter(
            SourceObservation.source_record_id == uuid.UUID(blank_id)
        ).delete()
        db.commit()

        holder["user"] = client_user
        opened = http.get(f"/api/client/records/{patient.id}/notes/{record_id}")
        assert opened.status_code == 200
        assert set(opened.json()) == {
            "record_id",
            "title",
            "recorded_on",
            "client_summary",
            "source_name",
            "observations",
        }
        assert opened.json()["observations"] == [
            {"name": "Ferritin", "value": "Low", "unit": "ug/L", "reference_range": "30 to 400"},
            {"name": "Haemoglobin", "value": "140.5", "unit": "g/L", "reference_range": "115–150"},
        ]
        assert all(set(row) == {"name", "value", "unit", "reference_range"} for row in opened.json()["observations"])
        assert_no_marker_leak(opened, note=True)
        empty = http.get(f"/api/client/records/{patient.id}/notes/{blank_id}")
        assert empty.status_code == 200
        assert empty.json()["observations"] == []
        assert_no_marker_leak(empty, note=True)
        still_quiet = http.get(f"/api/client/records/{patient.id}/notes/{quiet_id}")
        assert "observations" not in still_quiet.json()
        assert_no_marker_leak(still_quiet, note=True)
        still_note = http.get(f"/api/client/records/{patient.id}/notes/{note_id}")
        assert "observations" not in still_note.json()
        portal = http.get(f"/api/client/records/{patient.id}")
        listed = [item for group in portal.json()["sources"] for item in group["records"]]
        assert all(set(item) == {"record_id", "title", "recorded_on"} for item in listed)
        assert_no_marker_leak(portal)
        assert "Ferritin" not in portal.text
        assert "140.5" not in portal.text
        today = http.get("/api/client/today")
        assert all(set(item) == {"title", "recorded_on", "patient_id"} for item in today.json()["from_clinic"])
        assert_no_marker_leak(today)
        assert "Ferritin" not in today.text
        assert "140.5" not in today.text

        holder["user"] = stranger
        missing = http.get(f"/api/client/records/{patient.id}/notes/{record_id}")
        assert missing.status_code == 404
        assert_no_marker_leak(missing, note=True)
        assert "Ferritin" not in missing.text

        holder["user"] = practitioner
        withdrawn = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/panels/{record_id}/marker-release",
            json={"released": False},
        )
        assert withdrawn.status_code == 200
        holder["user"] = client_user
        after = http.get(f"/api/client/records/{patient.id}/notes/{record_id}")
        assert after.status_code == 200
        assert "observations" not in after.json()
        assert after.json()["client_summary"] == "The blood count is ready to discuss."
        assert "Ferritin" not in after.text
        assert "Haemoglobin" not in after.text
        assert "140.5" not in after.text
        assert_no_marker_leak(after, note=True)
        stored_link = db.query(ShareLink).one()
        assert stored_link.token == "marker-display-token"
        assert stored_link.is_active is True
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
