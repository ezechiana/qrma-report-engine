"""Share links stay token access and do not claim or depend on a client account."""

import inspect
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_share
from app.api.deps import get_db
from app.api.routes_share_links import revoke_share
from app.api.share_helpers import _enforce_share_payment_or_402, _share_payment_state
from app.db.models import (
    AuditLog,
    Case,
    Patient,
    PatientClaimInvite,
    PractitionerSettings,
    ReportStatus,
    ReportVersion,
    ShareLink,
    User,
)
from app.services.patient_claim_service import cancel_claim_invite, hash_claim_token
from app.services.share_link_service import create_share_link


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


class _PaymentResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _PaymentDB:
    def __init__(self, row):
        self.row = row
        self.sql = ""

    def execute(self, statement, params):
        self.sql = str(statement)
        return _PaymentResult(self.row)


def test_share_routes_do_not_reference_claim_records():
    share_source = inspect.getsource(routes_share)
    payment_source = inspect.getsource(_share_payment_state)
    cancel_source = inspect.getsource(cancel_claim_invite)
    revoke_source = inspect.getsource(revoke_share)
    assert "PatientClaimInvite" not in share_source
    assert "claimed_by_user_id" not in share_source
    assert "patient_claim" not in payment_source
    assert "ShareLink" not in cancel_source
    assert "users" not in revoke_source.lower()
    assert "is_active = false" in revoke_source


def test_payment_gate_still_locks_and_unlocks_without_a_claim():
    link = SimpleNamespace(id=uuid.uuid4())
    unpaid = {
        "requires_payment": True,
        "payment_status": "unpaid",
        "price_amount": 2500,
        "price_currency": "gbp",
        "stripe_checkout_session_id": None,
        "paid_at": None,
        "access_label": "Client progress view",
    }
    unpaid_db = _PaymentDB(unpaid)
    state = _share_payment_state(unpaid_db, link)
    assert state["is_unlocked"] is False
    assert "FROM share_links" in unpaid_db.sql
    with pytest.raises(HTTPException) as blocked:
        _enforce_share_payment_or_402(unpaid_db, link)
    assert blocked.value.status_code == 402

    paid = dict(unpaid, payment_status="paid")
    opened = _share_payment_state(_PaymentDB(paid), link)
    assert opened["is_unlocked"] is True
    assert _enforce_share_payment_or_402(_PaymentDB(paid), link)["payment_status"] == "paid"


def test_share_links_keep_working_with_or_without_a_claim_invite(monkeypatch):
    payment_flags = {}

    def payment_state(db, link):
        flags = payment_flags.get(link.token, {"requires_payment": False, "payment_status": "not_required"})
        requires_payment = flags["requires_payment"]
        payment_status = flags["payment_status"]
        return {
            "requires_payment": requires_payment,
            "payment_status": payment_status,
            "price_amount": 2500,
            "price_currency": "gbp",
            "is_unlocked": (not requires_payment) or payment_status in {"paid", "not_required"},
            "access_label": "Client progress view",
            "price_display": "GBP 25",
        }

    monkeypatch.setattr(routes_share, "_share_payment_state", payment_state)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models = (
        User,
        PractitionerSettings,
        Patient,
        PatientClaimInvite,
        Case,
        ReportVersion,
        ShareLink,
        AuditLog,
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
        practitioner = User(
            id=uuid.uuid4(),
            email="clinic@example.com",
            full_name="Clinic Practitioner",
            clinic_name="North Clinic",
            password_hash="unused",
            is_active=True,
            role="practitioner",
        )
        client = User(
            id=uuid.uuid4(),
            email="ada@example.com",
            full_name="Ada Lovelace",
            clinic_name=None,
            password_hash="client-password",
            is_active=True,
            role="client",
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add_all([practitioner, client])
        db.commit()

        plain = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Grace",
            last_name="Hopper",
            full_name="Grace Hopper",
            notes="Practitioner note",
        )
        invited_patient = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
            notes="Another practitioner note",
        )
        db.add_all([plain, invited_patient])
        db.commit()

        def add_report(patient, report_type):
            case = Case(
                id=uuid.uuid4(),
                user_id=practitioner.id,
                patient_id=patient.id,
                title=report_type,
                status="draft",
            )
            db.add(case)
            db.commit()
            report = ReportVersion(
                id=uuid.uuid4(),
                case_id=case.id,
                created_by_user_id=practitioner.id,
                version_number=1,
                status=ReportStatus.ready,
                recommendation_mode="natural_approaches_clinical",
                report_type=report_type,
                report_json={"viewer": {"tenant": {"brand_name": "North Clinic"}}},
                is_archived=False,
            )
            db.add(report)
            db.commit()
            return report

        plain_report = add_report(plain, "assessment")
        plain_trend = add_report(plain, "trend")
        invited_report = add_report(invited_patient, "assessment")
        locked_report = add_report(invited_patient, "assessment")
        expired_report = add_report(plain, "assessment")
        paid_report = add_report(invited_patient, "trend")

        future = datetime.utcnow() + timedelta(days=7)
        plain_link = create_share_link(
            db, report_version=plain_report, patient_id=plain.id, share_type="report", expires_at=future,
        )
        trend_link = create_share_link(
            db, report_version=plain_trend, patient_id=plain.id, share_type="trend", expires_at=future,
        )
        invited_link = create_share_link(
            db, report_version=invited_report, patient_id=invited_patient.id, share_type="report", expires_at=future,
        )
        locked_link = create_share_link(
            db,
            report_version=locked_report,
            patient_id=invited_patient.id,
            share_type="report",
            password="SharePass1",
            expires_at=future,
        )
        expired_link = create_share_link(
            db,
            report_version=expired_report,
            patient_id=plain.id,
            share_type="report",
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        pay_link = create_share_link(
            db, report_version=paid_report, patient_id=invited_patient.id, share_type="trend", expires_at=future,
        )
        payment_flags[pay_link.token] = {"requires_payment": True, "payment_status": "unpaid"}

        invite = PatientClaimInvite(
            id=uuid.uuid4(),
            patient_id=invited_patient.id,
            practitioner_user_id=practitioner.id,
            email="ada@example.com",
            token_hash=hash_claim_token("invite-token"),
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            created_at=datetime.now(timezone.utc),
        )
        db.add(invite)
        db.commit()

        app = FastAPI()
        app.include_router(routes_share.router)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        http = TestClient(app)

        def assert_unclaimed():
            db.refresh(plain)
            db.refresh(invited_patient)
            assert plain.claimed_by_user_id is None
            assert invited_patient.claimed_by_user_id is None
            assert db.query(User).count() == 2

        opened = http.get(f"/share/{plain_link.token}")
        assert opened.status_code == 200
        assert "Download PDF" in opened.text
        trend = http.get(f"/share/{trend_link.token}")
        assert trend.status_code == 200
        assert "Progress snapshot" in trend.text
        with_invite = http.get(f"/share/{invited_link.token}")
        assert with_invite.status_code == 200
        assert "Download PDF" in with_invite.text
        assert_unclaimed()

        locked = http.get(f"/share/{locked_link.token}")
        assert locked.status_code == 200
        assert "Unlock Report" in locked.text
        rejected = http.post(
            f"/share/{locked_link.token}/verify-password",
            data={"password": "wrong-password"},
        )
        assert rejected.status_code == 401
        unlocked = http.post(
            f"/share/{locked_link.token}/verify-password",
            data={"password": "SharePass1"},
        )
        assert unlocked.status_code == 200
        assert "Download PDF" in unlocked.text
        assert_unclaimed()

        expired = http.get(f"/share/{expired_link.token}")
        assert expired.status_code == 404
        assert "has expired" in expired.text

        paywall = http.get(f"/share/{pay_link.token}/trend")
        assert paywall.status_code == 200
        assert "Continue to secure payment" in paywall.text
        payment_flags[pay_link.token] = {"requires_payment": True, "payment_status": "paid"}
        paid_page = http.get(f"/share/{pay_link.token}/trend")
        assert paid_page.status_code == 200
        assert "Progress snapshot" in paid_page.text
        assert "Continue to secure payment" not in paid_page.text
        assert_unclaimed()

        invited_patient.claimed_by_user_id = client.id
        invited_patient.claimed_at = datetime.now(timezone.utc)
        db.commit()
        claimed_open = http.get(f"/share/{invited_link.token}")
        assert claimed_open.status_code == 200
        assert "Download PDF" in claimed_open.text
        db.refresh(invited_patient)
        db.refresh(client)
        assert invited_patient.claimed_by_user_id == client.id
        assert client.role == "client"
        assert db.query(User).filter(User.id == client.id).count() == 1

        cancel_claim_invite(db, practitioner.id, invited_patient.id, invite.id)
        db.refresh(invited_link)
        db.refresh(invite)
        assert invite.status == "cancelled"
        assert invited_link.is_active is True
        assert invited_link.token
        still_open = http.get(f"/share/{invited_link.token}")
        assert still_open.status_code == 200

        revoked = revoke_share(str(invited_link.id), db=db, user=practitioner)
        assert revoked == {"success": True}
        db.expire_all()
        assert db.query(User).filter(User.id == client.id).count() == 1
        assert db.get(Patient, invited_patient.id) is not None
        assert db.get(Patient, invited_patient.id).claimed_by_user_id == client.id
        # SQLite stores the UUID without dashes, so the route's dashed id matches
        # zero rows here. Postgres accepts that id. Apply the same update with the
        # stored id to confirm a revoked link is the only thing that changes.
        stored_id = db.execute(
            text("SELECT id FROM share_links WHERE token = :token"),
            {"token": invited_link.token},
        ).scalar()
        db.execute(
            text("UPDATE share_links SET is_active = false WHERE id = :id"),
            {"id": stored_id},
        )
        db.commit()
        withdrawn = http.get(f"/share/{invited_link.token}")
        assert withdrawn.status_code == 404
        assert "no longer available" in withdrawn.text
        assert db.query(User).count() == 2
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
