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
    """Return the client-safe progress view for one claimed record.

    Handles patient identity. The view is included only when this clinic has
    released it. Share links are not consulted, and practitioner notes are omitted.
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


@router.post("/records/{patient_id}/close-access", response_model=ClaimAccessRead)
def close_client_access(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The client stops their own access to one claimed record.

    The clinic keeps the patient, cases, and reports. Share links are not revoked.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    patient = end_claim_access(db, patient, "client")
    return claim_access_state(patient)
