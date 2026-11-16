"""Practitioner clinic notes. These routes handle patient notes and summaries."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Patient, User
from app.services.patient_source_service import (
    create_clinic_note,
    create_lab_panel,
    list_patient_sources,
    set_clinic_note_release,
    set_lab_panel_release,
    update_clinic_note,
    update_lab_panel,
)

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


class LabObservationCreate(BaseModel):
    name: str = Field(max_length=255)
    value_text: str | None = Field(default=None, max_length=255)
    value_numeric: float | None = None
    unit: str | None = Field(default=None, max_length=40)
    reference_low: float | None = None
    reference_high: float | None = None
    reference_text: str | None = Field(default=None, max_length=255)
    interpretation: Literal["normal", "low", "high", "abnormal"] | None = None
    code: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def required_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A marker name is required.")
        return cleaned

    @field_validator("value_text", "unit", "reference_text", "code")
    @classmethod
    def clean_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def marker_is_usable(self):
        if self.value_numeric is None and not self.value_text:
            raise ValueError("A marker needs a value.")
        if (
            self.reference_low is not None
            and self.reference_high is not None
            and self.reference_low > self.reference_high
        ):
            raise ValueError("The reference low cannot be above the high.")
        return self


class LabPanelCreate(BaseModel):
    title: str = Field(max_length=255)
    recorded_on: date
    specimen: str = Field(default="", max_length=255)
    laboratory_name: str = Field(default="", max_length=255)
    client_summary: str = Field(default="", max_length=20000)
    observations: list[LabObservationCreate] = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def required_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned

    @field_validator("specimen", "laboratory_name", "client_summary")
    @classmethod
    def clean_panel_text(cls, value: str) -> str:
        return value.strip()


class LabPanelUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    recorded_on: date | None = None
    specimen: str | None = Field(default=None, max_length=255)
    laboratory_name: str | None = Field(default=None, max_length=255)
    client_summary: str | None = Field(default=None, max_length=20000)
    observations: list[LabObservationCreate] | None = None

    @field_validator("title")
    @classmethod
    def required_title_when_sent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned

    @field_validator("specimen", "laboratory_name", "client_summary")
    @classmethod
    def clean_sent_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("observations")
    @classmethod
    def markers_when_sent(cls, value: list[LabObservationCreate] | None):
        if value is not None and not 1 <= len(value) <= 100:
            raise ValueError("A panel needs between 1 and 100 markers.")
        return value


class ClinicNoteReleaseUpdate(BaseModel):
    released: bool


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
    """List sources for a patient the practitioner owns.

    Handles patient identity, note text, marker values, and wearable days the
    client has released. Unreleased wearable days are omitted. Share links are
    not included.
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


@router.post("/{patient_id}/sources/{source_id}/panels")
def post_lab_panel(
    patient_id: UUID,
    source_id: UUID,
    payload: LabPanelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save a lab panel. It is not released to the client.

    Handles patient identity and marker values. A share link is not created.
    """
    patient = _owned_patient(db, patient_id, current_user)
    return create_lab_panel(
        db,
        patient,
        source_id,
        title=payload.title,
        recorded_on=payload.recorded_on,
        specimen=payload.specimen,
        laboratory_name=payload.laboratory_name,
        client_summary=payload.client_summary,
        observations=[item.model_dump() for item in payload.observations],
        created_by_user_id=current_user.id,
    )


@router.patch("/{patient_id}/sources/{source_id}/panels/{record_id}")
def patch_lab_panel(
    patient_id: UUID,
    source_id: UUID,
    record_id: UUID,
    payload: LabPanelUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a lab panel. Omitted fields stay as they are. This does not release the panel.

    Handles patient identity and marker values. A share link is not created.
    """
    provided = payload.model_fields_set
    if not provided:
        raise HTTPException(status_code=422, detail="Nothing to update.")
    patient = _owned_patient(db, patient_id, current_user)
    changes = {}
    for field in provided:
        value = getattr(payload, field)
        if field == "observations" and value is not None:
            value = [item.model_dump() for item in value]
        changes[field] = value
    return update_lab_panel(db, patient, source_id, record_id, changes)


@router.post("/{patient_id}/sources/{source_id}/panels/{record_id}/release")
def post_lab_panel_release(
    patient_id: UUID,
    source_id: UUID,
    record_id: UUID,
    payload: ClinicNoteReleaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Release or withdraw one lab panel for the client who claimed this record.

    Handles patient identity. Marker values and the client summary are not returned.
    A share link is not created, and individual markers are not released.
    """
    patient = _owned_patient(db, patient_id, current_user)
    return set_lab_panel_release(db, patient, source_id, record_id, payload.released)


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


@router.post("/{patient_id}/sources/{source_id}/records/{record_id}/release")
def post_clinic_note_release(
    patient_id: UUID,
    source_id: UUID,
    record_id: UUID,
    payload: ClinicNoteReleaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Release or withdraw one clinic note for the client who claimed this record.

    Handles patient identity. The note text and client summary are not returned.
    A share link is not created.
    """
    patient = _owned_patient(db, patient_id, current_user)
    return set_clinic_note_release(db, patient, source_id, record_id, payload.released)
