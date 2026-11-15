"""Patient record routes stay on /api/patients after the split."""

import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_patient_records, routes_patients
from app.api.deps import get_current_user, get_db
from app.api.routes_patients import get_patient_trends
from app.db.models import Case, Patient, ReportVersion, User


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_history_and_trends_stay_importable_from_routes_patients():
    assert get_patient_trends is routes_patients.get_patient_trends
    history = {
        (route.path, ",".join(sorted(route.methods or [])))
        for route in routes_patients.router.routes
    }
    assert history == {
        ("/api/patients/{patient_id}/cases", "GET"),
        ("/api/patients/{patient_id}/reports", "GET"),
        ("/api/patients/{patient_id}/trends", "GET"),
    }
    records = {
        (route.path, ",".join(sorted(route.methods or [])))
        for route in routes_patient_records.router.routes
    }
    assert records == {
        ("/api/patients", "GET"),
        ("/api/patients", "POST"),
        ("/api/patients/{patient_id}", "DELETE"),
        ("/api/patients/{patient_id}", "GET"),
        ("/api/patients/{patient_id}", "PATCH"),
    }


def test_patient_list_and_create_still_work():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    for model in (User, Patient, Case, ReportVersion):
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
    try:
        for model in (User, Patient, Case, ReportVersion):
            model.__table__.create(engine)
        db = Session(engine)
        user = User(
            id=uuid.uuid4(),
            email="records@example.invalid",
            full_name="Records Practitioner",
            password_hash="unused",
            is_active=True,
        )
        other = User(
            id=uuid.uuid4(),
            email="other-records@example.invalid",
            full_name="Other Practitioner",
            password_hash="unused",
            is_active=True,
        )
        db.add_all([user, other])
        db.commit()

        app = FastAPI()
        app.include_router(routes_patient_records.router)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(app)

        empty = client.get("/api/patients")
        assert empty.status_code == 200
        assert empty.json() == []

        created = client.post(
            "/api/patients",
            json={"first_name": "Ada", "last_name": "Lovelace"},
        )
        assert created.status_code == 200
        body = created.json()
        assert body["first_name"] == "Ada"
        assert body["last_name"] == "Lovelace"
        assert body["full_name"] == "Ada Lovelace"
        assert body["case_count"] == 0
        assert body["report_count"] == 0
        assert body["data_sources"] == []

        listed = client.get("/api/patients")
        assert listed.status_code == 200
        assert [item["full_name"] for item in listed.json()] == ["Ada Lovelace"]

        other_patient = Patient(
            user_id=other.id,
            first_name="Grace",
            last_name="Hopper",
            full_name="Grace Hopper",
        )
        db.add(other_patient)
        db.commit()
        visible = client.get("/api/patients")
        assert [item["full_name"] for item in visible.json()] == ["Ada Lovelace"]
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
