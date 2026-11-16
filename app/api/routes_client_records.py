# app/api/routes_client_records.py

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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
from app.services.personal_record_service import open_unclaimed_personal_record
from app.services.personal_source_service import (
    add_personal_lab,
    add_personal_note,
    list_personal_labs,
    list_personal_notes,
    set_personal_share,
)
from app.services.patient_source_service import client_released_note
from app.services.wearable_connection_service import (
    connect_wearable,
    open_wearable_record,
    wearable_connection,
)
from app.services.wearable_summary_service import (
    list_wearable_days,
    set_wearable_day_release,
    sync_wearable_days,
)

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


class WearableConnect(BaseModel):
    external_account_ref: str = Field(max_length=255)

    @field_validator("external_account_ref")
    @classmethod
    def required_account(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned


class WearableDayInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recorded_on: date
    steps: float | None = None
    resting_heart_rate: float | None = None
    sleep_duration: float | None = None
    hrv: float | None = None

    @field_validator("steps", "resting_heart_rate", "sleep_duration", "hrv", mode="before")
    @classmethod
    def metric_value(cls, value):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("This field is invalid.")
        if value < 0:
            raise ValueError("This field is invalid.")
        return value


class WearableDaySync(BaseModel):
    days: list[WearableDayInput] = Field(max_length=366)


class WearableDayReleaseUpdate(BaseModel):
    released: bool


class PersonalNoteCreate(BaseModel):
    title: str = Field(max_length=255)
    recorded_on: date
    text: str = Field(max_length=20000)

    @field_validator("title", "text")
    @classmethod
    def required_personal_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned


class PersonalLabMarker(BaseModel):
    name: str = Field(max_length=255)
    value_text: str | None = Field(default=None, max_length=255)
    value_numeric: float | None = None
    unit: str | None = Field(default=None, max_length=40)

    @field_validator("name")
    @classmethod
    def required_marker_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A marker name is required.")
        return cleaned

    @field_validator("value_text", "unit")
    @classmethod
    def clean_marker_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def marker_has_a_value(self):
        if self.value_numeric is None and not self.value_text:
            raise ValueError("A marker needs a value.")
        return self


class PersonalLabCreate(BaseModel):
    title: str = Field(max_length=255)
    recorded_on: date
    observations: list[PersonalLabMarker] = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def required_lab_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned


class PersonalReleaseUpdate(BaseModel):
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


@router.get("/records/{patient_id}/wearable")
def get_wearable_connection(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return whether this person has connected a wearable on this record.

    Handles patient identity. The Open Health Sync user id is included only
    for this person. Daily summaries are not included.
    """
    patient = open_wearable_record(db, current_user, patient_id)
    return wearable_connection(db, patient, current_user)


@router.post("/records/{patient_id}/wearable")
def post_wearable_connection(
    patient_id: UUID,
    body: WearableConnect,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Connect one wearable account on this record.

    Handles patient identity. The Open Health Sync user id is stored on the
    Wearables shelf. A later connection uses the same shelf. Daily summaries
    and share links are not created.
    """
    patient = open_wearable_record(db, current_user, patient_id)
    return connect_wearable(db, patient, current_user, body.external_account_ref)


@router.get("/records/{patient_id}/wearable/days")
def get_wearable_days(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List wearable days for this person, including days that are not shared.

    Handles patient identity. Each day includes the date, the four daily
    values, and whether that day is shared. The clinic source list is not used.
    """
    patient = open_wearable_record(db, current_user, patient_id)
    return list_wearable_days(db, patient, current_user)


@router.post("/records/{patient_id}/wearable/days")
def post_wearable_days(
    patient_id: UUID,
    body: WearableDaySync,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Store one wearable summary per calendar day for this person.

    Handles patient identity. A later sync of the same day updates that day.
    Days are not released to a clinic, and no share link is created.
    """
    patient = open_wearable_record(db, current_user, patient_id)
    return sync_wearable_days(
        db,
        patient,
        current_user,
        [day.model_dump() for day in body.days],
    )


@router.post("/records/{patient_id}/wearable/days/{record_id}/release")
def post_wearable_day_release(
    patient_id: UUID,
    record_id: UUID,
    body: WearableDayReleaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Release or withdraw one wearable day when a clinic is connected.

    Handles patient identity. The daily values are not returned. A personal
    record with no clinic connection is refused. Share links are not created.
    """
    patient = open_wearable_record(db, current_user, patient_id)
    return set_wearable_day_release(db, patient, current_user, record_id, body.released)


@router.get("/records/{patient_id}/notes")
def get_personal_notes(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List notes this person saved on their personal record.

    Handles patient identity and note text. Each note is unshared. Another
    person's notes are not included.
    """
    patient = open_unclaimed_personal_record(db, current_user, patient_id)
    return list_personal_notes(db, patient, current_user)


@router.post("/records/{patient_id}/notes")
def post_personal_note(
    patient_id: UUID,
    body: PersonalNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save a note on this personal record. It is not shared with a clinic."""
    patient = open_unclaimed_personal_record(db, current_user, patient_id)
    return add_personal_note(
        db,
        patient,
        current_user,
        title=body.title,
        recorded_on=body.recorded_on,
        text=body.text,
    )


@router.post("/records/{patient_id}/notes/{record_id}/release")
def post_personal_note_release(
    patient_id: UUID,
    record_id: UUID,
    body: PersonalReleaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Release or withdraw one personal note while a clinic connection is active.

    Handles patient identity. The note text is not returned. No share link is created.
    """
    patient = open_unclaimed_personal_record(db, current_user, patient_id)
    return set_personal_share(db, patient, current_user, record_id, body.released)


@router.get("/records/{patient_id}/labs")
def get_personal_labs(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List lab panels this person saved, including the values they entered.

    Handles patient identity and marker values. Each panel is unshared.
    """
    patient = open_unclaimed_personal_record(db, current_user, patient_id)
    return list_personal_labs(db, patient, current_user)


@router.post("/records/{patient_id}/labs")
def post_personal_lab(
    patient_id: UUID,
    body: PersonalLabCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save a lab panel on this personal record. It is not shared with a clinic.

    Handles patient identity and marker values.
    """
    patient = open_unclaimed_personal_record(db, current_user, patient_id)
    return add_personal_lab(
        db,
        patient,
        current_user,
        title=body.title,
        recorded_on=body.recorded_on,
        observations=[item.model_dump() for item in body.observations],
    )


@router.post("/records/{patient_id}/labs/{record_id}/release")
def post_personal_lab_release(
    patient_id: UUID,
    record_id: UUID,
    body: PersonalReleaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Release or withdraw one personal lab panel while a clinic connection is active.

    Handles patient identity. Marker values are not returned. No share link is created.
    """
    patient = open_unclaimed_personal_record(db, current_user, patient_id)
    return set_personal_share(db, patient, current_user, record_id, body.released)


@router.post("/records/{patient_id}/close-access", response_model=ClaimAccessRead)
def close_client_access(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The client stops their own access to one claimed record.

    The clinic keeps the patient, cases, reports, clinic notes, lab panels, entries
    the client sent, and wearable days. Portal releases are withdrawn, including
    released lab panels, those entries, and wearable days. Marker values, entry
    text, and daily summaries stay in the clinic record. Share links are not revoked.
    """
    client = _require_client(current_user)
    patient = require_claimed_patient(db, client, patient_id)
    patient = end_claim_access(db, patient, "client")
    return claim_access_state(patient)
