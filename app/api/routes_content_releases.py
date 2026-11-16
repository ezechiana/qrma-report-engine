# app/api/routes_content_releases.py

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Patient, User
from app.services.content_release_service import progress_release_state, set_progress_release

router = APIRouter(prefix="/api/patients", tags=["patients"])


class ProgressReleaseUpdate(BaseModel):
    released: bool


def _owned_patient(db: Session, patient_id: UUID, current_user: User) -> Patient:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.get("/{patient_id}/progress-release")
def get_progress_release(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Show whether this clinic has released the progress view.

    Handles patient identity. The client account id is not included.
    """
    patient = _owned_patient(db, patient_id, current_user)
    return progress_release_state(db, patient)


@router.post("/{patient_id}/progress-release")
def update_progress_release(
    patient_id: UUID,
    payload: ProgressReleaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Release or withdraw the progress view. Does not create a share link."""
    patient = _owned_patient(db, patient_id, current_user)
    return set_progress_release(db, patient, payload.released)
