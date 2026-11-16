"""Practitioner clinic notes. These routes handle patient notes and summaries."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Patient, User
from app.services.patient_source_service import create_clinic_note, list_patient_sources, update_clinic_note

router = APIRouter(prefix="/api/patients", tags=["patients"])


class ClinicNoteCreate(BaseModel):
    title: str = Field(max_length=255)
    recorded_on: date
    text: str = Field(max_length=20000)
    client_summary: str = Field(default="", max_length=20000)

    @field_validator("title", "text")
    @classmethod
    def required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned

    @field_validator("client_summary")
    @classmethod
    def clean_summary(cls, value: str) -> str:
        return value.strip()


class ClinicNoteUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    recorded_on: date | None = None
    text: str | None = Field(default=None, max_length=20000)
    client_summary: str | None = Field(default=None, max_length=20000)

    @field_validator("title", "text")
    @classmethod
    def required_when_sent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned

    @field_validator("client_summary")
    @classmethod
    def clean_optional_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


def _owned_patient(db: Session, patient_id: UUID, current_user: User) -> Patient:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.get("/{patient_id}/sources")
def get_patient_sources(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List clinic notes for a patient the practitioner owns.

    Handles patient identity and note text. Share links are not included.
    """
    patient = _owned_patient(db, patient_id, current_user)
    return list_patient_sources(db, patient)


@router.post("/{patient_id}/sources/{source_id}/records")
def post_clinic_note(
    patient_id: UUID,
    source_id: UUID,
    payload: ClinicNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save a clinic note. It is not released to the client."""
    patient = _owned_patient(db, patient_id, current_user)
    return create_clinic_note(
        db,
        patient,
        source_id,
        title=payload.title,
        recorded_on=payload.recorded_on,
        text=payload.text,
        client_summary=payload.client_summary,
        created_by_user_id=current_user.id,
    )


@router.patch("/{patient_id}/sources/{source_id}/records/{record_id}")
def patch_clinic_note(
    patient_id: UUID,
    source_id: UUID,
    record_id: UUID,
    payload: ClinicNoteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a clinic note. Omitted fields stay as they are. This does not release the note."""
    provided = payload.model_fields_set
    if not provided:
        raise HTTPException(status_code=422, detail="Nothing to update.")
    patient = _owned_patient(db, patient_id, current_user)
    changes = {field: getattr(payload, field) for field in provided}
    return update_clinic_note(db, patient, source_id, record_id, changes)
