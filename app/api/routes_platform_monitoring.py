from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_platform_admin
from app.db.models import User

router = APIRouter(tags=["platform-monitoring"])
templates = Jinja2Templates(directory="app/templates")

ZERO_DECIMAL_CURRENCIES = {"jpy", "krw"}


def _iso(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _safe_scalar(db: Session, sql: str, params: dict | None = None, default: Any = 0) -> Any:
    try:
        return db.execute(text(sql), params or {}).scalar() or default
    except Exception as exc:
        db.rollback()
        print(f"[platform-monitoring] scalar query failed: {exc}")
        return default


def _safe_one(db: Session, sql: str, params: dict | None = None) -> dict[str, Any]:
    try:
        row = db.execute(text(sql), params or {}).mappings().first()
        return dict(row or {})
    except Exception as exc:
        db.rollback()
        print(f"[platform-monitoring] row query failed: {exc}")
        return {}


def _safe_all(db: Session, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    try:
        rows = db.execute(text(sql), params or {}).mappings().all()
        return [dict(row) for row in rows]
    except Exception as exc:
        db.rollback()
        print(f"[platform-monitoring] list query failed: {exc}")
        return []


def _stripe_mode() -> str:
    key = os.getenv("STRIPE_SECRET_KEY") or ""
    if key.startswith("sk_live"):
        return "live"
    if key.startswith("sk_test"):
        return "test"
    return "not_configured"


def _db_ok(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1")).scalar()
        return True
    except Exception:
        db.rollback()
        return False


def _severity_rank(level: str) -> int:
    return {"green": 0, "amber": 1, "red": 2}.get(level, 0)


def _event_age_hours(value: Any) -> float | None:
    if not value:
        return None
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - value).total_seconds() / 3600, 2)
    except Exception:
        return None


@router.get("/app/platform-monitoring", response_class=HTMLResponse)
def platform_monitoring_page(
    request: Request,
    current_user: User = Depends(require_platform_admin),
):
    return templates.TemplateResponse(
        request=request,
        name="platform_monitoring.html",
        context={"request": request, "title": "Platform Monitoring", "user": current_user},
    )


@router.get("/api/platform-monitoring")
def platform_monitoring_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_platform_admin),
):
    app_env = (os.getenv("APP_ENV") or "development").lower()
    stripe_mode = _stripe_mode()
    webhook_secret_present = bool(os.getenv("STRIPE_WEBHOOK_SECRET") or os.getenv("STRIPE_SUBSCRIPTION_WEBHOOK_SECRET"))
    db_reachable = _db_ok(db)

    latest_webhook = _safe_one(
        db,
        """
        SELECT event_type, processed_at
        FROM stripe_webhook_events
        ORDER BY processed_at DESC
        LIMIT 1
        """,
    )

    latest_payment = _safe_one(
        db,
        """
        SELECT payment_status, paid_at, created_at, price_amount, price_currency, stripe_connect_mode,
               stripe_session_id, stripe_payment_intent_id, stripe_charge_id
        FROM share_bundles
        WHERE requires_payment = true
        ORDER BY COALESCE(paid_at, created_at) DESC
        LIMIT 1
        """,
    )

    today = _safe_one(
        db,
        """
        SELECT
            COUNT(*) FILTER (WHERE payment_status = 'paid') AS paid_bundles,
            COUNT(*) FILTER (WHERE payment_status IN ('unpaid', 'checkout_started')) AS pending_bundles,
            COUNT(*) FILTER (WHERE payment_status NOT IN ('paid', 'unpaid', 'checkout_started', 'not_required')) AS other_status_bundles,
            COALESCE(SUM(price_amount) FILTER (WHERE payment_status = 'paid'), 0) AS gross_minor,
            COALESCE(SUM(platform_fee_amount) FILTER (WHERE payment_status = 'paid'), 0) AS platform_commission_minor,
            COALESCE(SUM(stripe_fee_amount) FILTER (WHERE payment_status = 'paid'), 0) AS stripe_fee_minor,
            COALESCE(SUM(practitioner_payout_amount) FILTER (WHERE payment_status = 'paid'), 0) AS practitioner_allocation_minor,
            COALESCE(SUM(
              CASE
                WHEN payment_status = 'paid'
                 AND COALESCE(stripe_connect_mode, 'platform_only') IN ('platform_only', 'platform_fallback', 'platform')
                 AND COALESCE(platform_fee_amount, 0) - COALESCE(stripe_fee_amount, 0) < 0
                THEN 0
                WHEN payment_status = 'paid'
                THEN COALESCE(platform_fee_amount, 0) - COALESCE(stripe_fee_amount, 0)
                ELSE 0
              END
            ), 0) AS platform_net_display_minor,
            COALESCE(SUM(
              CASE
                WHEN payment_status = 'paid'
                THEN COALESCE(platform_fee_amount, 0) - COALESCE(stripe_fee_amount, 0)
                ELSE 0
              END
            ), 0) AS platform_net_raw_minor
        FROM share_bundles
        WHERE requires_payment = true
          AND DATE(COALESCE(paid_at, created_at)) = CURRENT_DATE
        """,
    )

    connect_modes = _safe_all(
        db,
        """
        SELECT COALESCE(stripe_connect_mode, 'unknown') AS mode, COUNT(*) AS count
        FROM share_bundles
        WHERE requires_payment = true
          AND payment_status = 'paid'
        GROUP BY COALESCE(stripe_connect_mode, 'unknown')
        ORDER BY count DESC
        """,
    )

    activity_today = {
        "new_users": _safe_scalar(db, "SELECT COUNT(*) FROM users WHERE DATE(created_at) = CURRENT_DATE"),
        "reports_generated": _safe_scalar(db, "SELECT COUNT(*) FROM report_versions WHERE DATE(generated_at) = CURRENT_DATE"),
        "failed_reports": _safe_scalar(db, "SELECT COUNT(*) FROM report_versions WHERE status = 'failed' AND DATE(failed_at) = CURRENT_DATE"),
        "share_links_created": _safe_scalar(db, "SELECT COUNT(*) FROM share_links WHERE DATE(created_at) = CURRENT_DATE"),
        "bundles_created": _safe_scalar(db, "SELECT COUNT(*) FROM share_bundles WHERE DATE(created_at) = CURRENT_DATE"),
        "settings_updated": _safe_scalar(db, "SELECT COUNT(*) FROM practitioner_settings WHERE DATE(updated_at) = CURRENT_DATE"),
    }

    charts = {
        "payments_14d": _safe_all(
            db,
            """
            WITH days AS (
                SELECT generate_series(CURRENT_DATE - INTERVAL '13 days', CURRENT_DATE, INTERVAL '1 day')::date AS day
            )
            SELECT
                days.day::text AS day,
                COUNT(b.id) FILTER (WHERE b.payment_status = 'paid') AS paid,
                COUNT(b.id) FILTER (WHERE b.payment_status IN ('unpaid', 'checkout_started')) AS pending,
                COALESCE(SUM(b.price_amount) FILTER (WHERE b.payment_status = 'paid'), 0) AS gross_minor
            FROM days
            LEFT JOIN share_bundles b
              ON DATE(COALESCE(b.paid_at, b.created_at)) = days.day
             AND b.requires_payment = true
            GROUP BY days.day
            ORDER BY days.day
            """,
        ),
        "reports_14d": _safe_all(
            db,
            """
            WITH days AS (
                SELECT generate_series(CURRENT_DATE - INTERVAL '13 days', CURRENT_DATE, INTERVAL '1 day')::date AS day
            )
            SELECT
                days.day::text AS day,
                COUNT(rv.id) FILTER (WHERE rv.status != 'failed') AS generated,
                COUNT(rv.id) FILTER (WHERE rv.status = 'failed') AS failed
            FROM days
            LEFT JOIN report_versions rv
              ON DATE(COALESCE(rv.failed_at, rv.generated_at)) = days.day
            GROUP BY days.day
            ORDER BY days.day
            """,
        ),
        "bundle_conversion_14d": _safe_all(
            db,
            """
            WITH days AS (
                SELECT generate_series(CURRENT_DATE - INTERVAL '13 days', CURRENT_DATE, INTERVAL '1 day')::date AS day
            )
            SELECT
                days.day::text AS day,
                COUNT(b.id) AS created,
                COUNT(b.id) FILTER (WHERE b.payment_status = 'paid') AS paid
            FROM days
            LEFT JOIN share_bundles b
              ON DATE(b.created_at) = days.day
             AND b.requires_payment = true
            GROUP BY days.day
            ORDER BY days.day
            """,
        ),
    }

    checkout_started_total = _safe_scalar(
        db,
        """
        SELECT COUNT(*)
        FROM share_bundles
        WHERE requires_payment = true
          AND payment_status = 'checkout_started'
        """,
    )

    checkout_stale_30m = _safe_scalar(
        db,
        """
        SELECT COUNT(*)
        FROM share_bundles
        WHERE requires_payment = true
          AND payment_status = 'checkout_started'
          AND created_at < NOW() - INTERVAL '30 minutes'
        """,
    )

    checkout_stale_2h = _safe_scalar(
        db,
        """
        SELECT COUNT(*)
        FROM share_bundles
        WHERE requires_payment = true
          AND payment_status = 'checkout_started'
          AND created_at < NOW() - INTERVAL '2 hours'
        """,
    )

    paid_missing_stripe_refs = _safe_scalar(
        db,
        """
        SELECT COUNT(*)
        FROM share_bundles
        WHERE requires_payment = true
          AND payment_status = 'paid'
          AND (stripe_payment_intent_id IS NULL OR stripe_charge_id IS NULL)
        """,
    )

    negative_platform_net_rows = _safe_scalar(
        db,
        """
        SELECT COUNT(*)
        FROM share_bundles
        WHERE requires_payment = true
          AND payment_status = 'paid'
          AND COALESCE(stripe_connect_mode, 'platform_only') IN ('platform_only', 'platform_fallback', 'platform')
          AND COALESCE(platform_fee_amount, 0) - COALESCE(stripe_fee_amount, 0) < 0
        """,
    )

    fallback_paid = _safe_scalar(
        db,
        """
        SELECT COUNT(*)
        FROM share_bundles
        WHERE requires_payment = true
          AND payment_status = 'paid'
          AND COALESCE(stripe_connect_mode, '') IN ('platform_fallback', 'platform_only', 'platform')
        """,
    )

    webhook_age_hours = _event_age_hours(latest_webhook.get("processed_at"))

    anomalies = []

    def add_anomaly(level: str, title: str, detail: str, metric: Any = None, code: str | None = None):
        anomalies.append({"level": level, "title": title, "detail": detail, "metric": metric, "code": code})

    # Red/amber launch thresholds.
    if not db_reachable:
        add_anomaly("red", "Database check failed", "The monitoring endpoint could not confirm basic database connectivity.", code="db_unreachable")

    if app_env == "production" and stripe_mode != "live":
        add_anomaly("red", "Production is not using a live Stripe key", "Production should use sk_live_… before accepting real payments.", stripe_mode, "stripe_wrong_mode")

    if app_env in {"staging", "development", "dev"} and stripe_mode == "live":
        add_anomaly("red", "Non-production environment is using a live Stripe key", "Staging/dev must use Stripe test mode to prevent accidental real charges.", stripe_mode, "stripe_live_in_nonprod")

    if stripe_mode != "not_configured" and not webhook_secret_present:
        add_anomaly("red", "Stripe webhook secret missing", "Webhook verification cannot work safely without STRIPE_WEBHOOK_SECRET or STRIPE_SUBSCRIPTION_WEBHOOK_SECRET.", code="webhook_secret_missing")

    if int(checkout_started_total or 0) > 0 and not latest_webhook:
        add_anomaly("red", "Checkout activity but no webhook events", "A checkout has started, but no Stripe webhook event has been recorded. Verify the Stripe webhook endpoint and secret.", checkout_started_total, "webhook_missing_after_checkout")
    elif stripe_mode != "not_configured" and not latest_webhook:
        add_anomaly("amber", "No Stripe webhook events recorded", "This may be normal before launch, but should change after the first checkout/webhook test.", code="no_webhook_yet")

    if webhook_age_hours is not None:
        if app_env == "production" and webhook_age_hours > 72:
            add_anomaly("red", "No Stripe webhook activity for over 72 hours", "Production webhook activity is stale. Confirm Stripe is still delivering events.", webhook_age_hours, "webhook_stale_red")
        elif app_env == "production" and webhook_age_hours > 24:
            add_anomaly("amber", "No recent Stripe webhook activity", "Latest recorded webhook event is older than 24 hours.", webhook_age_hours, "webhook_stale_amber")

    if int(checkout_stale_2h or 0) > 0:
        add_anomaly("red", "Checkout sessions older than 2 hours", "Some checkout_started bundles are stale and may indicate missed webhooks or abandoned payments.", checkout_stale_2h, "checkout_stale_red")
    elif int(checkout_stale_30m or 0) > 0:
        add_anomaly("amber", "Checkout sessions older than 30 minutes", "Review whether these are abandoned payments or missed webhook completions.", checkout_stale_30m, "checkout_stale_amber")

    if int(paid_missing_stripe_refs or 0) > 0:
        add_anomaly("amber", "Paid bundles missing Stripe references", "Some paid bundles do not have complete payment_intent/charge references. Revenue may be reconciled but incomplete.", paid_missing_stripe_refs, "missing_stripe_refs")

    if int(activity_today.get("failed_reports") or 0) > 0:
        add_anomaly("red", "Report generation failures today", "At least one report failed today and should be reviewed before launch traffic increases.", activity_today["failed_reports"], "report_failures")

    if int(negative_platform_net_rows or 0) > 0:
        add_anomaly("amber", "Reconciliation-only negative platform net detected", "Some platform-only/fallback payments have Stripe fees greater than platform commission. This is shown as reconciliation, not operating loss.", negative_platform_net_rows, "negative_platform_net_reconciliation")

    if int(fallback_paid or 0) > 0:
        add_anomaly("amber", "Platform fallback payments detected", "These payments were collected by the platform because Stripe Connect was not fully routed for the practitioner.", fallback_paid, "platform_fallback_paid")

    if not anomalies:
        add_anomaly("green", "No launch-critical anomalies detected", "Core monitoring checks are currently green.", code="healthy")

    overall = max((a["level"] for a in anomalies), key=_severity_rank) if anomalies else "green"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "health": {
            "environment": app_env,
            "database_reachable": db_reachable,
            "stripe_mode": stripe_mode,
            "webhook_secret_present": webhook_secret_present,
            "base_url": os.getenv("BASE_URL"),
            "frontend_url": os.getenv("FRONTEND_URL"),
            "marketing_url": os.getenv("MARKETING_URL"),
            "s3_bucket_configured": bool(os.getenv("S3_BUCKET")),
            "s3_prefix": os.getenv("S3_PREFIX"),
        },
        "webhook": {
            "latest_event_type": latest_webhook.get("event_type"),
            "latest_processed_at": _iso(latest_webhook.get("processed_at")),
            "age_hours": webhook_age_hours,
        },
        "latest_payment": {
            "payment_status": latest_payment.get("payment_status"),
            "paid_at": _iso(latest_payment.get("paid_at")),
            "created_at": _iso(latest_payment.get("created_at")),
            "price_amount": latest_payment.get("price_amount") or 0,
            "price_currency": (latest_payment.get("price_currency") or "gbp").upper(),
            "connect_mode": latest_payment.get("stripe_connect_mode") or "—",
            "stripe_session_id": latest_payment.get("stripe_session_id"),
            "stripe_payment_intent_id": latest_payment.get("stripe_payment_intent_id"),
            "stripe_charge_id": latest_payment.get("stripe_charge_id"),
        },
        "today": today,
        "connect_modes": connect_modes,
        "activity_today": activity_today,
        "charts": charts,
        "anomaly_counts": {
            "checkout_started_total": checkout_started_total,
            "checkout_stale_30m": checkout_stale_30m,
            "checkout_stale_2h": checkout_stale_2h,
            "paid_missing_stripe_refs": paid_missing_stripe_refs,
            "negative_platform_net_rows": negative_platform_net_rows,
            "fallback_paid": fallback_paid,
        },
        "anomalies": sorted(anomalies, key=lambda a: _severity_rank(a["level"]), reverse=True),
    }
