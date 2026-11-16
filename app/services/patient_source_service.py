"""Manual clinic notes on a patient. QRMA reports and share links are not used."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Patient, PatientSource, SourceRecord

MANUAL = "manual"
MANUAL_NAME = "Clinic notes"


def _note_text(record: SourceRecord) -> str:
    payload = record.payload_json if isinstance(record.payload_json, dict) else {}
    text = payload.get("text")
    return text.strip() if isinstance(text, str) else ""


def _record_payload(record: SourceRecord) -> dict:
    recorded = record.recorded_at
    recorded_on = recorded.date().isoformat() if isinstance(recorded, datetime) else None
    return {
        "record_id": str(record.id),
        "title": record.title,
        "recorded_on": recorded_on,
        "text": _note_text(record),
        "client_summary": record.client_summary or "",
    }


def _source_payload(source: PatientSource, records: list[SourceRecord]) -> dict:
    return {
        "source_id": str(source.id),
        "source_type": source.source_type,
        "display_name": source.display_name,
        "status": source.status,
        "records": [_record_payload(record) for record in records],
    }


def ensure_manual_source(db: Session, patient: Patient) -> PatientSource:
    """Return the patient's one manual source, creating it when missing."""
    source = (
        db.query(PatientSource)
        .filter(
            PatientSource.patient_id == patient.id,
            PatientSource.source_type == MANUAL,
        )
        .first()
    )
    if source is not None:
        return source
    source = PatientSource(
        patient_id=patient.id,
        practitioner_user_id=patient.user_id,
        source_type=MANUAL,
        display_name=MANUAL_NAME,
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def list_patient_sources(db: Session, patient: Patient) -> dict:
    """Clinic-facing sources and notes. The raw note text stays in this response."""
    source = ensure_manual_source(db, patient)
    records = (
        db.query(SourceRecord)
        .filter(
            SourceRecord.patient_source_id == source.id,
            SourceRecord.patient_id == patient.id,
            SourceRecord.is_archived.is_(False),
        )
        .order_by(SourceRecord.recorded_at.desc(), SourceRecord.created_at.desc())
        .all()
    )
    return {"patient_id": str(patient.id), "sources": [_source_payload(source, records)]}


def create_clinic_note(
    db: Session,
    patient: Patient,
    source_id: UUID,
    *,
    title: str,
    recorded_on: date,
    text: str,
    client_summary: str,
    created_by_user_id: UUID,
) -> dict:
    """Save a private note. Does not release it or create a share link."""
    source = (
        db.query(PatientSource)
        .filter(
            PatientSource.id == source_id,
            PatientSource.patient_id == patient.id,
            PatientSource.practitioner_user_id == patient.user_id,
            PatientSource.source_type == MANUAL,
        )
        .first()
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    record = SourceRecord(
        patient_source_id=source.id,
        patient_id=patient.id,
        recorded_at=datetime.combine(recorded_on, time.min, tzinfo=timezone.utc),
        title=title,
        status="ready",
        payload_json={"kind": "note", "text": text},
        client_summary=client_summary,
        is_archived=False,
        created_by_user_id=created_by_user_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _record_payload(record)
