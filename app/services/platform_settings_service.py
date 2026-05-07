from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

PLATFORM_CONFIG_KEY = "platform_config"
REFERRAL_CONFIG_KEY = "referral_config"
REVENUE_CONFIG_KEY = "revenue_config"
FX_CONFIG_KEY = "fx_config"
SUBSCRIPTION_CONFIG_KEY = "subscription_config"
SHARE_CONFIG_KEY = "share_config"
STRIPE_CONNECT_CONFIG_KEY = "stripe_connect_config"
REPORT_CONFIG_KEY = "report_config"
FEATURE_FLAGS_CONFIG_KEY = "feature_flags_config"

VALID_FEE_MODELS = {"platform_absorbs", "practitioner_absorbs", "split_proportional"}
VALID_CURRENCIES = {"USD", "GBP", "EUR", "AED", "JPY", "KRW"}


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, default)).strip())
    except Exception:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, default)).strip())
    except Exception:
        return default


def _currency(value: str | None, default: str = "USD") -> str:
    code = (value or default).strip().upper()
    return code if code in VALID_CURRENCIES else default


def _load_json_setting(db: Session, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    default = default or {}
    try:
        row = db.execute(text("SELECT value FROM app_settings WHERE key = :key"), {"key": key}).mappings().first()
    except Exception:
        db.rollback()
        return dict(default)
    if not row:
        return dict(default)
    value = row["value"] or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}
    if not isinstance(value, dict):
        value = {}
    merged = dict(default)
    merged.update(value)
    return merged


def save_json_setting(db: Session, key: str, value: dict[str, Any]) -> dict[str, Any]:
    db.execute(
        text(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (:key, CAST(:value AS JSONB), NOW())
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """
        ),
        {"key": key, "value": json.dumps(value)},
    )
    db.commit()
    return value


def default_platform_settings() -> dict[str, Any]:
    return {
        "platform": {
            "admin_emails": os.getenv("PLATFORM_ADMIN_EMAILS", ""),
            "support_email": os.getenv("PLATFORM_SUPPORT_EMAIL", ""),
            "terms_url": os.getenv("PLATFORM_TERMS_URL", "/terms"),
            "privacy_url": os.getenv("PLATFORM_PRIVACY_URL", "/privacy"),
        },
        "revenue": {
            "fee_model": os.getenv("REVENUE_FEE_MODEL", "platform_absorbs"),
            "default_currency": _currency(os.getenv("DEFAULT_DASHBOARD_CURRENCY", "USD")),
            "default_monthly_goal_minor": _int_env("DEFAULT_MONTHLY_GOAL_MINOR", 200000),
        },
        "fx": {
            "provider_url": os.getenv("FX_PROVIDER_URL", "https://open.er-api.com/v6/latest/USD"),
            "provider_timeout_seconds": _float_env("FX_PROVIDER_TIMEOUT_SECONDS", 6.0),
            "update_schedule_label": os.getenv("FX_UPDATE_SCHEDULE_LABEL", "1st of each month, 01:00 UK time"),
            "default_rates_json": os.getenv("FX_DEFAULT_RATES_JSON", ""),
        },
        "referral": {
            "enabled": _bool_env("REFERRAL_ENABLED", True),
            "reward_type": "free_months",
            "reward_months": _int_env("REFERRAL_REWARD_MONTHS", 1),
            "trigger": "referred_user_becomes_paid",
        },
        "subscription": {
            "trial_days": _int_env("SUBSCRIPTION_TRIAL_DAYS", 30),
            "plan_code": os.getenv("DEFAULT_SUBSCRIPTION_PLAN_CODE", os.getenv("SUBSCRIPTION_PLAN_CODE", "practitioner_monthly")),
            "plan_name": os.getenv("SUBSCRIPTION_PLAN_NAME", "go360 Practitioner"),
            "price_label": os.getenv("SUBSCRIPTION_PRICE_LABEL", "$59/month"),
            "trial_label": os.getenv("SUBSCRIPTION_TRIAL_LABEL", "30-day free trial"),
            "allow_promotion_codes": _bool_env("STRIPE_ALLOW_PROMOTION_CODES", True),
            "subscription_required": _bool_env("SUBSCRIPTION_REQUIRED", True),
            "stripe_price_id_configured": bool(os.getenv("STRIPE_SUBSCRIPTION_PRICE_ID")),
        },
        "share": {
            "default_share_price_amount": _int_env("DEFAULT_SHARE_PRICE_AMOUNT", 2500),
            "default_share_price_currency": os.getenv("DEFAULT_SHARE_PRICE_CURRENCY", "gbp").lower(),
            "share_access_cookie_max_age": _int_env("SHARE_ACCESS_COOKIE_MAX_AGE", 43200),
        },
        "stripe_connect": {
            "enabled": _bool_env("STRIPE_CONNECT_ENABLED", True),
            "account_type": os.getenv("STRIPE_CONNECT_ACCOUNT_TYPE", "express"),
            "country": os.getenv("STRIPE_CONNECT_COUNTRY", "GB"),
            "fallback_to_platform": _bool_env("STRIPE_CONNECT_FALLBACK_TO_PLATFORM", True),
            "platform_fee_percent": _float_env("STRIPE_PLATFORM_FEE_PERCENT", 15.0),
            "platform_fee_fixed_amount": _int_env("STRIPE_PLATFORM_FEE_FIXED_AMOUNT", 0),
        },
        "report": {
            "recommendation_mode": os.getenv("REPORT_RECOMMENDATION_MODE", "vitalhealth_clinical_optimised"),
            "include_product_recommendations": True,
            "include_appendix": True,
            "include_toc": True,
            "max_sections": 6,
            "max_markers_per_section": 3,
            "tone": "clinical",
        },
        "features": {
            "referrals_enabled": _bool_env("FEATURE_REFERRALS_ENABLED", True),
            "subscriptions_enabled": _bool_env("FEATURE_SUBSCRIPTIONS_ENABLED", True),
            "paid_shares_enabled": _bool_env("FEATURE_PAID_SHARES_ENABLED", True),
            "stripe_connect_enabled": _bool_env("FEATURE_STRIPE_CONNECT_ENABLED", True),
            "revenue_dashboard_enabled": _bool_env("FEATURE_REVENUE_DASHBOARD_ENABLED", True),
            "trend_reports_enabled": _bool_env("FEATURE_TREND_REPORTS_ENABLED", True),
            "ai_recommendations_enabled": _bool_env("FEATURE_AI_RECOMMENDATIONS_ENABLED", True),
            "platform_settings_enabled": _bool_env("FEATURE_PLATFORM_SETTINGS_ENABLED", True),
        },
    }


def get_platform_settings(db: Session) -> dict[str, Any]:
    defaults = default_platform_settings()
    settings = {
        "platform": _load_json_setting(db, PLATFORM_CONFIG_KEY, defaults["platform"]),
        "revenue": _load_json_setting(db, REVENUE_CONFIG_KEY, defaults["revenue"]),
        "fx": _load_json_setting(db, FX_CONFIG_KEY, defaults["fx"]),
        "referral": _load_json_setting(db, REFERRAL_CONFIG_KEY, defaults["referral"]),
        "subscription": _load_json_setting(db, SUBSCRIPTION_CONFIG_KEY, defaults["subscription"]),
        "share": _load_json_setting(db, SHARE_CONFIG_KEY, defaults["share"]),
        "stripe_connect": _load_json_setting(db, STRIPE_CONNECT_CONFIG_KEY, defaults["stripe_connect"]),
        "report": _load_json_setting(db, REPORT_CONFIG_KEY, defaults["report"]),
        "features": _load_json_setting(db, FEATURE_FLAGS_CONFIG_KEY, defaults["features"]),
    }
    # Defensive normalisation.
    settings["revenue"]["fee_model"] = settings["revenue"].get("fee_model") if settings["revenue"].get("fee_model") in VALID_FEE_MODELS else defaults["revenue"]["fee_model"]
    settings["revenue"]["default_currency"] = _currency(settings["revenue"].get("default_currency"), defaults["revenue"]["default_currency"])
    settings["referral"]["reward_months"] = int(settings["referral"].get("reward_months") or defaults["referral"]["reward_months"])
    settings["subscription"]["trial_days"] = int(settings["subscription"].get("trial_days") or defaults["subscription"]["trial_days"])
    for key, default_value in defaults["features"].items():
        settings["features"][key] = bool(settings["features"].get(key, default_value))
    return settings


def save_platform_settings(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    current = get_platform_settings(db)
    key_map = {
        "platform": PLATFORM_CONFIG_KEY,
        "revenue": REVENUE_CONFIG_KEY,
        "fx": FX_CONFIG_KEY,
        "referral": REFERRAL_CONFIG_KEY,
        "subscription": SUBSCRIPTION_CONFIG_KEY,
        "share": SHARE_CONFIG_KEY,
        "stripe_connect": STRIPE_CONNECT_CONFIG_KEY,
        "report": REPORT_CONFIG_KEY,
        "features": FEATURE_FLAGS_CONFIG_KEY,
    }
    for section, key in key_map.items():
        incoming = payload.get(section)
        if not isinstance(incoming, dict):
            continue
        merged = dict(current.get(section) or {})
        merged.update(incoming)
        save_json_setting(db, key, merged)
    return get_platform_settings(db)


def _admin_email_set_from_settings(db: Session | None = None) -> set[str]:
    raw = os.getenv("PLATFORM_ADMIN_EMAILS", "").strip()
    if db is not None:
        try:
            platform = _load_json_setting(db, PLATFORM_CONFIG_KEY, default_platform_settings()["platform"])
            raw = str(platform.get("admin_emails") or raw or "")
        except Exception:
            pass
    return {item.strip().lower() for item in raw.split(",") if item.strip()}



def is_platform_admin_email(email: str | None, db: Session | None = None) -> bool:
    """Return True only when the email is explicitly allow-listed as a platform super admin.

    The allow-list comes from Platform Settings first, falling back to the
    PLATFORM_ADMIN_EMAILS environment variable. There is intentionally no
    permissive development fallback: an empty allow-list means no email-based
    platform admin access.
    """
    if not email:
        return False

    email = email.lower().strip()
    allowed = _admin_email_set_from_settings(db)
    return email in allowed if allowed else False




def get_user_role(user: Any) -> str:
    return str(getattr(user, "role", None) or "practitioner").strip().lower()


def is_platform_admin_user(db: Session | None, user: Any) -> bool:
    """Authoritative platform-admin gate.

    Access is granted only when:
    1) users.role is explicitly set to 'admin', or
    2) the user's email is explicitly listed in PLATFORM_ADMIN_EMAILS / Platform Settings.

    New self-registered users should still have role='practitioner'; the env
    allow-list is reserved for trusted super-admin owner accounts.
    """
    if get_user_role(user) == "admin":
        return True
    return is_platform_admin_email(getattr(user, "email", None), db=db)


def get_feature_flags(db: Session) -> dict[str, bool]:
    return {k: bool(v) for k, v in get_platform_settings(db).get("features", {}).items()}


def is_feature_enabled(db: Session, key: str, default: bool = True) -> bool:
    flags = get_feature_flags(db)
    return bool(flags.get(key, default))


def get_revenue_fee_model_from_settings(db: Session) -> str:
    settings = get_platform_settings(db).get("revenue", {})
    value = str(settings.get("fee_model") or os.getenv("REVENUE_FEE_MODEL", "platform_absorbs")).strip().lower()
    return value if value in VALID_FEE_MODELS else "platform_absorbs"
