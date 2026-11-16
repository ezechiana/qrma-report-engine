"""Personal notes and lab panels before any clinic connection."""

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

NOTE = "Personal sleep note 77221"
LAB_NAME = "Ferritin personal"
LAB_VALUE = 77222


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_personal_notes_and_labs_stay_on_the_personal_record():
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
        db.add_all([person, clinic_user, stranger])
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

        note = http.post(
            f"/api/client/records/{personal.id}/notes",
            json={"title": "Sleep", "recorded_on": "2026-11-02", "text": f"  {NOTE}  "},
        )
        assert note.status_code == 200
        saved_note = note.json()
        assert saved_note["title"] == "Sleep"
        assert saved_note["recorded_on"] == "2026-11-02"
        assert saved_note["text"] == NOTE
        assert saved_note["released"] is False
        note_id = saved_note["record_id"]

        lab = http.post(
            f"/api/client/records/{personal.id}/labs",
            json={
                "title": "Home panel",
                "recorded_on": "2026-11-03",
                "observations": [
                    {"name": LAB_NAME, "value_numeric": LAB_VALUE, "unit": "ug/L"},
                ],
            },
        )
        assert lab.status_code == 200
        saved_lab = lab.json()
        assert saved_lab["title"] == "Home panel"
        assert saved_lab["recorded_on"] == "2026-11-03"
        assert saved_lab["released"] is False
        assert saved_lab["observations"] == [
            {"name": LAB_NAME, "value_text": "", "value_numeric": LAB_VALUE, "unit": "ug/L"}
        ]
        lab_id = saved_lab["record_id"]

        notes = http.get(f"/api/client/records/{personal.id}/notes")
        assert notes.status_code == 200
        assert notes.json()["notes"][0]["record_id"] == note_id
        assert notes.json()["notes"][0]["text"] == NOTE
        assert notes.json()["notes"][0]["released"] is False
        labs = http.get(f"/api/client/records/{personal.id}/labs")
        assert labs.status_code == 200
        assert labs.json()["labs"][0]["record_id"] == lab_id
        assert labs.json()["labs"][0]["observations"][0]["value_numeric"] == LAB_VALUE
        assert labs.json()["labs"][0]["released"] is False

        refused_note = http.post(
            f"/api/client/records/{personal.id}/notes/{note_id}/release",
            json={"released": True},
        )
        refused_lab = http.post(
            f"/api/client/records/{personal.id}/labs/{lab_id}/release",
            json={"released": True},
        )
        assert refused_note.status_code == 403
        assert refused_lab.status_code == 403
        assert NOTE not in refused_note.text
        assert LAB_NAME not in refused_lab.text
        assert str(LAB_VALUE) not in refused_lab.text
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0

        listed = http.get("/api/patients")
        assert listed.status_code == 200
        assert listed.json() == []
        own_sources = http.get(f"/api/patients/{personal.id}/sources")
        assert own_sources.status_code == 200
        assert own_sources.json()["sources"][0]["records"] == []
        assert own_sources.json()["sources"][1]["records"] == []
        assert NOTE not in own_sources.text
        assert LAB_NAME not in own_sources.text
        assert str(LAB_VALUE) not in own_sources.text

        holder["user"] = clinic_user
        other_sources = http.get(f"/api/patients/{clinic_patient.id}/sources")
        assert other_sources.status_code == 200
        assert NOTE not in other_sources.text
        assert LAB_NAME not in other_sources.text
        hidden = http.get(f"/api/patients/{personal.id}/sources")
        assert hidden.status_code == 404
        assert NOTE not in hidden.text
        denied = http.post(
            f"/api/client/records/{personal.id}/notes",
            json={"title": "Nope", "recorded_on": "2026-11-02", "text": "Hidden"},
        )
        assert denied.status_code == 403

        holder["user"] = stranger
        stolen = http.get(f"/api/client/records/{personal.id}/notes")
        assert stolen.status_code == 404
        assert NOTE not in stolen.text
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0
        db.close()
    finally:
        for column, original in originals:
            column.type = original
        engine.dispose()
