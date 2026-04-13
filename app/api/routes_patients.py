#app/api/routes_patients.py

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Patient, User
from app.schemas.patients import PatientCreate, PatientRead, PatientUpdate
from app.services.patient_service import create_patient, update_patient

router = APIRouter(prefix="/api/patients", tags=["patients"])


@router.get("", response_model=list[PatientRead])
def list_patients(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Patient).filter(Patient.user_id == current_user.id).order_by(Patient.created_at.desc()).all()


@router.post("", response_model=PatientRead)
def create_patient_endpoint(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return create_patient(db, current_user, payload)


@router.get("/{patient_id}", response_model=PatientRead)
def get_patient(patient_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    patient = db.query(Patient).filter(Patient.id == patient_id, Patient.user_id == current_user.id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.patch("/{patient_id}", response_model=PatientRead)
def update_patient_endpoint(
    patient_id: UUID,
    payload: PatientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = db.query(Patient).filter(Patient.id == patient_id, Patient.user_id == current_user.id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return update_patient(db, patient, payload)

