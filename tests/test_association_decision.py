"""Accepting, declining, or cancelling a request leaves the personal record in place."""

import os
import uuid
from datetime import date, datetime, timezone

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
from app.api.routes_association_requests import router
from app.db.models import (
    ClinicAssociation,
    ContentRelease,
    Patient,
    PatientSource,
    ShareLink,
    SourceRecord,
    User,
    UserCapability,
)
from app.services.clinic_association_service import request_practitioner_by_email
from app.services.personal_record_service import clinic_charts, ensure_personal_record
from app.services.personal_source_service import add_personal_note, list_personal_notes


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
    UserCapability,
    Patient,
    ShareLink,
    ClinicAssociation,
    ContentRelease,
    PatientSource,
    SourceRecord,
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


def _client(db):
    app = FastAPI()
    app.include_router(router)
    holder = {"user": None}

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: holder["user"]
    return TestClient(app), holder


def _request(db, person, practitioner):
    personal = ensure_personal_record(db, person, commit=True)
    personal.date_of_birth = date(1984, 7, 19)
    personal.notes = "Private chart note 66110"
    db.commit()
    add_personal_note(
        db,
        personal,
        person,
        title="Personal shelf",
        recorded_on=date(2026, 11, 16),
        text="Personal shelf note 66111",
    )
    request_practitioner_by_email(db, person, practitioner.email)
    row = db.query(ClinicAssociation).one()
    return personal, row


def test_accepting_opens_a_clinic_chart_without_copying_the_personal_record():
    engine, originals, db = _session()
    try:
        person = _user("ada@example.com", "client", "Ada Lovelace")
        practitioner = _user("clinic@example.com", "practitioner", "North Clinic")
        other = _user("other@example.com", "practitioner", "Other Clinic")
        db.add_all([person, practitioner, other])
        db.commit()
        personal, row = _request(db, person, practitioner)
        http, holder = _client(db)

        holder["user"] = person
        blocked = http.post(f"/api/association-requests/{row.id}/accept")
        assert blocked.status_code == 403

        holder["user"] = other
        missing = http.post(f"/api/association-requests/{row.id}/accept")
        assert missing.status_code == 404
        assert "ada@example.com" not in missing.text
        assert "Personal shelf note 66111" not in missing.text

        holder["user"] = practitioner
        accepted = http.post(f"/api/association-requests/{row.id}/accept")
        assert accepted.status_code == 200
        assert "Personal shelf note 66111" not in accepted.text
        assert "Private chart note 66110" not in accepted.text
        assert "1984-07-19" not in accepted.text

        db.refresh(personal)
        db.refresh(row)
        chart = db.query(Patient).filter(Patient.id == row.clinic_patient_id).one()
        assert accepted.json() == {
            "request_id": str(row.id),
            "status": "active",
            "clinic_patient_id": str(chart.id),
        }
        assert chart.id != personal.id
        assert chart.user_id == practitioner.id
        assert chart.subject_user_id is None
        assert chart.full_name == "Ada Lovelace"
        assert chart.date_of_birth is None
        assert chart.email is None
        assert chart.notes is None
        assert chart.claimed_by_user_id is None
        assert row.status == "active"
        assert row.activated_at is not None
        assert row.personal_patient_id == personal.id
        assert personal.subject_user_id == person.id
        assert personal.user_id == person.id
        assert personal.claimed_by_user_id is None
        assert personal.date_of_birth == date(1984, 7, 19)
        note = db.query(SourceRecord).one()
        assert note.patient_id == personal.id
        assert list_personal_notes(db, personal, person)["notes"][0]["text"] == "Personal shelf note 66111"
        assert [item.id for item in clinic_charts(db, practitioner)] == [chart.id]
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
        assert http.get("/api/association-requests").json() == {"requests": []}
        again = http.post(f"/api/association-requests/{row.id}/accept")
        assert again.status_code == 404
        db.close()
    finally:
        _restore(engine, originals)


def test_decline_and_cancel_leave_the_personal_record_available():
    engine, originals, db = _session()
    try:
        person = _user("ada@example.com", "client", "Ada Lovelace")
        practitioner = _user("clinic@example.com", "practitioner", "North Clinic")
        second = _user("south@example.com", "practitioner", "South Clinic")
        db.add_all([person, practitioner, second])
        db.commit()
        personal, first = _request(db, person, practitioner)
        request_practitioner_by_email(db, person, second.email)
        other_row = (
            db.query(ClinicAssociation)
            .filter(ClinicAssociation.practitioner_user_id == second.id)
            .one()
        )
        http, holder = _client(db)

        holder["user"] = practitioner
        declined = http.post(f"/api/association-requests/{first.id}/decline")
        assert declined.status_code == 200
        assert declined.json() == {"request_id": str(first.id), "status": "ended"}
        assert "Personal shelf note 66111" not in declined.text
        db.refresh(first)
        assert first.status == "ended"
        assert first.ended_by == "practitioner"
        assert first.clinic_patient_id is None
        assert db.query(Patient).count() == 1

        holder["user"] = person
        cancelled = http.post(f"/api/client/association-requests/{other_row.id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json() == {"request_id": str(other_row.id), "status": "ended"}
        db.refresh(other_row)
        assert other_row.status == "ended"
        assert other_row.ended_by == "person"
        assert other_row.clinic_patient_id is None

        db.refresh(personal)
        assert personal.subject_user_id == person.id
        assert personal.claimed_by_user_id is None
        assert list_personal_notes(db, personal, person)["notes"][0]["text"] == "Personal shelf note 66111"
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
        holder["user"] = practitioner
        assert http.get("/api/association-requests").json() == {"requests": []}
        assert http.post(f"/api/association-requests/{first.id}/decline").status_code == 404
        holder["user"] = second
        hidden = http.post(f"/api/client/association-requests/{first.id}/cancel")
        assert hidden.status_code == 404
        db.close()
    finally:
        _restore(engine, originals)


def test_accept_refuses_a_second_active_connection_without_copying():
    engine, originals, db = _session()
    try:
        person = _user("ada@example.com", "client", "Ada Lovelace")
        first_clinic = _user("north@example.com", "practitioner", "North Clinic")
        second_clinic = _user("south@example.com", "practitioner", "South Clinic")
        db.add_all([person, first_clinic, second_clinic])
        db.commit()
        personal = ensure_personal_record(db, person, commit=True)
        existing_chart = Patient(
            id=uuid.uuid4(),
            user_id=first_clinic.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
        )
        db.add(existing_chart)
        db.flush()
        db.add(
            ClinicAssociation(
                personal_patient_id=personal.id,
                clinic_patient_id=existing_chart.id,
                practitioner_user_id=first_clinic.id,
                status="active",
                initiated_by="practitioner",
                created_at=datetime.now(timezone.utc),
                activated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        request_practitioner_by_email(db, person, second_clinic.email)
        pending = (
            db.query(ClinicAssociation)
            .filter(ClinicAssociation.status == "pending")
            .one()
        )
        http, holder = _client(db)
        holder["user"] = second_clinic
        refused = http.post(f"/api/association-requests/{pending.id}/accept")
        assert refused.status_code == 409
        assert refused.json()["detail"] == "This person already has an active clinic connection."
        assert "Personal shelf" not in refused.text
        db.refresh(pending)
        assert pending.status == "pending"
        assert pending.clinic_patient_id is None
        assert db.query(Patient).count() == 2
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
        db.close()
    finally:
        _restore(engine, originals)
