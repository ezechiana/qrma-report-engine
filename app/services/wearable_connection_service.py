"""Connect a wearable account on a record the person can open.

A claimed client connects the clinic chart. The subject of a personal record
can connect that record before any clinic association. This service does not
call Open Health Sync, and it does not create a daily summary, a release, or
a share link.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Patient, PatientSource, User

WEARABLE = "wearable"
WEARABLE_NAME = "Wearables"
REF_MAX = 255


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _owns_personal_record(patient: Patient, user: User) -> bool:
    """The subject's own record, with no clinic claim on this row."""
    return patient.subject_user_id == user.id and patient.claimed_by_user_id is None


def _require_wearable_owner(patient: Patient, user: User) -> None:
    if patient.claimed_by_user_id == user.id or _owns_personal_record(patient, user):
        return
    raise HTTPException(status_code=404, detail="Record not found")


def open_wearable_record(db: Session, user: User, patient_id: UUID) -> Patient:
    """Resolve the record for a wearable route.

    A verified subject may open an unclaimed personal record. Everyone else
    still needs a verified client account and a claim. A practitioner who does
    not own the personal record is refused before the lookup can show whether
    another person's record exists.
    """
    if not user.email_verified_at:
        raise HTTPException(
            status_code=403,
            detail="Verify your email before opening your record.",
        )
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if patient is not None and _owns_personal_record(patient, user):
        return patient
    if str(getattr(user, "role", "") or "").lower() != "client":
        raise HTTPException(status_code=403, detail="Client account required.")
    if patient is None or patient.claimed_by_user_id != user.id:
        raise HTTPException(status_code=404, detail="Record not found")
    return patient


def _required_ref(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="This field is required.")
    if len(cleaned) > REF_MAX:
        raise HTTPException(status_code=422, detail="This field is too long.")
    return cleaned


def _wearable_source(db: Session, patient: Patient) -> PatientSource | None:
    return (
        db.query(PatientSource)
        .filter(
            PatientSource.patient_id == patient.id,
            PatientSource.practitioner_user_id == patient.user_id,
            PatientSource.source_type == WEARABLE,
        )
        .first()
    )


def _connection_payload(source: PatientSource) -> dict:
    return {
        "connected": True,
        "source_id": str(source.id),
        "display_name": source.display_name,
        "external_account_ref": source.external_account_ref,
    }


def wearable_connection(db: Session, patient: Patient, client: User) -> dict:
    """Return the wearable connection for this claimed record.

    Handles patient identity. An Open Health Sync user id is included only
    when this client has connected one. Daily summaries are not included.
    """
    _require_wearable_owner(patient, client)
    source = _wearable_source(db, patient)
    ref = source.external_account_ref if source is not None else None
    if source is None or not (ref or "").strip():
        return {"connected": False}
    return _connection_payload(source)


def connect_wearable(
    db: Session,
    patient: Patient,
    client: User,
    external_account_ref: str,
) -> dict:
    """Store one Open Health Sync user id on this record's Wearables shelf.

    Handles patient identity. A later connection updates the same shelf.
    Does not create a daily summary, a release, or a share link.
    """
    _require_wearable_owner(patient, client)
    ref = _required_ref(external_account_ref)
    now = _now()
    source = _wearable_source(db, patient)
    if source is None:
        source = PatientSource(
            patient_id=patient.id,
            practitioner_user_id=patient.user_id,
            source_type=WEARABLE,
            display_name=WEARABLE_NAME,
            status="active",
            external_account_ref=ref,
            created_at=now,
            updated_at=now,
        )
        db.add(source)
    else:
        source.display_name = WEARABLE_NAME
        source.status = "active"
        source.external_account_ref = ref
        source.updated_at = now
    db.commit()
    db.refresh(source)
    return _connection_payload(source)
