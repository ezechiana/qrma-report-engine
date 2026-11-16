# app/api/routes_patient_records.py

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_
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
        .filter(
            Patient.user_id == current_user.id,
            or_(Patient.subject_user_id.is_(None), Patient.subject_user_id != current_user.id),
        )
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
