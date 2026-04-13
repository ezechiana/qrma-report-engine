#app/services/patient_service.py
from sqlalchemy.orm import Session

from app.db.models import Patient, User
from app.schemas.patients import PatientCreate, PatientUpdate


def build_full_name(first_name: str, last_name: str) -> str:
    return f"{first_name.strip()} {last_name.strip()}".strip()


def create_patient(db: Session, user: User, payload: PatientCreate) -> Patient:
    patient = Patient(
        user_id=user.id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        full_name=build_full_name(payload.first_name, payload.last_name),
        date_of_birth=payload.date_of_birth,
        age=payload.age,
        sex=payload.sex,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
        email=payload.email,
        phone=payload.phone,
        notes=payload.notes,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def update_patient(db: Session, patient: Patient, payload: PatientUpdate) -> Patient:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)

    patient.full_name = build_full_name(patient.first_name, patient.last_name)
    db.commit()
    db.refresh(patient)
    return patient



#app/services/case_service.py

from sqlalchemy.orm import Session

from app.db.models import Case, CaseStatus, Patient, RecommendationMode, User
from app.schemas.cases import CaseCreate, CaseUpdate


def create_case(db: Session, user: User, patient: Patient, payload: CaseCreate) -> Case:
    case = Case(
        user_id=user.id,
        patient_id=patient.id,
        title=payload.title,
        status=CaseStatus.draft,
        recommendation_mode=RecommendationMode(payload.recommendation_mode),
        clinical_context_json=payload.clinical_context_json.model_dump() if payload.clinical_context_json else None,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def update_case(db: Session, case: Case, payload: CaseUpdate) -> Case:
    data = payload.model_dump(exclude_unset=True)

    if "clinical_context_json" in data and data["clinical_context_json"] is not None:
        data["clinical_context_json"] = data["clinical_context_json"].model_dump()

    if "recommendation_mode" in data and data["recommendation_mode"] is not None:
        data["recommendation_mode"] = RecommendationMode(data["recommendation_mode"])

    if "status" in data and data["status"] is not None:
        data["status"] = CaseStatus(data["status"])

    for field, value in data.items():
        setattr(case, field, value)

    db.commit()
    db.refresh(case)
    return case

