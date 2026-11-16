"""Link a personal record to a clinic chart.

Accepting a practitioner invite creates or reactivates one active association.
A person can also ask a practitioner by email. That request stays pending,
stores no health data, and does not create a share link.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ClinicAssociation, Patient, User, UserCapability

ACTIVE = "active"
PENDING = "pending"
PRACTITIONER = "practitioner"
PERSON = "person"
GENERIC_REQUEST = "If that email belongs to a practitioner, they can see your request."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active_for_personal(db: Session, personal_patient_id) -> ClinicAssociation | None:
    return (
        db.query(ClinicAssociation)
        .filter(
            ClinicAssociation.personal_patient_id == personal_patient_id,
            ClinicAssociation.status == ACTIVE,
        )
        .first()
    )


def activate_practitioner_association(
    db: Session,
    *,
    personal: Patient,
    clinic: Patient,
) -> ClinicAssociation:
    """Create or reactivate the association for an accepted invite.

    Does not commit. A second active clinic for the same personal record is not added.
    """
    now = _now()
    existing = (
        db.query(ClinicAssociation)
        .filter(
            ClinicAssociation.personal_patient_id == personal.id,
            ClinicAssociation.clinic_patient_id == clinic.id,
            ClinicAssociation.practitioner_user_id == clinic.user_id,
        )
        .order_by(ClinicAssociation.created_at.desc())
        .first()
    )
    if existing is not None:
        if existing.status != ACTIVE:
            other = _active_for_personal(db, personal.id)
            if other is None or other.id == existing.id:
                existing.status = ACTIVE
                existing.activated_at = now
                existing.ended_at = None
                existing.ended_by = None
        return existing

    other = _active_for_personal(db, personal.id)
    if other is not None:
        return other

    row = ClinicAssociation(
        personal_patient_id=personal.id,
        clinic_patient_id=clinic.id,
        practitioner_user_id=clinic.user_id,
        status=ACTIVE,
        initiated_by=PRACTITIONER,
        created_at=now,
        activated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def backfill_legacy_claimed_chart(db: Session, clinic: Patient) -> ClinicAssociation | None:
    """Keep an already-claimed chart as one row and record its association.

    When the person has no separate personal record, this chart becomes that
    record. Shelves stay where they are. Nothing is released.
    """
    if clinic.claimed_by_user_id is None:
        return None
    personal = (
        db.query(Patient)
        .filter(Patient.subject_user_id == clinic.claimed_by_user_id)
        .one_or_none()
    )
    if personal is None:
        clinic.subject_user_id = clinic.claimed_by_user_id
        db.flush()
        personal = clinic
    return activate_practitioner_association(db, personal=personal, clinic=clinic)


def _plausible_email(value: str) -> bool:
    if not value or len(value) > 255 or any(ch.isspace() for ch in value):
        return False
    local, separator, domain = value.partition("@")
    return bool(separator and local and domain and "." in domain and not domain.startswith("."))


def is_practitioner_account(db: Session, user: User) -> bool:
    """True when this login can receive a clinic association."""
    if str(getattr(user, "role", "") or "").lower() == PRACTITIONER:
        return True
    row = (
        db.query(UserCapability)
        .filter(
            UserCapability.user_id == user.id,
            UserCapability.capability == PRACTITIONER,
        )
        .first()
    )
    return row is not None


def request_practitioner_by_email(db: Session, person: User, email: str) -> dict:
    """Store one pending request when the email belongs to a practitioner.

    The response is the same when the email is unknown or is not a practitioner.
    A second pending request to that same practitioner is not stored.
    Does not release anything or create a share link.
    """
    if not person.email_verified_at:
        raise HTTPException(status_code=403, detail="Verify your email before sending a request.")
    cleaned = (email or "").strip().lower()
    if not _plausible_email(cleaned):
        raise HTTPException(status_code=422, detail="Enter the practitioner's email address.")

    from app.services.auth_service import get_user_by_email
    from app.services.personal_record_service import ensure_personal_record

    personal = ensure_personal_record(db, person, commit=False)
    target = get_user_by_email(db, cleaned)
    if target is not None and target.id != person.id and is_practitioner_account(db, target):
        already = (
            db.query(ClinicAssociation)
            .filter(
                ClinicAssociation.personal_patient_id == personal.id,
                ClinicAssociation.practitioner_user_id == target.id,
                ClinicAssociation.status == PENDING,
            )
            .first()
        )
        if already is None:
            db.add(
                ClinicAssociation(
                    personal_patient_id=personal.id,
                    clinic_patient_id=None,
                    practitioner_user_id=target.id,
                    status=PENDING,
                    initiated_by=PERSON,
                    created_at=_now(),
                )
            )
    db.commit()
    return {"detail": GENERIC_REQUEST}


def list_person_requests(db: Session, practitioner: User) -> dict:
    """Pending requests for this practitioner. Name and email only."""
    if not practitioner.email_verified_at:
        raise HTTPException(status_code=403, detail="Verify your email before opening requests.")
    if not is_practitioner_account(db, practitioner):
        raise HTTPException(status_code=403, detail="Practitioner account required.")
    rows = (
        db.query(ClinicAssociation, User)
        .join(Patient, Patient.id == ClinicAssociation.personal_patient_id)
        .join(User, User.id == Patient.subject_user_id)
        .filter(
            ClinicAssociation.practitioner_user_id == practitioner.id,
            ClinicAssociation.status == PENDING,
            ClinicAssociation.initiated_by == PERSON,
        )
        .order_by(ClinicAssociation.created_at.desc())
        .all()
    )
    return {
        "requests": [
            {
                "request_id": str(association.id),
                "requester_name": requester.full_name,
                "requester_email": requester.email,
                "status": PENDING,
            }
            for association, requester in rows
        ]
    }
