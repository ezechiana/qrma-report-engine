"""Daily wearable summaries for a claimed client.

Open Health Sync is not called. A sync stores one day on the Wearables shelf
and updates that same day when it arrives again. Days stay unreleased.
Share links, the progress view, and other sources are not used.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ContentRelease, Patient, PatientSource, SourceObservation, SourceRecord, User
from app.services.wearable_connection_service import WEARABLE, _require_claimed, _wearable_source

DAY_KIND = "wearable_day"
DAY_TITLE = "Daily summary"
DAY_PREFIX = "wearable:"
CLIENT_TO_CLINIC = "client_to_clinic"
MAX_DAYS = 366
MAX_METRIC = Decimal("1000000000000")

METRICS = (
    ("steps", "Step count", "steps", 1),
    ("resting_heart_rate", "Resting heart rate", "bpm", 2),
    ("sleep_duration", "Sleep duration", "min", 3),
    ("hrv", "Heart rate variability", "ms", 4),
)
METRIC_CODES = {item[0] for item in METRICS}


def wearable_day_source_key(record_id: UUID) -> str:
    """Release key for one wearable day. It does not use the client-entry prefix."""
    return f"{DAY_PREFIX}{record_id}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _recorded_at(recorded_on: date) -> datetime:
    return datetime.combine(recorded_on, time.min, tzinfo=timezone.utc)


def _public_number(value: Decimal | None):
    if value is None:
        return None
    number = Decimal(value).normalize()
    if number == number.to_integral():
        return int(number)
    return float(number)


def _optional_metric(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise HTTPException(status_code=422, detail="This field is invalid.")
    try:
        number = Decimal(str(value))
    except Exception:
        raise HTTPException(status_code=422, detail="This field is invalid.") from None
    if not number.is_finite() or number < 0 or number >= MAX_METRIC:
        raise HTTPException(status_code=422, detail="This field is invalid.")
    return number.quantize(Decimal("0.000001"))


def _connected_source(db: Session, patient: Patient) -> PatientSource | None:
    source = _wearable_source(db, patient)
    ref = source.external_account_ref if source is not None else None
    if source is None or source.status != "active" or not (ref or "").strip():
        return None
    return source


def _require_connected(db: Session, patient: Patient) -> PatientSource:
    source = _connected_source(db, patient)
    if source is None:
        raise HTTPException(status_code=404, detail="Wearable not connected")
    return source


def _find_day(db: Session, patient: Patient, source: PatientSource, recorded_on: date) -> SourceRecord | None:
    rows = (
        db.query(SourceRecord)
        .filter(
            SourceRecord.patient_source_id == source.id,
            SourceRecord.patient_id == patient.id,
            SourceRecord.recorded_at == _recorded_at(recorded_on),
            SourceRecord.is_archived.is_(False),
        )
        .order_by(SourceRecord.created_at.asc())
        .all()
    )
    for record in rows:
        payload = record.payload_json if isinstance(record.payload_json, dict) else {}
        if payload.get("kind") == DAY_KIND:
            return record
    return None


def _replace_metrics(
    db: Session,
    patient: Patient,
    record: SourceRecord,
    metrics: dict[str, Decimal | None],
    observed_at: datetime,
) -> None:
    (
        db.query(SourceObservation)
        .filter(SourceObservation.source_record_id == record.id)
        .delete(synchronize_session=False)
    )
    db.flush()
    now = _now()
    for code, name, unit, position in METRICS:
        value = metrics.get(code)
        if value is None:
            continue
        db.add(
            SourceObservation(
                source_record_id=record.id,
                patient_id=patient.id,
                position=position,
                code=code,
                name=name,
                value_text=None,
                value_numeric=value,
                unit=unit,
                observed_at=observed_at,
                created_at=now,
                updated_at=now,
            )
        )


def _insert_day(
    db: Session,
    patient: Patient,
    client: User,
    source: PatientSource,
    recorded_on: date,
    now: datetime,
) -> SourceRecord:
    recorded_at = _recorded_at(recorded_on)
    record = SourceRecord(
        patient_source_id=source.id,
        patient_id=patient.id,
        recorded_at=recorded_at,
        title=DAY_TITLE,
        status="ready",
        payload_json={"kind": DAY_KIND},
        client_summary=None,
        is_archived=False,
        created_by_user_id=client.id,
        created_at=now,
        updated_at=now,
    )
    db.add(record)
    db.flush()
    return record


def _upsert_day(
    db: Session,
    patient: Patient,
    client: User,
    source: PatientSource,
    day: dict,
    now: datetime,
) -> SourceRecord:
    recorded_on = day["recorded_on"]
    metrics = {code: _optional_metric(day.get(code)) for code, *_rest in METRICS}
    record = _find_day(db, patient, source, recorded_on)
    if record is None:
        record = _insert_day(db, patient, client, source, recorded_on, now)
    record.title = DAY_TITLE
    record.payload_json = {"kind": DAY_KIND}
    record.client_summary = None
    record.updated_at = now
    _replace_metrics(db, patient, record, metrics, record.recorded_at)
    return record


def _released_keys(db: Session, patient: Patient, records: list[SourceRecord]) -> set[str]:
    if patient.claimed_by_user_id is None or not records:
        return set()
    keys = [wearable_day_source_key(record.id) for record in records]
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


def _observations(db: Session, records: list[SourceRecord]) -> dict[UUID, dict[str, SourceObservation]]:
    if not records:
        return {}
    ids = [record.id for record in records]
    rows = (
        db.query(SourceObservation)
        .filter(SourceObservation.source_record_id.in_(ids))
        .all()
    )
    grouped: dict[UUID, dict[str, SourceObservation]] = {}
    for row in rows:
        if row.code not in METRIC_CODES:
            continue
        grouped.setdefault(row.source_record_id, {})[row.code] = row
    return grouped


def _metric_value(row: SourceObservation | None, unit: str) -> dict | None:
    if row is None or row.value_numeric is None:
        return None
    return {"value": _public_number(row.value_numeric), "unit": unit}


def _day_payload(
    record: SourceRecord,
    observations: dict[str, SourceObservation],
    released_keys: set[str],
) -> dict:
    recorded = record.recorded_at
    recorded_on = recorded.date().isoformat() if isinstance(recorded, datetime) else None
    payload = {
        "record_id": str(record.id),
        "recorded_on": recorded_on,
        "released": wearable_day_source_key(record.id) in released_keys,
    }
    for code, _name, unit, _position in METRICS:
        payload[code] = _metric_value(observations.get(code), unit)
    return payload


def _payloads(db: Session, patient: Patient, records: list[SourceRecord]) -> list[dict]:
    observations = _observations(db, records)
    released_keys = _released_keys(db, patient, records)
    return [
        _day_payload(record, observations.get(record.id, {}), released_keys)
        for record in records
    ]


def _own_day(db: Session, patient: Patient, client: User, record_id: UUID) -> SourceRecord:
    _require_claimed(patient, client)
    record = (
        db.query(SourceRecord)
        .join(PatientSource, PatientSource.id == SourceRecord.patient_source_id)
        .filter(
            SourceRecord.id == record_id,
            SourceRecord.patient_id == patient.id,
            SourceRecord.created_by_user_id == client.id,
            SourceRecord.is_archived.is_(False),
            PatientSource.patient_id == patient.id,
            PatientSource.practitioner_user_id == patient.user_id,
            PatientSource.source_type == WEARABLE,
            PatientSource.status == "active",
        )
        .first()
    )
    payload = record.payload_json if record is not None and isinstance(record.payload_json, dict) else {}
    if record is None or payload.get("kind") != DAY_KIND:
        raise HTTPException(status_code=404, detail="Day not found")
    return record


def set_wearable_day_release(
    db: Session,
    patient: Patient,
    client: User,
    record_id: UUID,
    released: bool,
) -> dict:
    """Release or withdraw one wearable day for the owning clinic.

    Handles patient identity. The daily values are not returned. The saved
    values stay as they are. Does not create a share link.
    """
    record = _own_day(db, patient, client, record_id)
    now = _now()
    row = (
        db.query(ContentRelease)
        .filter(
            ContentRelease.patient_id == patient.id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.source_key == wearable_day_source_key(record.id),
            ContentRelease.direction == CLIENT_TO_CLINIC,
        )
        .first()
    )
    if released:
        if row is None:
            row = ContentRelease(
                patient_id=patient.id,
                practitioner_user_id=patient.user_id,
                source_key=wearable_day_source_key(record.id),
                direction=CLIENT_TO_CLINIC,
                released=True,
                released_at=now,
                created_at=now,
            )
            db.add(row)
        else:
            row.released = True
            row.released_at = now
    else:
        if row is None:
            raise HTTPException(status_code=404, detail="Day not found")
        row.released = False
        row.withdrawn_at = now
    db.commit()
    return {"record_id": str(record.id), "released": bool(row.released)}


def released_clinic_wearable_source(db: Session, patient: Patient) -> dict | None:
    """Released wearable days for the practitioner who owns this record.

    The group is omitted when nothing is currently released. Unreleased days
    and the release flag are not included.
    """
    source = (
        db.query(PatientSource)
        .filter(
            PatientSource.patient_id == patient.id,
            PatientSource.practitioner_user_id == patient.user_id,
            PatientSource.source_type == WEARABLE,
            PatientSource.status == "active",
        )
        .first()
    )
    if source is None:
        return None
    rows = (
        db.query(SourceRecord)
        .filter(
            SourceRecord.patient_source_id == source.id,
            SourceRecord.patient_id == patient.id,
            SourceRecord.is_archived.is_(False),
        )
        .order_by(SourceRecord.recorded_at.desc(), SourceRecord.created_at.desc())
        .all()
    )
    days = [
        record
        for record in rows
        if isinstance(record.payload_json, dict) and record.payload_json.get("kind") == DAY_KIND
    ]
    released = _released_keys(db, patient, days)
    visible = [record for record in days if wearable_day_source_key(record.id) in released]
    if not visible:
        return None
    observations = _observations(db, visible)
    records = []
    for record in visible:
        observed = observations.get(record.id, {})
        recorded = record.recorded_at
        item = {
            "record_id": str(record.id),
            "title": record.title,
            "recorded_on": recorded.date().isoformat() if isinstance(recorded, datetime) else None,
        }
        for code, _name, unit, _position in METRICS:
            item[code] = _metric_value(observed.get(code), unit)
        records.append(item)
    return {
        "source_id": str(source.id),
        "source_type": WEARABLE,
        "display_name": source.display_name,
        "status": source.status,
        "records": records,
    }


def list_wearable_days(db: Session, patient: Patient, client: User) -> dict:
    """List this client's wearable days, including days the clinic cannot see.

    Handles patient identity. Only the four daily values are included.
    """
    _require_claimed(patient, client)
    source = _connected_source(db, patient)
    if source is None:
        return {"days": []}
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
    days = [
        record
        for record in records
        if isinstance(record.payload_json, dict) and record.payload_json.get("kind") == DAY_KIND
    ]
    return {"days": _payloads(db, patient, days)}


def sync_wearable_days(db: Session, patient: Patient, client: User, days: list[dict]) -> dict:
    """Store daily summaries for the connected Wearables shelf.

    Handles patient identity. The same calendar day updates the existing summary.
    New days are not released to the clinic. Does not create a share link.
    """
    _require_claimed(patient, client)
    if len(days) > MAX_DAYS:
        raise HTTPException(status_code=422, detail="Too many days.")
    source = _require_connected(db, patient)
    latest: dict[date, dict] = {}
    order: list[date] = []
    for day in days:
        recorded_on = day["recorded_on"]
        if recorded_on not in latest:
            order.append(recorded_on)
        latest[recorded_on] = day
    now = _now()
    written = [
        _upsert_day(db, patient, client, source, latest[recorded_on], now)
        for recorded_on in order
    ]
    db.commit()
    for record in written:
        db.refresh(record)
    return {"days": _payloads(db, patient, written)}
