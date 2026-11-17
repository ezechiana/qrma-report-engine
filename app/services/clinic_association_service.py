"""Link a personal record to a clinic chart.

Accepting a practitioner invite creates or reactivates one active association.
A person can also ask a practitioner by email. That request stays pending,
stores no health data, and does not create a share link.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import inspect, or_
from sqlalchemy.orm import Session

from app.db.models import ClinicAssociation, ContentRelease, Patient, User, UserCapability

ACTIVE = "active"
PENDING = "pending"
ENDED = "ended"
PRACTITIONER = "practitioner"
PERSON = "person"
GENERIC_REQUEST = "If that email belongs to a practitioner, they can see your request."
REQUEST_NOT_FOUND = "Request not found"
ASSOCIATION_NOT_FOUND = "Association not found"


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


def _withdraw_association_releases(db: Session, row: ClinicAssociation, now: datetime) -> None:
    """Turn off releases for this connection, including marker values.

    Shelves and share links stay. A later connection does not turn these back on.
    """
    patient_ids = [row.personal_patient_id]
    if row.clinic_patient_id is not None and row.clinic_patient_id not in patient_ids:
        patient_ids.append(row.clinic_patient_id)
    releases = (
        db.query(ContentRelease)
        .filter(
            ContentRelease.practitioner_user_id == row.practitioner_user_id,
            ContentRelease.patient_id.in_(patient_ids),
            ContentRelease.released.is_(True),
        )
        .all()
    )
    for release in releases:
        release.released = False
        release.withdrawn_at = now


def _end_active(db: Session, row: ClinicAssociation, ended_by: str) -> dict:
    now = _now()
    _withdraw_association_releases(db, row, now)
    row.status = ENDED
    row.ended_at = now
    row.ended_by = ended_by
    db.commit()
    return {"association_id": str(row.id), "status": ENDED}


def end_association_for_clinic(db: Session, practitioner: User, clinic_patient_id) -> dict:
    """The practitioner ends the active connection for one clinic chart.

    Handles patient identity. Releases in both directions are withdrawn.
    The personal record, clinic chart, and share links stay.
    """
    _verified(practitioner, "Verify your email before ending a connection.")
    if not is_practitioner_account(db, practitioner):
        raise HTTPException(status_code=403, detail="Practitioner account required.")
    clinic = (
        db.query(Patient)
        .filter(Patient.id == clinic_patient_id, Patient.user_id == practitioner.id)
        .first()
    )
    row = None
    if clinic is not None:
        row = (
            db.query(ClinicAssociation)
            .filter(
                ClinicAssociation.clinic_patient_id == clinic.id,
                ClinicAssociation.practitioner_user_id == practitioner.id,
                ClinicAssociation.status == ACTIVE,
            )
            .first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail=ASSOCIATION_NOT_FOUND)
    return _end_active(db, row, PRACTITIONER)


def end_association_for_person(db: Session, person: User, patient_id) -> dict:
    """The person ends their active connection.

    Handles patient identity. The id may be the personal record or the clinic
    chart. Releases in both directions are withdrawn. Records and share links stay.
    """
    _verified(person, "Verify your email before ending a connection.")
    from app.services.personal_record_service import personal_record_for

    personal = personal_record_for(db, person)
    row = None
    if personal is not None and personal.subject_user_id == person.id:
        query = db.query(ClinicAssociation).filter(
            ClinicAssociation.personal_patient_id == personal.id,
            ClinicAssociation.status == ACTIVE,
        )
        if patient_id != personal.id:
            query = query.filter(ClinicAssociation.clinic_patient_id == patient_id)
        row = query.first()
    if row is None:
        raise HTTPException(status_code=404, detail=ASSOCIATION_NOT_FOUND)
    return _end_active(db, row, PERSON)


def _association_table(db: Session) -> bool:
    bind = db.get_bind()
    if bind is None:
        return False
    return inspect(bind).has_table(ClinicAssociation.__tablename__)


def _associations_touching(db: Session, patient_id) -> list[ClinicAssociation]:
    if not _association_table(db):
        return []
    return (
        db.query(ClinicAssociation)
        .filter(
            or_(
                ClinicAssociation.personal_patient_id == patient_id,
                ClinicAssociation.clinic_patient_id == patient_id,
            )
        )
        .all()
    )


def sharing_practitioner_id(db: Session, patient: Patient):
    """Practitioner who may share this chart, or None when sharing is closed.

    An active association is the authority. When no association row exists, a
    claimed clinic chart still shares with its practitioner.
    """
    rows = _associations_touching(db, patient.id)
    active = [row for row in rows if row.status == ACTIVE]
    if active:
        for row in active:
            if row.clinic_patient_id == patient.id and row.practitioner_user_id == patient.user_id:
                return row.practitioner_user_id
        for row in active:
            if row.personal_patient_id == patient.id:
                return row.practitioner_user_id
        return active[0].practitioner_user_id
    if rows:
        return None
    if patient.claimed_by_user_id is not None:
        return patient.user_id
    return None


def linked_personal_chart(db: Session, clinic: Patient) -> tuple[Patient, object] | None:
    """The other record linked to this clinic chart, when the association is active."""
    if not _association_table(db):
        return None
    row = (
        db.query(ClinicAssociation)
        .filter(
            ClinicAssociation.clinic_patient_id == clinic.id,
            ClinicAssociation.personal_patient_id != clinic.id,
            ClinicAssociation.status == ACTIVE,
        )
        .first()
    )
    if row is None:
        return None
    personal = db.query(Patient).filter(Patient.id == row.personal_patient_id).first()
    if personal is None:
        return None
    return personal, row.practitioner_user_id


def clinic_chart_for_subject(db: Session, subject: User, clinic_patient_id) -> Patient | None:
    """The clinic chart this person may read while the association is active."""
    if not _association_table(db):
        return None
    from app.services.personal_record_service import personal_record_for

    personal = personal_record_for(db, subject)
    if personal is None:
        return None
    row = (
        db.query(ClinicAssociation)
        .filter(
            ClinicAssociation.personal_patient_id == personal.id,
            ClinicAssociation.clinic_patient_id == clinic_patient_id,
            ClinicAssociation.status == ACTIVE,
        )
        .first()
    )
    if row is None:
        return None
    return db.query(Patient).filter(Patient.id == clinic_patient_id).first()
