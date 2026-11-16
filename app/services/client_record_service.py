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
from app.db.models import Patient, User
from app.services.content_release_service import progress_release_state

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
