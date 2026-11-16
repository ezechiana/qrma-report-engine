"""Link a personal record to a clinic chart.

Accepting a practitioner invite creates or reactivates one active association.
It does not copy shelves, release anything, or change a share link.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import ClinicAssociation, Patient

ACTIVE = "active"
PRACTITIONER = "practitioner"


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
