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


def _clean_category_name(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text.lower() in {"marker", "markers", "none", "null", "unknown", "—", "-"}:
        return None
    return text


def _marker_payload_from_row(row: dict, category: str | None = None) -> dict | None:
    name = (
        row.get("display_name")
        or row.get("marker")
        or row.get("marker_name")
        or row.get("source_name")
        or row.get("name")
        or row.get("title")
    )

    value = (
        row.get("actual_value_numeric")
        if row.get("actual_value_numeric") is not None
        else row.get("actual_value")
        if row.get("actual_value") is not None
        else row.get("actual_value_text")
        if row.get("actual_value_text") is not None
        else row.get("value")
    )

    numeric_value = _safe_float(value)

    if not name or numeric_value is None:
        return None

    key = _norm_key(name)

    if key in {"health_index", "overall_scan_score", "score", "value"}:
        return None

    category_value = _clean_category_name(
        row.get("category")
        or row.get("category_name")
        or row.get("qrma_category")
        or row.get("original_report_category")
        or row.get("section")
        or row.get("section_title")
        or category
    )

    return {
        "key": key,
        "value": numeric_value,
        "label": str(name),
        "category": category_value,
        "range": row.get("normal_range_text") or row.get("range") or row.get("reference_range"),
        "severity": row.get("severity") or row.get("status") or row.get("band"),
        "severity_class": row.get("severity_class"),
        "status_pointer_position": row.get("status_pointer_position"),
        "status_bar_variant": row.get("status_bar_variant"),
        "color": row.get("color") or row.get("colour") or row.get("severity_color"),
        "unit": row.get("unit") or row.get("units"),
    }


def _extract_markers_from_viewer(viewer_payload: dict) -> dict:
    markers = {}

    # First pass: use the canonical full marker tables from the report viewer.
    # These tables are already grouped by QRMA category in the generated report.
    detail = (viewer_payload or {}).get("detail") or {}
    for section in detail.get("full_marker_tables") or []:
        if not isinstance(section, dict):
            continue

        category = _clean_category_name(
            section.get("title")
            or section.get("display_title")
            or section.get("source_title")
            or section.get("section")
        )

        for row in section.get("rows") or []:
            if not isinstance(row, dict):
                continue

            payload = _marker_payload_from_row(row, category)
            if not payload:
                continue

            key = payload.pop("key")
            markers[key] = payload

    # Appendix fallback.
    for row in detail.get("appendix_rows") or []:
        if not isinstance(row, dict):
            continue

        payload = _marker_payload_from_row(row, row.get("section"))
        if not payload:
            continue

        key = payload.pop("key")
        markers.setdefault(key, payload)

    # Generic fallback for any marker-like structures not present in full_marker_tables.
    for d in _walk_dicts(viewer_payload or {}):
        payload = _marker_payload_from_row(d)
        if not payload:
            continue

        key = payload.pop("key")

        if key in markers:
            if markers[key].get("category") and not payload.get("category"):
                payload["category"] = markers[key]["category"]
            elif not payload.get("category") and markers[key].get("category"):
                payload["category"] = markers[key]["category"]

        markers[key] = {**markers.get(key, {}), **payload}

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

        metrics_snapshot = _build_metrics_snapshot(viewer_payload, enriched_report)

        report_json = {
            "case_id": str(case.id),
            "version_number": version_number,
            "recommendation_mode": recommendation_mode,
            "html_path": html_path,
            "pdf_path": pdf_path,
            "viewer": viewer_payload,
            "metrics_snapshot": metrics_snapshot,
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