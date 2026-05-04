from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.services.referral_service import (
    ensure_user_referral_code,
    get_referral_config,
    get_referral_summary,
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
        return {
            "enabled": False,
            "referral_code": None,
            "referral_link": None,
            "counts": {"total": 0, "signed_up": 0, "paid": 0, "rewarded": 0},
            "months_awarded": 0,
            "months_available": 0,
            "months_used": 0,
            "recent": [],
            "headline": "Referral programme is currently disabled.",
        }

    summary = get_referral_summary(db, current_user)
    code = summary.get("referral_code") or ensure_user_referral_code(db, current_user)
    summary["referral_code"] = code
    summary["referral_link"] = f"{_base_url(request)}/register?ref={code}"
    return summary


@router.get("/stats")
def get_referral_stats(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    summary = get_referral_summary(db, current_user)
    counts = summary.get("counts") or {}
    return {
        "total": int(counts.get("total") or 0),
        "signed_up": int(counts.get("signed_up") or 0),
        "paid": int(counts.get("paid") or 0),
        "rewarded": int(counts.get("rewarded") or 0),
        "months_awarded": int(summary.get("months_awarded") or 0),
        "months_available": int(summary.get("months_available") or 0),
        "months_used": int(summary.get("months_used") or 0),
    }
