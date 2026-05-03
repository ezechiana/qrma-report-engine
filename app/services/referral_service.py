from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

REFERRAL_ENABLED = os.getenv("REFERRAL_ENABLED", "true").lower() == "true"
DEFAULT_REWARD_MONTHS = int(os.getenv("REFERRAL_REWARD_MONTHS", "1"))
REFERRAL_SETTINGS_KEY = "referral_config"
DEFAULT_PLAN_CODE = os.getenv("DEFAULT_SUBSCRIPTION_PLAN_CODE", os.getenv("SUBSCRIPTION_PLAN_CODE", "practitioner_monthly"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_code(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = "".join(ch for ch in str(value).strip().upper() if ch.isalnum() or ch in {"-", "_"})
    return cleaned or None


def generate_referral_code() -> str:
    return secrets.token_urlsafe(6).replace("-", "").replace("_", "").upper()[:10]


def get_referral_config(db: Session) -> dict[str, Any]:
    """Read referral configuration. Schema is managed by migrations, never request-time DDL."""
    default_config = {
        "enabled": REFERRAL_ENABLED,
        "reward_type": "free_months",
        "reward_months": DEFAULT_REWARD_MONTHS,
        "trigger": "referred_user_becomes_paid",
    }
    try:
        row = db.execute(
            text("SELECT value FROM app_settings WHERE key = :key"),
            {"key": REFERRAL_SETTINGS_KEY},
        ).mappings().first()
    except Exception:
        db.rollback()
        return default_config

    if not row:
        return default_config

    value = row["value"] or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}

    return {
        "enabled": bool(value.get("enabled", REFERRAL_ENABLED)),
        "reward_type": value.get("reward_type") or "free_months",
        "reward_months": int(value.get("reward_months") or DEFAULT_REWARD_MONTHS),
        "trigger": value.get("trigger") or "referred_user_becomes_paid",
    }


def ensure_user_referral_code(db: Session, user) -> str:
    row = db.execute(
        text("SELECT referral_code FROM users WHERE id = :user_id"),
        {"user_id": user.id},
    ).mappings().first()

    existing = _normalise_code(row["referral_code"] if row else None)
    if existing:
        try:
            setattr(user, "referral_code", existing)
        except Exception:
            pass
        return existing

    for _ in range(20):
        code = generate_referral_code()
        exists = db.execute(text("SELECT 1 FROM users WHERE referral_code = :code"), {"code": code}).first()
        if exists:
            continue
        db.execute(text("UPDATE users SET referral_code = :code WHERE id = :user_id"), {"code": code, "user_id": user.id})
        db.commit()
        try:
            setattr(user, "referral_code", code)
        except Exception:
            pass
        return code

    raise RuntimeError("Could not generate a unique referral code.")


def register_referral_signup(db: Session, *, referred_user, referral_code: str | None) -> None:
    config = get_referral_config(db)
    if not config.get("enabled"):
        return

    code = _normalise_code(referral_code)
    if not code:
        return

    referrer = db.execute(text("SELECT id FROM users WHERE referral_code = :code LIMIT 1"), {"code": code}).mappings().first()
    if not referrer:
        return

    referrer_id = referrer["id"]
    if str(referrer_id) == str(referred_user.id):
        return

    existing = db.execute(
        text("SELECT id FROM referrals WHERE referred_user_id = :referred_user_id LIMIT 1"),
        {"referred_user_id": referred_user.id},
    ).mappings().first()
    if existing:
        return

    db.execute(
        text("""
            INSERT INTO referrals (referrer_user_id, referred_user_id, referral_code, status, created_at, updated_at)
            VALUES (:referrer_user_id, :referred_user_id, :referral_code, 'signed_up', NOW(), NOW())
        """),
        {"referrer_user_id": referrer_id, "referred_user_id": referred_user.id, "referral_code": code},
    )
    db.commit()


def get_subscription_credit_summary(db: Session, *, user_id) -> dict[str, int]:
    row = db.execute(
        text("""
            SELECT months_available, months_used
            FROM subscription_credits
            WHERE user_id = :user_id
        """),
        {"user_id": user_id},
    ).mappings().first()

    rewards = db.execute(
        text("""
            SELECT COALESCE(SUM(months_awarded), 0) AS months_awarded
            FROM referral_rewards
            WHERE referrer_user_id = :user_id
        """),
        {"user_id": user_id},
    ).mappings().first()

    return {
        "months_available": int(row["months_available"] or 0) if row else 0,
        "months_used": int(row["months_used"] or 0) if row else 0,
        "months_awarded": int(rewards["months_awarded"] or 0) if rewards else 0,
    }


def apply_subscription_credits(db: Session, *, user_id) -> bool:
    """Apply any available free-month credit to the user's current subscription period."""
    credit = db.execute(
        text("""
            SELECT months_available
            FROM subscription_credits
            WHERE user_id = :user_id
            FOR UPDATE
        """),
        {"user_id": user_id},
    ).mappings().first()

    if not credit or int(credit["months_available"] or 0) <= 0:
        return False

    months = int(credit["months_available"] or 0)

    db.execute(
        text("""
            INSERT INTO subscriptions (user_id, plan_code, status, current_period_end, created_at, updated_at)
            SELECT :user_id, :plan_code, 'active', NOW(), NOW(), NOW()
            WHERE NOT EXISTS (SELECT 1 FROM subscriptions WHERE user_id = :user_id)
        """),
        {"user_id": user_id, "plan_code": DEFAULT_PLAN_CODE},
    )

    db.execute(
        text("""
            UPDATE subscriptions
            SET status = CASE
                    WHEN status IN ('canceled', 'trial_expired', 'expired', 'incomplete', 'past_due') THEN 'active'
                    ELSE status
                END,
                current_period_end = GREATEST(COALESCE(current_period_end, NOW()), NOW()) + (:months * INTERVAL '1 month'),
                updated_at = NOW()
            WHERE id = (
                SELECT id
                FROM subscriptions
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                LIMIT 1
            )
        """),
        {"user_id": user_id, "months": months},
    )

    db.execute(
        text("""
            UPDATE subscription_credits
            SET months_used = months_used + :months,
                months_available = months_available - :months,
                updated_at = NOW()
            WHERE user_id = :user_id
        """),
        {"user_id": user_id, "months": months},
    )
    db.commit()
    return True


def award_referral_if_eligible(db: Session, *, referred_user_id) -> bool:
    """Award the referrer once when the referred user becomes a paid subscriber."""
    config = get_referral_config(db)
    if not config.get("enabled"):
        return False

    months = int(config.get("reward_months") or DEFAULT_REWARD_MONTHS)
    if months <= 0:
        return False

    ref = db.execute(
        text("""
            SELECT r.*
            FROM referrals r
            WHERE r.referred_user_id = :referred_user_id
              AND r.status IN ('signed_up', 'trialing', 'paid')
              AND NOT EXISTS (
                  SELECT 1
                  FROM referral_rewards rr
                  WHERE rr.referrer_user_id = r.referrer_user_id
                    AND rr.referred_user_id = r.referred_user_id
              )
            ORDER BY r.created_at ASC
            LIMIT 1
        """),
        {"referred_user_id": referred_user_id},
    ).mappings().first()

    if not ref:
        return False

    db.execute(
        text("""
            INSERT INTO referral_rewards (referrer_user_id, referred_user_id, months_awarded, created_at)
            VALUES (:referrer_user_id, :referred_user_id, :months, NOW())
            ON CONFLICT (referrer_user_id, referred_user_id) DO NOTHING
        """),
        {"referrer_user_id": ref["referrer_user_id"], "referred_user_id": referred_user_id, "months": months},
    )

    db.execute(
        text("""
            INSERT INTO subscription_credits (user_id, months_available, months_used, updated_at)
            VALUES (:user_id, :months, 0, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET months_available = subscription_credits.months_available + :months,
                          updated_at = NOW()
        """),
        {"user_id": ref["referrer_user_id"], "months": months},
    )

    db.execute(
        text("""
            UPDATE referrals
            SET status = 'rewarded', updated_at = NOW()
            WHERE id = :referral_id
        """),
        {"referral_id": ref["id"]},
    )
    db.commit()

    # V1 policy: apply free-month credit immediately to the current subscription period.
    apply_subscription_credits(db, user_id=ref["referrer_user_id"])
    return True


def referral_summary(db: Session, *, user, base_url: str) -> dict[str, Any]:
    config = get_referral_config(db)
    code = ensure_user_referral_code(db, user)
    base = base_url.rstrip("/")
    link = f"{base}/register?ref={code}"

    counts = db.execute(
        text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'signed_up') AS signed_up,
                COUNT(*) FILTER (WHERE status = 'paid') AS paid,
                COUNT(*) FILTER (WHERE status = 'rewarded') AS rewarded
            FROM referrals
            WHERE referrer_user_id = :user_id
        """),
        {"user_id": user.id},
    ).mappings().first()

    credit_summary = get_subscription_credit_summary(db, user_id=user.id)

    recent_rows = db.execute(
        text("""
            SELECT r.status, r.created_at, r.updated_at, u.email, u.full_name
            FROM referrals r
            LEFT JOIN users u ON u.id = r.referred_user_id
            WHERE r.referrer_user_id = :user_id
            ORDER BY r.created_at DESC
            LIMIT 10
        """),
        {"user_id": user.id},
    ).mappings().all()

    reward_months = int(config.get("reward_months") or DEFAULT_REWARD_MONTHS)

    return {
        "enabled": bool(config.get("enabled")),
        "reward_type": config.get("reward_type"),
        "reward_months": reward_months,
        "headline": f"Give a colleague access. Get {reward_months} month{'s' if reward_months != 1 else ''} free.",
        "referral_code": code,
        "referral_link": link,
        "counts": {
            "total": int(counts["total"] or 0) if counts else 0,
            "signed_up": int(counts["signed_up"] or 0) if counts else 0,
            "paid": int(counts["paid"] or 0) if counts else 0,
            "rewarded": int(counts["rewarded"] or 0) if counts else 0,
        },
        "months_awarded": credit_summary["months_awarded"],
        "months_available": credit_summary["months_available"],
        "months_used": credit_summary["months_used"],
        "credit_summary": credit_summary,
        "recent": [
            {
                "status": row["status"],
                "email": row["email"],
                "full_name": row["full_name"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
            for row in recent_rows
        ],
    }
