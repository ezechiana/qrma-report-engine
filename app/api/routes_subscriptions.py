from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User

router = APIRouter(tags=["subscriptions"])

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_SUBSCRIPTION_WEBHOOK_SECRET = os.getenv("STRIPE_SUBSCRIPTION_WEBHOOK_SECRET") or os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_SUBSCRIPTION_PRICE_ID = os.getenv("STRIPE_SUBSCRIPTION_PRICE_ID")
STRIPE_ALLOW_PROMOTION_CODES = os.getenv("STRIPE_ALLOW_PROMOTION_CODES", "true").lower() == "true"
SUBSCRIPTION_PORTAL_RETURN_PATH = os.getenv("SUBSCRIPTION_PORTAL_RETURN_PATH", "/app/settings")
DEFAULT_PLAN_CODE = os.getenv("DEFAULT_SUBSCRIPTION_PLAN_CODE", "practitioner_monthly")
TRIAL_DAYS = int(os.getenv("SUBSCRIPTION_TRIAL_DAYS", "30"))

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


class SubscriptionCheckoutRequest(BaseModel):
    voucher_code: Optional[str] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_iso(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _timestamp_to_dt(ts) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except Exception:
        return None


def _map_stripe_status(value: str | None) -> str:
    value = (value or "incomplete").lower()
    if value in {"trialing", "active", "past_due", "canceled", "incomplete"}:
        return value
    if value in {"unpaid", "paused"}:
        return "past_due"
    return "incomplete"


def _row_to_dict(row) -> dict[str, Any] | None:
    return dict(row._mapping) if row else None


def _is_subscription_usable(sub: dict[str, Any] | None) -> bool:
    if not sub:
        return False
    status = (sub.get("status") or "").lower()
    if status in {"active", "trialing"}:
        end = sub.get("current_period_end")
        if not end:
            return True
        if isinstance(end, str):
            try:
                end = datetime.fromisoformat(end.replace("Z", "+00:00"))
            except Exception:
                return True
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end >= _now()
    return False


def _ensure_subscription_record(db: Session, current_user: User) -> dict[str, Any]:
    """
    SQL-only subscription bootstrap.

    This avoids relying on an ORM Subscription import, so routes_subscriptions.py keeps
    working even if the model class is not imported or the table has recently changed.
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
        {"user_id": current_user.id},
    ).mappings().first()

    if row:
        return dict(row)

    trial_end = _now() + timedelta(days=TRIAL_DAYS)
    row = db.execute(
        text(
            """
            INSERT INTO subscriptions (
                id,
                user_id,
                plan_code,
                status,
                current_period_end,
                cancel_at_period_end,
                created_at,
                updated_at
            ) VALUES (
                gen_random_uuid(),
                :user_id,
                :plan_code,
                'trialing',
                :trial_end,
                false,
                NOW(),
                NOW()
            )
            RETURNING *
            """
        ),
        {
            "user_id": current_user.id,
            "plan_code": DEFAULT_PLAN_CODE,
            "trial_end": trial_end,
        },
    ).mappings().first()
    db.commit()
    return dict(row)


def _days_remaining(end: Any) -> int | None:
    if not end:
        return None
    try:
        if isinstance(end, str):
            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        seconds = (end - _now()).total_seconds()
        if seconds < 0:
            return 0
        return int((seconds + 86399) // 86400)
    except Exception:
        return None


def _subscription_payload(sub: dict[str, Any], *, stripe_connect: dict[str, Any] | None = None) -> dict[str, Any]:
    status = sub.get("status") or "incomplete"
    usable = _is_subscription_usable(sub)
    end = sub.get("current_period_end")
    trial_end = sub.get("current_period_end") if status == "trialing" else None
    days_remaining = _days_remaining(end)

    if usable and status == "active":
        access_message = "Subscription active."
        trial_banner = None
        upgrade_nudge = None
    elif usable and status == "trialing":
        label = f"{days_remaining} day{'s' if days_remaining != 1 else ''}" if days_remaining is not None else "your trial period"
        access_message = f"Trial active — {label} remaining."
        trial_banner = {
            "show": True,
            "level": "info" if (days_remaining or 30) > 7 else "warning",
            "title": "30-day trial active",
            "message": f"You have {label} remaining. Upgrade before the trial ends to keep creating reports and paid share bundles.",
            "days_remaining": days_remaining,
            "cta_label": "Start / upgrade subscription",
            "cta_action": "checkout",
        }
        upgrade_nudge = {
            "show": True,
            "reason": "trialing",
            "message": "Add billing now so your reporting workflow continues uninterrupted after the trial.",
        }
    else:
        access_message = "Subscription required to create new reports and share bundles."
        trial_banner = None
        upgrade_nudge = {
            "show": True,
            "reason": "subscription_required",
            "message": "Start or upgrade your subscription to create new reports and share bundles.",
        }

    stripe_connect = stripe_connect or {}
    connect_reminder = {
        "show": not bool(stripe_connect.get("has_stripe_connect_account")),
        "level": "warning",
        "title": "Stripe Connect setup recommended",
        "message": "Set up Stripe Connect so paid report/share-bundle revenue can be routed directly to your practitioner account.",
        "cta_label": "Set up Stripe Connect",
        "cta_url": stripe_connect.get("onboarding_url") or "/app/revenue",
    }

    return {
        "id": str(sub.get("id")) if sub.get("id") else None,
        "subscription_id": str(sub.get("id")) if sub.get("id") else None,
        "plan_code": sub.get("plan_code") or DEFAULT_PLAN_CODE,
        "status": status,
        "effective_status": status,
        "is_active": usable,
        "can_create_reports": usable,
        "can_create_shares": usable,
        "can_create_bundles": usable,
        "can_create_share_bundles": usable,
        "can_view_existing": True,
        "current_period_end": _dt_to_iso(end),
        "trial_ends_at": _dt_to_iso(trial_end),
        "days_remaining": days_remaining,
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
        "stripe_customer_id": sub.get("stripe_customer_id"),
        "stripe_subscription_id": sub.get("stripe_subscription_id"),
        "stripe_price_id": sub.get("stripe_price_id"),
        "voucher_code": sub.get("voucher_code"),
        "access_message": access_message,
        "trial_banner": trial_banner,
        "upgrade_nudge": upgrade_nudge,
        "stripe_connect": stripe_connect,
        "stripe_connect_reminder": connect_reminder,
    }


def _upsert_subscription_from_stripe(
    db: Session,
    *,
    user_id=None,
    customer_id=None,
    subscription_id=None,
    status=None,
    current_period_end=None,
    cancel_at_period_end=False,
    price_id=None,
    voucher_code=None,
):
    if not user_id and customer_id:
        row = db.execute(
            text(
                """
                SELECT user_id
                FROM subscriptions
                WHERE stripe_customer_id = :customer_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"customer_id": customer_id},
        ).mappings().first()
        user_id = row["user_id"] if row else None

    if not user_id:
        return

    existing = db.execute(
        text(
            """
            SELECT id
            FROM subscriptions
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"user_id": user_id},
    ).mappings().first()

    mapped_status = _map_stripe_status(status)

    if existing:
        db.execute(
            text(
                """
                UPDATE subscriptions
                SET stripe_customer_id = COALESCE(:customer_id, stripe_customer_id),
                    stripe_subscription_id = COALESCE(:subscription_id, stripe_subscription_id),
                    stripe_price_id = COALESCE(:price_id, stripe_price_id),
                    status = :status,
                    current_period_end = COALESCE(:current_period_end, current_period_end),
                    cancel_at_period_end = :cancel_at_period_end,
                    voucher_code = COALESCE(:voucher_code, voucher_code),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": existing["id"],
                "customer_id": customer_id,
                "subscription_id": subscription_id,
                "price_id": price_id,
                "status": mapped_status,
                "current_period_end": current_period_end,
                "cancel_at_period_end": bool(cancel_at_period_end),
                "voucher_code": voucher_code,
            },
        )
    else:
        db.execute(
            text(
                """
                INSERT INTO subscriptions (
                    id,
                    user_id,
                    stripe_customer_id,
                    stripe_subscription_id,
                    stripe_price_id,
                    plan_code,
                    status,
                    current_period_end,
                    cancel_at_period_end,
                    voucher_code,
                    created_at,
                    updated_at
                ) VALUES (
                    gen_random_uuid(),
                    :user_id,
                    :customer_id,
                    :subscription_id,
                    :price_id,
                    :plan_code,
                    :status,
                    :current_period_end,
                    :cancel_at_period_end,
                    :voucher_code,
                    NOW(),
                    NOW()
                )
                """
            ),
            {
                "user_id": user_id,
                "customer_id": customer_id,
                "subscription_id": subscription_id,
                "price_id": price_id,
                "plan_code": DEFAULT_PLAN_CODE,
                "status": mapped_status,
                "current_period_end": current_period_end,
                "cancel_at_period_end": bool(cancel_at_period_end),
                "voucher_code": voucher_code,
            },
        )

    db.commit()


def _get_stripe_connect_status(db: Session, current_user: User) -> dict[str, Any]:
    """Best-effort Stripe Connect status for subscription/settings UI.

    This is intentionally defensive because staging/prod schemas may differ while
    the revenue/Connect module is evolving. Missing columns/tables should never
    break subscription status.
    """
    candidates = [
        (
            "practitioner_settings",
            """
            SELECT
                stripe_connect_account_id,
                stripe_connect_onboarding_complete,
                stripe_connect_charges_enabled,
                stripe_connect_payouts_enabled
            FROM practitioner_settings
            WHERE user_id = :user_id
            LIMIT 1
            """,
        ),
        (
            "stripe_connect_accounts",
            """
            SELECT
                stripe_account_id AS stripe_connect_account_id,
                onboarding_complete AS stripe_connect_onboarding_complete,
                charges_enabled AS stripe_connect_charges_enabled,
                payouts_enabled AS stripe_connect_payouts_enabled
            FROM stripe_connect_accounts
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 1
            """,
        ),
    ]

    row = None
    for _name, sql in candidates:
        try:
            row = db.execute(text(sql), {"user_id": current_user.id}).mappings().first()
            if row:
                break
        except Exception:
            db.rollback()
            row = None

    account_id = None
    onboarding_complete = False
    charges_enabled = False
    payouts_enabled = False

    if row:
        account_id = row.get("stripe_connect_account_id")
        onboarding_complete = bool(row.get("stripe_connect_onboarding_complete"))
        charges_enabled = bool(row.get("stripe_connect_charges_enabled"))
        payouts_enabled = bool(row.get("stripe_connect_payouts_enabled"))

    has_account = bool(account_id)
    ready = has_account and onboarding_complete and charges_enabled and payouts_enabled

    return {
        "has_stripe_connect_account": has_account,
        "stripe_connect_account_id": account_id,
        "onboarding_complete": onboarding_complete,
        "charges_enabled": charges_enabled,
        "payouts_enabled": payouts_enabled,
        "ready_for_direct_payouts": ready,
        "onboarding_url": "/app/revenue",
    }


@router.get("/api/subscription/status")
def get_subscription_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.platform_settings_service import is_platform_admin_user

    if is_platform_admin_user(db, current_user):
        return {
            "id": None,
            "subscription_id": None,
            "plan_code": "internal_admin",
            "status": "platform_admin",
            "effective_status": "platform_admin",
            "is_active": True,
            "can_create_reports": True,
            "can_create_shares": True,
            "can_create_bundles": True,
            "can_create_share_bundles": True,
            "can_view_existing": True,
            "current_period_end": None,
            "trial_ends_at": None,
            "days_remaining": None,
            "cancel_at_period_end": False,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "stripe_price_id": None,
            "voucher_code": None,
            "access_message": "Platform admin access.",
            "trial_banner": None,
            "upgrade_nudge": None,
            "stripe_connect": {"has_stripe_connect_account": True, "ready_for_direct_payouts": True},
            "stripe_connect_reminder": {"show": False},
        }

    sub = _ensure_subscription_record(db, current_user)
    stripe_connect = _get_stripe_connect_status(db, current_user)
    return _subscription_payload(sub, stripe_connect=stripe_connect)


@router.post("/api/subscription/checkout")
def create_subscription_checkout(
    payload: SubscriptionCheckoutRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe is not configured. Set STRIPE_SECRET_KEY.")
    if not STRIPE_SUBSCRIPTION_PRICE_ID:
        raise HTTPException(status_code=500, detail="Subscription price is not configured. Set STRIPE_SUBSCRIPTION_PRICE_ID.")

    sub = _ensure_subscription_record(db, current_user)
    customer_id = sub.get("stripe_customer_id")

    if not customer_id:
        customer = stripe.Customer.create(
            email=current_user.email,
            name=current_user.full_name,
            metadata={"user_id": str(current_user.id), "source": "qrma_subscription"},
        )
        customer_id = customer.id
        db.execute(
            text(
                """
                UPDATE subscriptions
                SET stripe_customer_id = :customer_id,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"customer_id": customer_id, "id": sub["id"]},
        )
        db.commit()

    voucher_code = (payload.voucher_code or "").strip() or None

    checkout_args = {
        "mode": "subscription",
        "customer": customer_id,
        "success_url": f"{BASE_URL.rstrip('/')}/app/settings?subscription=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{BASE_URL.rstrip('/')}/app/settings?subscription=cancelled",
        "client_reference_id": str(current_user.id),
        "line_items": [{"price": STRIPE_SUBSCRIPTION_PRICE_ID, "quantity": 1}],
        "metadata": {
            "kind": "practitioner_subscription",
            "user_id": str(current_user.id),
            "voucher_code": voucher_code or "",
        },
        "subscription_data": {
            "metadata": {
                "kind": "practitioner_subscription",
                "user_id": str(current_user.id),
                "voucher_code": voucher_code or "",
            }
        },
    }

    if voucher_code:
        promo = stripe.PromotionCode.list(code=voucher_code, active=True, limit=1)
        if not promo or not promo.data:
            raise HTTPException(status_code=400, detail="Voucher code was not found or is no longer active.")
        checkout_args["discounts"] = [{"promotion_code": promo.data[0].id}]
    elif STRIPE_ALLOW_PROMOTION_CODES:
        checkout_args["allow_promotion_codes"] = True

    session = stripe.checkout.Session.create(**checkout_args)

    db.execute(
        text(
            """
            UPDATE subscriptions
            SET stripe_checkout_session_id = :session_id,
                stripe_price_id = :price_id,
                voucher_code = COALESCE(:voucher_code, voucher_code),
                updated_at = NOW()
            WHERE id = :id
            """
        ),
        {
            "session_id": session.id,
            "price_id": STRIPE_SUBSCRIPTION_PRICE_ID,
            "voucher_code": voucher_code,
            "id": sub["id"],
        },
    )
    db.commit()

    return {"url": session.url, "session_id": session.id}


@router.post("/api/subscription/customer-portal")
def create_subscription_customer_portal(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe is not configured. Set STRIPE_SECRET_KEY.")

    sub = _ensure_subscription_record(db, current_user)
    customer_id = sub.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=409, detail="No Stripe customer exists yet. Start a subscription checkout first.")

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{BASE_URL.rstrip('/')}{SUBSCRIPTION_PORTAL_RETURN_PATH}",
    )
    return {"url": session.url}


@router.post("/api/subscription/stripe-webhook")
async def subscription_stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not STRIPE_SUBSCRIPTION_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Subscription webhook secret is not configured.")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_SUBSCRIPTION_WEBHOOK_SECRET)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Stripe webhook: {exc}")

    event_type = event.get("type")
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed" and (obj.get("metadata") or {}).get("kind") == "practitioner_subscription":
        user_id = (obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id")
        subscription_id = obj.get("subscription")
        subscription_obj = None

        if subscription_id:
            try:
                subscription_obj = stripe.Subscription.retrieve(subscription_id)
            except Exception:
                subscription_obj = None

        _upsert_subscription_from_stripe(
            db,
            user_id=user_id,
            customer_id=obj.get("customer"),
            subscription_id=subscription_id,
            status=(subscription_obj.get("status") if subscription_obj else "active"),
            current_period_end=_timestamp_to_dt(subscription_obj.get("current_period_end")) if subscription_obj else None,
            cancel_at_period_end=bool(subscription_obj.get("cancel_at_period_end")) if subscription_obj else False,
            price_id=STRIPE_SUBSCRIPTION_PRICE_ID,
            voucher_code=(obj.get("metadata") or {}).get("voucher_code") or None,
        )

    elif event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        metadata = obj.get("metadata") or {}
        user_id = metadata.get("user_id")
        items = ((obj.get("items") or {}).get("data") or [])
        price_id = None
        if items:
            price = items[0].get("price") or {}
            price_id = price.get("id")

        _upsert_subscription_from_stripe(
            db,
            user_id=user_id,
            customer_id=obj.get("customer"),
            subscription_id=obj.get("id"),
            status=obj.get("status"),
            current_period_end=_timestamp_to_dt(obj.get("current_period_end")),
            cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
            price_id=price_id,
            voucher_code=metadata.get("voucher_code"),
        )

    elif event_type == "invoice.payment_failed":
        customer_id = obj.get("customer")
        row = db.execute(
            text(
                """
                SELECT id
                FROM subscriptions
                WHERE stripe_customer_id = :customer_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"customer_id": customer_id},
        ).mappings().first()
        if row:
            db.execute(
                text("UPDATE subscriptions SET status = 'past_due', updated_at = NOW() WHERE id = :id"),
                {"id": row["id"]},
            )
            db.commit()

    return {"received": True}
