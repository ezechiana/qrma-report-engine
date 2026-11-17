"""Today lists for the clinic dashboard and the client home.

These reads return names, item types, titles, and dates. Note text, marker
values, report bodies, and share tokens are not selected.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, inspect
from sqlalchemy.orm import Session

from app.db.models import (
    Case,
    ClinicAssociation,
    ContentRelease,
    Patient,
    PatientSource,
    ReportStatus,
    ReportVersion,
    SourceRecord,
    User,
)
from app.services.clinic_association_service import ACTIVE, sharing_practitioner_id

CLIENT_TO_CLINIC = "client_to_clinic"
CLINIC_TO_CLIENT = "clinic_to_client"
PROGRESS_VIEW = "progress_view"
FINISHED = (ReportStatus.ready, ReportStatus.final)
_SECTIONS = {
    "Wearable day": "wearables",
    "From the client": "client",
    "Note": "notes",
    "Lab panel": "labs",
}
_WEARABLE_METRICS = ("steps", "resting_heart_rate", "sleep_duration", "hrv")


def _has_table(db: Session, name: str) -> bool:
    bind = db.get_bind()
    if bind is None:
        return False
    return inspect(bind).has_table(name)


def _date_text(value) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return None


def _item_type(db: Session, source_key: str) -> str:
    if source_key.startswith("wearable:"):
        return "Wearable day"
    if source_key.startswith("client:"):
        return "From the client"
    if not source_key.startswith("record:"):
        return "Item"
    try:
        record_id = UUID(source_key.split(":", 1)[1])
    except ValueError:
        return "Note"
    if not _has_table(db, SourceRecord.__tablename__):
        return "Note"
    source_type = (
        db.query(PatientSource.source_type)
        .join(SourceRecord, SourceRecord.patient_source_id == PatientSource.id)
        .filter(SourceRecord.id == record_id)
        .scalar()
    )
    if source_type == "laboratory":
        return "Lab panel"
    return "Note"


def _clinic_chart_id(db: Session, practitioner: User, patient: Patient):
    if patient.user_id == practitioner.id:
        return patient.id
    if not _has_table(db, ClinicAssociation.__tablename__):
        return None
    row = (
        db.query(ClinicAssociation.clinic_patient_id)
        .filter(
            ClinicAssociation.personal_patient_id == patient.id,
            ClinicAssociation.practitioner_user_id == practitioner.id,
            ClinicAssociation.status == ACTIVE,
            ClinicAssociation.clinic_patient_id.is_not(None),
        )
        .first()
    )
    if row is None:
        return None
    return row[0]


def practitioner_attention(db: Session, practitioner: User) -> dict:
    """Items that need a look on the clinic dashboard.

    Requests are filled by the existing name-and-email list. Released rows are
    a patient name, an item type, and a date. Finished reports that are not
    released are a patient name and a report title.
    """
    return {
        "released_items": _released_personal_items(db, practitioner),
        "unreleased_reports": _unreleased_reports(db, practitioner),
    }


def _released_personal_items(db: Session, practitioner: User) -> list[dict]:
    if not _has_table(db, ContentRelease.__tablename__):
        return []
    rows = (
        db.query(ContentRelease)
        .filter(
            ContentRelease.practitioner_user_id == practitioner.id,
            ContentRelease.direction == CLIENT_TO_CLINIC,
            ContentRelease.released.is_(True),
        )
        .order_by(ContentRelease.released_at.desc())
        .limit(50)
        .all()
    )
    items = []
    for row in rows:
        patient = db.get(Patient, row.patient_id)
        if patient is None:
            continue
        chart_id = _clinic_chart_id(db, practitioner, patient)
        if chart_id is None:
            continue
        chart = patient if chart_id == patient.id else db.get(Patient, chart_id)
        item_type = _item_type(db, row.source_key)
        items.append({
            "patient_id": str(chart_id),
            "patient_name": (chart.full_name if chart is not None else None) or patient.full_name or "Patient",
            "item_type": item_type,
            "released_on": _date_text(row.released_at),
            "section": _SECTIONS.get(item_type, "activity"),
        })
    return items


def _unreleased_reports(db: Session, practitioner: User) -> list[dict]:
    if not _has_table(db, ReportVersion.__tablename__):
        return []
    released = set()
    if _has_table(db, ContentRelease.__tablename__):
        keys = (
            db.query(ContentRelease.source_key)
            .filter(
                ContentRelease.practitioner_user_id == practitioner.id,
                ContentRelease.direction == CLINIC_TO_CLIENT,
                ContentRelease.released.is_(True),
            )
            .all()
        )
        released = {key for (key,) in keys}
    rows = (
        db.query(
            ReportVersion.id,
            ReportVersion.report_type,
            Case.title,
            Case.patient_id,
            Patient.full_name,
        )
        .join(Case, Case.id == ReportVersion.case_id)
        .join(Patient, Patient.id == Case.patient_id)
        .filter(
            Case.user_id == practitioner.id,
            ReportVersion.is_archived.is_(False),
            ReportVersion.status.in_(FINISHED),
            ReportVersion.report_type.in_(("assessment", "trend")),
        )
        .order_by(ReportVersion.generated_at.desc())
        .limit(40)
        .all()
    )
    items = []
    for report_id, report_type, title, patient_id, name in rows:
        if str(report_id) in released:
            continue
        label = (title or "").strip() or ("Trend report" if report_type == "trend" else "Assessment report")
        items.append({
            "patient_id": str(patient_id),
            "patient_name": name or "Patient",
            "report_title": label,
            "section": "reports",
        })
        if len(items) >= 20:
            break
    return items


def client_today(db: Session, client: User) -> dict:
    """Wearable numbers for this person, plus released clinic titles.

    The clinic list is title and date for items released in the last 30 days.
    Unreleased titles are omitted.
    """
    from app.services.client_record_service import (
        _clinic_name,
        claimed_patients,
        client_connection_status,
    )
    from app.services.personal_record_service import personal_record_for

    status = client_connection_status(db, client)
    personal = personal_record_for(db, client)
    clinic_name = None
    released = []
    if status == "connected":
        charts = claimed_patients(db, client)
        if charts:
            clinic_name = _clinic_name(charts[0].user)
        released = _released_clinic_titles(db, charts)
    return {
        "connection": status,
        "clinic_name": clinic_name,
        "wearable": _own_wearable(db, client, personal),
        "from_clinic": released,
    }


def _own_wearable(db: Session, client: User, personal: Patient | None) -> dict | None:
    if personal is None:
        return None
    try:
        from app.services.wearable_summary_service import list_wearable_days

        days = list_wearable_days(db, personal, client).get("days") or []
    except HTTPException:
        return None
    if not days:
        return None
    day = days[0]
    tile = {"recorded_on": day.get("recorded_on") if isinstance(day.get("recorded_on"), str) else None}
    for code in _WEARABLE_METRICS:
        metric = day.get(code)
        if isinstance(metric, dict) and metric.get("value") is not None:
            tile[code] = {"value": metric.get("value"), "unit": metric.get("unit")}
        else:
            tile[code] = None
    return tile


def _released_clinic_titles(db: Session, charts: list[Patient]) -> list[dict]:
    if not charts or not _has_table(db, ContentRelease.__tablename__):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    items = []
    for patient in charts:
        if sharing_practitioner_id(db, patient) is None:
            continue
        rows = (
            db.query(ContentRelease)
            .filter(
                ContentRelease.patient_id == patient.id,
                ContentRelease.practitioner_user_id == patient.user_id,
                ContentRelease.direction == CLINIC_TO_CLIENT,
                ContentRelease.released.is_(True),
                ContentRelease.released_at.is_not(None),
                ContentRelease.released_at >= cutoff,
            )
            .all()
        )
        for row in rows:
            titled = _released_title(db, row)
            if titled is None:
                continue
            title, recorded_on = titled
            items.append({
                "title": title,
                "recorded_on": recorded_on,
                "patient_id": str(patient.id),
            })
    items.sort(key=lambda item: item["recorded_on"] or "", reverse=True)
    return items[:20]


def _released_title(db: Session, row: ContentRelease) -> tuple[str, str | None] | None:
    released_on = _date_text(row.released_at)
    key = row.source_key or ""
    if key == PROGRESS_VIEW:
        return "Progress", released_on
    if key.startswith("markers:"):
        return None
    if key.startswith("record:"):
        return _record_title(db, key, released_on)
    try:
        report_id = UUID(key)
    except ValueError:
        return None
    return _report_title(db, report_id, released_on)


def _record_title(db: Session, source_key: str, released_on: str | None):
    if not _has_table(db, SourceRecord.__tablename__):
        return None
    try:
        record_id = UUID(source_key.split(":", 1)[1])
    except ValueError:
        return None
    found = (
        db.query(SourceRecord.title, SourceRecord.recorded_at)
        .filter(
            SourceRecord.id == record_id,
            func.length(func.trim(SourceRecord.client_summary)) > 0,
            func.length(func.trim(SourceRecord.title)) > 0,
        )
        .first()
    )
    if found is None:
        return None
    recorded_on = _date_text(found[1]) or released_on
    return found[0].strip(), recorded_on


def _report_title(db: Session, report_id: UUID, released_on: str | None):
    if not _has_table(db, ReportVersion.__tablename__):
        return None
    found = (
        db.query(Case.title, ReportVersion.generated_at, ReportVersion.report_type)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            ReportVersion.id == report_id,
            ReportVersion.is_archived.is_(False),
            ReportVersion.status.in_(FINISHED),
        )
        .first()
    )
    if found is None:
        return None
    title = (found[0] or "").strip() or ("Trend report" if found[2] == "trend" else "Assessment report")
    recorded_on = _date_text(found[1]) or released_on
    return title, recorded_on
