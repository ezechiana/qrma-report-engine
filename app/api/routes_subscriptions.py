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
        end = sub.get("current_period_end") or sub.get("trial_ends_at")
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
                trial_ends_at,
                cancel_at_period_end,
                created_at,
                updated_at
            ) VALUES (
                gen_random_uuid(),
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
        {
            "user_id": current_user.id,
            "plan_code": DEFAULT_PLAN_CODE,
            "trial_end": trial_end,
        },
    ).mappings().first()
    db.commit()
    return dict(row)


def _subscription_payload(sub: dict[str, Any]) -> dict[str, Any]:
    status = sub.get("status") or "incomplete"
    usable = _is_subscription_usable(sub)
    end = sub.get("current_period_end") or sub.get("trial_ends_at")
    trial_end = sub.get("trial_ends_at")

    return {
        "id": str(sub.get("id")) if sub.get("id") else None,
        "plan_code": sub.get("plan_code") or DEFAULT_PLAN_CODE,
        "status": status,
        "is_active": usable,
        "can_create_reports": usable,
        "can_create_shares": usable,
        "can_create_bundles": usable,
        "current_period_end": _dt_to_iso(end),
        "trial_ends_at": _dt_to_iso(trial_end),
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
        "stripe_customer_id": sub.get("stripe_customer_id"),
        "stripe_subscription_id": sub.get("stripe_subscription_id"),
        "stripe_price_id": sub.get("stripe_price_id"),
        "voucher_code": sub.get("voucher_code"),
        "access_message": (
            "Subscription active."
            if usable and status == "active"
            else "Trial active."
            if usable and status == "trialing"
            else "Subscription required to create new reports and share bundles."
        ),
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

    if mapped_status == "active":
        try:
            from app.services.referral_service import award_referral_if_eligible

            award_referral_if_eligible(db, referred_user_id=user_id)
        except Exception as exc:
            db.rollback()
            print(f"[referrals] failed to award referral for user {user_id}: {exc}")


@router.get("/api/subscription/status")
def get_subscription_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = _ensure_subscription_record(db, current_user)
    return _subscription_payload(sub)


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
