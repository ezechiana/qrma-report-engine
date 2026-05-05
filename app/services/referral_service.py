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


def _table_exists(db: Session, table_name: str) -> bool:
    try:
        row = db.execute(
            text("SELECT to_regclass(:name) AS table_name"),
            {"name": f"public.{table_name}"},
        ).mappings().first()
        return bool(row and row.get("table_name"))
    except Exception:
        db.rollback()
        return False


def generate_referral_code() -> str:
    return secrets.token_urlsafe(8).replace("-", "").replace("_", "").upper()[:10]


def get_referral_config(db: Session) -> dict[str, Any]:
    """Read referral configuration safely.

    This function never creates tables and never raises for settings read failures;
    referral UI should not be able to break the app shell.
    """
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
    if not isinstance(value, dict):
        value = {}

    return {
        "enabled": bool(value.get("enabled", REFERRAL_ENABLED)),
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
    """Record a referral relationship when a new user registers via /register?ref=CODE."""
    config = get_referral_config(db)
    if not config.get("enabled") or not _table_exists(db, "referrals"):
        return False

    code = _normalise_code(referral_code)
    if not code:
        return False

    referrer = db.execute(
        text("SELECT id FROM users WHERE referral_code = :code LIMIT 1"),
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
            ON CONFLICT (referred_user_id) DO NOTHING
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


def get_subscription_credit_summary(db: Session, *, user_id) -> dict[str, int]:
    if not _table_exists(db, "subscription_credits"):
        return {"months_available": 0, "months_used": 0, "months_awarded": 0}

    row = db.execute(
        text(
            """
            SELECT months_available, months_used
            FROM subscription_credits
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    ).mappings().first()

    months_awarded = 0
    if _table_exists(db, "referral_rewards"):
        rewards = db.execute(
            text(
                """
                SELECT COALESCE(SUM(months_awarded), 0) AS months_awarded
                FROM referral_rewards
                WHERE referrer_user_id = :user_id
                """
            ),
            {"user_id": user_id},
        ).mappings().first()
        months_awarded = int(rewards["months_awarded"] or 0) if rewards else 0

    return {
        "months_available": int(row["months_available"] or 0) if row else 0,
        "months_used": int(row["months_used"] or 0) if row else 0,
        "months_awarded": months_awarded,
    }


def apply_subscription_credits(db: Session, *, user_id) -> bool:
    """Apply available free-month credit to the user's latest subscription period."""
    if not _table_exists(db, "subscription_credits"):
        return False

    credit = db.execute(
        text(
            """
            SELECT months_available
            FROM subscription_credits
            WHERE user_id = :user_id
            FOR UPDATE
            """
        ),
        {"user_id": user_id},
    ).mappings().first()

    if not credit or int(credit["months_available"] or 0) <= 0:
        return False

    months = int(credit["months_available"] or 0)

    db.execute(
        text(
            """
            INSERT INTO subscriptions (id, user_id, plan_code, status, current_period_end, created_at, updated_at)
            SELECT gen_random_uuid(), :user_id, :plan_code, 'active', NOW(), NOW(), NOW()
            WHERE NOT EXISTS (SELECT 1 FROM subscriptions WHERE user_id = :user_id)
            """
        ),
        {"user_id": user_id, "plan_code": DEFAULT_PLAN_CODE},
    )

    db.execute(
        text(
            """
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
            """
        ),
        {"user_id": user_id, "months": months},
    )

    db.execute(
        text(
            """
            UPDATE subscription_credits
            SET months_used = months_used + :months,
                months_available = months_available - :months,
                updated_at = NOW()
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id, "months": months},
    )
    db.commit()
    return True


def award_referral_if_eligible(db: Session, *, referred_user_id) -> bool:
    """Award the referrer once when the referred user first becomes a paid subscriber.

    V3 behaviour:
    - only signed_up referrals are eligible
    - duplicate rewards are prevented by referral_rewards unique constraint
    - subscription credits are recorded, then applied immediately to the referrer
    - subscription lifecycle must never fail because of referral logic; callers should
      still wrap this function defensively.
    """
    config = get_referral_config(db)
    if not config.get("enabled"):
        return False
    if not (_table_exists(db, "referrals") and _table_exists(db, "referral_rewards") and _table_exists(db, "subscription_credits")):
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
              AND r.status = 'signed_up'
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

    inserted_reward = db.execute(
        text(
            """
            INSERT INTO referral_rewards (referrer_user_id, referred_user_id, months_awarded, created_at)
            VALUES (:referrer_user_id, :referred_user_id, :months, NOW())
            ON CONFLICT (referrer_user_id, referred_user_id) DO NOTHING
            RETURNING id
            """
        ),
        {"referrer_user_id": ref["referrer_user_id"], "referred_user_id": referred_user_id, "months": months},
    ).mappings().first()

    if not inserted_reward:
        db.rollback()
        return False

    db.execute(
        text(
            """
            INSERT INTO subscription_credits (user_id, months_available, months_used, updated_at)
            VALUES (:user_id, :months, 0, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET months_available = subscription_credits.months_available + :months,
                          updated_at = NOW()
            """
        ),
        {"user_id": ref["referrer_user_id"], "months": months},
    )

    db.execute(
        text(
            """
            UPDATE referrals
            SET status = 'rewarded',
                converted_at = COALESCE(converted_at, NOW()),
                rewarded_at = COALESCE(rewarded_at, NOW()),
                updated_at = NOW()
            WHERE id = :referral_id
            """
        ),
        {"referral_id": ref["id"]},
    )
    db.commit()

    apply_subscription_credits(db, user_id=ref["referrer_user_id"])
    return True



def _referral_revenue_summary(db: Session, *, user_id) -> dict[str, Any]:
    """Return attributed referral revenue by currency.

    V4 is intentionally defensive: if the referral revenue table has not been
    migrated yet, the existing referral dashboard continues to load and simply
    reports zero revenue.
    """
    empty = {
        "revenue_generated_by_currency": {},
        "commission_generated_by_currency": {},
        "revenue_event_count": 0,
    }
    if not _table_exists(db, "referral_revenue_events"):
        return empty

    try:
        rows = db.execute(
            text(
                """
                SELECT
                    LOWER(COALESCE(currency, 'gbp')) AS currency,
                    COALESCE(SUM(revenue_minor), 0) AS revenue_minor,
                    COALESCE(SUM(commission_minor), 0) AS commission_minor,
                    COUNT(*) AS event_count
                FROM referral_revenue_events
                WHERE referrer_user_id = :user_id
                GROUP BY LOWER(COALESCE(currency, 'gbp'))
                """
            ),
            {"user_id": user_id},
        ).mappings().all()
    except Exception:
        db.rollback()
        return empty

    return {
        "revenue_generated_by_currency": {
            row["currency"]: int(row["revenue_minor"] or 0) for row in rows
        },
        "commission_generated_by_currency": {
            row["currency"]: int(row["commission_minor"] or 0) for row in rows
        },
        "revenue_event_count": sum(int(row["event_count"] or 0) for row in rows),
    }


def referral_summary(db: Session, *, user, base_url: str) -> dict[str, Any]:
    config = get_referral_config(db)
    code = ensure_user_referral_code(db, user)
    base = base_url.rstrip("/")
    link = f"{base}/register?ref={code}"
    reward_months = int(config.get("reward_months") or DEFAULT_REWARD_MONTHS)

    if not _table_exists(db, "referrals"):
        return {
            "enabled": bool(config.get("enabled")),
            "reward_type": config.get("reward_type"),
            "reward_months": reward_months,
            "headline": f"Give a colleague access. Get {reward_months} month{'s' if reward_months != 1 else ''} free.",
            "referral_code": code,
            "referral_link": link,
            "counts": {"total": 0, "signed_up": 0, "paid": 0, "rewarded": 0},
            "months_awarded": 0,
            "months_available": 0,
            "months_used": 0,
            "credit_summary": {"months_available": 0, "months_used": 0, "months_awarded": 0},
            "revenue_generated_by_currency": {},
            "commission_generated_by_currency": {},
            "revenue_event_count": 0,
            "recent": [],
        }

    counts = db.execute(
        text(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'signed_up') AS signed_up,
                COUNT(*) FILTER (WHERE status IN ('paid', 'rewarded')) AS paid,
                COUNT(*) FILTER (WHERE status = 'rewarded') AS rewarded
            FROM referrals
            WHERE referrer_user_id = :user_id
            """
        ),
        {"user_id": user.id},
    ).mappings().first()

    credit_summary = get_subscription_credit_summary(db, user_id=user.id)
    revenue_summary = _referral_revenue_summary(db, user_id=user.id)

    recent_rows = db.execute(
        text(
            """
            SELECT r.status, r.created_at, r.updated_at, r.converted_at, r.rewarded_at, u.email, u.full_name
            FROM referrals r
            LEFT JOIN users u ON u.id = r.referred_user_id
            WHERE r.referrer_user_id = :user_id
            ORDER BY r.created_at DESC
            LIMIT 10
            """
        ),
        {"user_id": user.id},
    ).mappings().all()

    total = int(counts["total"] or 0) if counts else 0
    signed_up = int(counts["signed_up"] or 0) if counts else 0
    paid = int(counts["paid"] or 0) if counts else 0
    rewarded = int(counts["rewarded"] or 0) if counts else 0

    return {
        "enabled": bool(config.get("enabled")),
        "reward_type": config.get("reward_type"),
        "reward_months": reward_months,
        "headline": f"Give a colleague access. Get {reward_months} month{'s' if reward_months != 1 else ''} free.",
        "referral_code": code,
        "referral_link": link,
        "counts": {
            "total": total,
            "signed_up": signed_up,
            "paid": paid,
            "rewarded": rewarded,
        },
        "months_awarded": credit_summary["months_awarded"],
        "months_available": credit_summary["months_available"],
        "months_used": credit_summary["months_used"],
        "credit_summary": credit_summary,
        "revenue_generated_by_currency": revenue_summary.get("revenue_generated_by_currency", {}),
        "commission_generated_by_currency": revenue_summary.get("commission_generated_by_currency", {}),
        "revenue_event_count": int(revenue_summary.get("revenue_event_count") or 0),
        "recent": [
            {
                "status": row["status"],
                "email": row["email"],
                "full_name": row["full_name"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                "converted_at": row["converted_at"].isoformat() if row.get("converted_at") else None,
                "rewarded_at": row["rewarded_at"].isoformat() if row.get("rewarded_at") else None,
            }
            for row in recent_rows
        ],
    }



def record_referral_revenue(
    db: Session,
    *,
    referred_user_id,
    amount_minor: int,
    currency: str,
    source_id=None,
    source_type: str = "share_bundle",
    commission_minor: int = 0,
    commission_rate: float = 0,
) -> bool:
    """Best-effort V4 revenue attribution hook.

    Safe behaviour:
    - no-op if V4 migration has not run
    - no-op if the paying practitioner was not referred
    - deduplicates by source_type/source_id
    - never raises into payment/subscription flows
    """
    if not (_table_exists(db, "referrals") and _table_exists(db, "referral_revenue_events")):
        return False

    try:
        ref = db.execute(
            text(
                """
                SELECT referrer_user_id, referred_user_id
                FROM referrals
                WHERE referred_user_id = :referred_user_id
                  AND referrer_user_id <> :referred_user_id
                ORDER BY created_at ASC
                LIMIT 1
                """
            ),
            {"referred_user_id": referred_user_id},
        ).mappings().first()

        if not ref:
            return False

        if source_id is not None:
            existing = db.execute(
                text(
                    """
                    SELECT id
                    FROM referral_revenue_events
                    WHERE source_type = :source_type
                      AND source_id = :source_id
                    LIMIT 1
                    """
                ),
                {"source_type": source_type, "source_id": source_id},
            ).mappings().first()
            if existing:
                return False

        db.execute(
            text(
                """
                INSERT INTO referral_revenue_events (
                    referrer_user_id,
                    referred_user_id,
                    source_type,
                    source_id,
                    revenue_minor,
                    currency,
                    commission_minor,
                    commission_rate,
                    created_at
                ) VALUES (
                    :referrer_user_id,
                    :referred_user_id,
                    :source_type,
                    :source_id,
                    :revenue_minor,
                    :currency,
                    :commission_minor,
                    :commission_rate,
                    NOW()
                )
                """
            ),
            {
                "referrer_user_id": ref["referrer_user_id"],
                "referred_user_id": ref["referred_user_id"],
                "source_type": source_type,
                "source_id": source_id,
                "revenue_minor": int(amount_minor or 0),
                "currency": (currency or "gbp").lower(),
                "commission_minor": int(commission_minor or 0),
                "commission_rate": commission_rate or 0,
            },
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
