from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.api.deps_saas_v1 import CurrentDB, CurrentPractitioner
from app.models.saas_v1 import (
    Case,
    CaseStatus,
    EventLog,
    FeedbackItem,
    Patient,
    ReportVersion,
    ReportVersionStatus,
    SharedReport,
)
from app.schemas.saas_v1 import (
    APIMessage,
    CaseCreate,
    CaseListItem,
    CaseListResponse,
    CaseRead,
    CaseUpdate,
    FeedbackCreate,
    FeedbackRead,
    GenerateReportResponse,
    PatientCreate,
    PatientListItem,
    PatientListResponse,
    PatientRead,
    PatientUpdate,
    ReportListItem,
    ReportListResponse,
    ReportVersionRead,
    ReportViewerResponse,
    ShareReportRequest,
    ShareReportResponse,
)

router = APIRouter(prefix="/v1", tags=["saas-v1"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@router.get("/patients", response_model=PatientListResponse)
def list_patients(db: CurrentDB, practitioner: CurrentPractitioner) -> PatientListResponse:
    rows = (
        db.query(
            Patient.id,
            Patient.full_name,
            Patient.sex,
            Patient.date_of_birth,
            Patient.created_at,
            func.count(ReportVersion.id).label("report_count"),
        )
        .outerjoin(Case, Case.patient_id == Patient.id)
        .outerjoin(ReportVersion, ReportVersion.case_id == Case.id)
        .filter(Patient.practitioner_id == practitioner.id)
        .group_by(Patient.id)
        .order_by(Patient.created_at.desc())
        .all()
    )

    return PatientListResponse(
        items=[
            PatientListItem(
                id=row.id,
                full_name=row.full_name,
                sex=row.sex,
                date_of_birth=row.date_of_birth,
                created_at=row.created_at,
                report_count=row.report_count or 0,
            )
            for row in rows
        ]
    )


@router.post("/patients", response_model=PatientRead, status_code=status.HTTP_201_CREATED)
def create_patient(payload: PatientCreate, db: CurrentDB, practitioner: CurrentPractitioner) -> Patient:
    patient = Patient(
        practitioner_id=practitioner.id,
        external_ref=payload.external_ref,
        full_name=payload.full_name,
        sex=payload.sex,
        date_of_birth=payload.date_of_birth,
        email=payload.email,
        phone=payload.phone,
        notes=payload.notes,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


@router.get("/patients/{patient_id}", response_model=PatientRead)
def get_patient(patient_id: UUID, db: CurrentDB, practitioner: CurrentPractitioner) -> Patient:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.practitioner_id == practitioner.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return patient


@router.patch("/patients/{patient_id}", response_model=PatientRead)
def update_patient(
    patient_id: UUID,
    payload: PatientUpdate,
    db: CurrentDB,
    practitioner: CurrentPractitioner,
) -> Patient:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.practitioner_id == practitioner.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)

    db.commit()
    db.refresh(patient)
    return patient


@router.delete("/patients/{patient_id}", response_model=APIMessage)
def delete_patient(patient_id: UUID, db: CurrentDB, practitioner: CurrentPractitioner) -> APIMessage:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.practitioner_id == practitioner.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    db.delete(patient)
    db.commit()
    return APIMessage(message="Patient deleted.")


@router.get("/cases", response_model=CaseListResponse)
def list_cases(db: CurrentDB, practitioner: CurrentPractitioner) -> CaseListResponse:
    rows = (
        db.query(Case, Patient.full_name.label("patient_name"))
        .join(Patient, Patient.id == Case.patient_id)
        .filter(Case.practitioner_id == practitioner.id)
        .order_by(Case.created_at.desc())
        .all()
    )

    return CaseListResponse(
        items=[
            CaseListItem(
                id=case.id,
                patient_id=case.patient_id,
                patient_name=patient_name,
                title=case.title,
                recommendation_mode=case.recommendation_mode,
                status=case.status.value if hasattr(case.status, "value") else str(case.status),
                created_at=case.created_at,
            )
            for case, patient_name in rows
        ]
    )


@router.post("/cases", response_model=CaseRead, status_code=status.HTTP_201_CREATED)
def create_case(payload: CaseCreate, db: CurrentDB, practitioner: CurrentPractitioner) -> Case:
    patient = (
        db.query(Patient)
        .filter(Patient.id == payload.patient_id, Patient.practitioner_id == practitioner.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    case = Case(
        practitioner_id=practitioner.id,
        patient_id=payload.patient_id,
        title=payload.title,
        source_type=payload.source_type,
        recommendation_mode=payload.recommendation_mode,
        status=CaseStatus.draft,
        intake_payload_json=payload.intake_payload_json,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.get("/cases/{case_id}", response_model=CaseRead)
def get_case(case_id: UUID, db: CurrentDB, practitioner: CurrentPractitioner) -> Case:
    case = (
        db.query(Case)
        .filter(Case.id == case_id, Case.practitioner_id == practitioner.id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case


@router.patch("/cases/{case_id}", response_model=CaseRead)
def update_case(
    case_id: UUID,
    payload: CaseUpdate,
    db: CurrentDB,
    practitioner: CurrentPractitioner,
) -> Case:
    case = (
        db.query(Case)
        .filter(Case.id == case_id, Case.practitioner_id == practitioner.id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(case, field, value)

    db.commit()
    db.refresh(case)
    return case


@router.post("/cases/{case_id}/generate-report", response_model=GenerateReportResponse)
def generate_report(case_id: UUID, db: CurrentDB, practitioner: CurrentPractitioner) -> GenerateReportResponse:
    case = (
        db.query(Case)
        .filter(Case.id == case_id, Case.practitioner_id == practitioner.id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")

    latest_version = (
        db.query(func.max(ReportVersion.version_number))
        .filter(ReportVersion.case_id == case.id)
        .scalar()
    ) or 0

    report = ReportVersion(
        case_id=case.id,
        practitioner_id=practitioner.id,
        version_number=latest_version + 1,
        status=ReportVersionStatus.queued,
        recommendation_mode=case.recommendation_mode,
        report_json=None,
        html_path=None,
        pdf_path=None,
        build_version=None,
        job_id=None,
    )
    db.add(report)

    case.status = CaseStatus.queued

    db.add(
        EventLog(
            practitioner_id=practitioner.id,
            patient_id=case.patient_id,
            case_id=case.id,
            report_version_id=report.id,
            event_type="report_queued",
            metadata_json={"version_number": latest_version + 1},
        )
    )

    db.commit()
    db.refresh(report)

    # Later: enqueue background job here

    return GenerateReportResponse(
        case_id=case.id,
        report_version_id=report.id,
        status=report.status.value,
    )


@router.get("/reports", response_model=ReportListResponse)
def list_reports(db: CurrentDB, practitioner: CurrentPractitioner) -> ReportListResponse:
    rows = (
        db.query(
            ReportVersion,
            Patient.id.label("patient_id"),
            Patient.full_name.label("patient_name"),
        )
        .join(Case, Case.id == ReportVersion.case_id)
        .join(Patient, Patient.id == Case.patient_id)
        .filter(ReportVersion.practitioner_id == practitioner.id)
        .order_by(ReportVersion.created_at.desc())
        .all()
    )

    return ReportListResponse(
        items=[
            ReportListItem(
                id=report.id,
                case_id=report.case_id,
                patient_id=patient_id,
                patient_name=patient_name,
                version_number=report.version_number,
                status=report.status.value if hasattr(report.status, "value") else str(report.status),
                recommendation_mode=report.recommendation_mode,
                created_at=report.created_at,
            )
            for report, patient_id, patient_name in rows
        ]
    )


@router.get("/reports/{report_id}", response_model=ReportVersionRead)
def get_report(report_id: UUID, db: CurrentDB, practitioner: CurrentPractitioner) -> ReportVersion:
    report = (
        db.query(ReportVersion)
        .filter(ReportVersion.id == report_id, ReportVersion.practitioner_id == practitioner.id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    return report


@router.get("/reports/{report_id}/viewer", response_model=ReportViewerResponse)
def get_report_viewer(report_id: UUID, db: CurrentDB, practitioner: CurrentPractitioner) -> ReportViewerResponse:
    report = (
        db.query(ReportVersion)
        .filter(ReportVersion.id == report_id, ReportVersion.practitioner_id == practitioner.id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    report_json = report.report_json or {}
    return ReportViewerResponse(
        id=report.id,
        case_id=report.case_id,
        status=report.status.value if hasattr(report.status, "value") else str(report.status),
        recommendation_mode=report.recommendation_mode,
        viewer=report_json.get("viewer"),
        pdf_url=f"/api/v1/reports/{report.id}/pdf" if report.pdf_path else None,
        html_url=f"/api/v1/reports/{report.id}/html" if report.html_path else None,
    )


@router.post("/reports/{report_id}/share", response_model=ShareReportResponse)
def share_report(
    report_id: UUID,
    payload: ShareReportRequest,
    db: CurrentDB,
    practitioner: CurrentPractitioner,
) -> ShareReportResponse:
    report = (
        db.query(ReportVersion)
        .filter(ReportVersion.id == report_id, ReportVersion.practitioner_id == practitioner.id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)

    expires_at = None
    if payload.expires_in_days:
        expires_at = _utcnow() + timedelta(days=payload.expires_in_days)

    shared = SharedReport(
        report_version_id=report.id,
        token_hash=token_hash,
        is_active=True,
        expires_at=expires_at,
    )
    db.add(shared)
    db.commit()

    db.add(
        EventLog(
            practitioner_id=practitioner.id,
            case_id=report.case_id,
            report_version_id=report.id,
            event_type="report_shared",
            metadata_json={"expires_at": expires_at.isoformat() if expires_at else None},
        )
    )
    db.commit()

    return ShareReportResponse(
        share_url=f"https://app.example.com/share/{raw_token}",
        expires_at=expires_at,
    )


@router.post("/reports/{report_id}/revoke-share", response_model=APIMessage)
def revoke_report_share(report_id: UUID, db: CurrentDB, practitioner: CurrentPractitioner) -> APIMessage:
    shares = (
        db.query(SharedReport)
        .join(ReportVersion, ReportVersion.id == SharedReport.report_version_id)
        .filter(ReportVersion.id == report_id, ReportVersion.practitioner_id == practitioner.id)
        .filter(SharedReport.is_active.is_(True))
        .all()
    )

    for share in shares:
        share.is_active = False
        share.revoked_at = _utcnow()

    db.add(
        EventLog(
            practitioner_id=practitioner.id,
            report_version_id=report_id,
            event_type="report_share_revoked",
            metadata_json={"revoked_count": len(shares)},
        )
    )
    db.commit()

    return APIMessage(message="Share links revoked.")


@router.post("/reports/{report_id}/feedback", response_model=FeedbackRead, status_code=status.HTTP_201_CREATED)
def create_feedback(
    report_id: UUID,
    payload: FeedbackCreate,
    db: CurrentDB,
    practitioner: CurrentPractitioner,
) -> FeedbackItem:
    report = (
        db.query(ReportVersion)
        .filter(ReportVersion.id == report_id, ReportVersion.practitioner_id == practitioner.id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    item = FeedbackItem(
        practitioner_id=practitioner.id,
        report_version_id=report.id,
        section_key=payload.section_key,
        marker_key=payload.marker_key,
        sentiment=payload.sentiment,
        comment=payload.comment,
    )
    db.add(item)
    db.add(
        EventLog(
            practitioner_id=practitioner.id,
            report_version_id=report.id,
            event_type="feedback_created",
            metadata_json={
                "section_key": payload.section_key,
                "marker_key": payload.marker_key,
                "sentiment": payload.sentiment,
            },
        )
    )
    db.commit()
    db.refresh(item)
    return item