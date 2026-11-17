"""Signup creates one personal health record and no clinic release."""

import os
import uuid
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_auth, routes_patient_records
from app.api.deps import get_current_user, get_db
from app.db.models import Case, ContentRelease, Patient, ReportVersion, ShareLink, User, UserCapability
from app.schemas.auth import AuthRegisterRequest
from app.services.auth_service import get_or_create_social_user, register_user
from app.services.patient_service import create_patient
from app.schemas.patients import PatientCreate


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    models = (User, Patient, Case, ReportVersion, ShareLink, ContentRelease, UserCapability)
    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
    for model in models:
        model.__table__.create(engine)
    return engine, Session(engine), originals


def _restore(originals):
    for column, original in originals:
        column.type = original


def test_migration_adds_a_unique_subject_user_id():
    sql = open("app/db/migrations/20261117_personal_record.sql", encoding="utf-8").read()
    assert "subject_user_id" in sql
    assert "uq_patients_subject_user_id" in sql
    assert "REFERENCES users" in sql


def test_signup_creates_one_personal_record_and_no_release(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_auth._send_verification_for_user",
        lambda *args, **kwargs: {"token": "dev-token"},
    )
    engine, db, originals = _session()
    try:
        app = FastAPI()
        app.include_router(routes_auth.router)
        app.include_router(routes_patient_records.router)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        password = "PersonalPass1"
        created = client.post(
            "/auth/register",
            json={
                "email": "Ada@Example.com",
                "password": password,
                "confirm_password": password,
                "first_name": "Ada",
                "last_name": "Lovelace",
            },
        )
        assert created.status_code == 200
        body = created.json()
        user = db.query(User).filter(User.email == "ada@example.com").one()
        patients = db.query(Patient).filter(Patient.subject_user_id == user.id).all()
        assert len(patients) == 1
        record = patients[0]
        assert str(record.id) == body["personal_record"]["patient_id"]
        assert body["personal_record"]["full_name"] == "Ada Lovelace"
        assert body["personal_record"]["subject_user_id"] == str(user.id)
        assert record.user_id == user.id
        assert record.subject_user_id == user.id
        assert record.claimed_by_user_id is None
        assert set(body["personal_record"]) == {"patient_id", "full_name", "subject_user_id"}
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0

        app.dependency_overrides[get_current_user] = lambda: user
        visible = client.get("/auth/record")
        assert visible.status_code == 200
        assert visible.json() == body["personal_record"]
        clinic_list = client.get("/api/patients")
        assert clinic_list.status_code == 200
        assert clinic_list.json() == []

        other = register_user(
            db,
            AuthRegisterRequest(
                email="grace@example.com",
                password=password,
                confirm_password=password,
                first_name="Grace",
                last_name="Hopper",
            ),
        )
        other_rows = db.query(Patient).filter(Patient.subject_user_id == other.id).all()
        assert len(other_rows) == 1
        assert other_rows[0].id != record.id
        app.dependency_overrides[get_current_user] = lambda: other
        other_view = client.get("/auth/record")
        assert other_view.status_code == 200
        assert other_view.json()["patient_id"] == str(other_rows[0].id)
        assert str(record.id) not in other_view.text
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
        assert db.query(Patient).count() == 2
    finally:
        db.close()
        engine.dispose()
        _restore(originals)


def test_existing_accounts_keep_their_clinic_chart(monkeypatch):
    engine, db, originals = _session()
    try:
        practitioner = User(
            id=uuid.uuid4(),
            email="clinic@example.com",
            full_name="Clinic Practitioner",
            password_hash="unused",
            is_active=True,
            role="practitioner",
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(practitioner)
        db.commit()
        chart = create_patient(
            db,
            practitioner,
            PatientCreate(first_name="Existing", last_name="Client"),
        )
        assert chart.subject_user_id is None
        assert db.query(Patient).count() == 1

        same = get_or_create_social_user(
            db,
            provider="google",
            provider_subject="existing-subject",
            email="clinic@example.com",
            full_name="Clinic Practitioner",
        )
        assert same.id == practitioner.id
        assert db.query(Patient).filter(Patient.subject_user_id.isnot(None)).count() == 0
        assert db.query(Patient).count() == 1

        app = FastAPI()
        app.include_router(routes_auth.router)
        app.include_router(routes_patient_records.router)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: practitioner
        client = TestClient(app)
        listed = client.get("/api/patients")
        assert listed.status_code == 200
        assert [row["id"] for row in listed.json()] == [str(chart.id)]
        missing = client.get("/auth/record")
        assert missing.status_code == 404
        assert db.query(ContentRelease).count() == 0
    finally:
        db.close()
        engine.dispose()
        _restore(originals)


def test_new_social_account_gets_one_personal_record():
    engine, db, originals = _session()
    try:
        created = get_or_create_social_user(
            db,
            provider="google",
            provider_subject="new-subject",
            email="New.Social@Example.com",
            full_name="New Social",
        )
        again = get_or_create_social_user(
            db,
            provider="google",
            provider_subject="new-subject",
            email="new.social@example.com",
            full_name="New Social",
        )
        assert again.id == created.id
        rows = db.query(Patient).all()
        assert len(rows) == 1
        assert rows[0].subject_user_id == created.id
        assert rows[0].user_id == created.id
        assert rows[0].claimed_by_user_id is None
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
    finally:
        db.close()
        engine.dispose()
        _restore(originals)
