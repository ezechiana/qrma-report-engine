from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

TRIAL_DAYS = int(os.getenv("SUBSCRIPTION_TRIAL_DAYS", "30"))
DEFAULT_PLAN_CODE = os.getenv("SUBSCRIPTION_PLAN_CODE", os.getenv("DEFAULT_SUBSCRIPTION_PLAN_CODE", "practitioner_monthly"))
SUBSCRIPTION_REQUIRED = os.getenv("SUBSCRIPTION_REQUIRED", "true").lower() == "true"

PLAN_NAME = os.getenv("SUBSCRIPTION_PLAN_NAME", "go360 Practitioner")
PRICE_LABEL = os.getenv("SUBSCRIPTION_PRICE_LABEL", "$59/month")
TRIAL_LABEL = os.getenv("SUBSCRIPTION_TRIAL_LABEL", f"{TRIAL_DAYS}-day free trial")

ACTIVE_STATUSES = {"trialing", "active"}
SOFT_BLOCK_STATUSES = {"past_due", "incomplete"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | str | None) -> datetime | None:
    if not dt:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def ensure_subscription_schema(db: Session) -> None:
    """Keep subscription rollout safe across local/Railway databases.

    This is intentionally lightweight and idempotent. It avoids deployment failures
    when an older database has the subscriptions table but is missing newer columns.
    """
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            stripe_customer_id TEXT UNIQUE,
            stripe_subscription_id TEXT UNIQUE,
            stripe_checkout_session_id TEXT,
            stripe_price_id TEXT,
            plan_code TEXT NOT NULL DEFAULT 'practitioner_monthly',
            status TEXT NOT NULL DEFAULT 'incomplete',
            current_period_end TIMESTAMPTZ,
            trial_ends_at TIMESTAMPTZ,
            cancel_at_period_end BOOLEAN NOT NULL DEFAULT false,
            voucher_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    db.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS stripe_checkout_session_id TEXT"))
    db.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS stripe_price_id TEXT"))
    db.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ"))
    db.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS voucher_code TEXT"))
    db.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN NOT NULL DEFAULT false"))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_subscriptions_user_created ON subscriptions (user_id, created_at DESC)"))
    db.commit()


def ensure_subscription_record(db: Session, user) -> dict[str, Any]:
    """Ensure every practitioner has a local subscription row.

    V1 policy: new users receive a full-access trial. Existing Stripe subscription
    state is preserved. Older users get a trial row on first access if none exists.
    """
    ensure_subscription_schema(db)
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
                cancel_at_period_end,
                created_at,
                updated_at
            )
            VALUES (
                :user_id,
                :plan_code,
                'trialing',
                :trial_end,
                :trial_end,
                false,
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


def _effective_status(raw_status: str, current_period_end: datetime | None, trial_ends_at: datetime | None) -> str:
    status = (raw_status or "incomplete").lower()
    now = _utcnow()
    deadline = current_period_end or trial_ends_at

    if status == "trialing" and deadline and deadline < now:
        return "trial_expired"
    if status == "active":
        # Stripe active subscriptions are generally active until updated by webhook.
        return "active"
    if status in {"past_due", "incomplete"} and deadline and deadline < now:
        return "expired"
    return status


def subscription_status(db: Session, user) -> dict[str, Any]:
    sub = ensure_subscription_record(db, user)
    status = str(sub.get("status") or "incomplete").lower()
    current_period_end = _as_aware(sub.get("current_period_end"))
    trial_ends_at = _as_aware(sub.get("trial_ends_at"))
    effective_status = _effective_status(status, current_period_end, trial_ends_at)
    now = _utcnow()

    can_create = effective_status in ACTIVE_STATUSES
    deadline = current_period_end or trial_ends_at
    days_remaining = None
    if deadline:
        days_remaining = max(0, (deadline.date() - now.date()).days)

    if can_create and effective_status == "trialing":
        access_message = f"Free trial active. {days_remaining if days_remaining is not None else '—'} day(s) remaining."
    elif can_create and effective_status == "active":
        access_message = "Subscription active."
    elif effective_status == "trial_expired":
        access_message = "Your free trial has ended. Upgrade to create new reports and share links."
    elif effective_status == "past_due":
        access_message = "Payment issue detected. Update billing to continue creating new reports and shares."
    else:
        access_message = "Subscription required to create new reports and share links."

    return {
        "subscription_id": str(sub.get("id")) if sub.get("id") else None,
        "status": status,
        "effective_status": effective_status,
        "is_active": can_create,
        "plan_code": sub.get("plan_code") or DEFAULT_PLAN_CODE,
        "plan_name": PLAN_NAME,
        "price_label": PRICE_LABEL,
        "trial_label": TRIAL_LABEL,
        "trial_ends_at": trial_ends_at.isoformat() if trial_ends_at else None,
        "current_period_end": current_period_end.isoformat() if current_period_end else None,
        "days_remaining": days_remaining,
        "can_create_reports": can_create,
        "can_create_share_bundles": can_create,
        "can_create_shares": can_create,
        "can_view_existing": True,
        "stripe_customer_id": sub.get("stripe_customer_id"),
        "stripe_subscription_id": sub.get("stripe_subscription_id"),
        "stripe_price_id": sub.get("stripe_price_id"),
        "voucher_code": sub.get("voucher_code"),
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
        "access_message": access_message,
    }


def require_subscription_feature(db: Session, user, feature: str = "create") -> None:
    if not SUBSCRIPTION_REQUIRED:
        return

    state = subscription_status(db, user)

    if feature == "report_generation" and state["can_create_reports"]:
        return
    if feature in {"share_bundle", "share_link"} and state["can_create_share_bundles"]:
        return
    if feature == "create" and (state["can_create_reports"] or state["can_create_share_bundles"]):
        return

    feature_labels = {
        "report_generation": "generate new reports",
        "share_link": "create new client share links",
        "share_bundle": "create new client share bundles",
        "create": "create new reports and shares",
    }

    raise HTTPException(
        status_code=402,
        detail={
            "code": "subscription_required",
            "message": f"Your trial or subscription is not active. Upgrade to {feature_labels.get(feature, 'continue')}.",
            "feature": feature,
            "subscription": state,
        },
    )
