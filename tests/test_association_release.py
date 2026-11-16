"""Releases in both directions work only while an association is active."""

import os
import uuid
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api.deps import get_current_user, get_db
from app.api import routes_client_records, routes_content_releases, routes_patient_sources
from app.db.models import (
    Case,
    ClinicAssociation,
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
from app.services.clinic_association_service import accept_person_request, request_practitioner_by_email
from app.services.content_release_service import progress_release_state, released_client_reports, set_report_release
from app.services.patient_source_service import (
    client_released_note,
    create_lab_panel,
    ensure_laboratory_source,
    list_patient_sources,
    released_client_sources,
    set_lab_panel_release,
)
from app.services.personal_record_service import ensure_personal_record
from app.services.personal_source_service import list_personal_notes
from app.services.client_record_service import require_claimed_patient


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


MODELS = (
    User,
    Patient,
    ClinicAssociation,
    ContentRelease,
    PatientSource,
    SourceRecord,
    SourceObservation,
    Case,
    ReportVersion,
    ShareLink,
)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    for model in MODELS:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
    for model in MODELS:
        model.__table__.create(engine)
    return engine, originals, Session(engine)


def _restore(engine, originals):
    for column, kind in originals:
        column.type = kind
    engine.dispose()


def _user(email, role, name):
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash="hash",
        full_name=name,
        role=role,
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )


def test_releases_follow_the_active_association():
    engine, originals, db = _session()
    try:
        person = _user("ada@example.com", "client", "Ada Lovelace")
        practitioner = _user("clinic@example.com", "practitioner", "North Clinic")
        practitioner.clinic_name = "North Clinic"
        db.add_all([person, practitioner])
        db.commit()
        personal = ensure_personal_record(db, person, commit=True)
        request_practitioner_by_email(db, person, practitioner.email)
        pending = db.query(ClinicAssociation).one()

        app = FastAPI()
        app.include_router(routes_client_records.router)
        app.include_router(routes_patient_sources.router)
        app.include_router(routes_content_releases.router)
        holder = {"user": person}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        note = http.post(
            f"/api/client/records/{personal.id}/notes",
            json={"title": "Shelf", "recorded_on": "2026-11-16", "text": "Personal shelf note 77110"},
        )
        assert note.status_code == 200
        note_id = note.json()["record_id"]
        refused_note = http.post(
            f"/api/client/records/{personal.id}/notes/{note_id}/release",
            json={"released": True},
        )
        assert refused_note.status_code == 403
        assert "Personal shelf note 77110" not in refused_note.text

        assert http.post(
            f"/api/client/records/{personal.id}/wearable",
            json={"external_account_ref": "ohs-77111"},
        ).status_code == 200
        synced = http.post(
            f"/api/client/records/{personal.id}/wearable/days",
            json={"days": [{"recorded_on": "2026-11-16", "steps": 77111}]},
        )
        assert synced.status_code == 200
        day_id = synced.json()["days"][0]["record_id"]
        refused_day = http.post(
            f"/api/client/records/{personal.id}/wearable/days/{day_id}/release",
            json={"released": True},
        )
        assert refused_day.status_code == 403
        assert "77111" not in refused_day.text

        accepted = accept_person_request(db, practitioner, pending.id)
        clinic = db.query(Patient).filter(Patient.id == uuid.UUID(accepted["clinic_patient_id"])).one()
        link = ShareLink(
            id=uuid.uuid4(),
            report_version_id=uuid.uuid4(),
            token="kept-release-share",
            is_active=True,
            share_type="report",
            patient_id=clinic.id,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        case = Case(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            patient_id=clinic.id,
            title="Morning scan",
            status="draft",
        )
        db.add(case)
        db.flush()
        report = ReportVersion(
            id=link.report_version_id,
            case_id=case.id,
            created_by_user_id=practitioner.id,
            version_number=1,
            status=ReportStatus.ready,
            recommendation_mode="natural_approaches_clinical",
            report_type="assessment",
            is_archived=False,
        )
        db.add_all([report, link])
        db.commit()

        holder["user"] = practitioner
        blocked = http.post(
            f"/api/patients/{personal.id}/progress-release",
            json={"released": True},
        )
        assert blocked.status_code == 404

        clinic_note = http.post(
            f"/api/patients/{clinic.id}/sources/{list_patient_sources(db, clinic)['sources'][0]['source_id']}/records",
            json={
                "title": "Visit",
                "recorded_on": "2026-11-16",
                "text": "Private clinic wording 77112",
                "client_summary": "Summary the client could see 77113",
            },
        )
        assert clinic_note.status_code == 200
        clinic_note_id = clinic_note.json()["record_id"]
        lab_source = ensure_laboratory_source(db, clinic)
        panel = create_lab_panel(
            db,
            clinic,
            lab_source.id,
            title="Blood",
            recorded_on=date(2026, 11, 16),
            specimen="",
            laboratory_name="",
            client_summary="Lab summary 77115",
            observations=[{"name": "Ferritin shared", "value_numeric": 77114, "unit": "ug/L"}],
            created_by_user_id=practitioner.id,
        )

        holder["user"] = person
        released_note = http.post(
            f"/api/client/records/{personal.id}/notes/{note_id}/release",
            json={"released": True},
        )
        assert released_note.status_code == 200
        assert released_note.json() == {"record_id": note_id, "released": True}
        assert "Personal shelf note 77110" not in released_note.text
        released_day = http.post(
            f"/api/client/records/{personal.id}/wearable/days/{day_id}/release",
            json={"released": True},
        )
        assert released_day.status_code == 200
        assert released_day.json()["released"] is True

        holder["user"] = practitioner
        assert http.post(
            f"/api/patients/{clinic.id}/sources/{list_patient_sources(db, clinic)['sources'][0]['source_id']}/records/{clinic_note_id}/release",
            json={"released": True},
        ).status_code == 200
        assert set_lab_panel_release(db, clinic, lab_source.id, uuid.UUID(panel["record_id"]), True)["released"] is True
        assert http.post(f"/api/patients/{clinic.id}/progress-release", json={"released": True}).status_code == 200
        report_state = set_report_release(db, clinic, report.id, True)
        assert report_state["released"] is True

        clinic_view = str(list_patient_sources(db, clinic))
        assert "Personal shelf note 77110" in clinic_view
        assert "77111" in clinic_view
        assert "Ferritin shared" not in clinic_view or "77114" in clinic_view
        person_sources = str(released_client_sources(db, clinic))
        assert "Visit" in person_sources
        assert "Private clinic wording 77112" not in person_sources
        assert "Personal shelf note 77110" not in person_sources
        summary = client_released_note(db, clinic, uuid.UUID(clinic_note_id))
        assert summary["client_summary"] == "Summary the client could see 77113"
        assert "text" not in summary
        assert progress_release_state(db, clinic)["released"] is True
        assert released_client_reports(db, clinic)["assessment_reports"][0]["report_id"] == str(report.id)
        assert require_claimed_patient(db, person, clinic.id).id == clinic.id
        assert list_personal_notes(db, personal, person)["notes"][0]["text"] == "Personal shelf note 77110"
        assert db.query(SourceRecord).filter(SourceRecord.patient_id == personal.id, SourceRecord.id == uuid.UUID(note_id)).count() == 1
        db.refresh(link)
        assert link.token == "kept-release-share"
        assert link.is_active is True
        assert db.query(ShareLink).count() == 1

        holder["user"] = person
        assert http.post(
            f"/api/client/records/{personal.id}/notes/{note_id}/release",
            json={"released": False},
        ).json()["released"] is False
        assert http.post(
            f"/api/client/records/{personal.id}/wearable/days/{day_id}/release",
            json={"released": False},
        ).json()["released"] is False
        hidden = str(list_patient_sources(db, clinic))
        assert "Personal shelf note 77110" not in hidden
        assert "77111" not in hidden

        holder["user"] = practitioner
        assert http.post(
            f"/api/patients/{clinic.id}/sources/{list_patient_sources(db, clinic)['sources'][0]['source_id']}/records/{clinic_note_id}/release",
            json={"released": False},
        ).status_code == 200
        assert "Visit" not in str(released_client_sources(db, clinic))

        association = db.query(ClinicAssociation).filter(ClinicAssociation.id == pending.id).one()
        association.status = "ended"
        association.ended_at = datetime.now(timezone.utc)
        association.ended_by = "practitioner"
        db.commit()

        holder["user"] = person
        assert http.post(
            f"/api/client/records/{personal.id}/notes/{note_id}/release",
            json={"released": True},
        ).status_code == 403
        assert http.post(
            f"/api/client/records/{personal.id}/wearable/days/{day_id}/release",
            json={"released": True},
        ).status_code == 403
        holder["user"] = practitioner
        assert http.post(f"/api/patients/{clinic.id}/progress-release", json={"released": True}).status_code == 409
        assert progress_release_state(db, clinic)["released"] is False
        assert released_client_reports(db, clinic)["assessment_reports"] == []
        assert "Personal shelf note 77110" not in str(list_patient_sources(db, clinic))
        assert list_personal_notes(db, personal, person)["notes"][0]["released"] is False
        assert list_personal_notes(db, personal, person)["notes"][0]["text"] == "Personal shelf note 77110"
        db.refresh(link)
        assert link.token == "kept-release-share"
        db.close()
    finally:
        _restore(engine, originals)
