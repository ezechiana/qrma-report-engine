"""A person can ask a practitioner to connect without sharing health data."""

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
    Case,
    ClinicAssociation,
    ContentRelease,
    Patient,
    PatientSource,
    ReportVersion,
    ShareLink,
    SourceRecord,
    User,
    UserCapability,
)
from app.services.clinic_association_service import GENERIC_REQUEST, PENDING
from app.services.personal_record_service import ensure_personal_record
from app.services.personal_source_service import add_personal_note


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
    Case,
    ReportVersion,
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


def _user(email, role, name, verified=True):
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash="hash",
        full_name=name,
        role=role,
        is_active=True,
        email_verified_at=datetime.now(timezone.utc) if verified else None,
    )


def test_request_routes_keep_decisions_on_the_request():
    paths = set()
    for route in router.routes:
        for method in route.methods or ():
            paths.add((route.path, method))
    assert ("/api/client/association-requests", "POST") in paths
    assert ("/api/association-requests", "GET") in paths
    assert ("/api/association-requests/{request_id}/accept", "POST") in paths
    assert ("/api/association-requests/{request_id}/decline", "POST") in paths
    assert ("/api/client/association-requests/{request_id}/cancel", "POST") in paths
    assert all("share" not in path for path, _method in paths)


def test_person_can_request_a_practitioner_without_sharing_a_record():
    engine, originals, db = _session()
    try:
        person = _user("ada@example.com", "client", "Ada Lovelace")
        practitioner = _user("clinic@example.com", "practitioner", "North Clinic")
        other_clinic = _user("other-clinic@example.com", "practitioner", "Other Clinic")
        plain_client = _user("grace@example.com", "client", "Grace Hopper")
        capable = _user("capable@example.com", "client", "Capable Clinic")
        db.add_all([person, practitioner, other_clinic, plain_client, capable])
        db.commit()
        db.add(UserCapability(user_id=capable.id, capability="practitioner"))
        db.commit()
        personal = ensure_personal_record(db, person, commit=True)
        personal.date_of_birth = date(1984, 7, 19)
        db.commit()
        add_personal_note(
            db,
            personal,
            person,
            title="Personal shelf",
            recorded_on=date(2026, 11, 16),
            text="Personal shelf note 55221",
        )

        app = FastAPI()
        app.include_router(router)
        holder = {"user": person}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        unknown = http.post(
            "/api/client/association-requests",
            json={"practitioner_email": "missing@example.com"},
        )
        assert unknown.status_code == 200
        assert unknown.json() == {"detail": GENERIC_REQUEST}
        assert "missing@example.com" not in unknown.text
        assert db.query(ClinicAssociation).count() == 0

        not_practitioner = http.post(
            "/api/client/association-requests",
            json={"practitioner_email": "Grace@Example.com"},
        )
        assert not_practitioner.status_code == 200
        assert not_practitioner.json() == unknown.json()
        assert db.query(ClinicAssociation).count() == 0

        created = http.post(
            "/api/client/association-requests",
            json={"practitioner_email": " Clinic@Example.com "},
        )
        assert created.status_code == 200
        assert created.json() == unknown.json()
        assert "Personal shelf note 55221" not in created.text
        assert "1984-07-19" not in created.text

        duplicate = http.post(
            "/api/client/association-requests",
            json={"practitioner_email": "clinic@example.com"},
        )
        assert duplicate.status_code == 200
        assert duplicate.json() == unknown.json()

        second = http.post(
            "/api/client/association-requests",
            json={"practitioner_email": "capable@example.com"},
        )
        assert second.status_code == 200
        assert second.json() == unknown.json()

        rows = db.query(ClinicAssociation).order_by(ClinicAssociation.created_at.asc()).all()
        assert len(rows) == 2
        assert {row.practitioner_user_id for row in rows} == {practitioner.id, capable.id}
        for row in rows:
            assert row.status == PENDING
            assert row.initiated_by == "person"
            assert row.personal_patient_id == personal.id
            assert row.clinic_patient_id is None
            assert row.activated_at is None
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
        note = db.query(SourceRecord).one()
        assert note.patient_id == personal.id
        assert "Personal shelf note 55221" in note.payload_json["text"]

        holder["user"] = practitioner
        listed = http.get("/api/association-requests")
        assert listed.status_code == 200
        clinic_row = next(row for row in rows if row.practitioner_user_id == practitioner.id)
        body = listed.json()
        assert body == {
            "requests": [
                {
                    "request_id": str(clinic_row.id),
                    "requester_name": "Ada Lovelace",
                    "requester_email": "ada@example.com",
                    "status": "pending",
                }
            ]
        }
        listed_text = listed.text
        assert "Personal shelf note 55221" not in listed_text
        assert "1984-07-19" not in listed_text
        assert "date_of_birth" not in listed_text
        assert str(personal.id) not in listed_text

        holder["user"] = other_clinic
        assert http.get("/api/association-requests").json() == {"requests": []}

        holder["user"] = person
        refused = http.get("/api/association-requests")
        assert refused.status_code == 403
        assert refused.json()["detail"] == "Practitioner account required."

        invalid = http.post(
            "/api/client/association-requests",
            json={"practitioner_email": "not-an-email"},
        )
        assert invalid.status_code == 422
        assert db.query(ClinicAssociation).count() == 2

        holder["user"] = _user("new@example.com", "client", "New Person", verified=False)
        db.add(holder["user"])
        db.commit()
        unverified = http.post(
            "/api/client/association-requests",
            json={"practitioner_email": "clinic@example.com"},
        )
        assert unverified.status_code == 403
        assert db.query(ClinicAssociation).filter(
            ClinicAssociation.practitioner_user_id == practitioner.id
        ).count() == 1
        db.close()
    finally:
        _restore(engine, originals)
