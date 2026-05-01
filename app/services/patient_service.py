#app/services/patient_service.py
from sqlalchemy.orm import Session

from app.db.models import Patient, User
from app.schemas.patients import PatientCreate, PatientUpdate

from datetime import date
from app.db.models import Patient

def build_full_name(first_name: str, last_name: str) -> str:
    return f"{first_name.strip()} {last_name.strip()}".strip()



def _calculate_age(date_of_birth):
    if not date_of_birth:
        return None
    today = date.today()
    return (
        today.year
        - date_of_birth.year
        - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
    )


def _build_full_name(first_name: str, last_name: str) -> str:
    return f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()


def create_patient(db, current_user, payload):
    full_name = _build_full_name(payload.first_name, payload.last_name)

    patient = Patient(
        user_id=current_user.id,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        full_name=full_name,  # ✅ FIX
        date_of_birth=payload.date_of_birth,
        age=_calculate_age(payload.date_of_birth),
        sex=payload.sex,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
        # email removed
    )

    db.add(patient)
    db.commit()
    db.refresh(patient)

    return patient


def update_patient(db: Session, patient: Patient, payload: PatientUpdate) -> Patient:
    for field, value in payload.model_dump(exclude_unset=True).items():
        if hasattr(patient, field):
            setattr(patient, field, value)
            # Special handling for date_of_birth
            if field == "date_of_birth":
                patient.age = _calculate_age(value)
    
    if payload.first_name is not None:
        patient.first_name = payload.first_name.strip()

    if payload.last_name is not None:
        patient.last_name = payload.last_name.strip()

    # 🔥 ALWAYS recompute full_name if either changed
    if payload.first_name is not None or payload.last_name is not None:
        patient.full_name = _build_full_name(
            patient.first_name,
            patient.last_name
        )

    if payload.date_of_birth is not None:
        patient.date_of_birth = payload.date_of_birth
        patient.age = _calculate_age(payload.date_of_birth)
    
    patient.full_name = _build_full_name(patient.first_name, patient.last_name)
    patient.full_name = build_full_name(patient.first_name, patient.last_name)
    db.commit()
    db.refresh(patient)
    return patient


