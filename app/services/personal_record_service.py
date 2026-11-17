"""One personal health record for an account, with no clinic attached.

The subject is the person the record is about. Until a clinic is associated,
that same account is also the custodian (patients.user_id). Signup does not
create a share link or a content release.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import Patient, User


def _name_parts(full_name: str | None, email: str) -> tuple[str, str, str]:
    cleaned = " ".join((full_name or "").split())
    if not cleaned:
        local = (email.split("@")[0] or "Account").strip()[:120] or "Account"
        return local, "Account", local
    first, _, rest = cleaned.partition(" ")
    last = rest.strip() or "Account"
    return first[:120], last[:120], cleaned[:255]


def personal_record_for(db: Session, user: User) -> Patient | None:
    return db.query(Patient).filter(Patient.subject_user_id == user.id).one_or_none()


def clinic_charts(db: Session, user: User) -> list[Patient]:
    """Charts this account custodies for other people. The subject's own record is omitted."""
    return (
        db.query(Patient)
        .filter(
            Patient.user_id == user.id,
            or_(Patient.subject_user_id.is_(None), Patient.subject_user_id != user.id),
        )
        .order_by(Patient.created_at.desc())
        .all()
    )


def personal_record_payload(patient: Patient) -> dict:
    """Fields the subject may see. No clinic notes, labs, or release state."""
    return {
        "patient_id": str(patient.id),
        "full_name": patient.full_name,
        "subject_user_id": str(patient.subject_user_id),
    }


def ensure_personal_record(db: Session, user: User, *, commit: bool = True) -> Patient:
    """Create the subject's record once. A second call returns the same row."""
    existing = personal_record_for(db, user)
    if existing is not None:
        return existing

    first_name, last_name, full_name = _name_parts(user.full_name, user.email)
    patient = Patient(
        user_id=user.id,
        subject_user_id=user.id,
        first_name=first_name,
        last_name=last_name,
        full_name=full_name,
        email=(user.email or "").strip().lower() or None,
    )
    db.add(patient)
    if commit:
        db.commit()
        db.refresh(patient)
    else:
        db.flush()
    return patient


def open_unclaimed_personal_record(db: Session, user: User, patient_id: UUID) -> Patient:
    """Open this person's record when no clinic has claimed it.

    A verified subject may open it. Another practitioner is refused before the
    response can show whether a different person's record exists.
    """
    if not user.email_verified_at:
        raise HTTPException(
            status_code=403,
            detail="Verify your email before opening your record.",
        )
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if (
        patient is not None
        and patient.subject_user_id == user.id
        and patient.claimed_by_user_id is None
    ):
        return patient
    if str(getattr(user, "role", "") or "").lower() != "client":
        raise HTTPException(status_code=403, detail="Client account required.")
    raise HTTPException(status_code=404, detail="Record not found")
