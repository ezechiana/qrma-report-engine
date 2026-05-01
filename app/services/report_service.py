from __future__ import annotations

import os
import hashlib
from pathlib import Path
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db.models import Case, CaseStatus, ReportStatus, ReportVersion, User
from app.services.audit_service import log_action
from app.services.report_builder import build_report
from app.services.scan_import_service import load_case_html
from app.services.scoring_engine import build_metrics_snapshot as build_scoring_metrics_snapshot


def get_next_version_number(db: Session, case_id):
    current_max = (
        db.query(func.max(ReportVersion.version_number))
        .filter(ReportVersion.case_id == case_id)
        .scalar()
    )
    return 1 if current_max is None else current_max + 1


def _build_case_filenames(case: Case, version_number: int) -> tuple[str, str]:
    base = f"case_{case.id}_v{version_number}"
    user_id = str(case.user_id)
    case_id = str(case.id)

    html_key = f"reports/{user_id}/{case_id}/{base}.html"
    pdf_key = f"reports/{user_id}/{case_id}/{base}.pdf"
    return html_key, pdf_key


def _to_str_path(value):
    if isinstance(value, Path):
        return str(value)
    return value


def _source_hash_from_case_html(case: Case) -> str | None:
    if not case.raw_scan_html_path:
        return None
    try:
        html_bytes = load_case_html(case.raw_scan_html_path)
        if isinstance(html_bytes, str):
            html_bytes = html_bytes.encode("utf-8", errors="ignore")
        return hashlib.sha256(html_bytes).hexdigest()
    except Exception:
        return None


def _persist_report_source_hash(db: Session, report_id, source_hash: str | None) -> None:
    if not source_hash:
        return
    try:
        db.execute(text("ALTER TABLE report_versions ADD COLUMN IF NOT EXISTS source_hash TEXT"))
        db.execute(text("CREATE INDEX IF NOT EXISTS idx_report_versions_case_source_hash ON report_versions (case_id, source_hash)"))
        db.execute(
            text("UPDATE report_versions SET source_hash = :source_hash WHERE id = :report_id"),
            {"source_hash": source_hash, "report_id": str(report_id)},
        )
    except Exception:
        db.rollback()


def _viewer_payload_has_content(viewer_payload: dict | None) -> bool:
    if not isinstance(viewer_payload, dict) or not viewer_payload:
        return False
    overview = viewer_payload.get("overview") or {}
    systems = viewer_payload.get("systems") or {}
    recommendations = viewer_payload.get("recommendations") or {}
    detail = viewer_payload.get("detail") or {}
    return any([
        overview.get("overall_scan_score") is not None,
        overview.get("overall_summary"),
        overview.get("primary_pattern"),
        overview.get("practitioner_summary"),
        systems.get("system_score_cards"),
        systems.get("priority_overview"),
        recommendations.get("clinical_recommendations"),
        recommendations.get("protocol_plan"),
        detail.get("full_marker_tables"),
        detail.get("body_composition_block"),
    ])


def _extract_health_index_from_viewer(viewer_payload: dict) -> float | None:
    """
    Pull the canonical Health Index from the built viewer payload.

    We do not guess from arbitrary nested scores. We only use the known
    overview field that powers the report viewer.
    """
    if not isinstance(viewer_payload, dict):
        return None

    overview = viewer_payload.get("overview") or {}
    value = overview.get("overall_scan_score")

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_weight_from_enriched(enriched_report: dict) -> float | None:
    """
    Extract weight from enriched report payload.
    Handles multiple possible locations safely.
    """
    try:
        patient = enriched_report.get("patient") or {}
        weight = patient.get("weight_kg") or patient.get("weight")
        if weight is None:
            return None
        return float(weight)
    except Exception:
        return None

def _norm_key(value: str | None) -> str:
    return (value or "").strip().lower().replace("&", "and").replace("/", " ").replace("-", " ").replace(" ", "_")


def _safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def _walk_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_dicts(item)


def _extract_weight_from_sources(enriched_report: dict | None, viewer_payload: dict | None) -> float | None:
    for source in [enriched_report or {}, viewer_payload or {}]:
        for d in _walk_dicts(source):
            for key in ("weight_kg", "weight", "body_weight"):
                value = _safe_float(d.get(key))
                if value is not None:
                    return value
    return None


def _extract_systems_from_viewer(viewer_payload: dict) -> dict:
    systems = {}

    for d in _walk_dicts(viewer_payload or {}):
        title = d.get("title") or d.get("name") or d.get("display_name") or d.get("system")
        score = (
            d.get("score")
            if d.get("score") is not None
            else d.get("value")
            if d.get("value") is not None
            else d.get("health_index")
        )

        score = _safe_float(score)

        if title and score is not None and 0 <= score <= 100:
            key = _norm_key(title)
            # Avoid generic non-system labels becoming systems
            if key not in {"health_index", "overall_scan_score", "score", "value"}:
                systems.setdefault(key, score)

    return systems


def _extract_markers_from_viewer(viewer_payload: dict) -> dict:
    markers = {}

    for d in _walk_dicts(viewer_payload or {}):
        name = (
            d.get("display_name")
            or d.get("marker")
            or d.get("marker_name")
            or d.get("name")
            or d.get("title")
        )

        value = (
            d.get("actual_value_numeric")
            if d.get("actual_value_numeric") is not None
            else d.get("actual_value")
            if d.get("actual_value") is not None
            else d.get("value")
        )

        numeric_value = _safe_float(value)

        if not name or numeric_value is None:
            continue

        key = _norm_key(name)

        # Avoid charting report/system cards as markers
        if key in {"health_index", "overall_scan_score", "score", "value"}:
            continue

        markers[key] = {
            "value": numeric_value,
            "label": str(name),
            "range": d.get("normal_range_text") or d.get("range") or d.get("reference_range"),
            "severity": d.get("severity") or d.get("status") or d.get("band"),
            "severity_class": d.get("severity_class"),
            "status_pointer_position": d.get("status_pointer_position"),
            "status_bar_variant": d.get("status_bar_variant"),
            "color": d.get("color") or d.get("colour") or d.get("severity_color"),
            "unit": d.get("unit") or d.get("units"),
        }


    return markers


def _build_metrics_snapshot(viewer_payload: dict, enriched_report: dict | None = None) -> dict:
    weight_kg = _extract_weight_from_sources(enriched_report, viewer_payload)

    snapshot = build_scoring_metrics_snapshot(viewer_payload)
    if not isinstance(snapshot, dict):
        snapshot = {}

    snapshot.setdefault("health_index", _extract_health_index_from_viewer(viewer_payload))

    systems = snapshot.get("systems") or {}
    if not systems:
        systems = _extract_systems_from_viewer(viewer_payload)

    markers = snapshot.get("markers") or {}
    richer_markers = _extract_markers_from_viewer(viewer_payload)

    # Preserve existing numeric markers, but upgrade where richer metadata exists.
    for key, value in list(markers.items()):
        if key in richer_markers:
            markers[key] = richer_markers[key]
        elif isinstance(value, (int, float)):
            markers[key] = {
                "value": float(value),
                "label": key.replace("_", " ").title(),
                "range": None,
                "severity": None,
                "color": None,
                "unit": None,
            }

    for key, value in richer_markers.items():
        markers.setdefault(key, value)

    snapshot["systems"] = systems
    snapshot["markers"] = markers
    snapshot["weight_kg"] = weight_kg

    return snapshot



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
    source_hash: str | None = None,
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

    if source_hash:
        _persist_report_source_hash(db, report.id, source_hash)
        try:
            db.commit()
            db.refresh(report)
        except Exception:
            db.rollback()

    log_action(
        db,
        action="report_queued",
        user_id=user.id,
        case_id=case.id,
        report_version_id=report.id,
        metadata_json={
            "version_number": version_number,
            "job_id": job_id,
            "source_hash": source_hash,
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

        if not _viewer_payload_has_content(viewer_payload):
            raise ValueError("Report builder returned an empty or unusable viewer_payload.")

        metrics_snapshot = _build_metrics_snapshot(viewer_payload, enriched_report)

        report_json = {
            "case_id": str(case.id),
            "version_number": version_number,
            "recommendation_mode": recommendation_mode,
            "html_path": html_path,
            "pdf_path": pdf_path,
            "viewer": viewer_payload,
            "metrics_snapshot": metrics_snapshot,
            "build_warnings": built.get("build_warnings") or [],
        }

        report.report_json = report_json
        report.html_path = html_path
        report.pdf_path = pdf_path

        # Critical for trends
        if hasattr(report, "metrics_snapshot"):
            report.metrics_snapshot = metrics_snapshot

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
            metadata_json={
                "version_number": version_number,
                "source_hash": _source_hash_from_case_html(case),
                "build_warnings": built.get("build_warnings") or [],
            },
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
    force_new: bool = False,
) -> ReportVersion:
    """
    Backward-compatible convenience wrapper.

    Current callers can keep using:
        await generate_report_version(db, case, current_user)

    Internally it now follows the SaaS lifecycle:
    queued -> processing -> ready/failed
    """
    # Always build a fresh version. source_hash is persisted for audit only;
    # it is deliberately not used for report reuse in V1 because prior reuse
    # of failed/empty rows caused blank reports.
    source_hash = _source_hash_from_case_html(case)

    report = create_report_version(
        db=db,
        case=case,
        user=user,
        job_id=job_id,
        source_hash=source_hash,
    )
    report = await build_report_version(db=db, report=report, case=case, user=user)
    return report