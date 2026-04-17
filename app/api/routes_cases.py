from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Case, Patient, ReportVersion, User
from app.schemas.cases import (
    CaseCreate,
    CaseRead,
    CaseUpdate,
    CreateFromImportRequest,
    ImportFromHtmlResponse,
)
from app.schemas.patients import PatientCreate
from app.schemas.reports import GenerateReportResponse
from app.services.audit_service import log_action
from app.services.case_service import create_case, update_case
from app.services.patient_service import create_patient
from app.services.report_service import generate_report_version
from app.services.scan_import_service import (
    parse_qrma_html,
    save_temp_html,
    load_temp_html,
    save_case_html,
)

router = APIRouter(prefix="/api/cases", tags=["cases"])


def make_json_safe(obj):
    from datetime import date, datetime

    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_safe(v) for v in obj]
    elif isinstance(obj, (date, datetime)):
        return obj.isoformat()
    else:
        return obj


def _get_owned_case(
    db: Session,
    current_user: User,
    case_id: UUID,
) -> Case | None:
    return (
        db.query(Case)
        .filter(Case.id == case_id, Case.user_id == current_user.id)
        .first()
    )


def _get_owned_patient(
    db: Session,
    current_user: User,
    patient_id: UUID,
) -> Patient | None:
    return (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )


@router.get("", response_model=list[CaseRead])
def list_cases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Case)
        .filter(Case.user_id == current_user.id)
        .order_by(Case.created_at.desc())
        .all()
    )


@router.get("/patient/{patient_id}", response_model=list[CaseRead])
def list_patient_cases(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = _get_owned_patient(db, current_user, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    return (
        db.query(Case)
        .filter(Case.patient_id == patient.id, Case.user_id == current_user.id)
        .order_by(Case.created_at.desc())
        .all()
    )


@router.post("", response_model=CaseRead)
def create_case_endpoint(
    payload: CaseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = _get_owned_patient(db, current_user, payload.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    case = create_case(db, current_user, patient, payload)
    log_action(db, "case_created", user_id=current_user.id, case_id=case.id)
    return case


@router.get("/{case_id}", response_model=CaseRead)
def get_case(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = _get_owned_case(db, current_user, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/{case_id}/status")
def get_case_status(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = _get_owned_case(db, current_user, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    return {
        "case_id": str(case.id),
        "patient_id": str(case.patient_id),
        "title": case.title,
        "status": case.status.value if hasattr(case.status, "value") else str(case.status),
        "recommendation_mode": (
            case.recommendation_mode.value
            if hasattr(case.recommendation_mode, "value")
            else str(case.recommendation_mode)
        ),
        "has_source_html": bool(case.raw_scan_html_path),
        "scan_datetime": case.scan_datetime,
        "updated_at": case.updated_at,
    }


@router.get("/{case_id}/latest-report")
def get_latest_report_for_case(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the latest report version for a case owned by the current user.
    This is intended for practitioner UI convenience and avoids the UI having
    to infer latest report state client-side.
    """
    case = (
        db.query(Case)
        .filter(Case.id == case_id, Case.user_id == current_user.id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    report = (
        db.query(ReportVersion)
        .filter(ReportVersion.case_id == case.id)
        .order_by(ReportVersion.version_number.desc(), ReportVersion.generated_at.desc())
        .first()
    )

    if not report:
        raise HTTPException(status_code=404, detail="No report found for this case")

    return {
        "id": report.id,
        "report_version_id": report.id,
        "case_id": report.case_id,
        "version_number": report.version_number,
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "html_path": report.html_path,
        "pdf_path": report.pdf_path,
        "recommendation_mode": (
            report.recommendation_mode.value
            if hasattr(report.recommendation_mode, "value")
            else str(report.recommendation_mode)
        ),
    }




@router.patch("/{case_id}", response_model=CaseRead)
def update_case_endpoint(
    case_id: UUID,
    payload: CaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = _get_owned_case(db, current_user, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return update_case(db, case, payload)


@router.post("/import-from-html", response_model=ImportFromHtmlResponse)
async def import_from_html(
    file: UploadFile = File(...),
):
    """
    Upload QRMA HTML, save it temporarily, and return parsed patient/scan metadata
    for practitioner confirmation before creating the patient/case.
    """
    content = await file.read()
    temp_id, _ = save_temp_html(content)
    parsed = parse_qrma_html(content)

    return {
        "source_patient_data": parsed.get("source_patient_data", {}),
        "scan_metadata": parsed.get("scan_metadata", {}),
        "temporary_upload_id": temp_id,
    }


@router.post("/create-from-import")
def create_from_import(
    payload: CreateFromImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create patient + case from a previously uploaded QRMA HTML temp file,
    then persist the HTML into case storage for later report generation.
    """
    patient_payload = PatientCreate(
        first_name=payload.patient.first_name or "",
        last_name=payload.patient.last_name or "",
        date_of_birth=payload.patient.date_of_birth,
        age=payload.patient.age,
        sex=payload.patient.sex,
        height_cm=payload.patient.height_cm,
        weight_kg=payload.patient.weight_kg,
    )
    patient = create_patient(db, current_user, patient_payload)
    case = create_case(db, current_user, patient, payload.case)

    temp_html_bytes = load_temp_html(payload.temporary_upload_id)
    raw_scan_html_path = save_case_html(
        case_id=str(case.id),
        user_id=str(current_user.id),
        file_bytes=temp_html_bytes,
    )

    case.source_patient_data_json = make_json_safe(payload.patient.model_dump())
    case.raw_scan_html_path = raw_scan_html_path
    db.commit()
    db.refresh(case)

    log_action(
        db,
        "patient_confirmed_from_scan",
        user_id=current_user.id,
        case_id=case.id,
        metadata_json={"temporary_upload_id": payload.temporary_upload_id},
    )

    return {
        "patient_id": patient.id,
        "case_id": case.id,
    }


@router.post("/{case_id}/generate-report", response_model=GenerateReportResponse)
async def generate_report(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a new report version for a case by loading the stored QRMA HTML,
    running the SaaS lifecycle-aware report pipeline, and building HTML/PDF output.
    """
    case = _get_owned_case(db, current_user, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if not case.raw_scan_html_path:
        raise HTTPException(
            status_code=400,
            detail="Case has no uploaded QRMA HTML file",
        )

    report = await generate_report_version(db, case, current_user)

    return {
        "report_version_id": report.id,
        "version_number": report.version_number,
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
    }