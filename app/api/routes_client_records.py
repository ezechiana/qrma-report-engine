# app/api/routes_client_records.py

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.schemas.patient_claims import ClaimAccessRead
from app.services.client_entry_service import (
    list_client_entries,
    set_client_entry_release,
    submit_client_entry,
)
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


class ClientEntryCreate(BaseModel):
    title: str = Field(max_length=255)
    recorded_on: date
    text: str = Field(max_length=4000)
    reference: str = Field(default="", max_length=255)

    @field_validator("title", "text")
    @classmethod
    def required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned

    @field_validator("reference")
    @classmethod
    def clean_reference(cls, value: str) -> str:
        return value.strip()


class ClientEntryReleaseUpdate(BaseModel):
    released: bool


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


@router.post("/records/{patient_id}/entries")
def post_client_entry(
    patient_id: UUID,
    body: ClientEntryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save a plain-text entry and release it to the clinic.

    Handles patient identity. The entry text is not returned here. Share links
    are not created.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    return submit_client_entry(
        db,
        patient,
        client,
        title=body.title,
        recorded_on=body.recorded_on,
        text=body.text,
        reference=body.reference,
    )


@router.get("/records/{patient_id}/entries")
def get_client_entries(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List plain-text entries this client sent on one claimed record.

    Handles patient identity. The entry text is included because this client wrote it.
    Other patients' entries are not included.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    return list_client_entries(db, patient, client)


@router.post("/records/{patient_id}/entries/{record_id}/release")
def post_client_entry_release(
    patient_id: UUID,
    record_id: UUID,
    body: ClientEntryReleaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Withdraw or send again one entry this client already saved.

    Handles patient identity. The entry text is not returned. Share links are
    not created or changed.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    return set_client_entry_release(db, patient, client, record_id, body.released)


@router.post("/records/{patient_id}/close-access", response_model=ClaimAccessRead)
def close_client_access(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The client stops their own access to one claimed record.

    The clinic keeps the patient, cases, reports, clinic notes, lab panels, and
    entries the client sent. Portal releases are withdrawn, including released
    lab panels and those entries. Marker values and entry text stay in the clinic
    record. Share links are not revoked.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    patient = end_claim_access(db, patient, "client")
    return claim_access_state(patient)
