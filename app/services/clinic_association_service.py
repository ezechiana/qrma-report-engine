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
ENDED = "ended"
PRACTITIONER = "practitioner"
PERSON = "person"
GENERIC_REQUEST = "If that email belongs to a practitioner, they can see your request."
REQUEST_NOT_FOUND = "Request not found"


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


def _verified(user: User, detail: str) -> None:
    if not user.email_verified_at:
        raise HTTPException(status_code=403, detail=detail)


def _pending_for_practitioner(db: Session, practitioner: User, request_id) -> ClinicAssociation:
    _verified(practitioner, "Verify your email before answering a request.")
    if not is_practitioner_account(db, practitioner):
        raise HTTPException(status_code=403, detail="Practitioner account required.")
    row = db.query(ClinicAssociation).filter(ClinicAssociation.id == request_id).first()
    if (
        row is None
        or row.practitioner_user_id != practitioner.id
        or row.status != PENDING
        or row.initiated_by != PERSON
    ):
        raise HTTPException(status_code=404, detail=REQUEST_NOT_FOUND)
    return row


def _clinic_chart(db: Session, personal: Patient, practitioner: User) -> Patient:
    """A new clinic chart named for the requester. Health fields stay empty."""
    chart = Patient(
        user_id=practitioner.id,
        subject_user_id=None,
        first_name=(personal.first_name or "Account")[:120],
        last_name=(personal.last_name or "Account")[:120],
        full_name=(personal.full_name or "Account")[:255],
        claimed_by_user_id=None,
    )
    db.add(chart)
    db.flush()
    return chart


def accept_person_request(db: Session, practitioner: User, request_id) -> dict:
    """Activate one pending request and open a clinic chart when one is missing.

    Does not copy shelves or date of birth, and does not release anything.
    """
    row = _pending_for_practitioner(db, practitioner, request_id)
    other = _active_for_personal(db, row.personal_patient_id)
    if other is not None:
        raise HTTPException(
            status_code=409,
            detail="This person already has an active clinic connection.",
        )
    personal = db.query(Patient).filter(Patient.id == row.personal_patient_id).first()
    if personal is None:
        raise HTTPException(status_code=404, detail=REQUEST_NOT_FOUND)
    chart = _clinic_chart(db, personal, practitioner)
    row.clinic_patient_id = chart.id
    row.status = ACTIVE
    row.activated_at = _now()
    row.ended_at = None
    row.ended_by = None
    db.commit()
    return {
        "request_id": str(row.id),
        "status": ACTIVE,
        "clinic_patient_id": str(chart.id),
    }


def decline_person_request(db: Session, practitioner: User, request_id) -> dict:
    """End a pending request. The personal record stays with the person."""
    row = _pending_for_practitioner(db, practitioner, request_id)
    row.status = ENDED
    row.ended_at = _now()
    row.ended_by = PRACTITIONER
    db.commit()
    return {"request_id": str(row.id), "status": ENDED}


def cancel_person_request(db: Session, person: User, request_id) -> dict:
    """The requester withdraws a pending request. No clinic chart is created."""
    _verified(person, "Verify your email before cancelling a request.")
    from app.services.personal_record_service import personal_record_for

    personal = personal_record_for(db, person)
    row = db.query(ClinicAssociation).filter(ClinicAssociation.id == request_id).first()
    if (
        personal is None
        or row is None
        or row.personal_patient_id != personal.id
        or row.status != PENDING
        or row.initiated_by != PERSON
    ):
        raise HTTPException(status_code=404, detail=REQUEST_NOT_FOUND)
    row.status = ENDED
    row.ended_at = _now()
    row.ended_by = PERSON
    db.commit()
    return {"request_id": str(row.id), "status": ENDED}
