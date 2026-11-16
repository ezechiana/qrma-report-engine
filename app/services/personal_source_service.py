"""Notes and lab panels on a personal record before a clinic is connected.

The subject can read what they saved. Nothing is released, and no share link
is created. A clinic source list does not receive these rows.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Patient, PatientSource, SourceObservation, SourceRecord, User
from app.services.patient_source_service import (
    LABORATORY,
    MANUAL,
    create_clinic_note,
    create_lab_panel,
    ensure_laboratory_source,
    ensure_manual_source,
)


def _require_owner(patient: Patient, user: User) -> None:
    if patient.subject_user_id == user.id and patient.claimed_by_user_id is None:
        return
    raise HTTPException(status_code=404, detail="Record not found")


def _recorded_on(record: SourceRecord) -> str | None:
    recorded = record.recorded_at
    if recorded is None:
        return None
    return recorded.date().isoformat()


def _note_text(record: SourceRecord) -> str:
    payload = record.payload_json if isinstance(record.payload_json, dict) else {}
    text = payload.get("text")
    return text.strip() if isinstance(text, str) else ""


def _source(db: Session, patient: Patient, source_type: str) -> PatientSource | None:
    return (
        db.query(PatientSource)
        .filter(
            PatientSource.patient_id == patient.id,
            PatientSource.source_type == source_type,
        )
        .first()
    )


def _records(db: Session, patient: Patient, source: PatientSource | None) -> list[SourceRecord]:
    if source is None:
        return []
    return (
        db.query(SourceRecord)
        .filter(
            SourceRecord.patient_source_id == source.id,
            SourceRecord.patient_id == patient.id,
            SourceRecord.is_archived.is_(False),
        )
        .order_by(SourceRecord.recorded_at.desc(), SourceRecord.created_at.desc())
        .all()
    )


def _note_view(record: SourceRecord) -> dict:
    return {
        "record_id": str(record.id),
        "title": record.title,
        "recorded_on": _recorded_on(record),
        "text": _note_text(record),
        "released": False,
    }


def _marker_view(row: SourceObservation) -> dict:
    number = row.value_numeric
    if number is not None and not isinstance(number, int):
        number = float(number)
        if number.is_integer():
            number = int(number)
    return {
        "name": row.name,
        "value_text": row.value_text or "",
        "value_numeric": number,
        "unit": row.unit or "",
    }


def _lab_view(db: Session, record: SourceRecord) -> dict:
    rows = (
        db.query(SourceObservation)
        .filter(SourceObservation.source_record_id == record.id)
        .order_by(SourceObservation.position.asc())
        .all()
    )
    return {
        "record_id": str(record.id),
        "title": record.title,
        "recorded_on": _recorded_on(record),
        "released": False,
        "observations": [_marker_view(row) for row in rows],
    }


def add_personal_note(
    db: Session,
    patient: Patient,
    user: User,
    *,
    title: str,
    recorded_on: date,
    text: str,
) -> dict:
    """Save one note on this personal record. It is not shared."""
    _require_owner(patient, user)
    source = ensure_manual_source(db, patient)
    created = create_clinic_note(
        db,
        patient,
        source.id,
        title=title,
        recorded_on=recorded_on,
        text=text,
        client_summary="",
        created_by_user_id=user.id,
    )
    return {
        "record_id": created["record_id"],
        "title": created["title"],
        "recorded_on": created["recorded_on"],
        "text": created["text"],
        "released": False,
    }


def list_personal_notes(db: Session, patient: Patient, user: User) -> dict:
    """Notes this person saved. Each one is marked unshared."""
    _require_owner(patient, user)
    records = _records(db, patient, _source(db, patient, MANUAL))
    notes = [
        _note_view(record)
        for record in records
        if isinstance(record.payload_json, dict) and record.payload_json.get("kind") == "note"
    ]
    return {"notes": notes}


def add_personal_lab(
    db: Session,
    patient: Patient,
    user: User,
    *,
    title: str,
    recorded_on: date,
    observations: list[dict],
) -> dict:
    """Save one lab panel on this personal record. It is not shared."""
    _require_owner(patient, user)
    source = ensure_laboratory_source(db, patient)
    created = create_lab_panel(
        db,
        patient,
        source.id,
        title=title,
        recorded_on=recorded_on,
        specimen="",
        laboratory_name="",
        client_summary="",
        observations=observations,
        created_by_user_id=user.id,
    )
    return {
        "record_id": created["record_id"],
        "title": created["title"],
        "recorded_on": created["recorded_on"],
        "released": False,
        "observations": [
            {
                "name": row["name"],
                "value_text": row["value_text"],
                "value_numeric": row["value_numeric"],
                "unit": row["unit"],
            }
            for row in created["observations"]
        ],
    }


def list_personal_labs(db: Session, patient: Patient, user: User) -> dict:
    """Lab panels this person saved, including the values they entered."""
    _require_owner(patient, user)
    records = _records(db, patient, _source(db, patient, LABORATORY))
    panels = [
        _lab_view(db, record)
        for record in records
        if isinstance(record.payload_json, dict) and record.payload_json.get("kind") == "laboratory"
    ]
    return {"labs": panels}


def refuse_personal_share(db: Session, patient: Patient, user: User, record_id: UUID) -> None:
    """Refuse sharing while this record has no clinic connection.

    The saved text and marker values are not returned. No release row is written.
    """
    _require_owner(patient, user)
    record = (
        db.query(SourceRecord)
        .filter(
            SourceRecord.id == record_id,
            SourceRecord.patient_id == patient.id,
            SourceRecord.is_archived.is_(False),
        )
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    raise HTTPException(
        status_code=403,
        detail="A clinic connection is required before this can be shared.",
    )
