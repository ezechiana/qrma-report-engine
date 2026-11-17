"""Wearables on a personal record before any clinic connection."""

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

from app.api import routes_client_records, routes_patient_records, routes_patient_sources
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
)


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_personal_record_can_store_unshared_wearable_days():
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
        verified = datetime.now(timezone.utc)
        person = User(
            id=uuid.uuid4(),
            email="person@example.com",
            full_name="Personal Person",
            password_hash="unused",
            role="practitioner",
            is_active=True,
            email_verified_at=verified,
        )
        clinic_user = User(
            id=uuid.uuid4(),
            email="clinic@example.com",
            full_name="Clinic Practitioner",
            password_hash="unused",
            role="practitioner",
            is_active=True,
            email_verified_at=verified,
        )
        stranger = User(
            id=uuid.uuid4(),
            email="stranger@example.com",
            full_name="Other Client",
            password_hash="unused",
            role="client",
            is_active=True,
            email_verified_at=verified,
        )
        unverified = User(
            id=uuid.uuid4(),
            email="new@example.com",
            full_name="Unverified Person",
            password_hash="unused",
            role="practitioner",
            is_active=True,
            email_verified_at=None,
        )
        db.add_all([person, clinic_user, stranger, unverified])
        db.commit()
        personal = Patient(
            id=uuid.uuid4(),
            user_id=person.id,
            subject_user_id=person.id,
            first_name="Personal",
            last_name="Person",
            full_name="Personal Person",
        )
        clinic_patient = Patient(
            id=uuid.uuid4(),
            user_id=clinic_user.id,
            first_name="Nora",
            last_name="Clinicpatient",
            full_name="Nora Clinicpatient",
            notes="Clinic only note 77021",
        )
        db.add_all([personal, clinic_patient])
        db.commit()

        app = FastAPI()
        app.include_router(routes_client_records.router)
        app.include_router(routes_patient_records.router)
        app.include_router(routes_patient_sources.router)

        def override_db():
            yield db

        holder = {"user": person}
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        missing = http.get(f"/api/client/records/{personal.id}/wearable")
        assert missing.status_code == 200
        assert missing.json() == {"connected": False}

        connected = http.post(
            f"/api/client/records/{personal.id}/wearable",
            json={"external_account_ref": " ohs-personal-1 "},
        )
        assert connected.status_code == 200
        body = connected.json()
        assert body["connected"] is True
        assert body["display_name"] == "Wearables"
        assert body["external_account_ref"] == "ohs-personal-1"
        source_id = body["source_id"]
        again = http.post(
            f"/api/client/records/{personal.id}/wearable",
            json={"external_account_ref": "ohs-personal-1"},
        )
        assert again.json()["source_id"] == source_id
        assert db.query(PatientSource).filter(PatientSource.source_type == "wearable").count() == 1
        shelf = db.query(PatientSource).one()
        assert shelf.patient_id == personal.id
        assert shelf.practitioner_user_id == person.id

        synced = http.post(
            f"/api/client/records/{personal.id}/wearable/days",
            json={
                "days": [
                    {
                        "recorded_on": "2026-11-02",
                        "steps": 86111,
                        "resting_heart_rate": 54,
                        "sleep_duration": 420,
                        "hrv": 61,
                    }
                ]
            },
        )
        assert synced.status_code == 200
        day = synced.json()["days"][0]
        assert day["recorded_on"] == "2026-11-02"
        assert day["released"] is False
        assert day["steps"] == {"value": 86111, "unit": "steps"}
        record_id = day["record_id"]

        listed = http.get(f"/api/client/records/{personal.id}/wearable/days")
        assert listed.status_code == 200
        assert listed.json()["days"][0]["record_id"] == record_id
        assert listed.json()["days"][0]["released"] is False
        assert "77021" not in listed.text

        refused = http.post(
            f"/api/client/records/{personal.id}/wearable/days/{record_id}/release",
            json={"released": True},
        )
        assert refused.status_code == 403
        assert "86111" not in refused.text
        assert "ohs-personal-1" not in refused.text
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0

        clinic_list = http.get("/api/patients")
        assert clinic_list.status_code == 200
        assert clinic_list.json() == []
        own_sources = http.get(f"/api/patients/{personal.id}/sources")
        assert own_sources.status_code == 200
        assert [item["source_type"] for item in own_sources.json()["sources"]] == ["manual", "laboratory"]
        assert "wearable" not in own_sources.text
        assert "ohs-personal-1" not in own_sources.text
        assert "86111" not in own_sources.text

        holder["user"] = clinic_user
        other_sources = http.get(f"/api/patients/{clinic_patient.id}/sources")
        assert other_sources.status_code == 200
        assert "ohs-personal-1" not in other_sources.text
        assert "86111" not in other_sources.text
        hidden_chart = http.get(f"/api/patients/{personal.id}/sources")
        assert hidden_chart.status_code == 404
        assert "ohs-personal-1" not in hidden_chart.text
        assert "86111" not in hidden_chart.text

        holder["user"] = stranger
        stolen = http.get(f"/api/client/records/{personal.id}/wearable/days")
        assert stolen.status_code == 404
        assert "86111" not in stolen.text
        assert "ohs-personal-1" not in stolen.text

        holder["user"] = unverified
        blocked = http.post(
            f"/api/client/records/{personal.id}/wearable",
            json={"external_account_ref": "ohs-unverified"},
        )
        assert blocked.status_code == 403
        assert db.query(PatientSource).filter(PatientSource.external_account_ref == "ohs-unverified").count() == 0
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
        db.close()
    finally:
        for column, original in originals:
            column.type = original
        engine.dispose()
