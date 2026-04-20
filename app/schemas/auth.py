from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class AuthRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)

    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)

    # kept for backwards compatibility with current flow
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    clinic_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def validate_registration(self):
        self.first_name = self.first_name.strip()
        self.last_name = self.last_name.strip()

        if not self.first_name:
            raise ValueError("First name is required.")

        if not self.last_name:
            raise ValueError("Last name is required.")

        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")

        if not any(ch.isupper() for ch in self.password):
            raise ValueError("Password must contain at least one uppercase letter.")

        if not any(ch.islower() for ch in self.password):
            raise ValueError("Password must contain at least one lowercase letter.")

        if not any(ch.isdigit() for ch in self.password):
            raise ValueError("Password must contain at least one number.")

        self.full_name = f"{self.first_name} {self.last_name}".strip()

        if self.clinic_name is not None:
            self.clinic_name = self.clinic_name.strip() or None

        if self.phone is not None:
            self.phone = self.phone.strip() or None

        return self


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
    new_password: str = Field(min_length=10, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=10, max_length=128)


class AuthMessageResponse(BaseModel):
    ok: bool = True
    message: str