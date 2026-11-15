"""A client can claim one patient record without becoming the practitioner."""

import inspect
import os
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")
os.environ.setdefault("APP_ENV", "development")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_patient_claims, routes_share, routes_share_links
from app.api.deps import _client_blocked_from_practitioner_path, get_db
from app.db.models import Patient, PatientClaimInvite, User
from app.services.auth_service import verify_email_token
from app.services.patient_claim_service import (
    CLAIM_CONSENT_TEXT,
    complete_reserved_claim,
    hash_claim_token,
)


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


def _route_set(router):
    return {(route.path, ",".join(sorted(route.methods or []))) for route in router.routes}


def test_claim_account_routes_stay_off_share_links():
    assert _route_set(routes_patient_claims.router) == {
        ("/claim/{token}", "GET"),
        ("/claim/{token}", "POST"),
    }
    share_paths = [route.path for route in routes_share.router.routes]
    share_paths += [route.path for route in routes_share_links.router.routes]
    assert all(not path.startswith("/claim/") for path in share_paths)


def test_client_role_is_blocked_from_practitioner_tools():
    client = User(role="client")
    practitioner = User(role="practitioner")
    assert _client_blocked_from_practitioner_path(client, "/api/patients")
    assert _client_blocked_from_practitioner_path(client, "/api/share-links")
    assert _client_blocked_from_practitioner_path(practitioner, "/api/patients") is False
    assert _client_blocked_from_practitioner_path(client, "/claim/token") is False


def test_email_verification_completes_a_reserved_claim():
    assert "complete_reserved_claim" in inspect.getsource(verify_email_token)


def test_client_previews_consents_verifies_and_claims(monkeypatch):
    monkeypatch.setattr(
        "app.services.auth_service.create_email_verification_token",
        lambda db, user, expires_hours=24: "verify-token",
    )
    monkeypatch.setattr(
        "app.services.patient_claim_service.send_verification_email",
        lambda **kwargs: {"sent": True},
    )

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
            role="practitioner",
        )
        db.add(practitioner)
        db.commit()
        patient = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
            notes="Practitioner note that must stay private",
            date_of_birth=datetime(1990, 1, 1).date(),
        )
        db.add(patient)
        db.commit()
        raw_token = "invite-token"
        invite = PatientClaimInvite(
            id=uuid.uuid4(),
            patient_id=patient.id,
            practitioner_user_id=practitioner.id,
            email="ada@example.com",
            token_hash=hash_claim_token(raw_token),
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            created_at=datetime.now(timezone.utc),
        )
        db.add(invite)
        db.commit()
        owner_id = practitioner.id

        app = FastAPI()
        app.include_router(routes_patient_claims.router)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        password = "ClientPass1"

        preview = client.get(f"/claim/{raw_token}")
        assert preview.status_code == 200
        shown = preview.json()
        assert shown["clinic_name"] == "North Clinic"
        assert shown["client_name"] == "Ada Lovelace"
        assert shown["email"] == "ada@example.com"
        assert shown["consent_text"] == CLAIM_CONSENT_TEXT
        assert "notes" not in shown
        assert "date_of_birth" not in shown
        assert "Practitioner note" not in preview.text

        refused = client.post(
            f"/claim/{raw_token}",
            json={
                "email": "ada@example.com",
                "password": password,
                "confirm_password": password,
                "consent": False,
            },
        )
        assert refused.status_code == 400

        wrong_email = client.post(
            f"/claim/{raw_token}",
            json={
                "email": "other@example.com",
                "password": password,
                "confirm_password": password,
                "consent": True,
            },
        )
        assert wrong_email.status_code == 400

        started = client.post(
            f"/claim/{raw_token}",
            json={
                "email": "Ada@Example.com",
                "password": password,
                "confirm_password": password,
                "consent": True,
            },
        )
        assert started.status_code == 200
        assert started.json()["requires_email_verification"] is True
        assert raw_token not in started.json().get("dev_verification_url", "")
        db.refresh(patient)
        db.refresh(invite)
        assert patient.user_id == owner_id
        assert patient.claimed_by_user_id is None
        assert invite.status == "pending"
        assert invite.consent_version == "client-claim-v1"
        account = db.query(User).filter(User.email == "ada@example.com").one()
        assert account.role == "client"
        assert account.clinic_name is None
        assert account.email_verified_at is None

        account.email_verified_at = datetime.now(timezone.utc)
        claimed = complete_reserved_claim(db, account)
        db.commit()
        assert claimed is not None
        assert claimed.id == invite.id
        db.refresh(patient)
        db.refresh(invite)
        assert patient.user_id == owner_id
        assert patient.claimed_by_user_id == account.id
        assert patient.claimed_at is not None
        assert invite.status == "accepted"
        assert invite.claimed_by_user_id == account.id
        assert invite.accepted_at is not None

        used = client.get(f"/claim/{raw_token}")
        assert used.status_code == 409
        again = client.post(
            f"/claim/{raw_token}",
            json={
                "email": "ada@example.com",
                "password": password,
                "confirm_password": password,
                "consent": True,
            },
        )
        assert again.status_code == 409

        expired = PatientClaimInvite(
            id=uuid.uuid4(),
            patient_id=patient.id,
            practitioner_user_id=practitioner.id,
            email="expired@example.com",
            token_hash=hash_claim_token("expired-token"),
            status="pending",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            created_at=datetime.now(timezone.utc),
        )
        cancelled = PatientClaimInvite(
            id=uuid.uuid4(),
            patient_id=patient.id,
            practitioner_user_id=practitioner.id,
            email="cancelled@example.com",
            token_hash=hash_claim_token("cancelled-token"),
            status="cancelled",
            cancelled_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            created_at=datetime.now(timezone.utc),
        )
        db.add_all([expired, cancelled])
        db.commit()
        assert client.get("/claim/expired-token").status_code == 409
        assert client.get("/claim/cancelled-token").status_code == 409
        assert client.get("/claim/missing-token").status_code == 404
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
