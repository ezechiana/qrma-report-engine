"""Signed-in client access to a claimed longitudinal record.

The trend body is the client-safe view already returned for a shared trend.
Reports stay hidden until an active share link releases them. A trend share
releases the same ready-report set the public trend page uses. An assessment
share releases only that report.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.share_helpers import _patient_display_name
from app.db.models import Case, Patient, ReportVersion, ShareLink, User
from app.services.share_link_service import is_share_link_valid

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
    return (
        db.query(Patient)
        .filter(Patient.claimed_by_user_id == client.id)
        .order_by(Patient.full_name.asc())
        .all()
    )


def _active_links_for_patient(db: Session, patient: Patient) -> list[ShareLink]:
    links = (
        db.query(ShareLink)
        .join(ReportVersion, ReportVersion.id == ShareLink.report_version_id)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            ShareLink.is_active.is_(True),
            Case.patient_id == patient.id,
            Case.user_id == patient.user_id,
        )
        .all()
    )
    return [link for link in links if is_share_link_valid(link)]


def released_report_scope(db: Session, patient: Patient) -> tuple[bool, list[UUID]]:
    """Return whether the full trend set is released, and any single-report ids."""
    longitudinal = False
    report_ids: list[UUID] = []
    for link in _active_links_for_patient(db, patient):
        report = link.report_version
        report_type = getattr(report, "report_type", None) or ""
        if (link.share_type or "") == "trend" or report_type == "trend":
            longitudinal = True
            continue
        if link.report_version_id:
            report_ids.append(link.report_version_id)
    return longitudinal, report_ids


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
    longitudinal, report_ids = released_report_scope(db, patient)
    if not longitudinal and not report_ids:
        trend = _empty_trend()
    else:
        from app.api.routes_patients import get_patient_trends

        trend_payload = get_patient_trends(
            patient_id=patient.id,
            include_archived=False,
            view_mode="client",
            source_report_ids=[str(item) for item in report_ids] if not longitudinal else None,
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
    }


def require_claimed_patient(db: Session, client: User, patient_id: UUID) -> Patient:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.claimed_by_user_id == client.id)
        .first()
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return patient
