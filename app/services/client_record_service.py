"""Signed-in client access to a claimed longitudinal record.

The progress view is the client-safe trend already built for a practitioner.
It is included only when this clinic has released it. Share links do not
decide what this portal shows, and opening one does not change the release.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.share_helpers import _patient_display_name
from app.db.models import Patient, ReportVersion, User
from app.services.content_release_service import progress_release_state, released_client_reports
from app.services.patient_source_service import released_client_sources

_EMPTY_CLIENT_VIEW = {
    "summary": "Your practitioner has not released a progress view yet.",
    "progress_cards": [],
    "top_priorities": [],
    "recommendations": [],
    "review_note": "Client-safe summary. Detailed marker interpretation should be reviewed with your practitioner.",
}


def _clinic_name(practitioner: User | None) -> str:
    if practitioner is None:
        return "Your clinic"
    return (practitioner.clinic_name or practitioner.full_name or "Your clinic").strip()


def claimed_patients(db: Session, client: User) -> list[Patient]:
    rows = (
        db.query(Patient)
        .filter(Patient.claimed_by_user_id == client.id)
        .order_by(Patient.full_name.asc())
        .all()
    )
    from app.services.clinic_association_service import _association_table
    from app.services.personal_record_service import personal_record_for
    from app.db.models import ClinicAssociation

    if not _association_table(db):
        return rows
    personal = personal_record_for(db, client)
    if personal is None:
        return rows
    linked_ids = [
        row.clinic_patient_id
        for row in db.query(ClinicAssociation)
        .filter(
            ClinicAssociation.personal_patient_id == personal.id,
            ClinicAssociation.status == "active",
            ClinicAssociation.clinic_patient_id.is_not(None),
            ClinicAssociation.clinic_patient_id != personal.id,
        )
        .all()
        if row.clinic_patient_id is not None
    ]
    if not linked_ids:
        return rows
    known = {row.id for row in rows}
    extra = (
        db.query(Patient)
        .filter(Patient.id.in_(linked_ids))
        .all()
    )
    combined = rows + [row for row in extra if row.id not in known]
    combined.sort(key=lambda row: (row.full_name or "").lower())
    return combined


def _project_client_trend(payload: dict) -> dict:
    """Keep the fields the shared trend page reads, and leave practitioner controls out."""
    intelligence = payload.get("trend_intelligence") if isinstance(payload.get("trend_intelligence"), dict) else {}
    summary = payload.get("trend_summary") if isinstance(payload.get("trend_summary"), list) else []
    return {
        "view_mode": "client",
        "client_view": payload.get("client_view") or _EMPTY_CLIENT_VIEW,
        "health_index": payload.get("health_index") or [],
        "trend_summary": [row for row in summary if isinstance(row, dict) and row.get("is_meaningful") is True],
        "trend_intelligence": {
            key: intelligence.get(key)
            for key in ("confidence", "insights", "cards", "top_priorities", "clinical_narrative")
            if key in intelligence
        },
    }


def _empty_trend() -> dict:
    return {
        "view_mode": "client",
        "client_view": dict(_EMPTY_CLIENT_VIEW),
        "health_index": [],
        "trend_summary": [],
        "trend_intelligence": {},
    }


def client_record_payload(db: Session, client: User, patient: Patient) -> dict:
    practitioner = db.query(User).filter(User.id == patient.user_id).first()
    if not progress_release_state(db, patient)["released"]:
        trend = _empty_trend()
    else:
        from app.api.routes_patients import get_patient_trends

        trend_payload = get_patient_trends(
            patient_id=patient.id,
            include_archived=False,
            view_mode="client",
            source_report_ids=None,
            db=db,
            current_user=SimpleNamespace(id=patient.user_id),
        )
        trend = _project_client_trend(trend_payload)
    return {
        "viewer_mode": "patient",
        "patient": {
            "id": str(patient.id),
            "display_name": _patient_display_name(patient),
        },
        "tenant": {
            "tenant_name": _clinic_name(practitioner),
        },
        "trend": trend,
        "reports": released_client_reports(db, patient),
        "sources": released_client_sources(db, patient),
    }


def require_claimed_patient(db: Session, client: User, patient_id: UUID) -> Patient:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.claimed_by_user_id == client.id)
        .first()
    )
    if patient is not None:
        return patient
    from app.services.clinic_association_service import clinic_chart_for_subject

    linked = clinic_chart_for_subject(db, client, patient_id)
    if linked is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return linked


def _narrative(value) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _client_safe_report_text(report: ReportVersion) -> tuple[str, list[dict]]:
    """Narrative text from the client viewer. Markers, notes, and products are left out."""
    raw = report.report_json if isinstance(report.report_json, dict) else {}
    viewer = raw.get("viewer") if isinstance(raw.get("viewer"), dict) else {}
    overview = viewer.get("overview") if isinstance(viewer.get("overview"), dict) else {}
    overall = overview.get("overall_summary")
    summary = ""
    if isinstance(overall, str):
        summary = overall.strip()
    elif isinstance(overall, dict):
        summary = _narrative(overall.get("clinical_snapshot")) or _narrative(overall.get("summary"))
    sections = []
    why = _narrative(overview.get("why_this_matters"))
    if why:
        sections.append({"title": "Why this matters", "body": why})
    pattern = overview.get("primary_pattern")
    if isinstance(pattern, dict):
        body = _narrative(pattern.get("clinical_summary")) or _narrative(pattern.get("summary"))
        if body:
            sections.append({"title": "Pattern", "body": body})
    return summary, sections


def client_released_report(db: Session, patient: Patient, report_id: UUID) -> dict:
    """One released report for the client who claimed this record.

    Handles patient identity. An unreleased report is not found. Share links
    are not created.
    """
    visible = released_client_reports(db, patient)
    report_key = str(report_id)
    report_type = None
    label = None
    for item in visible["assessment_reports"]:
        if item["report_id"] == report_key:
            report_type = "assessment"
            label = item["label"]
    for item in visible["trend_reports"]:
        if item["report_id"] == report_key:
            report_type = "trend"
            label = item["label"]
    if report_type is None:
        raise HTTPException(status_code=404, detail="Report not found")
    report = db.query(ReportVersion).filter(ReportVersion.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    summary, sections = _client_safe_report_text(report)
    return {
        "report_id": report_key,
        "label": label,
        "report_type": report_type,
        "summary": summary,
        "sections": sections,
    }
