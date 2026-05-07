from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

TRIAL_DAYS = int(os.getenv("SUBSCRIPTION_TRIAL_DAYS", "30"))
DEFAULT_PLAN_CODE = os.getenv("SUBSCRIPTION_PLAN_CODE", "practitioner_monthly")
SUBSCRIPTION_REQUIRED = os.getenv("SUBSCRIPTION_REQUIRED", "true").lower() == "true"

ACTIVE_STATUSES = {"trialing", "active"}
SOFT_BLOCK_STATUSES = {"past_due", "incomplete"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def ensure_subscription_record(db: Session, user) -> dict[str, Any]:
    """Ensure every practitioner has a local subscription row.

    V1 policy: new users receive a full-access trial. Older users get a trial row
    on first access if none exists. Existing Stripe subscription state is preserved.
    """
    row = db.execute(
        text(
            """
            SELECT *
            FROM subscriptions
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"user_id": user.id},
    ).mappings().first()

    if row:
        return dict(row)

    trial_end = _utcnow() + timedelta(days=TRIAL_DAYS)
    inserted = db.execute(
        text(
            """
            INSERT INTO subscriptions (
                user_id,
                plan_code,
                status,
                current_period_end,
                trial_ends_at,
                created_at,
                updated_at
            )
            VALUES (
                :user_id,
                :plan_code,
                'trialing',
                :trial_end,
                :trial_end,
                NOW(),
                NOW()
            )
            RETURNING *
            """
        ),
        {"user_id": user.id, "plan_code": DEFAULT_PLAN_CODE, "trial_end": trial_end},
    ).mappings().first()
    db.commit()
    return dict(inserted)


def subscription_status(db: Session, user) -> dict[str, Any]:
    from app.services.platform_settings_service import is_platform_admin_user

    if is_platform_admin_user(db, user):
        return {
            "subscription_id": None,
            "status": "platform_admin",
            "effective_status": "platform_admin",
            "plan_code": "internal_admin",
            "trial_ends_at": None,
            "current_period_end": None,
            "days_remaining": None,
            "can_create_reports": True,
            "can_create_share_bundles": True,
            "can_view_existing": True,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "cancel_at_period_end": False,
        }

    sub = ensure_subscription_record(db, user)
    status = str(sub.get("status") or "incomplete")
    current_period_end = _as_aware(sub.get("current_period_end"))
    trial_ends_at = _as_aware(sub.get("trial_ends_at"))
    now = _utcnow()

    effective_status = status
    if status == "trialing" and current_period_end and current_period_end < now:
        effective_status = "trial_expired"
    elif status in {"active", "past_due", "incomplete"} and current_period_end and current_period_end < now and status != "active":
        effective_status = "expired"

    can_create = effective_status in ACTIVE_STATUSES
    days_remaining = None
    deadline = current_period_end or trial_ends_at
    if deadline:
        days_remaining = max(0, (deadline.date() - now.date()).days)

    return {
        "subscription_id": str(sub.get("id")) if sub.get("id") else None,
        "status": status,
        "effective_status": effective_status,
        "plan_code": sub.get("plan_code") or DEFAULT_PLAN_CODE,
        "trial_ends_at": trial_ends_at.isoformat() if trial_ends_at else None,
        "current_period_end": current_period_end.isoformat() if current_period_end else None,
        "days_remaining": days_remaining,
        "can_create_reports": can_create,
        "can_create_share_bundles": can_create,
        "can_view_existing": True,
        "stripe_customer_id": sub.get("stripe_customer_id"),
        "stripe_subscription_id": sub.get("stripe_subscription_id"),
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
    }


def require_subscription_feature(db: Session, user, feature: str = "create") -> None:
    if not SUBSCRIPTION_REQUIRED:
        return

    from app.services.platform_settings_service import is_platform_admin_user
    if is_platform_admin_user(db, user):
        return

    state = subscription_status(db, user)
    if feature == "report_generation" and state["can_create_reports"]:
        return
    if feature in {"share_bundle", "share_link"} and state["can_create_share_bundles"]:
        return
    if feature == "create" and (state["can_create_reports"] or state["can_create_share_bundles"]):
        return

    raise HTTPException(
        status_code=402,
        detail={
            "code": "subscription_required",
            "message": "Your trial or subscription has expired. Upgrade to continue creating new reports or share bundles.",
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
