"""Practitioner claim invites stay off the share-link routes."""

import os
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_patient_claim_invites, routes_patient_records, routes_share, routes_share_links
from app.api.deps import get_current_user, get_db
from app.db.models import Patient, PatientClaimInvite, User
from app.services.patient_claim_service import hash_claim_token


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


def _route_set(router):
    return {
        (route.path, ",".join(sorted(route.methods or [])))
        for route in router.routes
    }


def test_claim_invites_are_not_share_links():
    assert _route_set(routes_patient_claim_invites.router) == {
        ("/api/patients/{patient_id}/claim-invites", "GET"),
        ("/api/patients/{patient_id}/claim-invites", "POST"),
        ("/api/patients/{patient_id}/claim-invites/{invite_id}/cancel", "POST"),
        ("/api/patients/{patient_id}/claim-access", "GET"),
        ("/api/patients/{patient_id}/claim-access/unlink", "POST"),
    }
    share_paths = [route.path for route in routes_share.router.routes]
    share_paths += [route.path for route in routes_share_links.router.routes]
    assert all("claim-invite" not in path and "claim-access" not in path for path in share_paths)
    record_paths = [route.path for route in routes_patient_records.router.routes]
    assert "/api/patients/{patient_id}/claim-invites" not in record_paths


def test_practitioner_can_send_list_and_cancel_a_claim_invite(monkeypatch):
    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)
        return {"sent": True, "provider": "test"}

    monkeypatch.setattr("app.services.patient_claim_service.send_email", fake_send)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    for model in (User, Patient, PatientClaimInvite):
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
    try:
        for model in (User, Patient, PatientClaimInvite):
            model.__table__.create(engine)
        db = Session(engine)
        practitioner = User(
            id=uuid.uuid4(),
            email="clinic@example.com",
            full_name="Clinic Practitioner",
            clinic_name="North Clinic",
            password_hash="unused",
            is_active=True,
        )
        other = User(
            id=uuid.uuid4(),
            email="other-clinic@example.com",
            full_name="Other Practitioner",
            password_hash="unused",
            is_active=True,
        )
        db.add_all([practitioner, other])
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
        )
        db.add_all([patient, hidden])
        db.commit()

        app = FastAPI()
        app.include_router(routes_patient_claim_invites.router)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: practitioner
        client = TestClient(app)

        denied = client.post(
            f"/api/patients/{hidden.id}/claim-invites",
            json={"email": "grace@example.com"},
        )
        assert denied.status_code == 404

        created = client.post(
            f"/api/patients/{patient.id}/claim-invites",
            json={"email": "Ada@Example.com"},
        )
        assert created.status_code == 200
        body = created.json()
        assert body["email"] == "ada@example.com"
        assert body["status"] == "pending"
        assert body["patient_id"] == str(patient.id)
        assert body["email_sent"] is True
        token = body["claim_url"].rsplit("/", 1)[-1]
        assert token not in body["email"]
        stored = db.query(PatientClaimInvite).filter(PatientClaimInvite.id == uuid.UUID(body["id"])).one()
        assert stored.token_hash == hash_claim_token(token)
        assert stored.token_hash != token
        assert token in sent["text"]
        assert sent["to_email"] == "ada@example.com"
        assert "Lovelace" not in sent["text"]

        listed = client.get(f"/api/patients/{patient.id}/claim-invites")
        assert listed.status_code == 200
        visible = listed.json()
        assert [item["status"] for item in visible] == ["pending"]
        assert "claim_url" not in visible[0]
        assert "token" not in visible[0]
        assert "token_hash" not in visible[0]

        replaced = client.post(
            f"/api/patients/{patient.id}/claim-invites",
            json={"email": "ada.updated@example.com"},
        )
        assert replaced.status_code == 200
        assert replaced.json()["status"] == "pending"
        statuses = {
            item["email"]: item["status"]
            for item in client.get(f"/api/patients/{patient.id}/claim-invites").json()
        }
        assert statuses == {
            "ada@example.com": "cancelled",
            "ada.updated@example.com": "pending",
        }

        current_id = replaced.json()["id"]
        stored_current = db.query(PatientClaimInvite).filter(
            PatientClaimInvite.id == uuid.UUID(current_id)
        ).one()
        stored_current.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        expired = client.get(f"/api/patients/{patient.id}/claim-invites").json()
        assert next(item for item in expired if item["id"] == current_id)["status"] == "expired"

        cancelled = client.post(
            f"/api/patients/{patient.id}/claim-invites/{current_id}/cancel"
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        again = client.post(
            f"/api/patients/{patient.id}/claim-invites/{current_id}/cancel"
        )
        assert again.status_code == 409

        stored_current.status = "accepted"
        stored_current.accepted_at = datetime.now(timezone.utc)
        db.commit()
        accepted = client.get(f"/api/patients/{patient.id}/claim-invites").json()
        assert next(item for item in accepted if item["id"] == current_id)["status"] == "accepted"
        used = client.post(f"/api/patients/{patient.id}/claim-invites/{current_id}/cancel")
        assert used.status_code == 409

        def fail_send(**kwargs):
            raise RuntimeError("mailbox unavailable")

        monkeypatch.setattr("app.services.patient_claim_service.send_email", fail_send)
        before = db.query(PatientClaimInvite).count()
        failed = client.post(
            f"/api/patients/{patient.id}/claim-invites",
            json={"email": "ada@example.com"},
        )
        assert failed.status_code == 502
        assert db.query(PatientClaimInvite).count() == before
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
