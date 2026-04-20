from __future__ import annotations

import os
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.schemas.settings import (
    BrandAssetUploadResponse,
    PractitionerSettingsRead,
    PractitionerSettingsUpdate,
)
from app.services.settings_service import get_or_create_settings, update_settings
from app.services.storage_service import (
    generate_presigned_url,
    object_exists,
    upload_bytes,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
    "image/svg+xml",
}
MAX_BRAND_ASSET_BYTES = 8 * 1024 * 1024


def _resolve_preview_url(stored_value: str | None) -> str | None:
    if not stored_value:
        return None

    if stored_value.startswith("http://") or stored_value.startswith("https://"):
        return stored_value

    if object_exists(stored_value):
        return generate_presigned_url(stored_value)

    return stored_value


def _serialise_settings(settings) -> PractitionerSettingsRead:
    return PractitionerSettingsRead.model_validate({
        "clinic_name": settings.clinic_name,
        "report_title": settings.report_title,
        "report_subtitle": settings.report_subtitle,
        "logo_url": settings.logo_url,
        "cover_image_url": settings.cover_image_url,
        "accent_color": settings.accent_color,
        "support_email": settings.support_email,
        "website_url": settings.website_url,
        "recommendation_mode_default": settings.recommendation_mode_default.value
            if hasattr(settings.recommendation_mode_default, "value")
            else settings.recommendation_mode_default,
        "logo_preview_url": _resolve_preview_url(settings.logo_url),
        "cover_image_preview_url": _resolve_preview_url(settings.cover_image_url),
        "created_at": settings.created_at,
        "updated_at": settings.updated_at,
    })


def _guess_extension(filename: str, content_type: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext:
        return ext

    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
    }
    return mapping.get(content_type, ".bin")


async def _upload_brand_asset(
    *,
    file: UploadFile,
    current_user: User,
    asset_kind: str,
) -> BrandAssetUploadResponse:
    content_type = (file.content_type or "").lower().strip()
    if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload PNG, JPG, WEBP, GIF, or SVG.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(data) > MAX_BRAND_ASSET_BYTES:
        raise HTTPException(
            status_code=413,
            detail="File is too large. Maximum allowed size is 8 MB.",
        )

    extension = _guess_extension(file.filename or "", content_type)
    key = f"branding/{current_user.id}/{asset_kind}-{uuid4().hex}{extension}"
    storage_key = upload_bytes(key, data, content_type)
    preview_url = generate_presigned_url(storage_key)

    return BrandAssetUploadResponse(
        storage_key=storage_key,
        url=preview_url,
        content_type=content_type,
        size_bytes=len(data),
    )


@router.get("", response_model=PractitionerSettingsRead)
def read_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    settings = get_or_create_settings(db, current_user)
    return _serialise_settings(settings)


@router.put("", response_model=PractitionerSettingsRead)
def save_settings(
    payload: PractitionerSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    settings = get_or_create_settings(db, current_user)
    updated = update_settings(db, settings, payload.model_dump(exclude_unset=True))
    return _serialise_settings(updated)


@router.post("/upload-logo", response_model=BrandAssetUploadResponse)
async def upload_logo(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    return await _upload_brand_asset(
        file=file,
        current_user=current_user,
        asset_kind="logo",
    )


@router.post("/upload-cover", response_model=BrandAssetUploadResponse)
async def upload_cover(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    return await _upload_brand_asset(
        file=file,
        current_user=current_user,
        asset_kind="cover",
    )
