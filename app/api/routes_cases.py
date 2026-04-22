from uuid import UUID

from app.api.routes_reports import _format_scan_datetime
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
from app.schemas.cases import CaseCreateFromImport
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
from datetime import date, datetime
router = APIRouter(prefix="/api/cases", tags=["cases"])


def make_json_safe(obj):
   

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


def _patient_display_name(patient: Patient | None, source_patient_data_json: dict | None = None) -> str | None:
    if patient:
        full_name = getattr(patient, "full_name", None)
        if full_name:
            return full_name

        first_name = getattr(patient, "first_name", None)
        last_name = getattr(patient, "last_name", None)
        joined = " ".join(part for part in [first_name, last_name] if part)
        if joined:
            return joined

    source_patient_data_json = source_patient_data_json or {}
    full_name = source_patient_data_json.get("full_name")
    if full_name:
        return full_name

    first_name = source_patient_data_json.get("first_name")
    last_name = source_patient_data_json.get("last_name")
    joined = " ".join(part for part in [first_name, last_name] if part)
    return joined or None


def _case_display_name(case: Case) -> str:
    patient_name = _patient_display_name(
        getattr(case, "patient", None),
        case.source_patient_data_json or {},
    )

    if case.scan_datetime:
        date_text = case.scan_datetime.strftime("%d %b %Y")
    else:
        date_text = case.created_at.strftime("%d %b %Y") if case.created_at else None

    if patient_name and date_text:
        return f"{patient_name} — {date_text}"
    if patient_name:
        return patient_name
    if case.title:
        return case.title
    return f"Case {str(case.id)[:8]}"


def _serialize_case(case: Case) -> dict:
    patient_name = _patient_display_name(
        getattr(case, "patient", None),
        case.source_patient_data_json or {},
    )

    return {
        "id": case.id,
        "patient_id": case.patient_id,
        "title": case.title,
        "display_name": _case_display_name(case),
        "patient_display_name": patient_name,
        "status": case.status.value if hasattr(case.status, "value") else str(case.status),
        "recommendation_mode": (
            case.recommendation_mode.value
            if hasattr(case.recommendation_mode, "value")
            else str(case.recommendation_mode)
        ),
        "clinical_context_json": case.clinical_context_json,
        "source_patient_data_json": case.source_patient_data_json,
        "raw_scan_html_path": case.raw_scan_html_path,
        "scan_datetime": case.scan_datetime,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


@router.get("", response_model=list[CaseRead])
def list_cases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cases = (
        db.query(Case)
        .filter(Case.user_id == current_user.id)
        .order_by(Case.created_at.desc())
        .all()
    )
    return [_serialize_case(case) for case in cases]


@router.get("/patient/{patient_id}", response_model=list[CaseRead])
def list_patient_cases(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = _get_owned_patient(db, current_user, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    cases = (
        db.query(Case)
        .filter(Case.patient_id == patient.id, Case.user_id == current_user.id)
        .order_by(Case.created_at.desc())
        .all()
    )
    return [_serialize_case(case) for case in cases]


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
    return _serialize_case(case)


@router.get("/{case_id}", response_model=CaseRead)
def get_case(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = _get_owned_case(db, current_user, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return _serialize_case(case)


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
        "display_name": _case_display_name(case),
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
        "display_name": _case_display_name(case),
        "patient_display_name": _patient_display_name(
            getattr(case, "patient", None),
            case.source_patient_data_json or {},
        ),
        "scan_datetime": case.scan_datetime.isoformat() if case.scan_datetime else None,
            "scan_datetime_display": _format_scan_datetime(case),
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
    updated = update_case(db, case, payload)
    return _serialize_case(updated)


@router.post("/import-from-html", response_model=ImportFromHtmlResponse)
async def import_from_html(
    file: UploadFile = File(...),
):
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
    # Use an existing patient if chosen; otherwise create a new one
    if payload.existing_patient_id:
        patient = _get_owned_patient(db, current_user, payload.existing_patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Selected patient not found")

        # Optional explicit overwrite of patient profile measurements
        if payload.update_patient_measurements:
            if payload.patient.height_cm is not None:
                patient.height_cm = payload.patient.height_cm
            if payload.patient.weight_kg is not None:
                patient.weight_kg = payload.patient.weight_kg

            # keep age in sync if DOB supplied
            if payload.patient.date_of_birth is not None:
                patient.date_of_birth = payload.patient.date_of_birth

            db.flush()
    else:
        def _normalise_sex(value):
            if not value:
                return None

            v = str(value).strip().lower()

            mapping = {
                "female": "female",
                "f": "female",
                "male": "male",
                "m": "male",
                "other": "other",
                "prefer not to say": "prefer_not_to_say",
                "prefer_not_to_say": "prefer_not_to_say",
            }

            return mapping.get(v, None)


        def _normalise_sex(value):
            if not value:
                return None

            v = str(value).strip().lower()

            mapping = {
                "female": "female",
                "f": "female",
                "male": "male",
                "m": "male",
                "other": "other",
                "prefer not to say": "prefer_not_to_say",
                "prefer_not_to_say": "prefer_not_to_say",
            }

            return mapping.get(v, None)


        patient_payload = PatientCreate(
            first_name=(payload.patient.first_name or "").strip(),
            last_name=(payload.patient.last_name or "").strip(),
            date_of_birth=payload.patient.date_of_birth,
            sex=_normalise_sex(payload.patient.sex),  # ✅ FIX
            height_cm=payload.patient.height_cm,
            weight_kg=payload.patient.weight_kg,
        )

        patient = create_patient(db, current_user, patient_payload)

    case = create_case(db, current_user, patient, payload.case)

    try:
        temp_html_bytes = load_temp_html(payload.temporary_upload_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail="Temporary upload expired. Please upload the file again."
        )

    raw_scan_html_path = save_case_html(
        case_id=str(case.id),
        user_id=str(current_user.id),
        file_bytes=temp_html_bytes,
    )

    # Always keep the imported values as part of the scan/case snapshot
    case.source_patient_data_json = make_json_safe(payload.patient.model_dump())
    case.raw_scan_html_path = raw_scan_html_path

    # Re-parse server-side from the uploaded HTML so scan datetime does not depend
    # on what the browser sent back.
    reparsed = parse_qrma_html(temp_html_bytes)
    reparsed_scan_metadata = reparsed.get("scan_metadata", {}) or {}
    reparsed_scan_datetime = reparsed_scan_metadata.get("scan_datetime")

    if reparsed_scan_datetime:
        case.scan_datetime = reparsed_scan_datetime
    else:
        # Fallback to client payload only if server-side parse did not produce a datetime
        scan_metadata = getattr(payload, "scan_metadata", None)
        raw_scan_datetime = getattr(scan_metadata, "scan_datetime", None) if scan_metadata else None

        if raw_scan_datetime:
            if isinstance(raw_scan_datetime, datetime):
                case.scan_datetime = raw_scan_datetime
            elif isinstance(raw_scan_datetime, str):
                for fmt in (
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M",
                    "%d/%m/%Y %H:%M",
                    "%d/%m/%Y %H:%M:%S",
                ):
                    try:
                        case.scan_datetime = datetime.strptime(raw_scan_datetime, fmt)
                        break
                    except Exception:
                        continue

    db.commit()
    db.refresh(case)

    log_action(
        db,
        "patient_confirmed_from_scan",
        user_id=current_user.id,
        case_id=case.id,
        metadata_json={
            "temporary_upload_id": payload.temporary_upload_id,
            "linked_existing_patient": bool(payload.existing_patient_id),
            "existing_patient_id": str(payload.existing_patient_id) if payload.existing_patient_id else None,
            "update_patient_measurements": payload.update_patient_measurements,
        },
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


@router.post("/from-existing-patient-import")
def create_from_existing_patient_import(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):

    patient_id = payload.get("patient_id")
    temporary_upload_id = payload.get("temporary_upload_id")
    case_payload = payload.get("case", {})
    scan_metadata = payload.get("scan_metadata")

    if not patient_id:
        raise HTTPException(status_code=400, detail="Missing patient_id")

    patient = db.query(Patient).filter(
        Patient.id == patient_id,
        Patient.user_id == current_user.id
    ).first()

    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    case = create_case(
        db,
        current_user,
        patient,
        CaseCreateFromImport(**case_payload)
    )

    # Attach HTML
    html_bytes = load_temp_html(temporary_upload_id)
    path = save_case_html(
        case_id=str(case.id),
        user_id=str(current_user.id),
        file_bytes=html_bytes,
    )

    case.raw_scan_html_path = path

    # Persist scan datetime if available
    # Persist scan datetime from server-side parse first
    reparsed = parse_qrma_html(html_bytes)
    reparsed_scan_metadata = reparsed.get("scan_metadata", {}) or {}
    reparsed_scan_datetime = reparsed_scan_metadata.get("scan_datetime")

    if reparsed_scan_datetime:
        case.scan_datetime = reparsed_scan_datetime
    elif scan_metadata and scan_metadata.get("scan_datetime"):
        raw_scan_datetime = scan_metadata.get("scan_datetime")

        if isinstance(raw_scan_datetime, datetime):
            case.scan_datetime = raw_scan_datetime
        elif isinstance(raw_scan_datetime, str):
            for fmt in (
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M",
                "%d/%m/%Y %H:%M:%S",
            ):
                try:
                    case.scan_datetime = datetime.strptime(raw_scan_datetime, fmt)
                    break
                except Exception:
                    continue

    db.commit()
    db.refresh(case)

    return {
        "case_id": case.id
    }