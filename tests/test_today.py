"""Today lists stay limited to names, types, titles, and dates."""

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

from app.api import routes_association_requests, routes_client_records
from app.api.deps import get_current_user, get_db
from app.db.models import (
    Case,
    ClinicAssociation,
    ContentRelease,
    Patient,
    PatientSource,
    ReportStatus,
    ReportVersion,
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


SECRET = "Secret note body should stay hidden"
HIDDEN_VALUE = "Hidden lab value zz-secret"


def test_today_lists_omit_health_values():
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
        Case,
        ReportVersion,
        ContentRelease,
        ClinicAssociation,
    )
    originals = []
    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
    try:
        for model in models:
            model.__table__.create(engine)
        db = Session(engine)
        now = datetime.now(timezone.utc)
        practitioner = User(
            id=uuid.uuid4(),
            email="clinic@example.com",
            full_name="Clinic Practitioner",
            clinic_name="North Clinic",
            password_hash="unused",
            is_active=True,
            role="practitioner",
            email_verified_at=now,
        )
        client_user = User(
            id=uuid.uuid4(),
            email="ada@example.com",
            full_name="Ada Lovelace",
            password_hash="unused",
            is_active=True,
            role="client",
            email_verified_at=now,
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
            claimed_at=now,
        )
        db.add(claimed)
        db.commit()
        source = PatientSource(
            id=uuid.uuid4(),
            patient_id=claimed.id,
            practitioner_user_id=practitioner.id,
            source_type="manual",
            display_name="Clinic notes",
            status="active",
        )
        db.add(source)
        db.commit()
        record = SourceRecord(
            id=uuid.uuid4(),
            patient_source_id=source.id,
            patient_id=claimed.id,
            recorded_at=now,
            title="Visit note",
            status="ready",
            payload_json={"secret": HIDDEN_VALUE},
            client_summary=SECRET,
            is_archived=False,
            created_by_user_id=practitioner.id,
        )
        released_case = Case(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            patient_id=claimed.id,
            title="Released scan",
            status="draft",
        )
        hidden_case = Case(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            patient_id=claimed.id,
            title="Unreleased scan",
            status="draft",
        )
        db.add_all([record, released_case, hidden_case])
        db.commit()
        released = ReportVersion(
            id=uuid.uuid4(),
            case_id=released_case.id,
            created_by_user_id=practitioner.id,
            version_number=1,
            status=ReportStatus.ready,
            recommendation_mode="natural_approaches_clinical",
            report_type="assessment",
            is_archived=False,
            generated_at=now,
        )
        hidden = ReportVersion(
            id=uuid.uuid4(),
            case_id=hidden_case.id,
            created_by_user_id=practitioner.id,
            version_number=1,
            status=ReportStatus.ready,
            recommendation_mode="natural_approaches_clinical",
            report_type="assessment",
            is_archived=False,
            generated_at=now,
        )
        db.add_all([released, hidden])
        db.commit()
        db.add_all([
            ContentRelease(
                id=uuid.uuid4(),
                patient_id=claimed.id,
                practitioner_user_id=practitioner.id,
                source_key=f"record:{record.id}",
                direction="clinic_to_client",
                released=True,
                released_at=now,
            ),
            ContentRelease(
                id=uuid.uuid4(),
                patient_id=claimed.id,
                practitioner_user_id=practitioner.id,
                source_key=str(released.id),
                direction="clinic_to_client",
                released=True,
                released_at=now,
            ),
            ContentRelease(
                id=uuid.uuid4(),
                patient_id=claimed.id,
                practitioner_user_id=practitioner.id,
                source_key=f"record:{record.id}",
                direction="client_to_clinic",
                released=True,
                released_at=now,
            ),
        ])
        db.commit()

        app = FastAPI()
        app.include_router(routes_client_records.router)
        app.include_router(routes_association_requests.router)
        holder = {"user": client_user}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        today = http.get("/api/client/today")
        assert today.status_code == 200
        body = today.json()
        assert body["connection"] == "connected"
        assert body["clinic_name"] == "North Clinic"
        assert body["wearable"] is None
        titles = [item["title"] for item in body["from_clinic"]]
        assert titles == ["Visit note", "Released scan"] or set(titles) == {"Visit note", "Released scan"}
        assert all(set(item) == {"title", "recorded_on", "patient_id"} for item in body["from_clinic"])
        dumped = today.text
        assert SECRET not in dumped
        assert HIDDEN_VALUE not in dumped
        assert "Private practitioner note" not in dumped
        assert "Unreleased scan" not in dumped

        holder["user"] = practitioner
        attention = http.get("/api/dashboard/attention")
        assert attention.status_code == 200
        payload = attention.json()
        assert payload["requests"] == []
        assert payload["released_items"] == [{
            "patient_id": str(claimed.id),
            "patient_name": "Ada Lovelace",
            "item_type": "Note",
            "released_on": now.date().isoformat(),
            "section": "notes",
        }]
        assert payload["unreleased_reports"] == [{
            "patient_id": str(claimed.id),
            "patient_name": "Ada Lovelace",
            "report_title": "Unreleased scan",
            "section": "reports",
        }]
        clinic_text = attention.text
        assert SECRET not in clinic_text
        assert HIDDEN_VALUE not in clinic_text
        assert "Private practitioner note" not in clinic_text
        db.close()
    finally:
        for column, original in originals:
            column.type = original
