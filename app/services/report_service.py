from __future__ import annotations

import os
from pathlib import Path
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Case, ReportStatus, ReportVersion, User
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


async def generate_report_version(db: Session, case: Case, user: User) -> ReportVersion:
    version_number = get_next_version_number(db, case.id)
    html_filename, pdf_filename = _build_case_filenames(case, version_number)

    enriched_report = parse_and_enrich_case_html(case)

    built = await build_report(
        enriched_report,
        overrides=None,
        html_filename=html_filename,
        pdf_filename=pdf_filename,
    )

    html_path = _to_str_path(built.get("html_path"))
    pdf_path = _to_str_path(built.get("pdf_path"))

    report_json = {
        "case_id": str(case.id),
        "version_number": version_number,
        "recommendation_mode": case.recommendation_mode.value,
        "html_path": html_path,
        "pdf_path": pdf_path,
    }

    report = ReportVersion(
        case_id=case.id,
        created_by_user_id=user.id,
        version_number=version_number,
        status=ReportStatus.draft,
        report_json=report_json,
        html_path=html_path,
        pdf_path=pdf_path,
        build_version=os.getenv("REPORT_BUILD_VERSION", "dev"),
        recommendation_mode=case.recommendation_mode,
    )
    db.add(report)
    case.status = "generated"
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