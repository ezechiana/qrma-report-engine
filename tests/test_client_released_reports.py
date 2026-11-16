"""The client portal lists and opens only released assessment and trend reports."""

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

from app.api import routes_client_records, routes_ui
from app.api.deps import get_current_user, get_db
from app.db.models import Case, ContentRelease, Patient, ReportStatus, ReportVersion, ShareLink, User
from app.services.content_release_service import set_progress_release


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_client_portal_lists_and_opens_only_released_reports(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_patients.get_patient_trends",
        lambda **kwargs: {
            "view_mode": "client",
            "client_view": {"summary": "Progress is ready to review.", "progress_cards": [], "top_priorities": [], "recommendations": []},
        },
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    models = (User, Patient, Case, ReportVersion, ShareLink, ContentRelease)
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
        other_client = User(
            id=uuid.uuid4(),
            email="grace@example.com",
            full_name="Grace Hopper",
            password_hash="unused",
            is_active=True,
            role="client",
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add_all([practitioner, client_user, other_client])
        db.commit()
        patient = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
            notes="Private practitioner note",
            claimed_by_user_id=client_user.id,
            claimed_at=datetime.now(timezone.utc),
        )
        db.add(patient)
        db.commit()

        def add_report(title, report_type, summary):
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
                is_archived=False,
                report_json={
                    "viewer": {
                        "overview": {
                            "overall_summary": summary,
                            "why_this_matters": "This explains the change.",
                            "practitioner_summary": "Internal practitioner note",
                        },
                        "systems": {"secret_system": []},
                        "recommendations": {"products": [{"name": "Hidden product"}]},
                    },
                    "markers": {"secret_marker": []},
                },
            )
            db.add(report)
            db.commit()
            return report

        assessment = add_report("Morning scan", "assessment", "The morning scan is ready to read.")
        hidden = add_report("Private scan", "assessment", "This scan stays with the clinic.")
        trend = add_report("Quarter trend", "trend", "The quarter trend is ready to read.")
        db.add(ShareLink(
            id=uuid.uuid4(),
            report_version_id=hidden.id,
            patient_id=patient.id,
            token="hidden-share",
            is_active=True,
            share_type="report",
        ))
        db.add(ContentRelease(
            id=uuid.uuid4(),
            patient_id=patient.id,
            practitioner_user_id=practitioner.id,
            source_key=str(assessment.id),
            direction="clinic_to_client",
            released=True,
            released_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        ))
        db.add(ContentRelease(
            id=uuid.uuid4(),
            patient_id=patient.id,
            practitioner_user_id=practitioner.id,
            source_key=str(trend.id),
            direction="clinic_to_client",
            released=True,
            released_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()

        app = FastAPI()
        app.include_router(routes_client_records.router)
        app.include_router(routes_ui.router)
        holder = {"user": client_user}

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        http = TestClient(app)

        listed = http.get(f"/api/client/records/{patient.id}")
        assert listed.status_code == 200
        body = listed.json()
        assert body["trend"]["client_view"]["summary"] == "Your practitioner has not released a progress view yet."
        assert body["reports"]["assessment_reports"] == [{
            "report_id": str(assessment.id),
            "label": "Morning scan v1",
        }]
        assert body["reports"]["trend_reports"] == [{
            "report_id": str(trend.id),
            "label": "Quarter trend v1",
        }]
        assert "Private scan" not in listed.text
        assert "hidden-share" not in listed.text
        assert "Private practitioner note" not in listed.text
        assert "secret_marker" not in listed.text
        assert db.query(ShareLink).count() == 1

        opened = http.get(f"/api/client/records/{patient.id}/reports/{assessment.id}")
        assert opened.status_code == 200
        report_body = opened.json()
        assert set(report_body) == {"report_id", "label", "report_type", "summary", "sections"}
        assert report_body["label"] == "Morning scan v1"
        assert report_body["report_type"] == "assessment"
        assert report_body["summary"] == "The morning scan is ready to read."
        assert report_body["sections"] == [{"title": "Why this matters", "body": "This explains the change."}]
        for hidden_text in (
            "Internal practitioner note",
            "Private practitioner note",
            "Hidden product",
            "secret_marker",
            "secret_system",
            "recommendation_mode",
            "hidden-share",
        ):
            assert hidden_text not in opened.text
        assert db.query(ShareLink).count() == 1

        denied = http.get(f"/api/client/records/{patient.id}/reports/{hidden.id}")
        assert denied.status_code == 404

        holder["user"] = other_client
        foreign = http.get(f"/api/client/records/{patient.id}/reports/{assessment.id}")
        assert foreign.status_code == 404
        holder["user"] = client_user

        set_progress_release(db, patient, True)
        with_progress = http.get(f"/api/client/records/{patient.id}")
        assert with_progress.json()["trend"]["client_view"]["summary"] == "Progress is ready to review."
        assert [item["label"] for item in with_progress.json()["reports"]["assessment_reports"]] == ["Morning scan v1"]
        assert "Private scan" not in with_progress.text

        page = http.get(f"/client/records/{patient.id}/reports/{assessment.id}")
        assert page.status_code == 200
        assert "Loading your report" in page.text
        assert "Back to your record" in page.text
        assert str(patient.id) not in page.text
        assert str(assessment.id) not in page.text
        record_page = open("app/templates/client_record.html", encoding="utf-8").read()
        assert "Assessment reports" in record_page
        assert "Trend reports" in record_page
        assert "/reports/" in record_page
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
