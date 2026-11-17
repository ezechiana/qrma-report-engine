"""A practitioner's personal record stays out of the clinic list."""

import json
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
from app.db.models import (
    Case,
    ContentRelease,
    Patient,
    PatientSource,
    ReportVersion,
    ShareLink,
    SourceObservation,
    SourceRecord,
    User,
    UserCapability,
)
from app.schemas.auth import AuthRegisterRequest
from app.schemas.patients import PatientCreate
from app.services.auth_service import register_user
from app.services.patient_service import create_patient
from app.services.patient_source_service import list_patient_sources
from app.services.user_capability_service import grant_capability

NOTE = "Nora private note 44011"
LAB = "44012"
STEPS = "44013"


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
    models = (
        User,
        UserCapability,
        Patient,
        Case,
        ReportVersion,
        ShareLink,
        ContentRelease,
        PatientSource,
        SourceRecord,
        SourceObservation,
    )
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


def test_migration_backfills_capabilities_on_the_same_users():
    sql = open("app/db/migrations/20261117_user_capabilities.sql", encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS user_capabilities" in sql
    assert "UNIQUE (user_id, capability)" in sql
    assert "lower(role) = 'practitioner'" in sql
    assert "lower(role) = 'admin'" in sql
    assert "INSERT INTO users" not in sql


def test_personal_record_stays_out_of_the_clinic_list_and_other_patient_data():
    engine, db, originals = _session()
    try:
        practitioner = register_user(
            db,
            AuthRegisterRequest(
                email="pat@example.com",
                password="PersonalPass1",
                confirm_password="PersonalPass1",
                first_name="Pat",
                last_name="Practitioner",
            ),
        )
        personal_rows = db.query(Patient).filter(Patient.subject_user_id == practitioner.id).all()
        assert len(personal_rows) == 1
        personal = personal_rows[0]
        assert db.query(User).filter(User.email == "pat@example.com").count() == 1
        assert db.query(UserCapability).filter(UserCapability.user_id == practitioner.id).count() == 1

        clinic = create_patient(
            db,
            practitioner,
            PatientCreate(first_name="Nora", last_name="Clinicpatient"),
        )
        clinic.notes = NOTE
        when = datetime.now(timezone.utc)
        note_source = PatientSource(
            patient_id=clinic.id,
            practitioner_user_id=practitioner.id,
            source_type="manual",
            display_name="Clinic notes",
            status="active",
        )
        lab_source = PatientSource(
            patient_id=clinic.id,
            practitioner_user_id=practitioner.id,
            source_type="laboratory",
            display_name="Lab results",
            status="active",
        )
        wearable_source = PatientSource(
            patient_id=clinic.id,
            practitioner_user_id=practitioner.id,
            source_type="wearable",
            display_name="Wearables",
            status="active",
        )
        db.add_all([note_source, lab_source, wearable_source])
        db.flush()
        note = SourceRecord(
            patient_source_id=note_source.id,
            patient_id=clinic.id,
            recorded_at=when,
            title="Clinic note",
            payload_json={"kind": "note", "text": NOTE},
            created_by_user_id=practitioner.id,
        )
        lab = SourceRecord(
            patient_source_id=lab_source.id,
            patient_id=clinic.id,
            recorded_at=when,
            title="Lab panel",
            payload_json={"kind": "laboratory"},
            created_by_user_id=practitioner.id,
        )
        day = SourceRecord(
            patient_source_id=wearable_source.id,
            patient_id=clinic.id,
            recorded_at=when,
            title="Daily summary",
            payload_json={"kind": "wearable_day"},
            created_by_user_id=practitioner.id,
        )
        db.add_all([note, lab, day])
        db.flush()
        db.add_all(
            [
                SourceObservation(
                    source_record_id=lab.id,
                    patient_id=clinic.id,
                    position=1,
                    name="Haemoglobin",
                    value_text=LAB,
                    unit="g/L",
                ),
                SourceObservation(
                    source_record_id=day.id,
                    patient_id=clinic.id,
                    position=1,
                    name="Steps",
                    value_text=STEPS,
                    unit="steps",
                ),
            ]
        )
        db.commit()

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
        listed_ids = [row["id"] for row in listed.json()]
        assert listed_ids == [str(clinic.id)]
        assert str(personal.id) not in listed_ids
        assert "Pat Practitioner" not in listed.text

        opened = client.get("/auth/record")
        assert opened.status_code == 200
        assert opened.json()["patient_id"] == str(personal.id)
        assert opened.json()["full_name"] == "Pat Practitioner"
        assert opened.json()["subject_user_id"] == str(practitioner.id)
        for private in ("Nora", NOTE, LAB, STEPS, "Haemoglobin", "wearable"):
            assert private not in opened.text

        chart = client.get(f"/api/patients/{personal.id}")
        assert chart.status_code == 200
        assert chart.json()["full_name"] == "Pat Practitioner"
        for private in ("Nora", NOTE, LAB, STEPS):
            assert private not in chart.text

        own_sources = list_patient_sources(db, personal)
        own_text = json.dumps(own_sources)
        assert own_sources["patient_id"] == str(personal.id)
        assert own_sources["sources"][0]["records"] == []
        assert own_sources["sources"][1]["records"] == []
        for private in ("Nora", NOTE, LAB, STEPS, "Haemoglobin"):
            assert private not in own_text
        clinic_sources = json.dumps(list_patient_sources(db, clinic))
        assert NOTE in clinic_sources
        assert LAB in clinic_sources
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
    finally:
        db.close()
        engine.dispose()
        _restore(originals)


def test_granting_practitioner_capability_keeps_the_same_login():
    engine, db, originals = _session()
    try:
        account = User(
            id=uuid.uuid4(),
            email="casey@example.com",
            full_name="Casey Client",
            password_hash="unused",
            role="client",
            is_active=True,
        )
        db.add(account)
        db.commit()
        user_id = account.id

        grant_capability(db, account, "practitioner")
        grant_capability(db, account, "practitioner")

        db.refresh(account)
        assert account.id == user_id
        assert account.email == "casey@example.com"
        assert account.role == "client"
        assert db.query(User).count() == 1
        capabilities = db.query(UserCapability).all()
        assert len(capabilities) == 1
        assert capabilities[0].user_id == user_id
        assert capabilities[0].capability == "practitioner"
        records = db.query(Patient).all()
        assert len(records) == 1
        assert records[0].subject_user_id == user_id
        assert records[0].user_id == user_id
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
    finally:
        db.close()
        engine.dispose()
        _restore(originals)
