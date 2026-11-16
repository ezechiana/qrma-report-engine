"""The client portal lists and opens only released clinic notes."""

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

from app.api import routes_client_records, routes_content_releases, routes_patient_sources, routes_ui
from app.api.deps import get_current_user, get_db
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
from app.services.content_release_service import set_progress_release


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_client_sees_released_notes_without_the_raw_text(monkeypatch):
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
            token="note-portal-share",
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
        app.include_router(routes_ui.router)
        holder = {"user": practitioner}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        source_id = http.get(f"/api/patients/{patient.id}/sources").json()["sources"][0]["source_id"]

        def add_note(title, recorded_on, text, summary):
            response = http.post(
                f"/api/patients/{patient.id}/sources/{source_id}/records",
                json={
                    "title": title,
                    "recorded_on": recorded_on,
                    "text": text,
                    "client_summary": summary,
                },
            )
            assert response.status_code == 200
            return response.json()["record_id"]

        morning_id = add_note(
            "Morning observation",
            "2026-11-01",
            "Private raw observation",
            "Summary the client could see",
        )
        hidden_id = add_note(
            "Hidden title",
            "2026-11-02",
            "Hidden raw note",
            "Hidden summary",
        )
        cleared_id = add_note(
            "Cleared title",
            "2026-11-04",
            "Cleared raw note",
            "Will be cleared",
        )
        follow_id = add_note(
            "Follow up",
            "2026-11-05",
            "Follow up raw note",
            "Follow up summary",
        )

        holder["user"] = client_user
        before = http.get(f"/api/client/records/{patient.id}")
        assert before.status_code == 200
        assert before.json()["sources"] == []
        assert "Morning observation" not in before.text

        holder["user"] = practitioner
        for record_id in (morning_id, cleared_id, follow_id):
            released = http.post(
                f"/api/patients/{patient.id}/sources/{source_id}/records/{record_id}/release",
                json={"released": True},
            )
            assert released.status_code == 200
        cleared = http.patch(
            f"/api/patients/{patient.id}/sources/{source_id}/records/{cleared_id}",
            json={"client_summary": ""},
        )
        assert cleared.status_code == 200

        holder["user"] = client_user
        listed = http.get(f"/api/client/records/{patient.id}")
        assert listed.status_code == 200
        body = listed.json()
        assert body["trend"]["client_view"]["summary"] == "Your practitioner has not released a progress view yet."
        assert body["reports"]["assessment_reports"] == []
        assert body["reports"]["trend_reports"] == []
        assert body["sources"] == [{
            "display_name": "Clinic notes",
            "records": [
                {"record_id": follow_id, "title": "Follow up", "recorded_on": "2026-11-05"},
                {"record_id": morning_id, "title": "Morning observation", "recorded_on": "2026-11-01"},
            ],
        }]
        for hidden_text in (
            "Private raw observation",
            "Hidden title",
            "Hidden raw note",
            "Hidden summary",
            "Cleared title",
            "Cleared raw note",
            "Will be cleared",
            "Follow up raw note",
            "Follow up summary",
            "Summary the client could see",
            "note-portal-share",
            "payload_json",
        ):
            assert hidden_text not in listed.text
        assert db.query(ShareLink).count() == 1

        opened = http.get(f"/api/client/records/{patient.id}/notes/{morning_id}")
        assert opened.status_code == 200
        note = opened.json()
        assert set(note) == {"record_id", "title", "recorded_on", "client_summary", "source_name"}
        assert note["title"] == "Morning observation"
        assert note["recorded_on"] == "2026-11-01"
        assert note["client_summary"] == "Summary the client could see"
        assert note["source_name"] == "Clinic notes"
        assert "Private raw observation" not in opened.text
        assert "payload_json" not in opened.text

        assert http.get(f"/api/client/records/{patient.id}/notes/{hidden_id}").status_code == 404
        assert http.get(f"/api/client/records/{patient.id}/notes/{cleared_id}").status_code == 404

        holder["user"] = other_client
        assert http.get(f"/api/client/records/{patient.id}").status_code == 404
        assert http.get(f"/api/client/records/{patient.id}/notes/{morning_id}").status_code == 404

        holder["user"] = client_user
        set_progress_release(db, patient, True)
        with_progress = http.get(f"/api/client/records/{patient.id}")
        assert with_progress.json()["trend"]["client_view"]["summary"] == "Progress is ready to review."
        assert [item["title"] for item in with_progress.json()["sources"][0]["records"]] == [
            "Follow up",
            "Morning observation",
        ]
        assert with_progress.json()["reports"]["assessment_reports"] == []
        db.refresh(link)
        assert link.token == "note-portal-share"
        assert link.is_active is True

        holder["user"] = practitioner
        withdrawn = http.post(
            f"/api/patients/{patient.id}/sources/{source_id}/records/{morning_id}/release",
            json={"released": False},
        )
        assert withdrawn.status_code == 200
        holder["user"] = client_user
        after = http.get(f"/api/client/records/{patient.id}")
        assert [item["title"] for item in after.json()["sources"][0]["records"]] == ["Follow up"]
        assert "Morning observation" not in after.text
        assert http.get(f"/api/client/records/{patient.id}/notes/{morning_id}").status_code == 404
        assert after.json()["trend"]["client_view"]["summary"] == "Progress is ready to review."

        page = http.get(f"/client/records/{patient.id}/notes/{follow_id}")
        assert page.status_code == 200
        assert "Loading your note" in page.text
        assert "Back to your record" in page.text
        record_page = open("app/templates/client_record.html", encoding="utf-8").read()
        note_page = open("app/templates/client_note.html", encoding="utf-8").read()
        assert "/notes/" in record_page
        assert "client_summary" not in record_page
        assert "payload_json" not in record_page
        assert "client_summary" in note_page
        assert "payload_json" not in note_page
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
