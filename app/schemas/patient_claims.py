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


class ClaimInvitePreview(BaseModel):
    clinic_name: str
    client_name: str
    email: EmailStr
    expires_at: datetime
    consent_text: str
    consent_version: str


class ClaimAccountCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)
    consent: bool


class ClaimAccountStarted(BaseModel):
    ok: bool
    email: EmailStr
    requires_email_verification: bool
    message: str
    dev_verification_url: str | None = None
