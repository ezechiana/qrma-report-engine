"""One personal health record for an account, with no clinic attached.

The subject is the person the record is about. Until a clinic is associated,
that same account is also the custodian (patients.user_id). Signup does not
create a share link or a content release.
"""

from __future__ import annotations

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
