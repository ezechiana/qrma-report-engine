from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class ClaimInviteCreate(BaseModel):
    email: EmailStr


class ClaimInviteRead(BaseModel):
    id: UUID
    patient_id: UUID
    email: str
    status: str = Field(description="pending, accepted, expired, or cancelled")
    expires_at: datetime
    created_at: datetime
    cancelled_at: datetime | None = None
    accepted_at: datetime | None = None


class ClaimInviteCreated(ClaimInviteRead):
    """Returned once, to the practitioner who just sent the invite."""

    claim_url: str
    email_sent: bool
