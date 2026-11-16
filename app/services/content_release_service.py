"""Clinic-to-client release of a named information source.

Share links are a separate channel and are not created or changed here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ContentRelease, Patient

PROGRESS_VIEW = "progress_view"
CLINIC_TO_CLIENT = "clinic_to_client"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _progress_row(db: Session, patient: Patient) -> ContentRelease | None:
    return (
        db.query(ContentRelease)
        .filter(
            ContentRelease.patient_id == patient.id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.source_key == PROGRESS_VIEW,
            ContentRelease.direction == CLINIC_TO_CLIENT,
        )
        .first()
    )


def progress_release_state(db: Session, patient: Patient) -> dict:
    """Clinic-facing release flag. The client account id is not included."""
    row = _progress_row(db, patient)
    available = patient.claimed_by_user_id is not None
    return {
        "patient_id": patient.id,
        "source": PROGRESS_VIEW,
        "direction": CLINIC_TO_CLIENT,
        "released": bool(available and row and row.released),
        "available": available,
    }


def clear_progress_release(db: Session, patient: Patient, now: datetime | None = None) -> None:
    """Turn the progress view off without committing. Used when a claim ends."""
    row = _progress_row(db, patient)
    if row is None or not row.released:
        return
    row.released = False
    row.withdrawn_at = now or _now()


def set_progress_release(db: Session, patient: Patient, released: bool) -> dict:
    """Release or withdraw the progress view for the client who claimed this record."""
    if patient.claimed_by_user_id is None:
        raise HTTPException(
            status_code=409,
            detail="A client must claim this record before the progress view can be released.",
        )
    now = _now()
    row = _progress_row(db, patient)
    if row is None:
        row = ContentRelease(
            patient_id=patient.id,
            practitioner_user_id=patient.user_id,
            source_key=PROGRESS_VIEW,
            direction=CLINIC_TO_CLIENT,
            released=False,
            created_at=now,
        )
        db.add(row)
    if released:
        row.released = True
        row.released_at = now
    else:
        row.released = False
        row.withdrawn_at = now
    db.commit()
    db.refresh(row)
    return progress_release_state(db, patient)
