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


def update_settings(db: Session, settings: PractitionerSettings, payload: dict) -> PractitionerSettings:
    if "recommendation_mode_default" in payload and payload["recommendation_mode_default"] is not None:
        payload["recommendation_mode_default"] = RecommendationMode(
            normalize_recommendation_mode(payload["recommendation_mode_default"])
        )

    for field, value in payload.items():
        setattr(settings, field, value)

    db.commit()
    db.refresh(settings)
    return settings