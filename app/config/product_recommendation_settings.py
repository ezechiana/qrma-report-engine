# app/config/product_recommendation_settings.py

from __future__ import annotations

VALID_RECOMMENDATION_MODES = {
    "recommendations_off",
    "affiliate_vitalhealth",
    "vitalhealth_clinical_optimised",
    "natural_approaches_clinical",
    "mixed_clinical",
}

DEFAULT_RECOMMENDATION_MODE = "natural_approaches_clinical"


def normalize_recommendation_mode(mode: str | None) -> str:
    value = (mode or DEFAULT_RECOMMENDATION_MODE).strip().lower()
    if value not in VALID_RECOMMENDATION_MODES:
        return DEFAULT_RECOMMENDATION_MODE
    return value


def recommendations_enabled(mode: str | None) -> bool:
    return normalize_recommendation_mode(mode) != "recommendations_off"


def products_enabled(mode: str | None) -> bool:
    return normalize_recommendation_mode(mode) != "recommendations_off"


def clinical_recommendations_enabled(mode: str | None) -> bool:
    # keep clinical recommendations on by default except in a future stricter “full off” mode
    return True


