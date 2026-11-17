# app/api/routes_stripe_connect.py

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.share_helpers import (
    STRIPE_CONNECT_ACCOUNT_TYPE,
    STRIPE_CONNECT_COUNTRY,
    STRIPE_CONNECT_ENABLED,
    _ensure_practitioner_settings,
    _get_practitioner_connect_state,
    _stripe_api_request,
    _sync_connect_account_status,
)
from app.db.models import User
from app.services.audit_service import log_action

router = APIRouter(tags=["share"])


@router.get("/api/billing/stripe-connect/status")
def get_stripe_connect_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    state = _get_practitioner_connect_state(db, current_user.id)

    if state.get("account_id"):
        try:
            state = _sync_connect_account_status(db, current_user.id, state["account_id"])
        except HTTPException:
            # Keep local state if Stripe is temporarily unavailable.
            pass

    return state


@router.post("/api/billing/stripe-connect/account-link")
def create_stripe_connect_account_link(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not STRIPE_CONNECT_ENABLED:
        raise HTTPException(status_code=409, detail="Stripe Connect is disabled.")

    _ensure_practitioner_settings(db, current_user)
    state = _get_practitioner_connect_state(db, current_user.id)
    account_id = state.get("account_id")

    if not account_id:
        account = _stripe_api_request(
            "POST",
            "/accounts",
            {
                "type": STRIPE_CONNECT_ACCOUNT_TYPE,
                "country": STRIPE_CONNECT_COUNTRY,
                "capabilities[card_payments][requested]": "true",
                "capabilities[transfers][requested]": "true",
                "metadata[user_id]": str(current_user.id),
                "metadata[source]": "qrma_health_portal",
            },
        )
        account_id = account["id"]

        db.execute(
            text(
                """
                UPDATE practitioner_settings
                SET stripe_connect_account_id = :account_id,
                    stripe_connect_country = :country,
                    stripe_connect_account_type = :account_type,
                    stripe_connect_last_sync_at = NOW()
                WHERE user_id = :user_id
                """
            ),
            {
                "user_id": current_user.id,
                "account_id": account_id,
                "country": account.get("country") or STRIPE_CONNECT_COUNTRY,
                "account_type": account.get("type") or STRIPE_CONNECT_ACCOUNT_TYPE,
            },
        )
        db.commit()

    base_url = str(request.base_url).rstrip("/")
    account_link = _stripe_api_request(
        "POST",
        "/account_links",
        {
            "account": account_id,
            "refresh_url": f"{base_url}/app/settings?stripe_connect=refresh",
            "return_url": f"{base_url}/app/settings?stripe_connect=return",
            "type": "account_onboarding",
        },
    )

    log_action(
        db,
        "stripe_connect_account_link_created",
        user_id=current_user.id,
        metadata_json={"stripe_connect_account_id": account_id},
    )

    return {
        "url": account_link["url"],
        "account_id": account_id,
        "status": _get_practitioner_connect_state(db, current_user.id),
    }


@router.post("/api/billing/stripe-connect/refresh")
def refresh_stripe_connect_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    state = _get_practitioner_connect_state(db, current_user.id)
    account_id = state.get("account_id")
    if not account_id:
        return state

    return _sync_connect_account_status(db, current_user.id, account_id)
