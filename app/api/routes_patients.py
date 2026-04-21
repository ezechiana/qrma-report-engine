from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Patient, User, Case, ReportVersion
from app.schemas.patients import PatientCreate, PatientRead, PatientUpdate
from app.services.patient_service import create_patient, update_patient

router = APIRouter(prefix="/api/patients", tags=["patients"])


def _serialise_patient(db: Session, patient: Patient) -> dict:
    case_count = (
        db.query(func.count(Case.id))
        .filter(Case.patient_id == patient.id, Case.user_id == patient.user_id)
        .scalar()
        or 0
    )

    report_count = (
        db.query(func.count(ReportVersion.id))
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(Case.patient_id == patient.id, Case.user_id == patient.user_id)
        .scalar()
        or 0
    )

    latest_case_activity = (
        db.query(func.max(Case.updated_at))
        .filter(Case.patient_id == patient.id, Case.user_id == patient.user_id)
        .scalar()
    )

    latest_report_activity = (
        db.query(func.max(ReportVersion.generated_at))
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(Case.patient_id == patient.id, Case.user_id == patient.user_id)
        .scalar()
    )

    activity_candidates = [dt for dt in [patient.updated_at, latest_case_activity, latest_report_activity] if dt]
    last_activity_at = max(activity_candidates) if activity_candidates else None

    data_sources = ["QRMA"] if case_count > 0 else []

    return {
        "id": patient.id,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "full_name": getattr(patient, "full_name", None) or f"{patient.first_name} {patient.last_name}".strip(),
        "date_of_birth": patient.date_of_birth,
        "age": patient.age,
        "sex": patient.sex,
        "height_cm": patient.height_cm,
        "weight_kg": patient.weight_kg,
        "created_at": patient.created_at,
        "updated_at": patient.updated_at,
        "case_count": case_count,
        "report_count": report_count,
        "last_activity_at": last_activity_at,
        "data_sources": data_sources,
    }


@router.get("", response_model=list[PatientRead])
def list_patients(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patients = (
        db.query(Patient)
        .filter(Patient.user_id == current_user.id)
        .order_by(Patient.created_at.desc())
        .all()
    )
    return [_serialise_patient(db, patient) for patient in patients]


@router.post("", response_model=PatientRead)
def create_patient_endpoint(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = create_patient(db, current_user, payload)
    return _serialise_patient(db, patient)


@router.get("/{patient_id}", response_model=PatientRead)
def get_patient(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _serialise_patient(db, patient)


@router.patch("/{patient_id}", response_model=PatientRead)
def update_patient_endpoint(
    patient_id: UUID,
    payload: PatientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    updated = update_patient(db, patient, payload)
    return _serialise_patient(db, updated)


@router.delete("/{patient_id}")
def delete_patient(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    linked_cases = (
        db.query(func.count(Case.id))
        .filter(Case.patient_id == patient.id, Case.user_id == current_user.id)
        .scalar()
        or 0
    )

    if linked_cases > 0:
        raise HTTPException(
            status_code=409,
            detail="This patient has linked cases and cannot be deleted yet.",
        )

    db.delete(patient)
    db.commit()
    return {"ok": True}


@router.get("/{patient_id}/cases")
def list_patient_cases(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    cases = (
        db.query(Case)
        .filter(Case.patient_id == patient.id, Case.user_id == current_user.id)
        .order_by(Case.created_at.desc())
        .all()
    )

    return [
        {
            "id": case.id,
            "title": case.title,
            "display_name": getattr(case, "title", None) or f"Case {str(case.id)[:8]}",
            "status": case.status.value if hasattr(case.status, "value") else str(case.status),
            "created_at": case.created_at,
            "updated_at": case.updated_at,
            "scan_datetime": getattr(case, "scan_datetime", None),
        }
        for case in cases
    ]


@router.get("/{patient_id}/reports")
def list_patient_reports(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    reports = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(Case.patient_id == patient.id, Case.user_id == current_user.id)
        .order_by(ReportVersion.generated_at.desc())
        .all()
    )

    return [
        {
            "id": report.id,
            "case_id": report.case_id,
            "version_number": report.version_number,
            "status": report.status.value if hasattr(report.status, "value") else str(report.status),
            "generated_at": report.generated_at,
            "display_name": getattr(report.case, "title", None) or f"Report {str(report.id)[:8]}",
            "scan_datetime": getattr(report.case, "scan_datetime", None) if getattr(report, "case", None) else None,
        }
        for report in reports
    ]