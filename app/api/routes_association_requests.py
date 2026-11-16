"""Person-initiated clinic association requests.

The person submits an email. The practitioner later sees the requester's name
and email. Accept and decline are not part of this route.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.services.clinic_association_service import (
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
