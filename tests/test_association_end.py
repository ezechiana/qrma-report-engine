"""Ending an association withdraws both directions and keeps the records."""

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

from app.api.deps import get_current_user, get_db
from app.api.routes_association_requests import router
from app.db.models import (
    Case,
    ClinicAssociation,
    ContentRelease,
    Patient,
    PatientSource,
    ReportStatus,
    ReportVersion,
    ShareLink,
    SourceObservation,
    SourceRecord,
    User,
)
from app.services.clinic_association_service import activate_practitioner_association
from app.services.patient_source_service import list_patient_sources, released_client_sources
from app.services.personal_source_service import list_personal_notes


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
    Patient,
    ClinicAssociation,
    ContentRelease,
    PatientSource,
    SourceRecord,
    SourceObservation,
    Case,
    ReportVersion,
    ShareLink,
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


def _release(patient_id, practitioner_id, source_key, direction):
    now = datetime.now(timezone.utc)
    return ContentRelease(
        id=uuid.uuid4(),
        patient_id=patient_id,
        practitioner_user_id=practitioner_id,
        source_key=source_key,
        direction=direction,
        released=True,
        released_at=now,
        created_at=now,
    )


def test_either_side_can_end_and_a_later_connection_does_not_restore_releases():
    engine, originals, db = _session()
    try:
        person = _user("ada@example.com", "client", "Ada Lovelace")
        practitioner = _user("clinic@example.com", "practitioner", "North Clinic")
        stranger = _user("other@example.com", "practitioner", "Other Clinic")
        other_practitioner_id = uuid.uuid4()
        db.add_all([person, practitioner, stranger])
        db.commit()
        personal = Patient(
            id=uuid.uuid4(),
            user_id=person.id,
            subject_user_id=person.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
        )
        clinic = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
        )
        db.add_all([personal, clinic])
        db.flush()
        association = ClinicAssociation(
            id=uuid.uuid4(),
            personal_patient_id=personal.id,
            clinic_patient_id=clinic.id,
            practitioner_user_id=practitioner.id,
            status="active",
            initiated_by="person",
            created_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )
        personal_source = PatientSource(
            id=uuid.uuid4(),
            patient_id=personal.id,
            practitioner_user_id=person.id,
            source_type="manual",
            display_name="Clinic notes",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        wearable_source = PatientSource(
            id=uuid.uuid4(),
            patient_id=personal.id,
            practitioner_user_id=person.id,
            source_type="wearable",
            display_name="Wearables",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        clinic_source = PatientSource(
            id=uuid.uuid4(),
            patient_id=clinic.id,
            practitioner_user_id=practitioner.id,
            source_type="manual",
            display_name="Clinic notes",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        note = SourceRecord(
            id=uuid.uuid4(),
            patient_source_id=personal_source.id,
            patient_id=personal.id,
            recorded_at=datetime.now(timezone.utc),
            title="Shelf",
            status="ready",
            payload_json={"kind": "note", "text": "Personal shelf note 88120"},
            is_archived=False,
            created_by_user_id=person.id,
            created_at=datetime.now(timezone.utc),
        )
        day = SourceRecord(
            id=uuid.uuid4(),
            patient_source_id=wearable_source.id,
            patient_id=personal.id,
            recorded_at=datetime.now(timezone.utc),
            title="Daily summary",
            status="ready",
            payload_json={"kind": "wearable_day"},
            is_archived=False,
            created_by_user_id=person.id,
            created_at=datetime.now(timezone.utc),
        )
        clinic_note = SourceRecord(
            id=uuid.uuid4(),
            patient_source_id=clinic_source.id,
            patient_id=clinic.id,
            recorded_at=datetime.now(timezone.utc),
            title="Visit",
            status="ready",
            payload_json={"kind": "note", "text": "Private clinic wording 88122"},
            client_summary="Summary the client could see 88123",
            is_archived=False,
            created_by_user_id=practitioner.id,
            created_at=datetime.now(timezone.utc),
        )
        steps = SourceObservation(
            id=uuid.uuid4(),
            source_record_id=day.id,
            patient_id=personal.id,
            position=1,
            name="Steps",
            code="steps",
            value_numeric=88121,
            unit="steps",
            created_at=datetime.now(timezone.utc),
        )
        note_release = _release(personal.id, practitioner.id, f"record:{note.id}", "client_to_clinic")
        day_release = _release(personal.id, practitioner.id, f"wearable:{day.id}", "client_to_clinic")
        clinic_release = _release(clinic.id, practitioner.id, f"record:{clinic_note.id}", "clinic_to_client")
        other_release = _release(personal.id, other_practitioner_id, "record:other-clinic", "client_to_clinic")
        case = Case(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            patient_id=clinic.id,
            title="Morning scan",
            status="draft",
        )
        db.add(case)
        db.flush()
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
        link = ShareLink(
            id=uuid.uuid4(),
            report_version_id=report.id,
            token="kept-end-share",
            is_active=True,
            share_type="report",
            patient_id=clinic.id,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add_all([
            association,
            personal_source,
            wearable_source,
            clinic_source,
            note,
            day,
            clinic_note,
            steps,
            note_release,
            day_release,
            clinic_release,
            other_release,
            report,
            link,
        ])
        db.commit()

        app = FastAPI()
        app.include_router(router)
        holder = {"user": stranger}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        hidden = http.post(f"/api/patients/{clinic.id}/association/end")
        assert hidden.status_code == 404
        assert "Personal shelf note 88120" not in hidden.text
        assert "88121" not in hidden.text
        assert "Private clinic wording 88122" not in hidden.text
        assert "Personal shelf note 88120" in str(list_patient_sources(db, clinic))
        assert "88121" in str(list_patient_sources(db, clinic))
        assert "Visit" in str(released_client_sources(db, clinic))
        assert "Private clinic wording 88122" not in str(released_client_sources(db, clinic))

        holder["user"] = person
        ended = http.post(f"/api/client/records/{personal.id}/association/end")
        assert ended.status_code == 200
        assert ended.json() == {"association_id": str(association.id), "status": "ended"}
        assert "Personal shelf note 88120" not in ended.text
        assert "88121" not in ended.text
        assert "Summary the client could see 88123" not in ended.text

        db.refresh(association)
        db.refresh(personal)
        db.refresh(clinic)
        db.refresh(note)
        db.refresh(steps)
        db.refresh(clinic_note)
        db.refresh(link)
        db.refresh(note_release)
        db.refresh(day_release)
        db.refresh(clinic_release)
        db.refresh(other_release)
        assert association.status == "ended"
        assert association.ended_by == "person"
        assert personal.subject_user_id == person.id
        assert clinic.user_id == practitioner.id
        assert note.payload_json["text"] == "Personal shelf note 88120"
        assert int(steps.value_numeric) == 88121
        assert clinic_note.payload_json["text"] == "Private clinic wording 88122"
        assert note_release.released is False
        assert day_release.released is False
        assert clinic_release.released is False
        assert note_release.withdrawn_at is not None
        assert other_release.released is True
        assert link.token == "kept-end-share"
        assert link.is_active is True
        clinic_view = str(list_patient_sources(db, clinic))
        assert "Personal shelf note 88120" not in clinic_view
        assert "88121" not in clinic_view
        person_view = str(released_client_sources(db, clinic))
        assert "Visit" not in person_view
        assert "Private clinic wording 88122" not in person_view
        assert list_personal_notes(db, personal, person)["notes"][0]["text"] == "Personal shelf note 88120"

        activate_practitioner_association(db, personal=personal, clinic=clinic)
        db.commit()
        db.refresh(association)
        db.refresh(note_release)
        db.refresh(clinic_release)
        assert association.status == "active"
        assert note_release.released is False
        assert clinic_release.released is False
        assert "Personal shelf note 88120" not in str(list_patient_sources(db, clinic))
        assert "Summary the client could see 88123" not in str(released_client_sources(db, clinic))

        holder["user"] = practitioner
        closed = http.post(f"/api/patients/{clinic.id}/association/end")
        assert closed.status_code == 200
        assert closed.json()["status"] == "ended"
        assert "88121" not in closed.text
        db.refresh(association)
        assert association.ended_by == "practitioner"
        assert db.query(Patient).count() == 2
        assert db.query(SourceRecord).count() == 3
        db.refresh(link)
        assert link.token == "kept-end-share"
        db.close()
    finally:
        _restore(engine, originals)
