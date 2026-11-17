"""Commission for new paid-share checkouts, independent of access entitlements."""

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.services.platform_settings_service import STRIPE_CONNECT_CONFIG_KEY, _load_json_setting, default_platform_settings
from app.services.subscription_service import plan_access


def share_commission(db: Session, subscription: dict[str, Any]) -> dict[str, Any]:
    defaults = default_platform_settings()["stripe_connect"]
    settings = _load_json_setting(db, STRIPE_CONNECT_CONFIG_KEY, defaults)
    # A Pro trial enables features but is not a paid subscription.
    paid = plan_access(subscription)["status"] == "active"
    key = "pro_platform_fee_percent" if paid else "platform_fee_percent"
    return {
        "tier": "pro" if paid else "free",
        "percent": settings[key],
        "fixed_minor": settings["platform_fee_fixed_amount"],
    }


def commission_amount(amount_minor: int, commission: dict[str, Any]) -> int:
    fee = round(Decimal(amount_minor) * Decimal(str(commission["percent"])) / 100)
    return max(0, min(fee + commission["fixed_minor"], amount_minor))


def commission_metadata(commission: dict[str, Any], amount_minor: int) -> dict[str, str]:
    return {
        "commission_tier": commission["tier"],
        "commission_percent": str(commission["percent"]),
        "commission_fixed_minor": str(commission["fixed_minor"]),
        "platform_fee_amount": str(commission_amount(amount_minor, commission)),
    }
