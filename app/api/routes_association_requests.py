"""Person-initiated clinic association requests.

The person submits an email. The practitioner can accept or decline. The person
can cancel while the request is pending. Accepting opens a clinic chart and
does not copy or release the personal record.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.services.clinic_association_service import (
    accept_person_request,
    cancel_person_request,
    decline_person_request,
    list_person_requests,
    request_practitioner_by_email,
)

router = APIRouter(tags=["association-requests"])


class AssociationRequestCreate(BaseModel):
    practitioner_email: str = Field(max_length=255)

    @field_validator("practitioner_email")
    @classmethod
    def required_email(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Enter the practitioner's email address.")
        return cleaned


@router.post("/api/client/association-requests")
def create_association_request(
    body: AssociationRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ask a practitioner to connect. Handles the person's identity.

    The response does not say whether that email has an account. The stored
    request has no notes, labs, or wearable values, and no share link is created.
    """
    return request_practitioner_by_email(db, current_user, body.practitioner_email)


@router.get("/api/association-requests")
def get_association_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List pending requests for this practitioner. Handles patient identity.

    Each item is the requester's name and email. Health data is omitted.
    """
    return list_person_requests(db, current_user)


@router.post("/api/association-requests/{request_id}/accept")
def accept_association_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Accept a pending request. Handles patient identity.

    Opens a clinic chart when needed. Personal notes, labs, wearable values,
    and share links are left unchanged.
    """
    return accept_person_request(db, current_user, request_id)


@router.post("/api/association-requests/{request_id}/decline")
def decline_association_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Decline a pending request. Handles patient identity.

    The personal record stays with the person. No clinic chart is created.
    """
    return decline_person_request(db, current_user, request_id)


@router.post("/api/client/association-requests/{request_id}/cancel")
def cancel_association_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel the caller's pending request. Handles patient identity.

    The personal record stays available. No clinic chart is created.
    """
    return cancel_person_request(db, current_user, request_id)
