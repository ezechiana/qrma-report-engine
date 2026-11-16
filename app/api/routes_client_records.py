# app/api/routes_client_records.py

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.schemas.patient_claims import ClaimAccessRead
from app.services.client_record_service import (
    _clinic_name,
    claimed_patients,
    client_record_payload,
    client_released_report,
    require_claimed_patient,
)
from app.services.patient_claim_service import claim_access_state, end_claim_access
from app.services.patient_source_service import client_released_note

router = APIRouter(prefix="/api/client", tags=["client-record"])


def _require_client(current_user: User) -> User:
    if str(getattr(current_user, "role", "") or "").lower() != "client":
        raise HTTPException(status_code=403, detail="Client account required.")
    if not current_user.email_verified_at:
        raise HTTPException(status_code=403, detail="Verify your email before opening your record.")
    return current_user


@router.get("/records")
def list_client_records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the records this client has claimed.

    Handles patient identity. Practitioner notes and other patients are not included.
    """
    client = _require_client(current_user)
    rows = []
    for patient in claimed_patients(db, client):
        practitioner = patient.user
        rows.append({
            "patient_id": str(patient.id),
            "display_name": patient.full_name,
            "clinic_name": _clinic_name(practitioner),
        })
    return {"records": rows}


@router.get("/records/{patient_id}")
def get_client_record(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the client-safe record for one claimed patient.

    Handles patient identity. The progress view is included only when this clinic
    has released it. Released note titles are grouped by source. The raw note
    text is omitted. Share links are not consulted.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    return client_record_payload(db, client, patient)


@router.get("/records/{patient_id}/reports/{report_id}")
def get_client_report(
    patient_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the client-safe reading of one report this clinic has released.

    Handles patient identity. Unreleased reports, practitioner notes, and share
    links are not included.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    return client_released_report(db, patient, report_id)


@router.get("/records/{patient_id}/notes/{record_id}")
def get_client_note(
    patient_id: UUID,
    record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the client summary of one released note or lab panel.

    Handles patient identity. Note text and marker values are not included.
    An unreleased record, or one with no client summary, is not found.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    return client_released_note(db, patient, record_id)


@router.post("/records/{patient_id}/close-access", response_model=ClaimAccessRead)
def close_client_access(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The client stops their own access to one claimed record.

    The clinic keeps the patient, cases, reports, and clinic notes. Portal releases
    are withdrawn. Share links are not revoked.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    patient = end_claim_access(db, patient, "client")
    return claim_access_state(patient)
