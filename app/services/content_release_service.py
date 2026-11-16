"""Clinic-to-client release of a named information source.

Share links are a separate channel and are not created or changed here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Case, ContentRelease, Patient, ReportStatus, ReportVersion

PROGRESS_VIEW = "progress_view"
CLINIC_TO_CLIENT = "clinic_to_client"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _progress_row(db: Session, patient: Patient) -> ContentRelease | None:
    return (
        db.query(ContentRelease)
        .filter(
            ContentRelease.patient_id == patient.id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.source_key == PROGRESS_VIEW,
            ContentRelease.direction == CLINIC_TO_CLIENT,
        )
        .first()
    )


def progress_release_state(db: Session, patient: Patient) -> dict:
    """Clinic-facing release flag. The client account id is not included."""
    row = _progress_row(db, patient)
    available = patient.claimed_by_user_id is not None
    return {
        "patient_id": patient.id,
        "source": PROGRESS_VIEW,
        "direction": CLINIC_TO_CLIENT,
        "released": bool(available and row and row.released),
        "available": available,
    }


def clear_progress_release(db: Session, patient: Patient, now: datetime | None = None) -> None:
    """Turn the progress view off without committing. Used when a claim ends."""
    row = _progress_row(db, patient)
    if row is None or not row.released:
        return
    row.released = False
    row.withdrawn_at = now or _now()


def set_progress_release(db: Session, patient: Patient, released: bool) -> dict:
    """Release or withdraw the progress view for the client who claimed this record."""
    if patient.claimed_by_user_id is None:
        raise HTTPException(
            status_code=409,
            detail="A client must claim this record before the progress view can be released.",
        )
    now = _now()
    row = _progress_row(db, patient)
    if row is None:
        row = ContentRelease(
            patient_id=patient.id,
            practitioner_user_id=patient.user_id,
            source_key=PROGRESS_VIEW,
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
    db.refresh(row)
    return progress_release_state(db, patient)


def _as_report_id(value) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _report_label(report: ReportVersion) -> str:
    case = getattr(report, "case", None)
    title = ((getattr(case, "title", None) or "") if case is not None else "").strip()
    if not title:
        title = "Trend report" if (report.report_type or "") == "trend" else "Assessment report"
    return f"{title} v{report.version_number or 1}"


def _releasable_reports(db: Session, patient: Patient) -> list[ReportVersion]:
    return (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            Case.patient_id == patient.id,
            Case.user_id == patient.user_id,
            ReportVersion.status == ReportStatus.ready,
            ReportVersion.is_archived == False,
            ReportVersion.report_type.in_(("assessment", "trend")),
        )
        .order_by(ReportVersion.generated_at.desc(), ReportVersion.version_number.desc())
        .all()
    )


def _released_report_ids(db: Session, patient: Patient) -> set[str]:
    rows = (
        db.query(ContentRelease)
        .filter(
            ContentRelease.patient_id == patient.id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.direction == CLINIC_TO_CLIENT,
            ContentRelease.released.is_(True),
        )
        .all()
    )
    released = set()
    for row in rows:
        report_id = _as_report_id(row.source_key)
        if report_id is not None:
            released.add(str(report_id))
    return released


def report_release_state(db: Session, patient: Patient) -> dict:
    """Reports this clinic has chosen for the claimed client.

    Handles patient identity through the report label the practitioner already
    sees. The report body and the client account id are not included.
    """
    available = patient.claimed_by_user_id is not None
    assessment_reports = []
    trend_reports = []
    if available:
        released_ids = _released_report_ids(db, patient)
        for report in _releasable_reports(db, patient):
            item = {
                "report_id": str(_as_report_id(report.id) or report.id),
                "label": _report_label(report),
                "released": str(_as_report_id(report.id) or "") in released_ids,
            }
            if (report.report_type or "") == "trend":
                trend_reports.append(item)
            else:
                assessment_reports.append(item)
    return {
        "patient_id": patient.id,
        "available": available,
        "assessment_reports": assessment_reports,
        "trend_reports": trend_reports,
    }


def clear_report_releases(db: Session, patient: Patient, now: datetime | None = None) -> None:
    """Turn released reports off without committing. Used when a claim ends."""
    moment = now or _now()
    rows = (
        db.query(ContentRelease)
        .filter(
            ContentRelease.patient_id == patient.id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.direction == CLINIC_TO_CLIENT,
        )
        .all()
    )
    for row in rows:
        if _as_report_id(row.source_key) is None or not row.released:
            continue
        row.released = False
        row.withdrawn_at = moment


def set_report_release(db: Session, patient: Patient, report_id: UUID, released: bool) -> dict:
    """Release or withdraw one ready report. Does not create a share link."""
    if patient.claimed_by_user_id is None:
        raise HTTPException(
            status_code=409,
            detail="A client must claim this record before a report can be released.",
        )
    report = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            ReportVersion.id == report_id,
            Case.patient_id == patient.id,
            Case.user_id == patient.user_id,
            ReportVersion.status == ReportStatus.ready,
            ReportVersion.is_archived == False,
            ReportVersion.report_type.in_(("assessment", "trend")),
        )
        .first()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    now = _now()
    source_key = str(report_id)
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
    db.refresh(row)
    return {
        "patient_id": patient.id,
        "report_id": source_key,
        "report_type": report.report_type,
        "released": bool(row.released and patient.claimed_by_user_id is not None),
        "available": patient.claimed_by_user_id is not None,
    }
