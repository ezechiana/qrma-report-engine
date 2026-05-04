from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.platform_settings_service import is_feature_enabled

REFERRAL_ENABLED = os.getenv("REFERRAL_ENABLED", "true").lower() == "true"
DEFAULT_REWARD_MONTHS = int(os.getenv("REFERRAL_REWARD_MONTHS", "1"))
REFERRAL_SETTINGS_KEY = "referral_config"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_code(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = "".join(ch for ch in str(value).strip().upper() if ch.isalnum() or ch in {"-", "_"})
    return cleaned or None


def _row_to_dict(row) -> dict[str, Any] | None:
    return dict(row._mapping) if row else None


def _table_exists(db: Session, table_name: str) -> bool:
    try:
        row = db.execute(text("SELECT to_regclass(:name) AS exists"), {"name": f"public.{table_name}"}).mappings().first()
        return bool(row and row.get("exists"))
    except Exception:
        db.rollback()
        return False


def generate_referral_code() -> str:
    return secrets.token_urlsafe(8).replace("-", "").replace("_", "").upper()[:10]


def get_referral_config(db: Session) -> dict[str, Any]:
    """Read referral config from Platform Settings with env fallback.

    This function never creates tables and never raises for settings read failures;
    referral UI should not be able to break the app shell.
    """
    try:
        feature_enabled = is_feature_enabled(db, "referrals_enabled", default=REFERRAL_ENABLED)
    except Exception:
        db.rollback()
        feature_enabled = REFERRAL_ENABLED

    default_config = {
        "enabled": bool(feature_enabled),
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
    if not isinstance(value, dict):
        value = {}

    return {
        "enabled": bool(value.get("enabled", feature_enabled)) and bool(feature_enabled),
        "reward_type": value.get("reward_type") or "free_months",
        "reward_months": int(value.get("reward_months") or DEFAULT_REWARD_MONTHS),
        "trigger": value.get("trigger") or "referred_user_becomes_paid",
    }


def ensure_user_referral_code(db: Session, user) -> str:
    """Ensure the user has a referral code in users.referral_code."""
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

        db.execute(
            text("UPDATE users SET referral_code = :code, updated_at = NOW() WHERE id = :user_id"),
            {"code": code, "user_id": user.id},
        )
        db.commit()
        try:
            setattr(user, "referral_code", code)
        except Exception:
            pass
        return code

    raise RuntimeError("Could not generate a unique referral code.")


def register_referral_signup(db: Session, *, referred_user, referral_code: str | None) -> bool:
    """Record a referral relationship when a new user registers via /register?ref=CODE.

    Safe behaviour:
    - no-op if referrals are disabled
    - no-op if referrals table has not been migrated
    - no-op for self-referrals or invalid codes
    - no-op if this referred user already has a referral record
    """
    config = get_referral_config(db)
    if not config.get("enabled"):
        return False
    if not _table_exists(db, "referrals"):
        return False

    code = _normalise_code(referral_code)
    if not code:
        return False

    referrer = db.execute(
        text("SELECT id, email, full_name FROM users WHERE referral_code = :code LIMIT 1"),
        {"code": code},
    ).mappings().first()
    if not referrer:
        return False

    referrer_id = referrer["id"]
    if str(referrer_id) == str(referred_user.id):
        return False

    existing = db.execute(
        text("SELECT id FROM referrals WHERE referred_user_id = :referred_user_id LIMIT 1"),
        {"referred_user_id": referred_user.id},
    ).mappings().first()
    if existing:
        return False

    months = int(config.get("reward_months") or DEFAULT_REWARD_MONTHS)
    db.execute(
        text(
            """
            INSERT INTO referrals (
                referrer_user_id,
                referred_user_id,
                referral_code,
                status,
                reward_months,
                created_at,
                updated_at
            )
            VALUES (
                :referrer_user_id,
                :referred_user_id,
                :referral_code,
                'signed_up',
                :reward_months,
                NOW(),
                NOW()
            )
            """
        ),
        {
            "referrer_user_id": referrer_id,
            "referred_user_id": referred_user.id,
            "referral_code": code,
            "reward_months": months,
        },
    )
    db.commit()
    return True


def award_referral_if_eligible(db: Session, *, referred_user_id) -> bool:
    """Award the referrer once when the referred user becomes a paid subscriber."""
    config = get_referral_config(db)
    if not config.get("enabled"):
        return False
    if not _table_exists(db, "referrals") or not _table_exists(db, "referral_rewards"):
        return False

    months = int(config.get("reward_months") or DEFAULT_REWARD_MONTHS)
    if months <= 0:
        return False

    ref = db.execute(
        text(
            """
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
            """
        ),
        {"referred_user_id": referred_user_id},
    ).mappings().first()

    if not ref:
        return False

    reward_months = int(ref.get("reward_months") or months)

    db.execute(
        text(
            """
            INSERT INTO referral_rewards (
                referral_id,
                referrer_user_id,
                referred_user_id,
                reward_type,
                reward_months,
                status,
                applied_at,
                created_at
            )
            VALUES (
                :referral_id,
                :referrer_user_id,
                :referred_user_id,
                'free_months',
                :reward_months,
                'applied',
                NOW(),
                NOW()
            )
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "referral_id": ref["id"],
            "referrer_user_id": ref["referrer_user_id"],
            "referred_user_id": ref["referred_user_id"],
            "reward_months": reward_months,
        },
    )
    db.execute(
        text(
            """
            UPDATE referrals
            SET status = 'rewarded',
                converted_at = COALESCE(converted_at, NOW()),
                rewarded_at = COALESCE(rewarded_at, NOW()),
                updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": ref["id"]},
    )
    db.commit()
    return True


def get_referral_summary(db: Session, user) -> dict[str, Any]:
    """Return user-facing referral summary for Settings and Dashboard."""
    config = get_referral_config(db)
    code = ensure_user_referral_code(db, user) if config.get("enabled") else None

    counts = {"total": 0, "signed_up": 0, "paid": 0, "rewarded": 0}
    months_awarded = 0
    months_used = 0
    recent: list[dict[str, Any]] = []

    if _table_exists(db, "referrals"):
        try:
            rows = db.execute(
                text(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM referrals
                    WHERE referrer_user_id = :user_id
                    GROUP BY status
                    """
                ),
                {"user_id": user.id},
            ).mappings().all()
            for row in rows:
                status = str(row["status"] or "signed_up")
                count = int(row["count"] or 0)
                counts["total"] += count
                if status in counts:
                    counts[status] += count
                elif status in {"trialing"}:
                    counts["signed_up"] += count
                elif status in {"converted"}:
                    counts["paid"] += count
                elif status in {"rewarded"}:
                    counts["rewarded"] += count

            recent_rows = db.execute(
                text(
                    """
                    SELECT
                        r.id,
                        r.status,
                        r.created_at,
                        r.converted_at,
                        r.rewarded_at,
                        u.email,
                        u.full_name
                    FROM referrals r
                    LEFT JOIN users u ON u.id = r.referred_user_id
                    WHERE r.referrer_user_id = :user_id
                    ORDER BY r.created_at DESC
                    LIMIT 6
                    """
                ),
                {"user_id": user.id},
            ).mappings().all()
            recent = [
                {
                    "id": str(row["id"]),
                    "status": row["status"],
                    "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                    "converted_at": row["converted_at"].isoformat() if row.get("converted_at") else None,
                    "rewarded_at": row["rewarded_at"].isoformat() if row.get("rewarded_at") else None,
                    "email": row.get("email"),
                    "full_name": row.get("full_name"),
                }
                for row in recent_rows
            ]
        except Exception:
            db.rollback()

    if _table_exists(db, "referral_rewards"):
        try:
            reward_row = db.execute(
                text(
                    """
                    SELECT
                        COALESCE(SUM(reward_months), 0) AS months_awarded,
                        COALESCE(SUM(CASE WHEN status = 'used' THEN reward_months ELSE 0 END), 0) AS months_used
                    FROM referral_rewards
                    WHERE referrer_user_id = :user_id
                    """
                ),
                {"user_id": user.id},
            ).mappings().first()
            if reward_row:
                months_awarded = int(reward_row["months_awarded"] or 0)
                months_used = int(reward_row["months_used"] or 0)
        except Exception:
            db.rollback()

    reward_months = int(config.get("reward_months") or DEFAULT_REWARD_MONTHS)
    return {
        "enabled": bool(config.get("enabled")),
        "referral_code": code,
        "reward_months": reward_months,
        "reward_type": config.get("reward_type") or "free_months",
        "trigger": config.get("trigger") or "referred_user_becomes_paid",
        "counts": counts,
        "total_referrals": counts["total"],
        "signed_up": counts["signed_up"],
        "paid_referrals": counts["paid"],
        "rewarded_referrals": counts["rewarded"],
        "months_awarded": months_awarded,
        "months_available": max(0, months_awarded - months_used),
        "months_used": months_used,
        "headline": f"Refer a paying practitioner and get {reward_months} month{'s' if reward_months != 1 else ''} free.",
        "recent": recent,
    }
