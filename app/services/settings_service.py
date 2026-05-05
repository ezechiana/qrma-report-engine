from __future__ import annotations

from sqlalchemy.orm import Session

from app.config.product_recommendation_settings import normalize_recommendation_mode
from app.db.models import PractitionerSettings, RecommendationMode, User


def get_or_create_settings(db: Session, user: User) -> PractitionerSettings:
    settings = (
        db.query(PractitionerSettings)
        .filter(PractitionerSettings.user_id == user.id)
        .first()
    )

    if settings:
        if not settings.preferred_currency:
            settings.preferred_currency = "USD"

        if settings.monthly_goal_minor is None:
            settings.monthly_goal_minor = 0

        return settings

    settings = PractitionerSettings(
        user_id=user.id,
        clinic_name=user.clinic_name or "Health Portal",
        report_title="Personalised Wellness Scan Report",
        report_subtitle="Functional health overview",
        logo_url=None,
        cover_image_url=None,
        accent_color="#2f6fed",
        support_email=user.email,
        website_url=None,
        recommendation_mode_default=RecommendationMode.natural_approaches_clinical,
    )
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def _clean_placeholder(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "string", "null", "undefined"}:
        return None
    return value


def update_settings(db: Session, settings: PractitionerSettings, payload: dict) -> PractitionerSettings:
    if "recommendation_mode_default" in payload and payload["recommendation_mode_default"] is not None:
        payload["recommendation_mode_default"] = RecommendationMode(
            normalize_recommendation_mode(payload["recommendation_mode_default"])
        )

    for field, value in payload.items():
        value = _clean_placeholder(value)

        if field == "accent_color" and not value:
            value = "#2f6fed"

        if field == "preferred_currency":
            value = (value or "USD").upper()
            if value not in {"USD", "GBP", "EUR", "AED", "JPY", "KRW"}:
                value = "USD"

        if field == "monthly_goal_minor":
            value = int(value or 200000)

        if field == "report_theme" and not value:
            value = "default"

        setattr(settings, field, value)

    db.commit()
    db.refresh(settings)
    return settings