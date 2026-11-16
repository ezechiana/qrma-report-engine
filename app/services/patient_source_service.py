"""Manual clinic notes on a patient. QRMA reports and share links are not used."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ContentRelease, Patient, PatientSource, SourceRecord
from app.services.content_release_service import CLINIC_TO_CLIENT

MANUAL = "manual"
MANUAL_NAME = "Clinic notes"
RECORD_PREFIX = "record:"


def _note_text(record: SourceRecord) -> str:
    payload = record.payload_json if isinstance(record.payload_json, dict) else {}
    text = payload.get("text")
    return text.strip() if isinstance(text, str) else ""


def record_source_key(record_id: UUID) -> str:
    """Portal key for one note. A bare record id would look like a report release."""
    return f"{RECORD_PREFIX}{record_id}"


def _record_payload(record: SourceRecord, released: bool = False) -> dict:
    recorded = record.recorded_at
    recorded_on = recorded.date().isoformat() if isinstance(recorded, datetime) else None
    return {
        "record_id": str(record.id),
        "title": record.title,
        "recorded_on": recorded_on,
        "text": _note_text(record),
        "client_summary": record.client_summary or "",
        "released": released,
    }


def _released_record_keys(db: Session, patient: Patient, records: list[SourceRecord]) -> set[str]:
    if patient.claimed_by_user_id is None or not records:
        return set()
    keys = [record_source_key(record.id) for record in records]
    rows = (
        db.query(ContentRelease.source_key)
        .filter(
            ContentRelease.patient_id == patient.id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.direction == CLINIC_TO_CLIENT,
            ContentRelease.source_key.in_(keys),
            ContentRelease.released.is_(True),
        )
        .all()
    )
    return {row[0] for row in rows}


def _source_payload(source: PatientSource, records: list[SourceRecord], released_keys: set[str]) -> dict:
    return {
        "source_id": str(source.id),
        "source_type": source.source_type,
        "display_name": source.display_name,
        "status": source.status,
        "records": [
            _record_payload(record, released=record_source_key(record.id) in released_keys)
            for record in records
        ],
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
    released_keys = _released_record_keys(db, patient, records)
    return {"patient_id": str(patient.id), "sources": [_source_payload(source, records, released_keys)]}


def _manual_source(db: Session, patient: Patient, source_id: UUID) -> PatientSource:
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
    return source


def _clinic_note(db: Session, patient: Patient, source: PatientSource, record_id: UUID) -> SourceRecord:
    record = (
        db.query(SourceRecord)
        .filter(
            SourceRecord.id == record_id,
            SourceRecord.patient_source_id == source.id,
            SourceRecord.patient_id == patient.id,
            SourceRecord.is_archived.is_(False),
        )
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return record


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
    source = _manual_source(db, patient, source_id)
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
    return _record_payload(record, released=False)


def update_clinic_note(
    db: Session,
    patient: Patient,
    source_id: UUID,
    record_id: UUID,
    changes: dict,
) -> dict:
    """Change only the fields that were sent. Note text and client summary stay independent."""
    source = _manual_source(db, patient, source_id)
    record = _clinic_note(db, patient, source, record_id)
    if "title" in changes:
        record.title = changes["title"]
    if "recorded_on" in changes:
        record.recorded_at = datetime.combine(changes["recorded_on"], time.min, tzinfo=timezone.utc)
    if "text" in changes:
        record.payload_json = {"kind": "note", "text": changes["text"]}
    if "client_summary" in changes:
        record.client_summary = changes["client_summary"]
    record.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(record)
    released = record_source_key(record.id) in _released_record_keys(db, patient, [record])
    return _record_payload(record, released=released)


def set_clinic_note_release(
    db: Session,
    patient: Patient,
    source_id: UUID,
    record_id: UUID,
    released: bool,
) -> dict:
    """Release or withdraw one note. Does not create a share link or change a report."""
    if patient.claimed_by_user_id is None:
        raise HTTPException(
            status_code=409,
            detail="A client must claim this record before a note can be released.",
        )
    source = _manual_source(db, patient, source_id)
    record = _clinic_note(db, patient, source, record_id)
    summary = (record.client_summary or "").strip()
    if released and not summary:
        raise HTTPException(
            status_code=422,
            detail="Add a client summary before releasing this note.",
        )
    now = datetime.now(timezone.utc)
    source_key = record_source_key(record.id)
    row = (
        db.query(ContentRelease)
        .filter(
            ContentRelease.patient_id == patient.id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.source_key == source_key,
            ContentRelease.direction == CLINIC_TO_CLIENT,
        )
        .first()
    )
    if row is None:
        row = ContentRelease(
            patient_id=patient.id,
            practitioner_user_id=patient.user_id,
            source_key=source_key,
            direction=CLINIC_TO_CLIENT,
            released=False,
            created_at=now,
        )
        db.add(row)
    if released:
        row.released = True
        row.released_at = now
    else:
        row.released = False
        row.withdrawn_at = now
    db.commit()
    return {
        "patient_id": str(patient.id),
        "record_id": str(record.id),
        "released": bool(row.released),
        "available": True,
    }


def clear_clinic_note_releases(db: Session, patient: Patient, now: datetime | None = None) -> None:
    """Turn released clinic notes off without committing. Used when a claim ends.

    The note rows stay. Share links are not changed.
    """
    moment = now or datetime.now(timezone.utc)
    rows = (
        db.query(ContentRelease)
        .filter(
            ContentRelease.patient_id == patient.id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.direction == CLINIC_TO_CLIENT,
            ContentRelease.source_key.startswith(RECORD_PREFIX),
            ContentRelease.released.is_(True),
        )
        .all()
    )
    for row in rows:
        row.released = False
        row.withdrawn_at = moment


def _recorded_on(record: SourceRecord) -> str | None:
    recorded = record.recorded_at
    if isinstance(recorded, datetime):
        return recorded.date().isoformat()
    return None


def released_client_sources(db: Session, patient: Patient) -> list[dict]:
    """Released notes for the claimed client, grouped by source name.

    A note is included only when it is released and its client summary has text.
    The raw note text is not included.
    """
    rows = (
        db.query(SourceRecord, PatientSource)
        .join(PatientSource, PatientSource.id == SourceRecord.patient_source_id)
        .filter(
            SourceRecord.patient_id == patient.id,
            PatientSource.patient_id == patient.id,
            PatientSource.practitioner_user_id == patient.user_id,
            PatientSource.status == "active",
            SourceRecord.is_archived.is_(False),
        )
        .order_by(SourceRecord.recorded_at.desc(), SourceRecord.created_at.desc())
        .all()
    )
    released = _released_record_keys(db, patient, [record for record, _source in rows])
    groups: dict[str, dict] = {}
    order: list[str] = []
    for record, source in rows:
        summary = (record.client_summary or "").strip()
        if not summary or record_source_key(record.id) not in released:
            continue
        key = str(source.id)
        if key not in groups:
            groups[key] = {"display_name": source.display_name, "records": []}
            order.append(key)
        groups[key]["records"].append({
            "record_id": str(record.id),
            "title": record.title,
            "recorded_on": _recorded_on(record),
        })
    return [groups[key] for key in order]


def client_released_note(db: Session, patient: Patient, record_id: UUID) -> dict:
    """The client summary of one released note. The raw note text is not included."""
    record = (
        db.query(SourceRecord)
        .filter(
            SourceRecord.id == record_id,
            SourceRecord.patient_id == patient.id,
            SourceRecord.is_archived.is_(False),
        )
        .first()
    )
    source = None
    if record is not None:
        source = (
            db.query(PatientSource)
            .filter(
                PatientSource.id == record.patient_source_id,
                PatientSource.patient_id == patient.id,
                PatientSource.practitioner_user_id == patient.user_id,
                PatientSource.status == "active",
            )
            .first()
        )
    summary = (record.client_summary or "").strip() if record is not None else ""
    released = (
        record is not None
        and source is not None
        and bool(summary)
        and record_source_key(record.id) in _released_record_keys(db, patient, [record])
    )
    if not released:
        raise HTTPException(status_code=404, detail="Note not found")
    return {
        "record_id": str(record.id),
        "title": record.title,
        "recorded_on": _recorded_on(record),
        "client_summary": summary,
        "source_name": source.display_name,
    }
