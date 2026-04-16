from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AuthRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=255)
    clinic_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)


class AuthLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    clinic_name: str | None = None
    phone: str | None = None
    is_active: bool
    email_verified_at: datetime | None = None
    last_login_at: datetime | None = None
    recommendation_mode_default: str | None = None
    logo_url: str | None = None
    primary_color: str | None = None
    accent_color: str | None = None
    support_email: str | None = None
    website_url: str | None = None
    timezone: str | None = None
    created_at: datetime
    updated_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: AuthUserResponse


class AuthMeResponse(BaseModel):
    user: AuthUserResponse


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class AuthMessageResponse(BaseModel):
    ok: bool = True
    message: str