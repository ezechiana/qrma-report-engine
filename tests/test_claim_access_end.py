"""Ending a claim removes account access and leaves the clinical record in place."""

import os
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_client_records, routes_patient_claim_invites, routes_patient_records, routes_share_links
from app.api.deps import _client_blocked_from_practitioner_path, get_current_user, get_db
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
from app.services.patient_claim_service import (
    complete_reserved_claim,
    hash_claim_token,
    preview_claim_invite,
)


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def _build():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
    originals = []
    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
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
        clinic_name=None,
        password_hash="unused",
        is_active=True,
        role="client",
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add_all([practitioner, client_user])
    db.commit()
    claimed = Patient(
        id=uuid.uuid4(),
        user_id=practitioner.id,
        first_name="Ada",
        last_name="Lovelace",
        full_name="Ada Lovelace",
        notes="Private practitioner note",
        claimed_by_user_id=client_user.id,
        claimed_at=datetime.now(timezone.utc),
    )
    other = Patient(
        id=uuid.uuid4(),
        user_id=practitioner.id,
        first_name="Ada",
        last_name="Other",
        full_name="Ada Other",
        notes="Second private note",
        claimed_by_user_id=client_user.id,
        claimed_at=datetime.now(timezone.utc),
    )
    db.add_all([claimed, other])
    db.commit()
    case = Case(
        id=uuid.uuid4(),
        user_id=practitioner.id,
        patient_id=claimed.id,
        title="Kept scan",
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
        patient_id=claimed.id,
        token="still-shared",
        is_active=True,
        share_type="report",
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    pending = PatientClaimInvite(
        id=uuid.uuid4(),
        patient_id=claimed.id,
        practitioner_user_id=practitioner.id,
        email="ada@example.com",
        token_hash=hash_claim_token("pending-token"),
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        created_at=datetime.now(timezone.utc),
    )
    accepted = PatientClaimInvite(
        id=uuid.uuid4(),
        patient_id=claimed.id,
        practitioner_user_id=practitioner.id,
        email="ada@example.com",
        token_hash=hash_claim_token("used-token"),
        status="accepted",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        created_at=datetime.now(timezone.utc),
        accepted_at=datetime.now(timezone.utc),
        claimed_by_user_id=client_user.id,
    )
    db.add_all([link, pending, accepted])
    db.commit()

    app = FastAPI()
    app.include_router(routes_patient_records.router)
    app.include_router(routes_patient_claim_invites.router)
    app.include_router(routes_client_records.router)
    holder = {"user": practitioner}

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: holder["user"]
    http = TestClient(app)
    return engine, originals, db, http, holder, {
        "practitioner": practitioner,
        "client": client_user,
        "claimed": claimed,
        "other": other,
        "case": case,
        "report": report,
        "link": link,
        "pending": pending,
    }


def _restore(engine, originals):
    for column, kind in originals:
        column.type = kind
    engine.dispose()


def test_ending_access_stays_off_share_link_routes():
    client = User(role="client")
    assert _client_blocked_from_practitioner_path(client, "/api/patients/abc/claim-access/unlink")
    assert _client_blocked_from_practitioner_path(client, "/api/client/records/abc/close-access") is False
    share_paths = [route.path for route in routes_share_links.router.routes]
    assert "/api/share/revoke/{share_id}" in share_paths
    assert all("claim-access" not in path for path in share_paths)


def test_practitioner_unlink_hides_the_record_and_keeps_the_clinic_file():
    engine, originals, db, http, holder, rows = _build()
    try:
        claimed = rows["claimed"]
        holder["user"] = rows["client"]
        before = http.get("/api/client/records")
        assert before.status_code == 200
        assert {item["patient_id"] for item in before.json()["records"]} == {
            str(claimed.id),
            str(rows["other"].id),
        }

        holder["user"] = rows["practitioner"]
        unlinked = http.post(f"/api/patients/{claimed.id}/claim-access/unlink")
        assert unlinked.status_code == 200
        body = unlinked.json()
        assert body["claimed"] is False
        assert body["access_ended_by"] == "practitioner"
        assert body["access_ended_at"]
        assert "ada@example.com" not in unlinked.text
        assert "Private practitioner note" not in unlinked.text

        again = http.post(f"/api/patients/{claimed.id}/claim-access/unlink")
        assert again.status_code == 409

        visible = http.get(f"/api/patients/{claimed.id}/claim-access")
        assert visible.status_code == 200
        assert visible.json()["access_ended_by"] == "practitioner"
        assert visible.json()["claimed"] is False

        invites = http.get(f"/api/patients/{claimed.id}/claim-invites")
        assert invites.status_code == 200
        by_status = {item["status"] for item in invites.json()}
        assert by_status == {"cancelled", "accepted"}

        listed = http.get("/api/patients")
        assert listed.status_code == 200
        assert str(claimed.id) in {item["id"] for item in listed.json()}

        db.refresh(rows["pending"])
        db.refresh(rows["link"])
        db.refresh(claimed)
        assert rows["pending"].status == "cancelled"
        assert rows["link"].token == "still-shared"
        assert rows["link"].is_active is True
        assert claimed.notes == "Private practitioner note"
        assert claimed.user_id == rows["practitioner"].id
        assert db.query(Case).filter(Case.id == rows["case"].id).count() == 1
        assert db.query(ReportVersion).filter(ReportVersion.id == rows["report"].id).count() == 1
        with pytest.raises(HTTPException) as pending_use:
            preview_claim_invite(db, "pending-token")
        assert pending_use.value.status_code == 409

        rows["link"].is_active = False
        db.commit()
        db.refresh(rows["link"])
        assert rows["link"].is_active is False

        holder["user"] = rows["client"]
        after = http.get("/api/client/records")
        assert {item["patient_id"] for item in after.json()["records"]} == {str(rows["other"].id)}
        hidden = http.get(f"/api/client/records/{claimed.id}")
        assert hidden.status_code == 404

        later = PatientClaimInvite(
            id=uuid.uuid4(),
            patient_id=claimed.id,
            practitioner_user_id=rows["practitioner"].id,
            email="ada@example.com",
            token_hash=hash_claim_token("later-token"),
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            created_at=datetime.now(timezone.utc),
            reserved_by_user_id=rows["client"].id,
        )
        db.add(later)
        db.commit()
        completed = complete_reserved_claim(db, rows["client"])
        db.commit()
        db.refresh(claimed)
        assert completed is not None
        assert claimed.claimed_by_user_id == rows["client"].id
        assert claimed.claim_access_ended_at is None
    finally:
        db.close()
        _restore(engine, originals)


def test_client_can_close_access_to_one_claimed_record():
    engine, originals, db, http, holder, rows = _build()
    try:
        claimed = rows["claimed"]
        holder["user"] = rows["client"]
        closed = http.post(f"/api/client/records/{claimed.id}/close-access")
        assert closed.status_code == 200
        assert closed.json()["claimed"] is False
        assert closed.json()["access_ended_by"] == "client"
        assert closed.json()["access_ended_at"]
        assert "Private practitioner note" not in closed.text

        hidden = http.get(f"/api/client/records/{claimed.id}")
        assert hidden.status_code == 404
        remaining = http.get("/api/client/records")
        assert {item["patient_id"] for item in remaining.json()["records"]} == {str(rows["other"].id)}
        second = http.post(f"/api/client/records/{claimed.id}/close-access")
        assert second.status_code == 404

        holder["user"] = rows["practitioner"]
        blocked_client_route = http.post(f"/api/client/records/{rows['other'].id}/close-access")
        assert blocked_client_route.status_code == 403
        clinic = http.get(f"/api/patients/{claimed.id}/claim-access")
        assert clinic.status_code == 200
        assert clinic.json()["access_ended_by"] == "client"
        assert clinic.json()["access_ended_at"]

        db.refresh(claimed)
        db.refresh(rows["link"])
        db.refresh(rows["pending"])
        assert claimed.notes == "Private practitioner note"
        assert claimed.user_id == rows["practitioner"].id
        assert db.query(Case).filter(Case.patient_id == claimed.id).count() == 1
        assert rows["link"].is_active is True
        assert rows["link"].token == "still-shared"
        assert rows["pending"].status == "cancelled"
        assert db.get(Patient, rows["other"].id).claimed_by_user_id == rows["client"].id
    finally:
        db.close()
        _restore(engine, originals)
