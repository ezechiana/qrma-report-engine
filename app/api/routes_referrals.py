from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.services.referral_service import ensure_user_referral_code, get_referral_config

router = APIRouter(prefix="/api/referrals", tags=["referrals"])


@router.get("/me")
def get_my_referral(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    config = get_referral_config(db)

    if not config.get("enabled"):
        return {
            "enabled": False,
            "referral_link": None,
        }

    code = ensure_user_referral_code(db, current_user)

    base_url = "http://127.0.0.1:8000"  # replace later with env BASE_URL
    link = f"{base_url}/register?ref={code}"

    return {
        "enabled": True,
        "referral_code": code,
        "referral_link": link,
        "reward_months": config.get("reward_months", 1),
    }