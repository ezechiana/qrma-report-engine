# app/api/routes_trend_reports.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Case, Patient, RecommendationMode, ReportStatus, ReportVersion, User
from app.services.audit_service import log_action
from app.services.trend_payload_enrichment import enrich_trend_payload
from app.services.trend_marker_index import build_marker_index


router = APIRouter(prefix="/api/trend-reports", tags=["trend-reports"])


class TrendReportCreate(BaseModel):
    patient_id: UUID
    source_report_ids: list[UUID] = Field(min_length=2)
    title: str | None = None
    compare_mode: str = "all_selected"
    include_clinical_narrative: bool = True
    include_top_priorities: bool = True
    include_support_recommendations: bool = True
    include_simplified_marker_evidence: bool = True
    include_full_marker_table: bool = True
    include_charts: bool = True


def _display_name(report: ReportVersion) -> str:
    case = getattr(report, "case", None)
    patient = getattr(case, "patient", None) if case else None
    patient_name = getattr(patient, "full_name", None) or "Client"
    scan_dt = getattr(case, "scan_datetime", None) if case else None
    if scan_dt:
        return f"{patient_name} — Trend report — {scan_dt.strftime('%d %b %Y')}"
    return f"{patient_name} — Trend report"


def _recommendation_mode_from_source(report: ReportVersion):
    return (
        getattr(report, "recommendation_mode", None)
        or getattr(getattr(report, "case", None), "recommendation_mode", None)
        or RecommendationMode.natural_approaches_clinical
    )


def _health_index_from_trend_payload(trend_payload: dict[str, Any] | None) -> float | None:
    if not isinstance(trend_payload, dict):
        return None
    points = trend_payload.get("health_index") or trend_payload.get("trend_chart", {}).get("health_index")
    if not isinstance(points, list) or not points:
        return None
    latest = points[-1]
    if not isinstance(latest, dict):
        return None
    value = latest.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def _validate_source_reports(
    db: Session,
    current_user: User,
    patient_id: UUID,
    report_ids: list[UUID],
) -> list[ReportVersion]:
    reports = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            ReportVersion.id.in_(report_ids),
            Case.patient_id == patient_id,
            Case.user_id == current_user.id,
            ReportVersion.status == ReportStatus.ready,
        )
        .all()
    )

    found = {r.id for r in reports}
    missing = [str(rid) for rid in report_ids if rid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Selected report(s) not found or not ready: {', '.join(missing)}",
        )

    non_assessment = [
        str(r.id)
        for r in reports
        if (getattr(r, "report_type", None) or "assessment") != "assessment"
    ]
    if non_assessment:
        raise HTTPException(
            status_code=409,
            detail=f"Trend reports must be created from assessment reports only: {', '.join(non_assessment)}",
        )

    reports.sort(
        key=lambda r: (
            getattr(getattr(r, "case", None), "scan_datetime", None)
            or getattr(r, "generated_at", None)
            or datetime.min.replace(tzinfo=timezone.utc)
        )
    )
    return reports


@router.post("")
def create_trend_report(
    payload: TrendReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == payload.patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    source_reports = _validate_source_reports(
        db=db,
        current_user=current_user,
        patient_id=payload.patient_id,
        report_ids=payload.source_report_ids,
    )

    # Keep using the existing patient trend engine, but stabilise its output afterwards.
    from app.api.routes_patients import get_patient_trends

    raw_trend_payload = get_patient_trends(
        patient_id=payload.patient_id,
        include_archived=True,
        db=db,
        current_user=current_user,
    )

    # Constrain client-side lineage to the selected reports, and add stable chart/hierarchy keys.
    trend_payload = enrich_trend_payload(raw_trend_payload)

    # Build a canonical marker index
    if isinstance(trend_payload, dict):
        trend_payload["marker_index"] = build_marker_index(trend_payload)
    else:
        trend_payload = {"marker_index": {}}

    latest_source = source_reports[-1]
    title = payload.title or _display_name(latest_source)
    source_report_id_strings = [str(r.id) for r in source_reports]

    trend_options: dict[str, Any] = {
        "compare_mode": payload.compare_mode,
        "include_clinical_narrative": payload.include_clinical_narrative,
        "include_top_priorities": payload.include_top_priorities,
        "include_support_recommendations": payload.include_support_recommendations,
        "include_simplified_marker_evidence": payload.include_simplified_marker_evidence,
        "include_full_marker_table": payload.include_full_marker_table,
        "include_charts": payload.include_charts,
    }

    generated_at = datetime.now(timezone.utc)

    trend_report = ReportVersion(
        case_id=latest_source.case_id,
        created_by_user_id=current_user.id,
        version_number=1,
        status=ReportStatus.ready,
        recommendation_mode=_recommendation_mode_from_source(latest_source),
        report_type="trend",
        source_report_ids=source_report_id_strings,
        trend_options=trend_options,
        trend_payload_json=trend_payload,
        report_json={
            "viewer": {
                "case_title": title,
                "report_type": "trend",
            },
            "trend": trend_payload,
            "report_type": "trend",
            "source_report_ids": source_report_id_strings,
            "trend_options": trend_options,
            "display_name": title,
        },
        metrics_snapshot={
            "report_type": "trend",
            "source_report_ids": source_report_id_strings,
            "health_index": _health_index_from_trend_payload(trend_payload),
        },
        generated_at=generated_at,
        is_archived=False,
    )

    db.add(trend_report)
    db.commit()
    db.refresh(trend_report)

    log_action(
        db,
        "trend_report_created",
        user_id=current_user.id,
        case_id=trend_report.case_id,
        report_version_id=trend_report.id,
        metadata_json={
            "patient_id": str(payload.patient_id),
            "source_report_ids": source_report_id_strings,
            "trend_options": trend_options,
        },
    )

    return {
        "id": str(trend_report.id),
        "report_type": "trend",
        "display_name": title,
        "source_report_ids": source_report_id_strings,
        "trend_options": trend_options,
        "generated_at": trend_report.generated_at.isoformat() if trend_report.generated_at else None,
        "detail_url": f"/app/reports/{trend_report.id}",
        "viewer_url": f"/trend-reports/{trend_report.id}",
    }


@router.get("/{trend_report_id}")
def get_trend_report(
    trend_report_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            ReportVersion.id == trend_report_id,
            Case.user_id == current_user.id,
            ReportVersion.report_type == "trend",
        )
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Trend report not found")

    trend_payload = getattr(report, "trend_payload_json", None)
    if not trend_payload and isinstance(report.report_json, dict):
        trend_payload = report.report_json.get("trend")

    trend_payload = enrich_trend_payload(trend_payload or {})

    # Build a canonical marker index
    if isinstance(trend_payload, dict):
        trend_payload["marker_index"] = build_marker_index(trend_payload)
    else:
        trend_payload = {"marker_index": {}}


    display_name = None
    if isinstance(report.report_json, dict):
        display_name = (
            report.report_json.get("display_name")
            or (report.report_json.get("viewer") or {}).get("case_title")
        )

    return {
        "id": str(report.id),
        "case_id": str(report.case_id),
        "report_type": "trend",
        "display_name": display_name,
        "source_report_ids": getattr(report, "source_report_ids", []) or [],
        "trend_options": getattr(report, "trend_options", {}) or {},
        "trend": trend_payload,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "detail_url": f"/app/reports/{report.id}",
        "viewer_url": f"/trend-reports/{report.id}",
    }


