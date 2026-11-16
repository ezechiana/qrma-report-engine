"""Portal report releases and share links do not change each other."""

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
    PractitionerSettings,
    ReportStatus,
    ReportVersion,
    ShareLink,
    User,
)
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


def test_report_releases_and_share_links_stay_independent(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_patients.get_patient_trends",
        lambda **kwargs: {
            "view_mode": "client",
            "client_view": {
                "summary": RELEASED_SUMMARY,
                "progress_cards": [],
                "top_priorities": [],
                "recommendations": [],
            },
        },
    )

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

        def add_report(title, report_type):
            case = Case(
                id=uuid.uuid4(),
                user_id=practitioner.id,
                patient_id=patient.id,
                title=title,
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

        assessment = add_report("Morning scan", "assessment")
        trend = add_report("Quarter trend", "trend")

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

        def release_report(report, released):
            holder["user"] = practitioner
            response = http.post(
                f"/api/patients/{patient.id}/report-releases/{report.id}",
                json={"released": released},
            )
            assert response.status_code == 200
            return response.json()["released"]

        def portal():
            holder["user"] = client_user
            opened = http.get(f"/api/client/records/{patient.id}")
            assert opened.status_code == 200
            body = opened.json()
            return (
                body["trend"]["client_view"]["summary"],
                [item["label"] for item in body["reports"]["assessment_reports"]],
                [item["label"] for item in body["reports"]["trend_reports"]],
                opened.text,
            )

        def share_fields(link):
            db.refresh(link)
            return (
                link.token,
                bool(link.is_active),
                link.share_type,
                link.expires_at.isoformat() if link.expires_at else None,
                link.password_hash,
            )

        assert release_report(assessment, True) is True
        assert release_report(trend, True) is True
        assert db.query(ShareLink).count() == 0
        summary, assessments, trends, page = portal()
        assert summary == EMPTY_SUMMARY
        assert assessments == ["Morning scan v1"]
        assert trends == ["Quarter trend v1"]

        link = create_share_link(
            db,
            report_version=assessment,
            patient_id=patient.id,
            share_type="report",
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        before = share_fields(link)
        summary, assessments, trends, page = portal()
        assert summary == EMPTY_SUMMARY
        assert assessments == ["Morning scan v1"]
        assert trends == ["Quarter trend v1"]
        assert link.token not in page

        opened_share = http.get(f"/share/{link.token}")
        assert opened_share.status_code == 200
        assert "Download PDF" in opened_share.text
        summary, assessments, trends, _page = portal()
        assert (summary, assessments, trends) == (
            EMPTY_SUMMARY,
            ["Morning scan v1"],
            ["Quarter trend v1"],
        )
        assert share_fields(link) == before

        assert release_report(assessment, False) is False
        assert share_fields(link) == before
        summary, assessments, trends, _page = portal()
        assert summary == EMPTY_SUMMARY
        assert assessments == []
        assert trends == ["Quarter trend v1"]

        assert release_report(assessment, True) is True
        stored_id = db.execute(
            text("SELECT id FROM share_links WHERE token = :token"),
            {"token": link.token},
        ).scalar()
        holder["user"] = practitioner
        revoked = http.post(f"/api/share/revoke/{stored_id}")
        assert revoked.status_code == 200
        assert revoked.json() == {"success": True}
        db.expire_all()
        assert share_fields(link)[1] is False
        closed = http.get(f"/share/{link.token}")
        assert closed.status_code == 404
        summary, assessments, trends, _page = portal()
        assert (summary, assessments, trends) == (
            EMPTY_SUMMARY,
            ["Morning scan v1"],
            ["Quarter trend v1"],
        )

        assert release_report(assessment, False) is False
        assert release_report(trend, False) is False
        fresh = create_share_link(
            db,
            report_version=assessment,
            patient_id=patient.id,
            share_type="report",
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        summary, assessments, trends, page = portal()
        assert summary == EMPTY_SUMMARY
        assert assessments == []
        assert trends == []
        assert fresh.token not in page
        trend_page = http.get(f"/share/{fresh.token}")
        assert trend_page.status_code == 200
        assert "Download PDF" in trend_page.text
        summary, assessments, trends, _page = portal()
        assert (summary, assessments, trends) == (EMPTY_SUMMARY, [], [])

        holder["user"] = practitioner
        progress = http.post(
            f"/api/patients/{patient.id}/progress-release",
            json={"released": True},
        )
        assert progress.status_code == 200
        assert progress.json()["released"] is True
        summary, assessments, trends, _page = portal()
        assert summary == RELEASED_SUMMARY
        assert assessments == []
        assert trends == []

        assert release_report(assessment, True) is True
        summary, assessments, trends, _page = portal()
        assert summary == RELEASED_SUMMARY
        assert assessments == ["Morning scan v1"]
        assert trends == []
        db.refresh(fresh)
        assert fresh.is_active is True
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
