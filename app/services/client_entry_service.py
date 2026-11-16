"""Entries a claimed client sends to the clinic.

The clinic release is written with the entry. Share links, the progress view,
and clinic-to-client notes and panels are not used.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ContentRelease, Patient, PatientSource, SourceRecord, User

CLIENT = "client"
CLIENT_NAME = "From the client"
CLIENT_PREFIX = "client:"
CLIENT_TO_CLINIC = "client_to_clinic"
TITLE_MAX = 255
TEXT_MAX = 4000
REFERENCE_MAX = 255


def client_entry_source_key(record_id: UUID) -> str:
    """Release key for one client entry. A bare id would look like a report."""
    return f"{CLIENT_PREFIX}{record_id}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_claimed(patient: Patient, client: User) -> None:
    if patient.claimed_by_user_id != client.id:
        raise HTTPException(status_code=404, detail="Record not found")


def _required_text(value: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="This field is required.")
    if len(cleaned) > limit:
        raise HTTPException(status_code=422, detail="This field is too long.")
    return cleaned


def _optional_text(value: str, limit: int) -> str:
    cleaned = value.strip()
    if len(cleaned) > limit:
        raise HTTPException(status_code=422, detail="This field is too long.")
    return cleaned


def _entry_parts(record: SourceRecord) -> tuple[str, str]:
    payload = record.payload_json if isinstance(record.payload_json, dict) else {}
    text = payload.get("text")
    reference = payload.get("reference")
    return (
        text.strip() if isinstance(text, str) else "",
        reference.strip() if isinstance(reference, str) else "",
    )


def _recorded_on(record: SourceRecord) -> str | None:
    recorded = record.recorded_at
    if isinstance(recorded, datetime):
        return recorded.date().isoformat()
    return None


def _client_source(db: Session, patient: Patient) -> PatientSource:
    source = (
        db.query(PatientSource)
        .filter(
            PatientSource.patient_id == patient.id,
            PatientSource.source_type == CLIENT,
        )
        .first()
    )
    if source is not None:
        return source
    source = PatientSource(
        patient_id=patient.id,
        practitioner_user_id=patient.user_id,
        source_type=CLIENT,
        display_name=CLIENT_NAME,
        status="active",
        created_at=_now(),
    )
    db.add(source)
    db.flush()
    return source


def _released_keys(db: Session, patient: Patient, records: list[SourceRecord]) -> set[str]:
    if patient.claimed_by_user_id is None or not records:
        return set()
    keys = [client_entry_source_key(record.id) for record in records]
    rows = (
        db.query(ContentRelease.source_key)
        .filter(
            ContentRelease.patient_id == patient.id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.direction == CLIENT_TO_CLINIC,
            ContentRelease.source_key.in_(keys),
            ContentRelease.released.is_(True),
        )
        .all()
    )
    return {row[0] for row in rows}


def _entry_payload(record: SourceRecord, released: bool) -> dict:
    text, reference = _entry_parts(record)
    return {
        "record_id": str(record.id),
        "title": record.title,
        "recorded_on": _recorded_on(record),
        "text": text,
        "reference": reference,
        "released": released,
    }


def submit_client_entry(
    db: Session,
    patient: Patient,
    client: User,
    *,
    title: str,
    recorded_on: date,
    text: str,
    reference: str = "",
) -> dict:
    """Save one plain-text entry and release it to the owning clinic.

    Handles patient identity. The entry text stays on the client's own list.
    Does not create a share link or change a clinic-to-client release.
    """
    _require_claimed(patient, client)
    title = _required_text(title, TITLE_MAX)
    text = _required_text(text, TEXT_MAX)
    reference = _optional_text(reference, REFERENCE_MAX)
    now = _now()
    source = _client_source(db, patient)
    payload: dict[str, str] = {"kind": "client_entry", "text": text}
    if reference:
        payload["reference"] = reference
    record = SourceRecord(
        patient_source_id=source.id,
        patient_id=patient.id,
        recorded_at=datetime.combine(recorded_on, time.min, tzinfo=timezone.utc),
        title=title,
        status="ready",
        payload_json=payload,
        client_summary=None,
        is_archived=False,
        created_by_user_id=client.id,
        created_at=now,
    )
    db.add(record)
    db.flush()
    db.add(
        ContentRelease(
            patient_id=patient.id,
            practitioner_user_id=patient.user_id,
            source_key=client_entry_source_key(record.id),
            direction=CLIENT_TO_CLINIC,
            released=True,
            released_at=now,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(record)
    return {
        "record_id": str(record.id),
        "title": record.title,
        "recorded_on": _recorded_on(record),
        "released": True,
    }


def list_client_entries(db: Session, patient: Patient, client: User) -> dict:
    """Entries this client sent on one claimed record, including ones they withdrew.

    Handles patient identity. Clinic notes, lab panels, and other clients' entries
    are not included.
    """
    _require_claimed(patient, client)
    rows = (
        db.query(SourceRecord)
        .join(PatientSource, PatientSource.id == SourceRecord.patient_source_id)
        .filter(
            SourceRecord.patient_id == patient.id,
            SourceRecord.created_by_user_id == client.id,
            SourceRecord.is_archived.is_(False),
            PatientSource.patient_id == patient.id,
            PatientSource.practitioner_user_id == patient.user_id,
            PatientSource.source_type == CLIENT,
            PatientSource.status == "active",
        )
        .order_by(SourceRecord.recorded_at.desc(), SourceRecord.created_at.desc())
        .all()
    )
    released = _released_keys(db, patient, rows)
    return {
        "entries": [
            _entry_payload(record, client_entry_source_key(record.id) in released)
            for record in rows
        ]
    }
