"""Ending a claim withdraws client-entry releases and leaves the entries stored."""

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

from app.api import routes_client_records, routes_content_releases, routes_patient_claim_invites, routes_patient_sources
from app.api.deps import get_current_user, get_db
from app.db.models import (
    Case,
    ContentRelease,
    Patient,
    ClinicAssociation,
    PatientClaimInvite,
    PatientSource,
    ReportStatus,
    ReportVersion,
    ShareLink,
    SourceRecord,
    User,
)
from app.services.content_release_service import set_progress_release
from app.services.patient_claim_service import complete_reserved_claim, hash_claim_token


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_ending_a_claim_withdraws_client_entry_releases_and_keeps_the_text():
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
        ClinicAssociation,
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
        holder = {"user": client_user}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        def send_entry(patient, title, text):
            created = http.post(
                f"/api/client/records/{patient.id}/entries",
                json={
                    "title": title,
                    "recorded_on": "2026-11-02",
                    "text": text,
                    "reference": "home-note.pdf",
                },
            )
            assert created.status_code == 200
            assert created.json()["released"] is True
            return created.json()["record_id"]

        record_id = send_entry(claimed, "Sleep note", "I slept poorly.")
        other_id = send_entry(other, "Other note", "Other private text.")
        set_progress_release(db, claimed, True)
        set_progress_release(db, other, True)
        entry_key = f"client:{record_id}"
        other_key = f"client:{other_id}"
        entry_row = db.query(ContentRelease).filter(ContentRelease.source_key == entry_key).one()
        assert entry_row.released is True
        assert entry_row.direction == "client_to_clinic"

        holder["user"] = practitioner
        visible = http.get(f"/api/patients/{claimed.id}/sources").json()["sources"]
        assert [item["display_name"] for item in visible] == ["Clinic notes", "Lab results", "From the client"]
        assert visible[2]["records"][0]["text"] == "I slept poorly."

        unlinked = http.post(f"/api/patients/{claimed.id}/claim-access/unlink")
        assert unlinked.status_code == 200
        assert unlinked.json()["claimed"] is False
        assert "I slept poorly." not in unlinked.text
        assert "home-note.pdf" not in unlinked.text

        db.refresh(entry_row)
        db.refresh(link)
        assert entry_row.released is False
        assert entry_row.withdrawn_at is not None
        assert db.query(ContentRelease).filter(ContentRelease.source_key == other_key).one().released is True
        progress_row = (
            db.query(ContentRelease)
            .filter(
                ContentRelease.patient_id == claimed.id,
                ContentRelease.source_key == "progress_view",
            )
            .one()
        )
        assert progress_row.released is False
        stored = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(record_id)).one()
        assert stored.payload_json == {
            "kind": "client_entry",
            "text": "I slept poorly.",
            "reference": "home-note.pdf",
        }
        assert stored.client_summary is None
        assert link.token == "kept-share-token"
        assert link.is_active is True
        assert db.query(ShareLink).count() == 2
        hidden = http.get(f"/api/patients/{claimed.id}/sources")
        assert [item["display_name"] for item in hidden.json()["sources"]] == ["Clinic notes", "Lab results"]
        assert "I slept poorly." not in hidden.text

        holder["user"] = client_user
        assert http.get(f"/api/client/records/{claimed.id}").status_code == 404
        assert http.get(f"/api/client/records/{claimed.id}/entries").status_code == 404

        later = PatientClaimInvite(
            id=uuid.uuid4(),
            patient_id=claimed.id,
            practitioner_user_id=practitioner.id,
            email="ada@example.com",
            token_hash=hash_claim_token("later-entry-token"),
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
        db.refresh(entry_row)
        assert entry_row.released is False

        reopened = http.get(f"/api/client/records/{claimed.id}/entries")
        assert reopened.status_code == 200
        assert reopened.json()["entries"][0]["text"] == "I slept poorly."
        assert reopened.json()["entries"][0]["reference"] == "home-note.pdf"
        assert reopened.json()["entries"][0]["released"] is False
        holder["user"] = practitioner
        still_hidden = http.get(f"/api/patients/{claimed.id}/sources")
        assert [item["display_name"] for item in still_hidden.json()["sources"]] == ["Clinic notes", "Lab results"]
        assert "I slept poorly." not in still_hidden.text

        holder["user"] = client_user
        again = http.post(
            f"/api/client/records/{claimed.id}/entries/{record_id}/release",
            json={"released": True},
        )
        assert again.status_code == 200
        assert again.json()["released"] is True
        holder["user"] = practitioner
        restored = http.get(f"/api/patients/{claimed.id}/sources").json()["sources"]
        assert restored[2]["display_name"] == "From the client"
        assert restored[2]["records"][0]["text"] == "I slept poorly."
        assert restored[2]["records"][0]["reference"] == "home-note.pdf"

        holder["user"] = client_user
        closed = http.post(f"/api/client/records/{other.id}/close-access")
        assert closed.status_code == 200
        assert closed.json()["claimed"] is False
        assert "Other private text." not in closed.text
        db.refresh(other_link)
        other_row = db.query(ContentRelease).filter(ContentRelease.source_key == other_key).one()
        assert other_row.released is False
        assert other_row.withdrawn_at is not None
        other_entry = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(other_id)).one()
        assert other_entry.payload_json["text"] == "Other private text."
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
        db.refresh(entry_row)
        assert entry_row.released is True
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
