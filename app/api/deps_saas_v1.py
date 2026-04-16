from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.models.saas_v1 import Practitioner, User
from app.db.session import get_db
# after db.refresh(report)

from app.services.saas_report_orchestrator import run_report_generation

run_report_generation(db, report)

def get_current_user() -> User:
    """
    Placeholder dependency.

    Replace this with your real auth/session implementation.
    For now, this intentionally raises until auth is wired.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Auth not wired yet.",
    )


def get_current_practitioner(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Practitioner:
    practitioner = (
        db.query(Practitioner)
        .filter(Practitioner.user_id == current_user.id)
        .first()
    )
    if not practitioner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Practitioner profile not found.",
        )
    return practitioner


CurrentDB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentPractitioner = Annotated[Practitioner, Depends(get_current_practitioner)]


def ensure_practitioner_owns_patient(db: Session, practitioner_id: UUID, patient) -> None:
    if not patient or patient.practitioner_id != practitioner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )


def ensure_practitioner_owns_case(db: Session, practitioner_id: UUID, case) -> None:
    if not case or case.practitioner_id != practitioner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found.",
        )


def ensure_practitioner_owns_report(db: Session, practitioner_id: UUID, report) -> None:
    if not report or report.practitioner_id != practitioner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )