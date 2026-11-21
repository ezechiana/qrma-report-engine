# app/api/share_helpers.py

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
from types import SimpleNamespace
from uuid import UUID
from datetime import datetime

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.share_commission import commission_amount
from app.db.models import Case, Patient, PractitionerSettings, ReportVersion, ShareLink, User, ReportStatus
from app.services.share_analytics import log_share_event

templates = Jinja2Templates(directory="app/templates")


def _ensure_share_link_events_table(db: Session) -> None:
    """Create the optional share analytics table if this environment has not been migrated yet.

    The Shares dashboard reads from share_link_events. Staging/prod databases may lag
    behind the current codebase during rollout, so this keeps the dashboard and
    client share analytics from failing with UndefinedTable.
    """
    from app.db.runtime_schema import existing_objects
    if existing_objects(db, ('share_link_events', 'idx_share_link_events_token', 'idx_share_link_events_event_type', 'idx_share_link_events_created_at')):
        db.commit()
        return
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS share_link_events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    link_token TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    metadata JSONB,
                    metadata_json JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        db.execute(text("CREATE INDEX IF NOT EXISTS idx_share_link_events_token ON share_link_events(link_token)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS idx_share_link_events_event_type ON share_link_events(event_type)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS idx_share_link_events_created_at ON share_link_events(created_at)"))
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[share-analytics] could not ensure share_link_events table: {exc}")


def _track_share_event(db: Session, token: str, event_type: str, metadata: dict | None = None) -> None:
    """Best-effort analytics logging for public share interactions.

    Analytics must never block a client from viewing or paying for a report.
    """
    try:
        _ensure_share_link_events_table(db)
        log_share_event(db, token, event_type, metadata or {})
    except Exception as exc:
        db.rollback()
        print(f"[share-analytics] failed to log {event_type} for {token}: {exc}")


SHARE_ACCESS_COOKIE_NAME = "qrma_share_access"
SHARE_ACCESS_COOKIE_MAX_AGE = int(os.getenv("SHARE_ACCESS_COOKIE_MAX_AGE", "43200"))  # 12 hours
SHARE_COOKIE_SECURE = os.getenv("SHARE_COOKIE_SECURE", "false").lower() == "true"
SHARE_COOKIE_SAMESITE = os.getenv("SHARE_COOKIE_SAMESITE", "lax")
SHARE_COOKIE_SECRET = os.getenv("SHARE_COOKIE_SECRET", os.getenv("OPENAI_API_KEY", "dev-share-secret"))

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
DEFAULT_SHARE_PRICE_AMOUNT = int(os.getenv("DEFAULT_SHARE_PRICE_AMOUNT", "2500"))
DEFAULT_SHARE_PRICE_CURRENCY = os.getenv("DEFAULT_SHARE_PRICE_CURRENCY", "gbp").strip().lower()

STRIPE_CONNECT_ENABLED = os.getenv("STRIPE_CONNECT_ENABLED", "true").lower() == "true"
STRIPE_CONNECT_COUNTRY = os.getenv("STRIPE_CONNECT_COUNTRY", "GB").strip().upper()
STRIPE_CONNECT_ACCOUNT_TYPE = os.getenv("STRIPE_CONNECT_ACCOUNT_TYPE", "express").strip().lower()
STRIPE_CONNECT_FALLBACK_TO_PLATFORM = os.getenv("STRIPE_CONNECT_FALLBACK_TO_PLATFORM", "true").lower() == "true"



# -----------------------------------------------------------------------------
# Optional Stripe paywall helpers for share links
# -----------------------------------------------------------------------------

def _format_money(amount: int | None, currency: str | None) -> str:
    amount = int(amount or 0)
    currency = (currency or DEFAULT_SHARE_PRICE_CURRENCY or "gbp").upper()
    major = amount / 100
    if major.is_integer():
        return f"{currency} {int(major)}"
    return f"{currency} {major:.2f}"


def _share_payment_state(db: Session, link: ShareLink) -> dict:
    row = db.execute(
        text(
            """
            SELECT
                COALESCE(requires_payment, false) AS requires_payment,
                COALESCE(payment_status, 'not_required') AS payment_status,
                price_amount,
                COALESCE(price_currency, :default_currency) AS price_currency,
                stripe_checkout_session_id,
                paid_at,
                COALESCE(access_label, 'Client progress view') AS access_label
            FROM share_links
            WHERE id = :id
            """
        ),
        {"id": link.id, "default_currency": DEFAULT_SHARE_PRICE_CURRENCY},
    ).mappings().first()

    if not row:
        return {
            "requires_payment": False,
            "payment_status": "not_required",
            "price_amount": None,
            "price_currency": DEFAULT_SHARE_PRICE_CURRENCY,
            "is_unlocked": True,
            "access_label": "Client progress view",
        }

    requires_payment = bool(row["requires_payment"])
    payment_status = row["payment_status"] or "not_required"
    is_unlocked = (not requires_payment) or payment_status in {"paid", "not_required"}

    price_amount = row["price_amount"]
    if requires_payment and not price_amount:
        price_amount = DEFAULT_SHARE_PRICE_AMOUNT

    return {
        "requires_payment": requires_payment,
        "payment_status": payment_status,
        "price_amount": int(price_amount) if price_amount is not None else None,
        "price_currency": row["price_currency"] or DEFAULT_SHARE_PRICE_CURRENCY,
        "stripe_checkout_session_id": row["stripe_checkout_session_id"],
        "paid_at": row["paid_at"].isoformat() if row["paid_at"] else None,
        "is_unlocked": is_unlocked,
        "access_label": row["access_label"] or "Client progress view",
        "price_display": _format_money(price_amount, row["price_currency"]),
    }


def _set_share_payment_config(
    db: Session,
    link: ShareLink,
    *,
    requires_payment: bool,
    price_amount: int | None = None,
    price_currency: str | None = None,
    access_label: str | None = None,
    commit: bool = True,
) -> None:
    amount = int(price_amount or DEFAULT_SHARE_PRICE_AMOUNT) if requires_payment else None
    currency = (price_currency or DEFAULT_SHARE_PRICE_CURRENCY or "gbp").strip().lower()
    status = "unpaid" if requires_payment else "not_required"

    db.execute(
        text(
            """
            UPDATE share_links
            SET requires_payment = :requires_payment,
                payment_status = :payment_status,
                price_amount = :price_amount,
                price_currency = :price_currency,
                access_label = :access_label
            WHERE id = :id
            """
        ),
        {
            "id": link.id,
            "requires_payment": requires_payment,
            "payment_status": status,
            "price_amount": amount,
            "price_currency": currency,
            "access_label": access_label or "Client progress view",
        },
    )
    if commit:
        db.commit()


def _mark_share_paid(
    db: Session,
    link: ShareLink,
    session_id: str,
    *,
    payment_intent_id: str | None = None,
    stripe_connect_account_id: str | None = None,
    stripe_connect_mode: str | None = None,
    platform_fee_amount: int | None = None,
    platform_fee_currency: str | None = None,
) -> None:
    db.execute(
        text(
            """
            UPDATE share_links
            SET payment_status = 'paid',
                stripe_checkout_session_id = :session_id,
                stripe_payment_intent_id = :payment_intent_id,
                stripe_connect_account_id = :stripe_connect_account_id,
                stripe_connect_mode = :stripe_connect_mode,
                platform_fee_amount = :platform_fee_amount,
                platform_fee_currency = :platform_fee_currency,
                paid_at = NOW()
            WHERE id = :id AND payment_status IS DISTINCT FROM 'paid'
            """
        ),
        {
            "id": link.id,
            "session_id": session_id,
            "payment_intent_id": payment_intent_id,
            "stripe_connect_account_id": stripe_connect_account_id,
            "stripe_connect_mode": stripe_connect_mode,
            "platform_fee_amount": platform_fee_amount,
            "platform_fee_currency": platform_fee_currency,
        },
    )
    db.commit()

def _stripe_api_request(method: str, path: str, data: dict | None = None) -> dict:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe is not configured. Set STRIPE_SECRET_KEY.")

    url = f"https://api.stripe.com/v1{path}"
    encoded = urllib.parse.urlencode(data or {}, doseq=True).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=encoded, method=method.upper())
    req.add_header("Authorization", f"Bearer {STRIPE_SECRET_KEY}")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Stripe error: {body}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Stripe request failed: {exc}")


def _render_share_unavailable(request: Request, reason: str):
    """
    reason: revoked | expired | not_found
    """
    return templates.TemplateResponse(
        request=request,
        name="share_link_unavailable.html",
        context={
            "request": request,
            "reason": reason,
        },
        status_code=404,
    )

# -----------------------------------------------------------------------------
# Stripe Connect helpers
# -----------------------------------------------------------------------------

def _report_owner_user_id(report: ReportVersion):
    case = getattr(report, "case", None)
    return getattr(case, "user_id", None) or getattr(report, "created_by_user_id", None)


def _connect_status_from_row(row) -> dict:
    if not row:
        return {
            "connected": False,
            "account_id": None,
            "charges_enabled": False,
            "payouts_enabled": False,
            "details_submitted": False,
            "can_receive_destination_charges": False,
        }

    account_id = row.get("stripe_connect_account_id")
    charges_enabled = bool(row.get("stripe_connect_charges_enabled"))
    payouts_enabled = bool(row.get("stripe_connect_payouts_enabled"))
    details_submitted = bool(row.get("stripe_connect_details_submitted"))

    return {
        "connected": bool(account_id),
        "account_id": account_id,
        "charges_enabled": charges_enabled,
        "payouts_enabled": payouts_enabled,
        "details_submitted": details_submitted,
        "can_receive_destination_charges": bool(account_id and charges_enabled),
        "country": row.get("stripe_connect_country"),
        "last_sync_at": row.get("stripe_connect_last_sync_at").isoformat() if row.get("stripe_connect_last_sync_at") else None,
    }


def _get_practitioner_connect_state(db: Session, user_id) -> dict:
    row = db.execute(
        text(
            """
            SELECT
                stripe_connect_account_id,
                COALESCE(stripe_connect_charges_enabled, false) AS stripe_connect_charges_enabled,
                COALESCE(stripe_connect_payouts_enabled, false) AS stripe_connect_payouts_enabled,
                COALESCE(stripe_connect_details_submitted, false) AS stripe_connect_details_submitted,
                stripe_connect_country,
                stripe_connect_last_sync_at
            FROM practitioner_settings
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    ).mappings().first()

    return _connect_status_from_row(row)


def _ensure_practitioner_settings(db: Session, user: User) -> PractitionerSettings:
    settings = (
        db.query(PractitionerSettings)
        .filter(PractitionerSettings.user_id == user.id)
        .first()
    )
    if settings:
        return settings

    settings = PractitionerSettings(user_id=user.id)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def _sync_connect_account_status(db: Session, user_id, account_id: str) -> dict:
    account = _stripe_api_request("GET", f"/accounts/{account_id}")

    charges_enabled = bool(account.get("charges_enabled"))
    payouts_enabled = bool(account.get("payouts_enabled"))
    details_submitted = bool(account.get("details_submitted"))
    country = account.get("country")

    db.execute(
        text(
            """
            UPDATE practitioner_settings
            SET stripe_connect_account_id = :account_id,
                stripe_connect_charges_enabled = :charges_enabled,
                stripe_connect_payouts_enabled = :payouts_enabled,
                stripe_connect_details_submitted = :details_submitted,
                stripe_connect_country = :country,
                stripe_connect_account_type = :account_type,
                stripe_connect_onboarded_at = CASE
                    WHEN :details_submitted = true THEN COALESCE(stripe_connect_onboarded_at, NOW())
                    ELSE stripe_connect_onboarded_at
                END,
                stripe_connect_last_sync_at = NOW()
            WHERE user_id = :user_id
            """
        ),
        {
            "user_id": user_id,
            "account_id": account_id,
            "charges_enabled": charges_enabled,
            "payouts_enabled": payouts_enabled,
            "details_submitted": details_submitted,
            "country": country,
            "account_type": account.get("type") or STRIPE_CONNECT_ACCOUNT_TYPE,
        },
    )
    db.commit()

    return {
        "connected": True,
        "account_id": account_id,
        "charges_enabled": charges_enabled,
        "payouts_enabled": payouts_enabled,
        "details_submitted": details_submitted,
        "can_receive_destination_charges": bool(charges_enabled),
        "country": country,
        "requirements": account.get("requirements") or {},
    }


def _calculate_platform_fee(amount: int, commission: dict) -> int:
    return commission_amount(amount, commission)


def _connect_destination_for_report(db: Session, report: ReportVersion) -> dict:
    if not STRIPE_CONNECT_ENABLED:
        return {"mode": "platform", "account_id": None, "platform_fee_amount": None, "reason": "connect_disabled"}

    owner_id = _report_owner_user_id(report)
    if not owner_id:
        return {"mode": "platform", "account_id": None, "platform_fee_amount": None, "reason": "missing_owner"}

    state = _get_practitioner_connect_state(db, owner_id)
    account_id = state.get("account_id")

    if account_id and state.get("charges_enabled"):
        return {
            "mode": "destination_charge",
            "account_id": account_id,
            "platform_fee_amount": None,
            "reason": "connect_ready",
        }

    if not STRIPE_CONNECT_FALLBACK_TO_PLATFORM and account_id:
        raise HTTPException(
            status_code=409,
            detail="Practitioner Stripe account is connected but not ready to receive payments yet.",
        )

    if not STRIPE_CONNECT_FALLBACK_TO_PLATFORM:
        raise HTTPException(
            status_code=409,
            detail="Practitioner has not connected Stripe yet.",
        )

    return {
        "mode": "platform_fallback",
        "account_id": account_id,
        "platform_fee_amount": None,
        "reason": "connect_not_ready",
    }


def _enforce_share_payment_or_402(db: Session, link: ShareLink) -> dict:
    state = _share_payment_state(db, link)
    if state["requires_payment"] and not state["is_unlocked"]:
        raise HTTPException(status_code=402, detail={"payment_required": True, "payment": state})
    return state


def _get_share_link(db: Session, token: str) -> ShareLink | None:
    return db.query(ShareLink).filter(ShareLink.token == token).first()


def _validate_share_link_or_render(request: Request, db: Session, token: str):
    link = _get_share_link(db, token)

    if not link:
        return None, _render_share_unavailable(request, "not_found")

    if not link.is_active:
        return None, _render_share_unavailable(request, "revoked")

    if link.expires_at:
        expires_at = link.expires_at
        # SQLAlchemy/Postgres may return timezone-aware datetimes while datetime.utcnow()
        # is timezone-naive. Normalize before comparing to avoid 500 errors on share links.
        now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.utcnow()
        if expires_at < now:
            return None, _render_share_unavailable(request, "expired")

    return link, None

def _get_report_for_share_or_404(db: Session, link: ShareLink) -> ReportVersion:
    report = db.query(ReportVersion).filter(
        ReportVersion.id == link.report_version_id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


def _get_practitioner_settings_for_report(db: Session, report: ReportVersion) -> PractitionerSettings | None:
    return (
        db.query(PractitionerSettings)
        .filter(PractitionerSettings.user_id == report.created_by_user_id)
        .first()
    )


def _tenant_theme_from_report(db: Session, report: ReportVersion) -> dict:
    report_json = report.report_json or {}
    stored_theme = report_json.get("tenant_theme", {}) if isinstance(report_json, dict) else {}
    settings = _get_practitioner_settings_for_report(db, report)

    return {
        "tenant_name": (
            stored_theme.get("tenant_name")
            or (settings.clinic_name if settings and settings.clinic_name else None)
            or os.getenv("VIEWER_TENANT_NAME", "Health Portal")
        ),
        "tagline": (
            stored_theme.get("tagline")
            or (settings.report_subtitle if settings and settings.report_subtitle else None)
            or os.getenv("VIEWER_TAGLINE", "Secure wellness report viewer")
        ),
        "logo_url": (
            stored_theme.get("logo_url")
            or (settings.logo_url if settings and settings.logo_url else None)
            or os.getenv("VIEWER_LOGO_URL", "")
        ),
        "cover_image_url": (
            stored_theme.get("cover_image_url")
            or (settings.cover_image_url if settings and settings.cover_image_url else None)
            or ""
        ),
        "primary_color": (
            stored_theme.get("primary_color")
            or (settings.accent_color if settings and settings.accent_color else None)
            or os.getenv("VIEWER_PRIMARY_COLOR", "#2f4f2f")
        ),
        "accent_color": (
            stored_theme.get("accent_color")
            or (settings.accent_color if settings and settings.accent_color else None)
            or os.getenv("VIEWER_ACCENT_COLOR", "#d97706")
        ),
        "text_color": stored_theme.get("text_color") or os.getenv("VIEWER_TEXT_COLOR", "#183028"),
        "background_color": stored_theme.get("background_color") or os.getenv("VIEWER_BACKGROUND_COLOR", "#f6f8f5"),
        "surface_color": stored_theme.get("surface_color") or os.getenv("VIEWER_SURFACE_COLOR", "#ffffff"),
        "surface_soft_color": stored_theme.get("surface_soft_color") or os.getenv("VIEWER_SURFACE_SOFT_COLOR", "#f9fbf8"),
        "border_color": stored_theme.get("border_color") or os.getenv("VIEWER_BORDER_COLOR", "#dfe7e1"),
        "muted_color": stored_theme.get("muted_color") or os.getenv("VIEWER_MUTED_COLOR", "#5f7269"),
        "support_email": (
            stored_theme.get("support_email")
            or (settings.support_email if settings and settings.support_email else None)
            or os.getenv("VIEWER_SUPPORT_EMAIL", "")
        ),
        "website_url": (
            stored_theme.get("website_url")
            or (settings.website_url if settings and settings.website_url else None)
            or os.getenv("VIEWER_WEBSITE_URL", "")
        ),
    }


def _public_report_payload(db: Session, report: ReportVersion, token: str) -> dict:
    report_json = report.report_json or {}
    viewer = dict(report_json.get("viewer") or {})
    viewer_tenant = dict(viewer.get("tenant") or {})
    settings = _get_practitioner_settings_for_report(db, report)

    if settings:
        if settings.report_title:
            viewer_tenant["report_title"] = settings.report_title
        if settings.report_subtitle:
            viewer_tenant["subtitle"] = settings.report_subtitle

    viewer["tenant"] = viewer_tenant

    return {
        "id": str(report.id),
        "case_id": str(report.case_id),
        "version_number": report.version_number,
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "recommendation_mode": (
            report.recommendation_mode.value
            if hasattr(report.recommendation_mode, "value")
            else str(report.recommendation_mode)
        ),
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "pdf_url": f"/share/{token}/pdf",
        "html_url": f"/share/{token}/html",
        "data_url": f"/share/{token}/data",
        "viewer": viewer,
    }


def _cookie_signature(token: str) -> str:
    return hmac.new(
        SHARE_COOKIE_SECRET.encode("utf-8"),
        msg=token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def _cookie_value_for_token(token: str) -> str:
    token_b64 = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii")
    sig = _cookie_signature(token)
    return f"{token_b64}.{sig}"


def _has_valid_share_cookie(request: Request, token: str) -> bool:
    raw = request.cookies.get(SHARE_ACCESS_COOKIE_NAME)
    if not raw:
        return False

    try:
        token_b64, provided_sig = raw.split(".", 1)
        cookie_token = base64.urlsafe_b64decode(token_b64.encode("ascii")).decode("utf-8")
    except Exception:
        return False

    if cookie_token != token:
        return False

    expected_sig = _cookie_signature(token)
    return hmac.compare_digest(provided_sig, expected_sig)


def _set_share_cookie(response, token: str) -> None:
    response.set_cookie(
        key=SHARE_ACCESS_COOKIE_NAME,
        value=_cookie_value_for_token(token),
        max_age=SHARE_ACCESS_COOKIE_MAX_AGE,
        httponly=True,
        secure=SHARE_COOKIE_SECURE,
        samesite=SHARE_COOKIE_SAMESITE,
        path="/",
    )


def _clear_share_cookie(response) -> None:
    response.delete_cookie(
        key=SHARE_ACCESS_COOKIE_NAME,
        path="/",
    )


def _enforce_password_gate(request: Request, link: ShareLink) -> None:
    """
    If a share link has a password, require a valid signed cookie issued after
    successful password verification.
    """
    if not link.password_hash:
        return

    if not _has_valid_share_cookie(request, link.token):
        raise HTTPException(status_code=401, detail="Password verification required")

# -----------------------------------------------------------------------------
# Client-safe shared trend/progress access
# -----------------------------------------------------------------------------

def _latest_ready_report_for_patient(db: Session, patient_id: UUID, user_id) -> ReportVersion | None:
    return (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            Case.patient_id == patient_id,
            Case.user_id == user_id,
            ReportVersion.status == ReportStatus.ready,
        )
        .order_by(ReportVersion.generated_at.desc(), ReportVersion.version_number.desc())
        .first()
    )


def _patient_display_name(patient: Patient | None) -> str:
    if not patient:
        return "Client"
    full = getattr(patient, "full_name", None)
    if full:
        return full
    joined = " ".join(x for x in [getattr(patient, "first_name", None), getattr(patient, "last_name", None)] if x)
    return joined or "Client"


def _trend_owner_from_report(report: ReportVersion):
    case = getattr(report, "case", None)
    owner_id = getattr(case, "user_id", None) or getattr(report, "created_by_user_id", None)
    return SimpleNamespace(id=owner_id)
