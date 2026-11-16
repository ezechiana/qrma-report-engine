"""Signed-in client access to a claimed longitudinal record.

The progress view is the client-safe trend already built for a practitioner.
It is included only when this clinic has released it. Share links do not
decide what this portal shows, and opening one does not change the release.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.share_helpers import _patient_display_name
from app.db.models import Patient, ReportVersion, User
from app.services.content_release_service import progress_release_state, released_client_reports
from app.services.patient_source_service import released_client_sources

_EMPTY_CLIENT_VIEW = {
    "summary": "Your practitioner has not released a progress view yet.",
    "progress_cards": [],
    "top_priorities": [],
    "recommendations": [],
    "review_note": "Client-safe summary. Detailed marker interpretation should be reviewed with your practitioner.",
}


def _clinic_name(practitioner: User | None) -> str:
    if practitioner is None:
        return "Your clinic"
    return (practitioner.clinic_name or practitioner.full_name or "Your clinic").strip()


def claimed_patients(db: Session, client: User) -> list[Patient]:
    rows = (
        db.query(Patient)
        .filter(Patient.claimed_by_user_id == client.id)
        .order_by(Patient.full_name.asc())
        .all()
    )
    from app.services.clinic_association_service import _association_table
    from app.services.personal_record_service import personal_record_for
    from app.db.models import ClinicAssociation

    if not _association_table(db):
        return rows
    personal = personal_record_for(db, client)
    if personal is None:
        return rows
    linked_ids = [
        row.clinic_patient_id
        for row in db.query(ClinicAssociation)
        .filter(
            ClinicAssociation.personal_patient_id == personal.id,
            ClinicAssociation.status == "active",
            ClinicAssociation.clinic_patient_id.is_not(None),
            ClinicAssociation.clinic_patient_id != personal.id,
        )
        .all()
        if row.clinic_patient_id is not None
    ]
    if not linked_ids:
        return rows
    known = {row.id for row in rows}
    extra = (
        db.query(Patient)
        .filter(Patient.id.in_(linked_ids))
        .all()
    )
    combined = rows + [row for row in extra if row.id not in known]
    combined.sort(key=lambda row: (row.full_name or "").lower())
    return combined


def client_connection_status(db: Session, user: User) -> str:
    """connected, pending, or none.

    The sidebar only needs this word. Clinic names, other people, and health
    values stay out of the response.
    """
    if claimed_patients(db, user):
        return "connected"
    from app.db.models import ClinicAssociation
    from app.services.clinic_association_service import _association_table
    from app.services.personal_record_service import personal_record_for

    if not _association_table(db):
        return "none"
    personal = personal_record_for(db, user)
    if personal is None:
        return "none"
    pending = (
        db.query(ClinicAssociation.id)
        .filter(
            ClinicAssociation.personal_patient_id == personal.id,
            ClinicAssociation.status == "pending",
        )
        .first()
    )
    return "pending" if pending is not None else "none"


_ITEM_LABELS = {
    "manual": "Clinic note",
    "laboratory": "Lab panel",
    "wearable": "Wearable day",
    "client": "From the client",
}


def latest_item_summary(db: Session, patient: Patient, *, audience: str) -> dict:
    """Newest item type and date for a list card.

    audience is owner, clinic, or client. The result has no title, note text,
    or lab value.
    """
    found = _newest_item(db, patient, audience)
    if found is None:
        return {"latest_type": None, "latest_on": None}
    label, moment = found
    return {"latest_type": label, "latest_on": moment.date().isoformat()}


def _newest_item(db: Session, patient: Patient, audience: str):
    from datetime import datetime

    from sqlalchemy import inspect

    from app.db.models import Case, ContentRelease, PatientSource, ReportVersion, SourceRecord
    from app.db.models import ReportStatus
    from app.services.clinic_association_service import sharing_practitioner_id
    from app.services.content_release_service import CLINIC_TO_CLIENT

    bind = db.get_bind()
    has_sources = inspect(bind).has_table("source_records") and inspect(bind).has_table("patient_sources")
    has_reports = inspect(bind).has_table("report_versions") and inspect(bind).has_table("cases")
    has_releases = inspect(bind).has_table("content_releases")
    best: tuple[datetime, str] | None = None

    def consider(moment, label: str) -> None:
        nonlocal best
        if moment is None or not label:
            return
        if getattr(moment, "tzinfo", None) is not None:
            moment = moment.replace(tzinfo=None)
        if best is None or moment > best[0]:
            best = (moment, label)

    def release_keys(patient_id, practitioner_id, direction: str) -> set[str]:
        if not has_releases or practitioner_id is None:
            return set()
        rows = (
            db.query(ContentRelease.source_key)
            .filter(
                ContentRelease.patient_id == patient_id,
                ContentRelease.practitioner_user_id == practitioner_id,
                ContentRelease.direction == direction,
                ContentRelease.released.is_(True),
            )
            .all()
        )
        return {row[0] for row in rows}

    def source_key(source_type: str, record_id) -> str:
        if source_type == "wearable":
            return f"wearable:{record_id}"
        if source_type == "client":
            return f"client:{record_id}"
        return f"record:{record_id}"

    def scan_sources(patient_id, *, released: set[str] | None, owner_note: bool) -> None:
        if not has_sources:
            return
        rows = (
            db.query(PatientSource.source_type, SourceRecord.id, SourceRecord.recorded_at)
            .join(PatientSource, PatientSource.id == SourceRecord.patient_source_id)
            .filter(
                SourceRecord.patient_id == patient_id,
                SourceRecord.is_archived.is_(False),
            )
            .all()
        )
        for source_type, record_id, recorded_at in rows:
            if released is not None and source_key(source_type, record_id) not in released:
                continue
            label = "Note" if owner_note and source_type == "manual" else _ITEM_LABELS.get(source_type)
            consider(recorded_at, label or "")

    if audience == "owner":
        scan_sources(patient.id, released=None, owner_note=True)
        return best

    if audience == "client":
        practitioner_id = sharing_practitioner_id(db, patient)
        if practitioner_id is None:
            return None
        scan_sources(
            patient.id,
            released=release_keys(patient.id, practitioner_id, CLINIC_TO_CLIENT),
            owner_note=False,
        )
        released_reports = release_keys(patient.id, practitioner_id, CLINIC_TO_CLIENT)
    else:
        scan_sources(patient.id, released=None, owner_note=False)
        released_reports = None
        _include_released_personal_items(db, patient, consider, has_sources, has_releases)

    if has_reports:
        reports = (
            db.query(ReportVersion.id, ReportVersion.generated_at)
            .join(Case, Case.id == ReportVersion.case_id)
            .filter(
                Case.patient_id == patient.id,
                Case.user_id == patient.user_id,
                ReportVersion.status == ReportStatus.ready,
                ReportVersion.is_archived.is_(False),
                ReportVersion.report_type.in_(("assessment", "trend")),
            )
            .all()
        )
        for report_id, generated_at in reports:
            if released_reports is not None and str(report_id) not in released_reports:
                continue
            consider(generated_at, "Report")
    return best


def _include_released_personal_items(db, patient: Patient, consider, has_sources: bool, has_releases: bool) -> None:
    """Released items that live on the linked personal record, type and date only."""
    if not has_sources or not has_releases:
        return
    from app.db.models import ClinicAssociation, ContentRelease, PatientSource, SourceRecord
    from app.services.clinic_association_service import _association_table

    if not _association_table(db):
        return
    link = (
        db.query(ClinicAssociation.personal_patient_id)
        .filter(
            ClinicAssociation.clinic_patient_id == patient.id,
            ClinicAssociation.status == "active",
            ClinicAssociation.personal_patient_id.is_not(None),
            ClinicAssociation.personal_patient_id != patient.id,
        )
        .first()
    )
    if link is None or link[0] is None:
        return
    personal_id = link[0]
    released = {
        row[0]
        for row in db.query(ContentRelease.source_key)
        .filter(
            ContentRelease.patient_id == personal_id,
            ContentRelease.practitioner_user_id == patient.user_id,
            ContentRelease.direction == "client_to_clinic",
            ContentRelease.released.is_(True),
        )
        .all()
    }
    if not released:
        return
    rows = (
        db.query(PatientSource.source_type, SourceRecord.id, SourceRecord.recorded_at)
        .join(PatientSource, PatientSource.id == SourceRecord.patient_source_id)
        .filter(
            SourceRecord.patient_id == personal_id,
            SourceRecord.is_archived.is_(False),
        )
        .all()
    )
    for source_type, record_id, recorded_at in rows:
        key = f"wearable:{record_id}" if source_type == "wearable" else (
            f"client:{record_id}" if source_type == "client" else f"record:{record_id}"
        )
        if key not in released:
            continue
        label = "Note" if source_type == "manual" else _ITEM_LABELS.get(source_type)
        consider(recorded_at, label or "")


def _project_client_trend(payload: dict) -> dict:
    """Keep the fields the shared trend page reads, and leave practitioner controls out."""
    intelligence = payload.get("trend_intelligence") if isinstance(payload.get("trend_intelligence"), dict) else {}
    summary = payload.get("trend_summary") if isinstance(payload.get("trend_summary"), list) else []
    return {
        "view_mode": "client",
        "client_view": payload.get("client_view") or _EMPTY_CLIENT_VIEW,
        "health_index": payload.get("health_index") or [],
        "trend_summary": [row for row in summary if isinstance(row, dict) and row.get("is_meaningful") is True],
        "trend_intelligence": {
            key: intelligence.get(key)
            for key in ("confidence", "insights", "cards", "top_priorities", "clinical_narrative")
            if key in intelligence
        },
    }


def _empty_trend() -> dict:
    return {
        "view_mode": "client",
        "client_view": dict(_EMPTY_CLIENT_VIEW),
        "health_index": [],
        "trend_summary": [],
        "trend_intelligence": {},
    }


def client_record_payload(db: Session, client: User, patient: Patient) -> dict:
    practitioner = db.query(User).filter(User.id == patient.user_id).first()
    if not progress_release_state(db, patient)["released"]:
        trend = _empty_trend()
    else:
        from app.api.routes_patients import get_patient_trends

        trend_payload = get_patient_trends(
            patient_id=patient.id,
            include_archived=False,
            view_mode="client",
            source_report_ids=None,
            db=db,
            current_user=SimpleNamespace(id=patient.user_id),
        )
        trend = _project_client_trend(trend_payload)
    return {
        "viewer_mode": "patient",
        "patient": {
            "id": str(patient.id),
            "display_name": _patient_display_name(patient),
        },
        "tenant": {
            "tenant_name": _clinic_name(practitioner),
        },
        "trend": trend,
        "reports": released_client_reports(db, patient),
        "sources": released_client_sources(db, patient),
    }


def require_claimed_patient(db: Session, client: User, patient_id: UUID) -> Patient:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.claimed_by_user_id == client.id)
        .first()
    )
    if patient is not None:
        return patient
    from app.services.clinic_association_service import clinic_chart_for_subject

    linked = clinic_chart_for_subject(db, client, patient_id)
    if linked is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return linked


def _narrative(value) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _client_safe_report_text(report: ReportVersion) -> tuple[str, list[dict]]:
    """Narrative text from the client viewer. Markers, notes, and products are left out."""
    raw = report.report_json if isinstance(report.report_json, dict) else {}
    viewer = raw.get("viewer") if isinstance(raw.get("viewer"), dict) else {}
    overview = viewer.get("overview") if isinstance(viewer.get("overview"), dict) else {}
    overall = overview.get("overall_summary")
    summary = ""
    if isinstance(overall, str):
        summary = overall.strip()
    elif isinstance(overall, dict):
        summary = _narrative(overall.get("clinical_snapshot")) or _narrative(overall.get("summary"))
    sections = []
    why = _narrative(overview.get("why_this_matters"))
    if why:
        sections.append({"title": "Why this matters", "body": why})
    pattern = overview.get("primary_pattern")
    if isinstance(pattern, dict):
        body = _narrative(pattern.get("clinical_summary")) or _narrative(pattern.get("summary"))
        if body:
            sections.append({"title": "Pattern", "body": body})
    return summary, sections


def client_released_report(db: Session, patient: Patient, report_id: UUID) -> dict:
    """One released report for the client who claimed this record.

    Handles patient identity. An unreleased report is not found. Share links
    are not created.
    """
    visible = released_client_reports(db, patient)
    report_key = str(report_id)
    report_type = None
    label = None
    for item in visible["assessment_reports"]:
        if item["report_id"] == report_key:
            report_type = "assessment"
            label = item["label"]
    for item in visible["trend_reports"]:
        if item["report_id"] == report_key:
            report_type = "trend"
            label = item["label"]
    if report_type is None:
        raise HTTPException(status_code=404, detail="Report not found")
    report = db.query(ReportVersion).filter(ReportVersion.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    summary, sections = _client_safe_report_text(report)
    return {
        "report_id": report_key,
        "label": label,
        "report_type": report_type,
        "summary": summary,
        "sections": sections,
    }
