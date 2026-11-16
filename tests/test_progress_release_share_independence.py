"""Portal progress-view release and share links do not change each other."""

import os
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.api import routes_client_records, routes_content_releases, routes_share, routes_share_links
from app.api.deps import get_current_user, get_db
from app.db.models import (
    AuditLog,
    Case,
    ContentRelease,
    Patient,
    PatientSource,
    PractitionerSettings,
    ReportStatus,
    ReportVersion,
    ShareLink,
    SourceRecord,
    User,
)
from app.services.content_release_service import progress_release_state
from app.services.share_link_service import create_share_link

EMPTY_SUMMARY = "Your practitioner has not released a progress view yet."
RELEASED_SUMMARY = "Progress is ready to review."


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_portal_release_and_share_links_stay_independent(monkeypatch):
    calls = []

    def fake_trends(**kwargs):
        calls.append(kwargs)
        return {
            "view_mode": "client",
            "client_view": {
                "summary": RELEASED_SUMMARY,
                "progress_cards": [],
                "top_priorities": [],
                "recommendations": [],
                "review_note": "Client-safe summary.",
            },
        }

    monkeypatch.setattr("app.api.routes_patients.get_patient_trends", fake_trends)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    models = (
        User,
        PractitionerSettings,
        Patient,
        PatientSource,
        SourceRecord,
        Case,
        ReportVersion,
        ShareLink,
        ContentRelease,
        AuditLog,
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
        db.add_all([practitioner, client_user])
        db.commit()
        patient = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
            claimed_by_user_id=client_user.id,
            claimed_at=datetime.now(timezone.utc),
        )
        db.add(patient)
        db.commit()
        case = Case(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            patient_id=patient.id,
            title="Scan",
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
            report_type="assessment",
            report_json={"viewer": {"tenant": {"brand_name": "North Clinic"}}},
            is_archived=False,
        )
        db.add(report)
        db.commit()

        app = FastAPI()
        app.include_router(routes_content_releases.router)
        app.include_router(routes_client_records.router)
        app.include_router(routes_share.router)
        app.include_router(routes_share_links.router)
        holder = {"user": practitioner}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        def portal_summary():
            holder["user"] = client_user
            opened = http.get(f"/api/client/records/{patient.id}")
            assert opened.status_code == 200
            return opened.json()["trend"]["client_view"]["summary"]

        def share_fields(link):
            db.refresh(link)
            return (
                link.token,
                bool(link.is_active),
                link.share_type,
                link.expires_at.isoformat() if link.expires_at else None,
                link.password_hash,
                str(link.report_version_id),
                str(link.patient_id),
            )

        released = http.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": True},
        )
        assert released.status_code == 200
        assert released.json()["released"] is True
        assert db.query(ShareLink).count() == 0
        assert portal_summary() == RELEASED_SUMMARY
        assert calls[-1]["source_report_ids"] is None

        link = create_share_link(
            db,
            report_version=report,
            patient_id=patient.id,
            share_type="report",
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        before = share_fields(link)
        assert progress_release_state(db, patient)["released"] is True
        assert db.query(ShareLink).count() == 1

        opened_share = http.get(f"/share/{link.token}")
        assert opened_share.status_code == 200
        assert "Download PDF" in opened_share.text
        assert progress_release_state(db, patient)["released"] is True
        assert share_fields(link) == before

        holder["user"] = practitioner
        withdrawn = http.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": False},
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json()["released"] is False
        assert share_fields(link) == before
        assert portal_summary() == EMPTY_SUMMARY
        assert len(calls) == 1

        still_open = http.get(f"/share/{link.token}")
        assert still_open.status_code == 200
        assert "Download PDF" in still_open.text
        assert progress_release_state(db, patient)["released"] is False
        assert share_fields(link) == before

        holder["user"] = practitioner
        restored = http.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": True},
        )
        assert restored.status_code == 200
        # SQLite stores the UUID without dashes, so the route's dashed id matches
        # zero rows here. Revoke with the stored id, which is what Postgres accepts.
        stored_id = db.execute(
            text("SELECT id FROM share_links WHERE token = :token"),
            {"token": link.token},
        ).scalar()
        revoked = http.post(f"/api/share/revoke/{stored_id}")
        assert revoked.status_code == 200
        assert revoked.json() == {"success": True}
        db.expire_all()
        assert share_fields(link)[1] is False
        assert progress_release_state(db, patient)["released"] is True
        assert portal_summary() == RELEASED_SUMMARY
        closed = http.get(f"/share/{link.token}")
        assert closed.status_code == 404
        assert "no longer available" in closed.text
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
