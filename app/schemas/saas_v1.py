from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class APIMessage(BaseModel):
    ok: bool = True
    message: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    is_active: bool
    email_verified_at: datetime | None = None
    created_at: datetime


class PractitionerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    display_name: str
    brand_name: str | None = None
    recommendation_mode_default: str
    logo_url: str | None = None
    primary_color: str | None = None
    accent_color: str | None = None
    support_email: EmailStr | None = None
    website_url: str | None = None
    timezone: str
    created_at: datetime


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    practitioner_id: UUID
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    plan_code: str
    status: str
    current_period_end: datetime | None = None
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime


class AuthRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=255)


class AuthLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserRead
    practitioner: PractitionerRead


class AuthMeResponse(BaseModel):
    user: UserRead
    practitioner: PractitionerRead
    subscription: SubscriptionRead | None = None


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    sex: str | None = Field(default=None, max_length=50)
    date_of_birth: date | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    notes: str | None = None
    external_ref: str | None = Field(default=None, max_length=100)


class PatientUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    sex: str | None = Field(default=None, max_length=50)
    date_of_birth: date | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    notes: str | None = None
    external_ref: str | None = Field(default=None, max_length=100)


class PatientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    practitioner_id: UUID
    external_ref: str | None = None
    full_name: str
    sex: str | None = None
    date_of_birth: date | None = None
    email: EmailStr | None = None
    phone: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class PatientListItem(BaseModel):
    id: UUID
    full_name: str
    sex: str | None = None
    date_of_birth: date | None = None
    created_at: datetime
    report_count: int = 0


class PatientListResponse(BaseModel):
    items: list[PatientListItem]


class CaseCreate(BaseModel):
    patient_id: UUID
    title: str = Field(min_length=2, max_length=255)
    source_type: str = "upload"
    recommendation_mode: str = "natural_approaches_clinical"
    intake_payload_json: dict[str, Any] | None = None


class CaseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=255)
    source_type: str | None = None
    recommendation_mode: str | None = None
    status: str | None = None
    intake_payload_json: dict[str, Any] | None = None


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    practitioner_id: UUID
    patient_id: UUID
    title: str
    source_type: str
    recommendation_mode: str
    status: str
    intake_payload_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class CaseListItem(BaseModel):
    id: UUID
    patient_id: UUID
    patient_name: str
    title: str
    recommendation_mode: str
    status: str
    created_at: datetime


class CaseListResponse(BaseModel):
    items: list[CaseListItem]


class GenerateReportResponse(BaseModel):
    case_id: UUID
    report_version_id: UUID
    status: str


class ReportVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    practitioner_id: UUID
    version_number: int
    status: str
    recommendation_mode: str
    report_json: dict[str, Any] | None = None
    html_path: str | None = None
    pdf_path: str | None = None
    build_version: str | None = None
    job_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ReportListItem(BaseModel):
    id: UUID
    case_id: UUID
    patient_id: UUID
    patient_name: str
    version_number: int
    status: str
    recommendation_mode: str
    created_at: datetime


class ReportListResponse(BaseModel):
    items: list[ReportListItem]


class ReportViewerResponse(BaseModel):
    id: UUID
    case_id: UUID
    status: str
    recommendation_mode: str
    viewer: dict[str, Any] | None = None
    pdf_url: str | None = None
    html_url: str | None = None


class ShareReportRequest(BaseModel):
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


class ShareReportResponse(BaseModel):
    share_url: str
    expires_at: datetime | None = None


class FeedbackCreate(BaseModel):
    section_key: str | None = None
    marker_key: str | None = None
    sentiment: Literal["positive", "negative", "neutral"]
    comment: str | None = None


class FeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    practitioner_id: UUID
    report_version_id: UUID
    section_key: str | None = None
    marker_key: str | None = None
    sentiment: str
    comment: str | None = None
    created_at: datetime