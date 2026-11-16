"""Ending a claim withdraws clinic-note releases and leaves the notes in place."""

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

from app.api import (
    routes_client_records,
    routes_content_releases,
    routes_patient_claim_invites,
    routes_patient_sources,
)
from app.api.deps import get_current_user, get_db
from app.db.models import (
    Case,
    ContentRelease,
    Patient,
    PatientClaimInvite,
    PatientSource,
    ReportStatus,
    ReportVersion,
    ShareLink,
    SourceRecord,
    User,
)
from app.services.content_release_service import set_progress_release, set_report_release
from app.services.patient_claim_service import complete_reserved_claim, hash_claim_token


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_ending_a_claim_withdraws_note_releases_and_keeps_the_notes():
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
        PatientClaimInvite,
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

        def add_patient(first, last):
            patient = Patient(
                id=uuid.uuid4(),
                user_id=practitioner.id,
                first_name=first,
                last_name=last,
                full_name=f"{first} {last}",
                claimed_by_user_id=client_user.id,
                claimed_at=datetime.now(timezone.utc),
            )
            db.add(patient)
            db.commit()
            return patient

        claimed = add_patient("Ada", "Lovelace")
        other = add_patient("Grace", "Hopper")
        case = Case(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            patient_id=claimed.id,
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
            token="kept-share-token",
            is_active=True,
            share_type="report",
            patient_id=claimed.id,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        other_link = ShareLink(
            id=uuid.uuid4(),
            report_version_id=report.id,
            token="other-share-token",
            is_active=True,
            share_type="report",
            patient_id=other.id,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add_all([link, other_link])
        db.commit()

        app = FastAPI()
        app.include_router(routes_patient_sources.router)
        app.include_router(routes_content_releases.router)
        app.include_router(routes_patient_claim_invites.router)
        app.include_router(routes_client_records.router)
        holder = {"user": practitioner}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        def add_released_note(patient, title, text, summary):
            source_id = http.get(f"/api/patients/{patient.id}/sources").json()["sources"][0]["source_id"]
            created = http.post(
                f"/api/patients/{patient.id}/sources/{source_id}/records",
                json={
                    "title": title,
                    "recorded_on": "2026-11-01",
                    "text": text,
                    "client_summary": summary,
                },
            )
            assert created.status_code == 200
            record_id = created.json()["record_id"]
            released = http.post(
                f"/api/patients/{patient.id}/sources/{source_id}/records/{record_id}/release",
                json={"released": True},
            )
            assert released.status_code == 200
            return source_id, record_id

        source_id, record_id = add_released_note(
            claimed,
            "Morning observation",
            "Private raw observation",
            "Summary the client could see",
        )
        private = http.post(
            f"/api/patients/{claimed.id}/sources/{source_id}/records",
            json={
                "title": "Still private",
                "recorded_on": "2026-11-02",
                "text": "Second raw note",
                "client_summary": "Not released yet",
            },
        )
        assert private.status_code == 200
        other_source_id, other_record_id = add_released_note(
            other,
            "Other observation",
            "Other raw note",
            "Other client summary",
        )
        set_progress_release(db, claimed, True)
        set_report_release(db, claimed, report.id, True)
        set_progress_release(db, other, True)
        note_key = f"record:{record_id}"
        other_key = f"record:{other_record_id}"
        note_row = db.query(ContentRelease).filter(ContentRelease.source_key == note_key).one()
        assert note_row.released is True

        holder["user"] = practitioner
        unlinked = http.post(f"/api/patients/{claimed.id}/claim-access/unlink")
        assert unlinked.status_code == 200
        assert unlinked.json()["claimed"] is False
        assert "Private raw observation" not in unlinked.text
        assert "Summary the client could see" not in unlinked.text

        db.refresh(note_row)
        db.refresh(link)
        assert note_row.released is False
        assert note_row.withdrawn_at is not None
        assert db.query(ContentRelease).filter(ContentRelease.source_key == other_key).one().released is True
        progress_row = (
            db.query(ContentRelease)
            .filter(
                ContentRelease.patient_id == claimed.id,
                ContentRelease.source_key == "progress_view",
            )
            .one()
        )
        report_row = (
            db.query(ContentRelease)
            .filter(
                ContentRelease.patient_id == claimed.id,
                ContentRelease.source_key == str(report.id),
            )
            .one()
        )
        assert progress_row.released is False
        assert report_row.released is False
        stored = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(record_id)).one()
        assert stored.payload_json["text"] == "Private raw observation"
        assert stored.client_summary == "Summary the client could see"
        assert db.query(SourceRecord).filter(SourceRecord.patient_id == claimed.id).count() == 2
        assert link.token == "kept-share-token"
        assert link.is_active is True
        assert db.query(ShareLink).count() == 2

        holder["user"] = client_user
        assert http.get(f"/api/client/records/{claimed.id}").status_code == 404

        later = PatientClaimInvite(
            id=uuid.uuid4(),
            patient_id=claimed.id,
            practitioner_user_id=practitioner.id,
            email="ada@example.com",
            token_hash=hash_claim_token("later-note-token"),
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            created_at=datetime.now(timezone.utc),
            reserved_by_user_id=client_user.id,
        )
        db.add(later)
        db.commit()
        completed = complete_reserved_claim(db, client_user)
        db.commit()
        assert completed is not None
        db.refresh(note_row)
        assert note_row.released is False

        reopened = http.get(f"/api/client/records/{claimed.id}")
        assert reopened.status_code == 200
        assert reopened.json()["sources"] == []
        assert reopened.json()["reports"]["assessment_reports"] == []
        assert reopened.json()["trend"]["client_view"]["summary"] == (
            "Your practitioner has not released a progress view yet."
        )
        assert "Private raw observation" not in reopened.text
        assert "Summary the client could see" not in reopened.text
        assert "Morning observation" not in reopened.text

        holder["user"] = practitioner
        clinic = http.get(f"/api/patients/{claimed.id}/sources").json()["sources"][0]["records"]
        by_id = {item["record_id"]: item for item in clinic}
        assert by_id[record_id]["text"] == "Private raw observation"
        assert by_id[record_id]["client_summary"] == "Summary the client could see"
        assert by_id[record_id]["released"] is False
        assert by_id[private.json()["record_id"]]["text"] == "Second raw note"

        holder["user"] = client_user
        closed = http.post(f"/api/client/records/{other.id}/close-access")
        assert closed.status_code == 200
        assert closed.json()["claimed"] is False
        assert "Other raw note" not in closed.text
        db.refresh(other_link)
        other_row = db.query(ContentRelease).filter(ContentRelease.source_key == other_key).one()
        assert other_row.released is False
        assert other_row.withdrawn_at is not None
        other_note = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(other_record_id)).one()
        assert other_note.payload_json["text"] == "Other raw note"
        assert other_note.client_summary == "Other client summary"
        assert other_link.token == "other-share-token"
        assert other_link.is_active is True
        other_progress = (
            db.query(ContentRelease)
            .filter(
                ContentRelease.patient_id == other.id,
                ContentRelease.source_key == "progress_view",
            )
            .one()
        )
        assert other_progress.released is False
        db.refresh(note_row)
        assert note_row.released is False
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
