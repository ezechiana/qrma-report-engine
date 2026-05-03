from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class PractitionerSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    clinic_name: Optional[str] = None
    report_title: Optional[str] = None
    report_subtitle: Optional[str] = None

    logo_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    accent_color: Optional[str] = None

    support_email: Optional[str] = None
    website_url: Optional[str] = None

    preferred_currency: Optional[str] = "USD"
    monthly_goal_minor: Optional[int] = 200000

    recommendation_mode_default: str = "natural_approaches_clinical"

    logo_preview_url: Optional[str] = None
    cover_image_preview_url: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PractitionerSettingsUpdate(BaseModel):
    clinic_name: Optional[str] = None
    report_title: Optional[str] = None
    report_subtitle: Optional[str] = None

    logo_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    accent_color: Optional[str] = None

    support_email: Optional[str] = None
    website_url: Optional[str] = None

    preferred_currency: Optional[str] = None
    monthly_goal_minor: Optional[int] = None

    recommendation_mode_default: Optional[str] = None


class BrandAssetUploadResponse(BaseModel):
    storage_key: str
    url: str
    content_type: str
    size_bytes: int