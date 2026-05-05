from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.services.referral_service import (
    ensure_user_referral_code,
    get_referral_config,
    referral_summary,
)

router = APIRouter(prefix="/api/referrals", tags=["referrals"])


def _base_url(request: Request) -> str:
    configured = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


@router.get("/me")
def get_my_referral(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    config = get_referral_config(db)

    if not config.get("enabled"):
        code = ensure_user_referral_code(db, current_user)
        return {
            "enabled": False,
            "referral_code": code,
            "referral_link": None,
            "reward_months": config.get("reward_months", 1),
            "counts": {"total": 0, "signed_up": 0, "paid": 0, "rewarded": 0},
            "months_awarded": 0,
            "months_available": 0,
            "months_used": 0,
            "recent": [],
            "headline": "Referral programme is currently disabled.",
            "revenue_generated_by_currency": {},
            "commission_generated_by_currency": {},
        }

    return referral_summary(db, user=current_user, base_url=_base_url(request))


@router.get("/stats")
def get_referral_stats(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    summary = referral_summary(db, user=current_user, base_url=_base_url(request))
    counts = summary.get("counts") or {}
    return {
        "total": int(counts.get("total") or 0),
        "signed_up": int(counts.get("signed_up") or 0),
        "paid": int(counts.get("paid") or 0),
        "rewarded": int(counts.get("rewarded") or 0),
        "months_awarded": int(summary.get("months_awarded") or 0),
        "months_available": int(summary.get("months_available") or 0),
        "months_used": int(summary.get("months_used") or 0),
        "revenue_generated_by_currency": summary.get("revenue_generated_by_currency") or {},
        "commission_generated_by_currency": summary.get("commission_generated_by_currency") or {},
    }
