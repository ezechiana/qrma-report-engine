# app/config/product_recommendation_settings.py

from __future__ import annotations

VALID_VITALHEALTH_STRATEGIES = {
    "legacy_vitalhealth",
    "mechanism_weighted",
    "hybrid",
}

DEFAULT_VITALHEALTH_STRATEGY = "mechanism_weighted"


def normalize_vitalhealth_strategy(strategy: str | None) -> str:
    value = (strategy or DEFAULT_VITALHEALTH_STRATEGY).strip().lower()
    if value not in VALID_VITALHEALTH_STRATEGIES:
        return DEFAULT_VITALHEALTH_STRATEGY
    return value