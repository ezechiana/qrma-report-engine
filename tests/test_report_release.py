"""Per-report portal release stays off share links and the progress view."""

import inspect
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

from app.api import routes_content_releases
from app.api.deps import _client_blocked_from_practitioner_path, get_current_user, get_db
from app.db.models import (
    Case,
    ContentRelease,
    Patient,
    PatientClaimInvite,
    ReportStatus,
    ReportVersion,
    ShareLink,
    User,
)
from app.services import client_record_service, content_release_service
from app.services.content_release_service import progress_release_state, set_progress_release
from app.services.patient_claim_service import end_claim_access


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


def test_report_release_is_separate_from_share_links_and_the_portal():
    source = inspect.getsource(content_release_service)
    assert "ShareLink" not in source
    assert "share_link" not in source
    portal = inspect.getsource(client_record_service)
    assert "ShareLink" not in portal
    assert "set_report_release" not in portal
    assert "released_client_reports" in portal
    client = User(role="client")
    assert _client_blocked_from_practitioner_path(client, "/api/patients/1/report-releases") is True
    page = open("app/templates/patient_detail.html", encoding="utf-8").read()
    assert "Client portal" in page
    assert "Not claimed" in page
    assert '>${busy ? "Releasing…" : "Release"}</button>' in page
    assert 'snapshotChip(progressReleased ? "Released to client" : "Not released"' in page
    reports_page = open("app/templates/reports.html", encoding="utf-8").read()
    assert "Client portal" in reports_page
    assert "portal-release-btn" in reports_page
    assert "Withdraw this report? Share links and the progress view stay as they are." in reports_page
    assert "Not released" not in reports_page
    assert "Released reports appear in the client's portal. This does not create a share link." in page
    assert "Release individual reports from the Reports section." in page
    assert "Withdraw this report? Share links and the progress view stay as they are." in page
    assert "/report-releases" in page
    assert "claim-report-release-btn" not in page
    detail = open("app/templates/report_detail.html", encoding="utf-8").read()
    assert "Client portal" in detail
    assert "Not released. The client will not see this report in their portal." in detail
    assert "Released. The client can open this report in their portal." in detail
    assert "A client must claim this record before this report can be released." in detail
    assert "Could not load the portal release." in detail
    assert "Releasing this report does not create a share link. Withdrawing it leaves existing share links unchanged." in detail
    assert "Create share link" in detail


def test_practitioner_can_release_and_withdraw_one_report():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    models = (User, Patient, PatientClaimInvite, Case, ReportVersion, ShareLink, ContentRelease)
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
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
            notes="Private practitioner note",
        )
        hidden = Patient(
            id=uuid.uuid4(),
            user_id=other.id,
            first_name="Grace",
            last_name="Hopper",
            full_name="Grace Hopper",
            claimed_by_user_id=client_user.id,
        )
        db.add_all([patient, hidden])
        db.commit()

        def add_report(owner, title, report_type, status, archived=False):
            case = Case(
                id=uuid.uuid4(),
                user_id=owner.id,
                patient_id=patient.id if owner is practitioner else hidden.id,
                title=title,
                status="draft",
            )
            db.add(case)
            db.commit()
            report = ReportVersion(
                id=uuid.uuid4(),
                case_id=case.id,
                created_by_user_id=owner.id,
                version_number=1,
                status=status,
                recommendation_mode="natural_approaches_clinical",
                report_type=report_type,
                is_archived=archived,
            )
            db.add(report)
            db.commit()
            return report

        assessment = add_report(practitioner, "Morning scan", "assessment", ReportStatus.ready)
        trend = add_report(practitioner, "Quarter trend", "trend", ReportStatus.ready)
        add_report(practitioner, "Hidden draft", "assessment", ReportStatus.queued)
        add_report(practitioner, "Old trend", "trend", ReportStatus.ready, archived=True)

        app = FastAPI()
        app.include_router(routes_content_releases.router)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: practitioner
        http = TestClient(app)

        before = http.get(f"/api/patients/{patient.id}/report-releases")
        assert before.status_code == 200
        assert before.json()["available"] is False
        assert before.json()["assessment_reports"] == []
        assert before.json()["trend_reports"] == []
        assert "Private practitioner note" not in before.text
        assert "Morning scan" not in before.text

        blocked = http.post(
            f"/api/patients/{patient.id}/report-releases/{assessment.id}",
            json={"released": True},
        )
        assert blocked.status_code == 409
        denied = http.post(
            f"/api/patients/{hidden.id}/report-releases/{assessment.id}",
            json={"released": True},
        )
        assert denied.status_code == 404
        assert db.query(ContentRelease).count() == 0
        assert db.query(ShareLink).count() == 0

        patient.claimed_by_user_id = client_user.id
        patient.claimed_at = datetime.now(timezone.utc)
        db.commit()

        listed = http.get(f"/api/patients/{patient.id}/report-releases")
        assert listed.status_code == 200
        body = listed.json()
        assert body["available"] is True
        assert [item["label"] for item in body["assessment_reports"]] == ["Morning scan v1"]
        assert [item["label"] for item in body["trend_reports"]] == ["Quarter trend v1"]
        assert body["assessment_reports"][0]["released"] is False
        assert body["trend_reports"][0]["released"] is False
        assert "Hidden draft" not in listed.text
        assert "Old trend" not in listed.text
        assert "Private practitioner note" not in listed.text

        missing = http.post(
            f"/api/patients/{patient.id}/report-releases/{uuid.uuid4()}",
            json={"released": True},
        )
        assert missing.status_code == 404

        db.add(ShareLink(
            id=uuid.uuid4(),
            report_version_id=assessment.id,
            patient_id=patient.id,
            token="existing-share",
            is_active=True,
            share_type="report",
        ))
        db.commit()

        released = http.post(
            f"/api/patients/{patient.id}/report-releases/{assessment.id}",
            json={"released": True},
        )
        assert released.status_code == 200
        assert set(released.json()) == {"patient_id", "report_id", "report_type", "released", "available"}
        assert released.json()["released"] is True
        assert released.json()["report_type"] == "assessment"
        assert released.json()["available"] is True
        share = db.query(ShareLink).one()
        assert share.token == "existing-share"
        assert share.is_active is True
        report_row = db.query(ContentRelease).one()
        assert report_row.source_key != "progress_view"
        assert report_row.released is True

        after = http.get(f"/api/patients/{patient.id}/report-releases")
        by_label = {item["label"]: item for item in after.json()["assessment_reports"] + after.json()["trend_reports"]}
        assert by_label["Morning scan v1"]["released"] is True
        assert by_label["Quarter trend v1"]["released"] is False

        later = add_report(practitioner, "Follow up scan", "assessment", ReportStatus.ready)
        again = http.get(f"/api/patients/{patient.id}/report-releases")
        labels = {item["label"]: item["released"] for item in again.json()["assessment_reports"]}
        assert labels["Follow up scan v1"] is False
        assert labels["Morning scan v1"] is True
        assert db.query(ContentRelease).count() == 1

        progress = set_progress_release(db, patient, True)
        assert progress["released"] is True
        assert db.query(ContentRelease).count() == 2
        still = http.get(f"/api/patients/{patient.id}/report-releases")
        still_labels = {item["label"]: item["released"] for item in still.json()["assessment_reports"]}
        assert still_labels["Morning scan v1"] is True
        assert still_labels["Follow up scan v1"] is False

        withdrawn = http.post(
            f"/api/patients/{patient.id}/report-releases/{assessment.id}",
            json={"released": False},
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json()["released"] is False
        db.refresh(share)
        assert share.token == "existing-share"
        assert share.is_active is True
        assert progress_release_state(db, patient)["released"] is True
        quiet = http.get(f"/api/patients/{patient.id}/report-releases")
        quiet_labels = {item["label"]: item["released"] for item in quiet.json()["assessment_reports"]}
        assert quiet_labels["Morning scan v1"] is False

        http.post(
            f"/api/patients/{patient.id}/report-releases/{trend.id}",
            json={"released": True},
        )
        end_claim_access(db, patient, "client")
        db.refresh(share)
        assert share.is_active is True
        assert progress_release_state(db, patient)["released"] is False
        cleared = db.query(ContentRelease).filter(ContentRelease.released.is_(True)).count()
        assert cleared == 0
        ended = http.get(f"/api/patients/{patient.id}/report-releases")
        assert ended.json()["available"] is False
        assert ended.json()["assessment_reports"] == []
        assert ended.json()["trend_reports"] == []
        db.close()
    finally:
        for column, kind in originals:
            column.type = kind
        engine.dispose()
