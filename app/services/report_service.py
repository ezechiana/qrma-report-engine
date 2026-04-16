from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Case, CaseStatus, ReportStatus, ReportVersion, User
from app.services.audit_service import log_action
from app.services.report_builder import build_report
from app.services.scan_import_service import load_case_html


def get_next_version_number(db: Session, case_id):
    current_max = (
        db.query(func.max(ReportVersion.version_number))
        .filter(ReportVersion.case_id == case_id)
        .scalar()
    )
    return 1 if current_max is None else current_max + 1


def _build_case_filenames(case: Case, version_number: int) -> tuple[str, str]:
    base = f"case_{case.id}_v{version_number}"
    return f"{base}.html", f"{base}.pdf"


def _to_str_path(value):
    if isinstance(value, Path):
        return str(value)
    return value


def parse_and_enrich_case_html(case: Case):
    """
    Reuse the exact legacy pipeline from app/api/routes.py
    so the SaaS path behaves the same as the old upload/build path.
    """
    if not case.raw_scan_html_path:
        raise ValueError("Case has no raw_scan_html_path")

    from app.api.routes import decode_uploaded_file, process_uploaded_html

    html_bytes = load_case_html(case.raw_scan_html_path)
    html = decode_uploaded_file(html_bytes)
    enriched = process_uploaded_html(html)
    return enriched


def create_report_version(
    db: Session,
    case: Case,
    user: User,
    job_id: Optional[str] = None,
) -> ReportVersion:
    """
    Create a queued report version row before generation starts.
    This is the SaaS-safe lifecycle entry point.
    """
    version_number = get_next_version_number(db, case.id)

    report = ReportVersion(
        case_id=case.id,
        created_by_user_id=user.id,
        version_number=version_number,
        status=ReportStatus.queued,
        report_json=None,
        html_path=None,
        pdf_path=None,
        build_version=os.getenv("REPORT_BUILD_VERSION", "dev"),
        recommendation_mode=case.recommendation_mode,
        job_id=job_id,
        started_at=None,
        completed_at=None,
        failed_at=None,
        error_message=None,
    )
    db.add(report)

    case.status = CaseStatus.queued
    db.commit()
    db.refresh(report)

    log_action(
        db,
        action="report_queued",
        user_id=user.id,
        case_id=case.id,
        report_version_id=report.id,
        metadata_json={
            "version_number": version_number,
            "job_id": job_id,
        },
    )

    return report


async def build_report_version(
    db: Session,
    report: ReportVersion,
    case: Case,
    user: User,
) -> ReportVersion:
    """
    Process a queued report version into a ready or failed report.
    Safe to call immediately after create_report_version() in a synchronous MVP,
    and later reusable inside a background worker.
    """
    try:
        report.status = ReportStatus.processing
        if hasattr(report, "started_at"):
            report.started_at = func.now()

        case.status = CaseStatus.processing
        db.commit()
        db.refresh(report)

        version_number = report.version_number
        html_filename, pdf_filename = _build_case_filenames(case, version_number)

        enriched_report = parse_and_enrich_case_html(case)

        recommendation_mode = (
            case.recommendation_mode.value
            if hasattr(case.recommendation_mode, "value")
            else str(case.recommendation_mode)
        )

        built = await build_report(
            enriched_report,
            overrides=None,
            html_filename=html_filename,
            pdf_filename=pdf_filename,
            recommendation_mode=recommendation_mode,
        )

        html_path = _to_str_path(built.get("html_path"))
        pdf_path = _to_str_path(built.get("pdf_path"))
        viewer_payload = built.get("viewer_payload") or {}

        report_json = {
            "case_id": str(case.id),
            "version_number": version_number,
            "recommendation_mode": recommendation_mode,
            "html_path": html_path,
            "pdf_path": pdf_path,
            "viewer": viewer_payload,
        }

        report.report_json = report_json
        report.html_path = html_path
        report.pdf_path = pdf_path
        report.status = ReportStatus.ready
        report.error_message = None

        if hasattr(report, "completed_at"):
            report.completed_at = func.now()
        if hasattr(report, "failed_at"):
            report.failed_at = None

        case.status = CaseStatus.generated

        db.commit()
        db.refresh(report)

        log_action(
            db,
            action="report_generated",
            user_id=user.id,
            case_id=case.id,
            report_version_id=report.id,
            metadata_json={"version_number": version_number},
        )

        return report

    except Exception as exc:
        report.status = ReportStatus.failed
        report.error_message = str(exc)

        if hasattr(report, "failed_at"):
            report.failed_at = func.now()

        case.status = CaseStatus.failed

        db.commit()
        db.refresh(report)

        log_action(
            db,
            action="report_generation_failed",
            user_id=user.id,
            case_id=case.id,
            report_version_id=report.id,
            metadata_json={"error": str(exc)},
        )

        raise


async def generate_report_version(
    db: Session,
    case: Case,
    user: User,
    job_id: Optional[str] = None,
) -> ReportVersion:
    """
    Backward-compatible convenience wrapper.

    Current callers can keep using:
        await generate_report_version(db, case, current_user)

    Internally it now follows the SaaS lifecycle:
    queued -> processing -> ready/failed
    """
    report = create_report_version(db=db, case=case, user=user, job_id=job_id)
    report = await build_report_version(db=db, report=report, case=case, user=user)
    return report