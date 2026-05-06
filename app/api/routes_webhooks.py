from __future__ import annotations

import json
import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import ShareBundle
from app.api.routes_share_bundles import (
    _extract_stripe_financials,
    _find_bundle_by_payment_intent,
    _mark_bundle_paid,
    _track_share_event,
)
from app.api.routes_subscriptions import (
    STRIPE_SUBSCRIPTION_PRICE_ID,
    _timestamp_to_dt,
    _upsert_subscription_from_stripe,
)
from app.services.referral_service import award_referral_if_eligible

router = APIRouter(tags=["webhooks"])

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


def _ensure_webhook_events_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                payload JSONB
            )
            """
        )
    )
    db.commit()


def _webhook_event_seen(db: Session, event_id: str | None) -> bool:
    if not event_id:
        return False
    _ensure_webhook_events_table(db)
    row = db.execute(
        text("SELECT 1 FROM stripe_webhook_events WHERE event_id = :event_id"),
        {"event_id": event_id},
    ).first()
    return bool(row)


def _record_webhook_event(db: Session, event_id: str | None, event_type: str | None, payload: dict | None = None) -> None:
    if not event_id:
        return
    _ensure_webhook_events_table(db)
    db.execute(
        text(
            """
            INSERT INTO stripe_webhook_events (event_id, event_type, payload)
            VALUES (:event_id, :event_type, CAST(:payload AS JSONB))
            ON CONFLICT (event_id) DO NOTHING
            """
        ),
        {
            "event_id": event_id,
            "event_type": event_type or "unknown",
            "payload": json.dumps(payload or {}),
        },
    )
    db.commit()


def _sync_subscription_checkout(db: Session, session: dict) -> None:
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != "practitioner_subscription":
        return

    user_id = metadata.get("user_id") or session.get("client_reference_id")
    subscription_id = session.get("subscription")
    subscription_obj = None

    if subscription_id:
        try:
            subscription_obj = stripe.Subscription.retrieve(subscription_id)
        except Exception as exc:
            print(f"[STRIPE] Could not retrieve subscription {subscription_id}: {exc}")

    _upsert_subscription_from_stripe(
        db,
        user_id=user_id,
        customer_id=session.get("customer"),
        subscription_id=subscription_id,
        status=(subscription_obj.get("status") if subscription_obj else "active"),
        current_period_end=_timestamp_to_dt(subscription_obj.get("current_period_end")) if subscription_obj else None,
        cancel_at_period_end=bool(subscription_obj.get("cancel_at_period_end")) if subscription_obj else False,
        price_id=STRIPE_SUBSCRIPTION_PRICE_ID,
        voucher_code=metadata.get("voucher_code") or None,
    )


def _sync_subscription_object(db: Session, subscription: dict) -> None:
    metadata = subscription.get("metadata") or {}
    user_id = metadata.get("user_id")

    items = ((subscription.get("items") or {}).get("data") or [])
    price_id = None
    if items:
        price = items[0].get("price") or {}
        price_id = price.get("id")

    _upsert_subscription_from_stripe(
        db,
        user_id=user_id,
        customer_id=subscription.get("customer"),
        subscription_id=subscription.get("id"),
        status=subscription.get("status"),
        current_period_end=_timestamp_to_dt(subscription.get("current_period_end")),
        cancel_at_period_end=bool(subscription.get("cancel_at_period_end")),
        price_id=price_id,
        voucher_code=metadata.get("voucher_code"),
    )

    if subscription.get("status") == "active":
        row = db.execute(
            text(
                """
                SELECT user_id
                FROM subscriptions
                WHERE stripe_subscription_id = :subscription_id
                   OR stripe_customer_id = :customer_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {
                "subscription_id": subscription.get("id"),
                "customer_id": subscription.get("customer"),
            },
        ).mappings().first()

        if row:
            try:
                award_referral_if_eligible(db, referred_user_id=row["user_id"])
            except Exception as exc:
                db.rollback()
                print(f"[REFERRAL] Award skipped after subscription activation: {exc}")


def _mark_subscription_past_due(db: Session, invoice: dict) -> None:
    customer_id = invoice.get("customer")
    if not customer_id:
        return

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


def _fulfil_share_bundle_checkout(db: Session, session: dict) -> None:
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != "share_bundle":
        return

    bundle_id = metadata.get("bundle_id")
    bundle = db.query(ShareBundle).filter(ShareBundle.id == bundle_id).first() if bundle_id else None
    if not bundle:
        print(f"[STRIPE] Share bundle not found for checkout session {session.get('id')}")
        return

    payment_intent_id = session.get("payment_intent")
    refs = _extract_stripe_financials(payment_intent_id)

    _mark_bundle_paid(
        db,
        bundle,
        session_id=session.get("id"),
        payment_intent_id=payment_intent_id,
        charge_id=refs.get("charge_id"),
        transfer_id=refs.get("transfer_id"),
        connect_account_id=metadata.get("stripe_connect_account_id") or None,
        connect_mode=metadata.get("connect_mode") or None,
        platform_fee_amount=int(metadata.get("platform_fee_amount") or 0),
        platform_fee_currency=(bundle.price_currency or "gbp").lower(),
        stripe_fee_amount=refs.get("stripe_fee_amount"),
        stripe_fee_currency=refs.get("stripe_fee_currency"),
    )

    _track_share_event(
        db,
        bundle.token,
        "paid",
        {
            "share_type": "bundle",
            "stripe_session_id": session.get("id"),
            "stripe_payment_intent_id": payment_intent_id,
            "source": "checkout.session.completed",
        },
    )


def _fulfil_share_bundle_payment_intent(db: Session, obj: dict, event_type: str) -> None:
    payment_intent_id = obj.get("id") if event_type == "payment_intent.succeeded" else obj.get("payment_intent")
    charge_id = obj.get("id") if event_type in {"charge.succeeded", "charge.updated"} else None

    bundle = _find_bundle_by_payment_intent(db, payment_intent_id)
    if not bundle:
        return

    refs = _extract_stripe_financials(payment_intent_id)

    _mark_bundle_paid(
        db,
        bundle,
        payment_intent_id=payment_intent_id,
        charge_id=refs.get("charge_id") or charge_id,
        transfer_id=refs.get("transfer_id"),
        stripe_fee_amount=refs.get("stripe_fee_amount"),
        stripe_fee_currency=refs.get("stripe_fee_currency"),
    )

    _track_share_event(
        db,
        bundle.token,
        "paid",
        {
            "share_type": "bundle",
            "stripe_payment_intent_id": payment_intent_id,
            "source": event_type,
        },
    )


@router.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Stripe webhook secret is not configured.")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Stripe webhook: {exc}")

    event_id = event.get("id")
    event_type = event.get("type")
    obj = event.get("data", {}).get("object", {})

    print(f"[STRIPE] Event received: {event_type} ({event_id})")

    if _webhook_event_seen(db, event_id):
        print(f"[STRIPE] Duplicate ignored: {event_id}")
        return {"received": True, "duplicate": True}

    try:
        if event_type == "checkout.session.completed":
            metadata = obj.get("metadata") or {}
            if metadata.get("kind") == "share_bundle":
                _fulfil_share_bundle_checkout(db, obj)
            elif metadata.get("kind") == "practitioner_subscription":
                _sync_subscription_checkout(db, obj)

        elif event_type in {"payment_intent.succeeded", "charge.succeeded", "charge.updated"}:
            _fulfil_share_bundle_payment_intent(db, obj, event_type)

        elif event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            _sync_subscription_object(db, obj)

        elif event_type == "invoice.payment_succeeded":
            subscription_id = obj.get("subscription")
            if subscription_id:
                subscription = stripe.Subscription.retrieve(subscription_id)
                _sync_subscription_object(db, subscription)

        elif event_type == "invoice.payment_failed":
            _mark_subscription_past_due(db, obj)

        _record_webhook_event(db, event_id, event_type, event)

    except Exception:
        db.rollback()
        print(f"[STRIPE] Event failed and will be retried by Stripe: {event_type} ({event_id})")
        raise

    return {"received": True}