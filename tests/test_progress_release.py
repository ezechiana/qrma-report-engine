"""Portal progress-view release stays off the share-link tables."""

import inspect
import os
import uuid
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_content_releases
from app.api.deps import _client_blocked_from_practitioner_path, get_current_user, get_db
from app.db.models import ContentRelease, Patient, PatientClaimInvite, User
from app.services import client_record_service, content_release_service
from app.services.patient_claim_service import end_claim_access


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


def test_progress_release_is_not_a_share_link():
    source = inspect.getsource(content_release_service)
    assert "ShareLink" not in source
    assert "share_link" not in source
    portal = inspect.getsource(client_record_service)
    assert "content_release" not in portal
    assert "ContentRelease" not in portal
    paths = {route.path for route in routes_content_releases.router.routes}
    assert paths == {"/api/patients/{patient_id}/progress-release"}
    client = User(role="client")
    assert _client_blocked_from_practitioner_path(client, "/api/patients/1/progress-release") is True


def test_practitioner_can_release_and_withdraw_the_progress_view():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    models = (User, Patient, PatientClaimInvite, ContentRelease)
    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
    try:
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
            password_hash="unused",
            is_active=True,
            role="client",
            email_verified_at=datetime.now(timezone.utc),
        )
        other = User(
            id=uuid.uuid4(),
            email="other@example.com",
            full_name="Other Practitioner",
            password_hash="unused",
            is_active=True,
            role="practitioner",
        )
        db.add_all([practitioner, client_user, other])
        db.commit()
        patient = Patient(
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
        )
        hidden = Patient(
            user_id=other.id,
            first_name="Grace",
            last_name="Hopper",
            full_name="Grace Hopper",
            claimed_by_user_id=client_user.id,
        )
        db.add_all([patient, hidden])
        db.commit()

        app = FastAPI()
        app.include_router(routes_content_releases.router)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: practitioner
        client = TestClient(app)

        denied = client.post(
            f"/api/patients/{hidden.id}/progress-release",
            json={"released": True},
        )
        assert denied.status_code == 404

        before = client.get(f"/api/patients/{patient.id}/progress-release")
        assert before.status_code == 200
        assert before.json()["released"] is False
        assert before.json()["available"] is False
        assert "claimed_by_user_id" not in before.json()

        blocked = client.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": True},
        )
        assert blocked.status_code == 409

        patient.claimed_by_user_id = client_user.id
        db.commit()

        released = client.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": True},
        )
        assert released.status_code == 200
        body = released.json()
        assert set(body) == {"patient_id", "source", "direction", "released", "available"}
        assert body["source"] == "progress_view"
        assert body["direction"] == "clinic_to_client"
        assert body["released"] is True
        assert body["available"] is True
        assert db.query(ContentRelease).count() == 1

        withdrawn = client.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": False},
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json()["released"] is False
        assert db.query(ContentRelease).count() == 1

        again = client.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": True},
        )
        assert again.json()["released"] is True
        end_claim_access(db, patient, "client")
        db.refresh(patient)
        assert patient.claimed_by_user_id is None
        after_end = client.get(f"/api/patients/{patient.id}/progress-release")
        assert after_end.json()["released"] is False
        assert after_end.json()["available"] is False

        page = open("app/templates/patient_detail.html", encoding="utf-8").read()
        assert "Release progress view" in page
        assert "Withdraw the progress view? Share links stay as they are." in page
        assert "/progress-release" in page
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
