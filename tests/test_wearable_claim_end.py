"""Ending a claim withdraws wearable-day releases and leaves the days stored."""

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
    PatientClaimInvite,
    PatientSource,
    ReportStatus,
    ReportVersion,
    ShareLink,
    SourceObservation,
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


def test_ending_a_claim_withdraws_wearable_day_releases_and_keeps_the_day():
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

        sources = http.get(f"/api/patients/{claimed.id}/sources").json()["sources"]
        note = http.post(
            f"/api/patients/{claimed.id}/sources/{sources[0]['source_id']}/records",
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
            f"/api/patients/{claimed.id}/sources/{sources[0]['source_id']}/records/{note_id}/release",
            json={"released": True},
        ).status_code == 200

        holder["user"] = client_user

        def connect_and_release(patient, account, steps):
            assert http.post(
                f"/api/client/records/{patient.id}/wearable",
                json={"external_account_ref": account},
            ).status_code == 200
            synced = http.post(
                f"/api/client/records/{patient.id}/wearable/days",
                json={"days": [{"recorded_on": "2026-11-02", "steps": steps, "hrv": 61}]},
            )
            assert synced.status_code == 200
            day_id = synced.json()["days"][0]["record_id"]
            released = http.post(
                f"/api/client/records/{patient.id}/wearable/days/{day_id}/release",
                json={"released": True},
            )
            assert released.status_code == 200
            assert released.json()["released"] is True
            return day_id

        day_id = connect_and_release(claimed, "ohs-user-1", 91001)
        other_day_id = connect_and_release(other, "ohs-user-2", 93003)
        entry = http.post(
            f"/api/client/records/{claimed.id}/entries",
            json={
                "title": "Sleep note",
                "recorded_on": "2026-11-02",
                "text": "I slept poorly.",
                "reference": "home-note.pdf",
            },
        )
        assert entry.status_code == 200
        entry_id = entry.json()["record_id"]
        set_progress_release(db, claimed, True)
        set_progress_release(db, other, True)

        day_key = f"wearable:{day_id}"
        other_key = f"wearable:{other_day_id}"
        day_row = db.query(ContentRelease).filter(ContentRelease.source_key == day_key).one()
        assert day_row.released is True
        assert day_row.direction == "client_to_clinic"

        holder["user"] = practitioner
        visible = http.get(f"/api/patients/{claimed.id}/sources").json()["sources"]
        assert [item["display_name"] for item in visible] == [
            "Clinic notes",
            "Lab results",
            "From the client",
            "Wearables",
        ]
        assert visible[3]["records"][0]["steps"] == {"value": 91001, "unit": "steps"}

        unlinked = http.post(f"/api/patients/{claimed.id}/claim-access/unlink")
        assert unlinked.status_code == 200
        assert unlinked.json()["claimed"] is False
        assert "91001" not in unlinked.text
        assert "I slept poorly." not in unlinked.text

        db.refresh(day_row)
        db.refresh(link)
        assert day_row.released is False
        assert day_row.withdrawn_at is not None
        assert db.query(ContentRelease).filter(ContentRelease.source_key == other_key).one().released is True
        assert db.query(ContentRelease).filter(ContentRelease.source_key == f"client:{entry_id}").one().released is False
        assert db.query(ContentRelease).filter(ContentRelease.source_key == f"record:{note_id}").one().released is False
        progress_row = (
            db.query(ContentRelease)
            .filter(
                ContentRelease.patient_id == claimed.id,
                ContentRelease.source_key == "progress_view",
            )
            .one()
        )
        assert progress_row.released is False
        stored = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(day_id)).one()
        assert stored.payload_json == {"kind": "wearable_day"}
        assert stored.client_summary is None
        assert stored.title == "Daily summary"
        steps = (
            db.query(SourceObservation)
            .filter(
                SourceObservation.source_record_id == stored.id,
                SourceObservation.code == "steps",
            )
            .one()
        )
        assert int(steps.value_numeric) == 91001
        note_row = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(note_id)).one()
        assert note_row.payload_json["text"] == "Private clinic wording"
        entry_row = db.query(SourceRecord).filter(SourceRecord.id == uuid.UUID(entry_id)).one()
        assert entry_row.payload_json["text"] == "I slept poorly."
        assert link.token == "kept-share-token"
        assert link.is_active is True
        assert db.query(ShareLink).count() == 2
        hidden = http.get(f"/api/patients/{claimed.id}/sources")
        assert [item["display_name"] for item in hidden.json()["sources"]] == ["Clinic notes", "Lab results"]
        assert "91001" not in hidden.text
        assert "I slept poorly." not in hidden.text

        holder["user"] = client_user
        assert http.get(f"/api/client/records/{claimed.id}/wearable/days").status_code == 404

        later = PatientClaimInvite(
            id=uuid.uuid4(),
            patient_id=claimed.id,
            practitioner_user_id=practitioner.id,
            email="ada@example.com",
            token_hash=hash_claim_token("later-wearable-token"),
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
        db.refresh(day_row)
        assert day_row.released is False

        reopened = http.get(f"/api/client/records/{claimed.id}/wearable/days")
        assert reopened.status_code == 200
        reopened_day = reopened.json()["days"][0]
        assert reopened_day["record_id"] == day_id
        assert reopened_day["released"] is False
        assert reopened_day["steps"] == {"value": 91001, "unit": "steps"}
        assert reopened_day["hrv"] == {"value": 61, "unit": "ms"}
        holder["user"] = practitioner
        still_hidden = http.get(f"/api/patients/{claimed.id}/sources")
        assert [item["display_name"] for item in still_hidden.json()["sources"]] == ["Clinic notes", "Lab results"]
        assert "91001" not in still_hidden.text

        holder["user"] = client_user
        again = http.post(
            f"/api/client/records/{claimed.id}/wearable/days/{day_id}/release",
            json={"released": True},
        )
        assert again.status_code == 200
        assert again.json()["released"] is True
        holder["user"] = practitioner
        restored = http.get(f"/api/patients/{claimed.id}/sources").json()["sources"]
        assert restored[2]["display_name"] == "Wearables"
        assert restored[2]["records"][0]["steps"] == {"value": 91001, "unit": "steps"}
        assert restored[2]["records"][0]["hrv"] == {"value": 61, "unit": "ms"}

        holder["user"] = client_user
        closed = http.post(f"/api/client/records/{other.id}/close-access")
        assert closed.status_code == 200
        assert closed.json()["claimed"] is False
        assert "93003" not in closed.text
        db.refresh(other_link)
        other_row = db.query(ContentRelease).filter(ContentRelease.source_key == other_key).one()
        assert other_row.released is False
        assert other_row.withdrawn_at is not None
        other_steps = (
            db.query(SourceObservation)
            .filter(
                SourceObservation.source_record_id == uuid.UUID(other_day_id),
                SourceObservation.code == "steps",
            )
            .one()
        )
        assert int(other_steps.value_numeric) == 93003
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
        db.refresh(day_row)
        assert day_row.released is True
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
