from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.platform_settings_service import FREE_PLAN_FEATURES, SUBSCRIPTION_CONFIG_KEY, _load_json_setting, free_feature_settings

DEFAULT_PLAN_CODE = os.getenv("DEFAULT_SUBSCRIPTION_PLAN_CODE", os.getenv("SUBSCRIPTION_PLAN_CODE", "practitioner_monthly"))
SUBSCRIPTION_REQUIRED = os.getenv("SUBSCRIPTION_REQUIRED", "true").lower() == "true"

FREE_FEATURES = frozenset({"view_existing"})
PRO_FEATURES = FREE_FEATURES | set(FREE_PLAN_FEATURES)


def configured_free_features(db: Session) -> frozenset[str]:
    config = _load_json_setting(db, SUBSCRIPTION_CONFIG_KEY)
    return FREE_FEATURES | {feature for feature, enabled in free_feature_settings(config).items() if enabled}


def latest_subscription(db: Session, user) -> dict[str, Any]:
    return latest_subscription_for_user(db, user.id)


def latest_subscription_for_user(db: Session, user_id) -> dict[str, Any]:
    row = db.execute(
        text("SELECT * FROM subscriptions WHERE user_id = :user_id ORDER BY created_at DESC LIMIT 1"),
        {"user_id": user_id},
    ).mappings().first()
    return dict(row) if row else {}


def _deadline(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def plan_access(sub: dict[str, Any], *, free_features: frozenset[str] = FREE_FEATURES, platform_admin: bool = False) -> dict[str, Any]:
    billing_status = sub.get("status")
    end = _deadline(sub.get("current_period_end"))
    trial_end = (_deadline(sub.get("trial_ends_at")) or end) if billing_status == "trialing" else None
    now = datetime.now(timezone.utc)
    if platform_admin:
        status, tier = "platform_admin", "internal_admin"
    elif billing_status == "trialing" and trial_end and trial_end > now:
        status, tier = "trialing", "pro"
    elif billing_status == "active" and (end is None or end > now):
        status, tier = "active", "pro"
    else:
        status, tier = "free", "free"

    features = PRO_FEATURES if tier != "free" or not SUBSCRIPTION_REQUIRED else free_features
    return {
        "tier": tier,
        "status": status,
        "effective_status": status,
        "billing_status": billing_status,
        "plan_code": (sub.get("plan_code") or DEFAULT_PLAN_CODE) if tier == "pro" else tier,
        "features": sorted(features),
        "is_active": tier != "free",
        "can_view_existing": "view_existing" in features,
        "can_create_reports": "report_generation" in features,
        "can_create_trend_reports": "trend_report_generation" in features,
        "can_sell_shares": "sell_shares" in features,
        "can_create_shares": "share_link" in features,
        "can_create_bundles": "share_bundle" in features,
        "can_create_share_bundles": "share_bundle" in features,
        "current_period_end": end.isoformat() if end else None,
        "trial_ends_at": trial_end.isoformat() if trial_end else None,
    }


def require_subscription_feature(db: Session, user, feature: str = "create") -> None:
    if not SUBSCRIPTION_REQUIRED:
        return

    from app.services.platform_settings_service import is_platform_admin_user

    if is_platform_admin_user(db, user):
        return

    state = plan_access(latest_subscription(db, user), free_features=configured_free_features(db))
    if feature in state["features"] or (feature == "create" and state["can_create_reports"]):
        return

    raise HTTPException(
        status_code=402,
        detail={
            "code": "subscription_required",
            "message": "Upgrade to Pro to create new reports or shares.",
            "subscription": state,
        },
    )


def apply_subscription_credits(db: Session, user_id) -> bool:
    """Apply available subscription credits. Kept here for callers that work through subscription_service."""
    from app.services.referral_service import apply_subscription_credits as _apply
    return _apply(db, user_id=user_id)


def subscription_credit_summary(db: Session, user_id) -> dict[str, int]:
    from app.services.referral_service import get_subscription_credit_summary
    return get_subscription_credit_summary(db, user_id=user_id)
