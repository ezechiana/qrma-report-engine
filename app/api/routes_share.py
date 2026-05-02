# app/api/routes_share.py

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
from pydantic import BaseModel, Field
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session


from app.api.deps import get_current_user, get_db
from app.db.models import Case, Patient, PractitionerSettings, ReportVersion, ShareLink, User, ReportStatus
from app.schemas.share import ShareLinkCreate, ShareLinkRead
from app.services.storage_service import object_exists, generate_presigned_url
from app.services.audit_service import log_action
from app.services.share_link_service import (
    create_share_link,
    validate_share_link_password,
)
from app.services.subscription_service import require_subscription_feature
from app.services.share_analytics import log_share_event

router = APIRouter(tags=["share"])
templates = Jinja2Templates(directory="app/templates")


def _track_share_event(db: Session, token: str, event_type: str, metadata: dict | None = None) -> None:
    """Best-effort analytics logging for public share interactions.

    Analytics must never block a client from viewing or paying for a report.
    """
    try:
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
STRIPE_PLATFORM_FEE_PERCENT = float(os.getenv("STRIPE_PLATFORM_FEE_PERCENT", "15"))
STRIPE_PLATFORM_FEE_FIXED_AMOUNT = int(os.getenv("STRIPE_PLATFORM_FEE_FIXED_AMOUNT", "0"))
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
            WHERE id = :id
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


def _calculate_platform_fee(amount: int) -> int:
    percent_fee = int(round(amount * (STRIPE_PLATFORM_FEE_PERCENT / 100.0)))
    fee = percent_fee + int(STRIPE_PLATFORM_FEE_FIXED_AMOUNT or 0)
    return max(0, min(fee, amount))


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


@router.post("/api/reports/{report_version_id}/share-links", response_model=ShareLinkRead)
def create_report_share_link(
    report_version_id: UUID,
    payload: ShareLinkCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_subscription_feature(db, current_user, "share_link")

    report = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ReportVersion.id == report_version_id, Case.user_id == current_user.id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    link = create_share_link(
        db,
        report_version=report,
        password=payload.password,
        expires_at=payload.expires_at,
    )

    log_action(
        db,
        "share_link_created",
        user_id=current_user.id,
        case_id=report.case_id,
        report_version_id=report.id,
    )

    base_url = str(request.base_url).rstrip("/")

    return {
        "share_url": f"{base_url}/share/{link.token}",
        "token": link.token,
    }


@router.get("/debug/share-links")
def list_links(db: Session = Depends(get_db)):
    links = db.query(ShareLink).all()
    return [
        {
            "token": l.token,
            "active": l.is_active,
            "expires_at": l.expires_at.isoformat() if l.expires_at else None,
            "report_version_id": str(l.report_version_id),
        }
        for l in links
    ]


@router.get("/share/{token}", response_class=HTMLResponse)
def access_shared_report(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    report = _get_report_for_share_or_404(db, link)

    if getattr(report, "report_type", "assessment") == "trend":
        return RedirectResponse(url=f"/share/{token}/trend", status_code=302)


    if link.password_hash and not _has_valid_share_cookie(request, token):
        response = templates.TemplateResponse(
            request=request,
            name="password_required.html",
            context={
                "request": request,
                "token": token,
            },
        )
        _clear_share_cookie(response)
        return response

    log_action(
        db,
        "share_link_opened",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )
    
    if request.query_params.get("preview") != "1":
        _track_share_event(db, token, "client_view", {"share_type": "assessment", "source": "report_view", "actor": "client"})

    payload = _public_report_payload(db, report, token)
    tenant = _tenant_theme_from_report(db, report)

    return templates.TemplateResponse(
        request=request,
        name="report_viewer.html",
        context={
            "request": request,
            "report": payload,
            "tenant": tenant,
            "viewer_mode": "patient",
        },
    )


@router.post("/share/{token}/verify-password", response_class=HTMLResponse)
def verify_share_password(
    token: str,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response

    if not link.password_hash:
        report = _get_report_for_share_or_404(db, link)
        payload = _public_report_payload(db, report, token)
        tenant = _tenant_theme_from_report(db, report)
        return templates.TemplateResponse(
            request=request,
            name="report_viewer.html",
            context={
                "request": request,
                "report": payload,
                "tenant": tenant,
                "viewer_mode": "patient",
            },
        )

    if not validate_share_link_password(link, password):
        raise HTTPException(status_code=401, detail="Invalid password")

    report = _get_report_for_share_or_404(db, link)

    log_action(
        db,
        "share_link_opened",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token, "password_verified": True},
    )
    _track_share_event(db, token, "client_view", {"share_type": "assessment", "source": "password_verified", "actor": "client"})

    payload = _public_report_payload(db, report, token)
    tenant = _tenant_theme_from_report(db, report)

    response = templates.TemplateResponse(
        request=request,
        name="report_viewer.html",
        context={
            "request": request,
            "report": payload,
            "tenant": tenant,
            "viewer_mode": "patient",
        },
    )
    _set_share_cookie(response, token)
    return response


@router.get("/share/{token}/data")
def get_shared_report_data(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    _enforce_password_gate(request, link)

    report = _get_report_for_share_or_404(db, link)
    payload = _public_report_payload(db, report, token)
    tenant = _tenant_theme_from_report(db, report)

    return JSONResponse(
        {
            "viewer_mode": "patient",
            "tenant": tenant,
            "report": payload,
        }
    )


@router.get("/share/{token}/pdf")
def get_shared_report_pdf(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    _enforce_password_gate(request, link)

    report = _get_report_for_share_or_404(db, link)

    if not report.pdf_path:
        raise HTTPException(status_code=404, detail="PDF not found")

    if not object_exists(report.pdf_path):
        raise HTTPException(status_code=404, detail="PDF file not found")

    log_action(
        db,
        "share_link_pdf_downloaded",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )

    signed_url = generate_presigned_url(report.pdf_path)
    return RedirectResponse(url=signed_url, status_code=302)


@router.get("/share/{token}/html")
def get_shared_report_html(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    _enforce_password_gate(request, link)

    report = _get_report_for_share_or_404(db, link)

    if not report.html_path:
        raise HTTPException(status_code=404, detail="HTML not found")

    if not object_exists(report.html_path):
        raise HTTPException(status_code=404, detail="HTML file not found")

    log_action(
        db,
        "share_link_html_downloaded",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )

    signed_url = generate_presigned_url(report.html_path)
    return RedirectResponse(url=signed_url, status_code=302)
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



@router.post("/api/patients/{patient_id}/trend-share-links")
def create_patient_trend_share_link(
    patient_id: UUID,
    payload: ShareLinkCreate,
    request: Request,
    requires_payment: bool = False,
    price_amount: int | None = None,
    price_currency: str = DEFAULT_SHARE_PRICE_CURRENCY,
    access_label: str = "Client progress view",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_subscription_feature(db, current_user, "share_link")

    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    latest_report = _latest_ready_report_for_patient(db, patient.id, current_user.id)
    if not latest_report:
        raise HTTPException(status_code=404, detail="No ready report found for this patient")

    link = create_share_link(
        db,
        report_version=latest_report,
        password=payload.password,
        expires_at=payload.expires_at,
    )

    _set_share_payment_config(
        db,
        link,
        requires_payment=bool(requires_payment),
        price_amount=price_amount,
        price_currency=price_currency,
        access_label=access_label,
    )

    log_action(
        db,
        "trend_share_link_created",
        user_id=current_user.id,
        case_id=latest_report.case_id,
        report_version_id=latest_report.id,
        metadata_json={"token": link.token, "patient_id": str(patient.id)},
    )

    base_url = str(request.base_url).rstrip("/")
    return {
        "share_url": f"{base_url}/share/{link.token}/trend",
        "token": link.token,
        "type": "patient_trend",
        "patient_id": str(patient.id),
        "report_version_id": str(latest_report.id),
        "requires_payment": bool(requires_payment),
        "payment_status": "unpaid" if requires_payment else "not_required",
    }


@router.get("/share/{token}/trend", response_class=HTMLResponse)
def access_shared_patient_trend(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    report = _get_report_for_share_or_404(db, link)

    if link.password_hash and not _has_valid_share_cookie(request, token):
        response = templates.TemplateResponse(
            request=request,
            name="patient_trend_password_required.html",
            context={"request": request, "token": token},
        )
        _clear_share_cookie(response)
        return response

    tenant = _tenant_theme_from_report(db, report)
    payment = _share_payment_state(db, link)

    if payment["requires_payment"] and not payment["is_unlocked"]:
        _track_share_event(db, token, "paywall_view", {"share_type": "trend", "payment_status": payment.get("payment_status")})
        return templates.TemplateResponse(
            request=request,
            name="patient_trend_paywall.html",
            context={"request": request, "token": token, "tenant": tenant, "payment": payment},
        )

    
    if request.query_params.get("preview") != "1":
        _track_share_event(db, token, "client_view", {"share_type": "trend", "payment_status": payment.get("payment_status"), "actor": "client"})

    log_action(
        db,
        "trend_share_link_opened",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token, "payment_status": payment.get("payment_status")},
    )

    return templates.TemplateResponse(
        request=request,
        name="patient_trend_share.html",
        context={"request": request, "token": token, "tenant": tenant},
    )


@router.post("/share/{token}/trend/verify-password", response_class=HTMLResponse)
def verify_trend_share_password(
    token: str,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response

    if link.password_hash and not validate_share_link_password(link, password):
        raise HTTPException(status_code=401, detail="Invalid password")

    report = _get_report_for_share_or_404(db, link)
    log_action(
        db,
        "trend_share_password_verified",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )

    response = RedirectResponse(url=f"/share/{token}/trend", status_code=302)
    _set_share_cookie(response, token)
    return response




@router.post("/api/share-links/{token}/payment-settings")
def update_share_link_payment_settings(
    token: str,
    request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    report = _get_report_for_share_or_404(db, link)
    case = getattr(report, "case", None)

    if not case or case.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Share link not found")

    _set_share_payment_config(
        db,
        link,
        requires_payment=bool(payload.get("requires_payment")),
        price_amount=payload.get("price_amount"),
        price_currency=payload.get("price_currency") or DEFAULT_SHARE_PRICE_CURRENCY,
        access_label=payload.get("access_label") or "Client progress view",
    )

    return {"ok": True, "token": token, "payment": _share_payment_state(db, link)}


@router.post("/share/{token}/checkout")
def create_share_checkout_session(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    report = _get_report_for_share_or_404(db, link)
    payment = _share_payment_state(db, link)

    if not payment["requires_payment"] or payment["is_unlocked"]:
        return RedirectResponse(url=f"/share/{token}/trend", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    label = payment.get("access_label") or "Client progress view"
    amount = int(payment.get("price_amount") or DEFAULT_SHARE_PRICE_AMOUNT)
    currency = (payment.get("price_currency") or DEFAULT_SHARE_PRICE_CURRENCY).lower()

    connect = _connect_destination_for_report(db, report)
    connect_mode = connect.get("mode") or "platform"
    destination_account = connect.get("account_id") if connect_mode == "destination_charge" else None
    platform_fee_amount = _calculate_platform_fee(amount) if destination_account else None

    checkout_payload = {
        "mode": "payment",
        "success_url": f"{base_url}/share/{token}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/share/{token}/trend",
        "client_reference_id": token,
        "metadata[token]": token,
        "metadata[share_link_id]": str(link.id),
        "metadata[report_version_id]": str(report.id),
        "metadata[stripe_connect_mode]": connect_mode,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][unit_amount]": str(amount),
        "line_items[0][price_data][product_data][name]": label,
    }

    if destination_account:
        checkout_payload.update({
            "metadata[destination_account]": destination_account,
            "metadata[platform_fee_amount]": str(platform_fee_amount or 0),
            "payment_intent_data[application_fee_amount]": str(platform_fee_amount or 0),
            "payment_intent_data[transfer_data][destination]": destination_account,
            "payment_intent_data[metadata][token]": token,
            "payment_intent_data[metadata][share_link_id]": str(link.id),
            "payment_intent_data[metadata][report_version_id]": str(report.id),
            "payment_intent_data[metadata][stripe_connect_mode]": connect_mode,
        })

    session = _stripe_api_request("POST", "/checkout/sessions", checkout_payload)

    db.execute(
        text(
            """
            UPDATE share_links
            SET stripe_checkout_session_id = :session_id,
                stripe_connect_account_id = :stripe_connect_account_id,
                stripe_connect_mode = :stripe_connect_mode,
                platform_fee_amount = :platform_fee_amount,
                platform_fee_currency = :platform_fee_currency
            WHERE id = :id
            """
        ),
        {
            "id": link.id,
            "session_id": session.get("id"),
            "stripe_connect_account_id": destination_account,
            "stripe_connect_mode": connect_mode,
            "platform_fee_amount": platform_fee_amount,
            "platform_fee_currency": currency if platform_fee_amount is not None else None,
        },
    )
    db.commit()
    _track_share_event(db, token, "payment_started", {
        "share_type": "trend",
        "stripe_session_id": session.get("id"),
        "amount": amount,
        "currency": currency,
        "stripe_connect_mode": connect_mode,
    })

    return RedirectResponse(url=session["url"], status_code=303)


@router.get("/share/{token}/payment-success")
def confirm_share_payment_success(
    token: str,
    session_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    session = _stripe_api_request("GET", f"/checkout/sessions/{session_id}")

    if session.get("client_reference_id") != token:
        raise HTTPException(status_code=400, detail="Stripe session does not match this share link")

    if session.get("payment_status") == "paid" or session.get("status") == "complete":
        report = _get_report_for_share_or_404(db, link)
        metadata = session.get("metadata") or {}

        connect_mode = metadata.get("stripe_connect_mode")
        destination_account = metadata.get("destination_account")
        payment_intent_id = session.get("payment_intent")

        try:
            platform_fee_amount = int(metadata.get("platform_fee_amount") or 0) if destination_account else None
        except Exception:
            platform_fee_amount = None

        payment_state = _share_payment_state(db, link)
        platform_fee_currency = (payment_state.get("price_currency") or DEFAULT_SHARE_PRICE_CURRENCY).lower()

        _mark_share_paid(
            db,
            link,
            session_id,
            payment_intent_id=payment_intent_id,
            stripe_connect_account_id=destination_account,
            stripe_connect_mode=connect_mode,
            platform_fee_amount=platform_fee_amount,
            platform_fee_currency=platform_fee_currency if platform_fee_amount is not None else None,
        )

        log_action(
            db,
            "share_link_payment_completed",
            case_id=report.case_id,
            report_version_id=report.id,
            metadata_json={
                "token": token,
                "stripe_session_id": session_id,
                "stripe_payment_intent_id": payment_intent_id,
                "stripe_connect_mode": connect_mode,
                "destination_account": destination_account,
                "platform_fee_amount": platform_fee_amount,
            },
        )
        _track_share_event(db, token, "paid", {
            "share_type": "trend",
            "stripe_session_id": session_id,
            "stripe_payment_intent_id": payment_intent_id,
            "stripe_connect_mode": connect_mode,
        })
        return RedirectResponse(url=f"/share/{token}/trend", status_code=303)

    raise HTTPException(status_code=402, detail="Payment has not completed")


@router.get("/share/{token}/trend-data")
def get_shared_patient_trend_data(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    _enforce_password_gate(request, link)
    payment = _enforce_share_payment_or_402(db, link)

    report = _get_report_for_share_or_404(db, link)
    case = getattr(report, "case", None)
    if not case or not getattr(case, "patient_id", None):
        raise HTTPException(status_code=404, detail="Patient not found for shared report")

    from app.api.routes_patients import get_patient_trends

    owner = _trend_owner_from_report(report)
    trend_payload = get_patient_trends(
        patient_id=case.patient_id,
        include_archived=False,
        view_mode="client",
        db=db,
        current_user=owner,
    )

    patient = getattr(case, "patient", None)
    tenant = _tenant_theme_from_report(db, report)

    return JSONResponse({
        "viewer_mode": "patient",
        "share_type": "patient_trend",
        "tenant": tenant,
        "patient": {
            "id": str(case.patient_id),
            "display_name": _patient_display_name(patient),
        },
        "trend": trend_payload,
        "payment": payment,
    })


@router.post("/api/share/create")
def create_share(payload: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_subscription_feature(db, user, "share_link")

    share = create_share_link(
        db=db,
        report_version_id=payload.get("report_version_id"),
        patient_id=payload.get("patient_id"),
        share_type=payload.get("share_type", "report"),
        price=payload.get("price"),
        expires_days=payload.get("expires_days"),
        password=payload.get("password"),
    )

    base = os.getenv("BASE_URL", "http://127.0.0.1:8000")

    if share.share_type == "trend":
        url = f"{base}/share/{share.token}/trend"
    else:
        url = f"{base}/share/{share.token}/report"

    return {
        "success": True,
        "url": url,
        "price": share.price_pence,
        "type": share.share_type,
    }


@router.get("/api/share/list")
def list_share_links(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """
    Shares dashboard API.

    Semantics:
    - items = number of reports inside a bundle (not multiplied by analytics events)
    - views = client views only (practitioner previews use ?preview=1 and are ignored)
    - payment_started = number of Stripe payment attempts started
    - payment_completed = whether the share/bundle is paid (max 1 conversion per share object)
    - payment_conversion_rate = payment_completed / payment_started
    """
    base = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")

    def _dt(value):
        if not value:
            return "—"
        try:
            return value.strftime("%d/%m/%Y, %H:%M")
        except Exception:
            return str(value)

    def _access_status(is_active, expires_at):
        if not is_active:
            return "revoked"
        if expires_at:
            now = datetime.now(expires_at.tzinfo) if getattr(expires_at, "tzinfo", None) else datetime.utcnow()
            if expires_at < now:
                return "expired"
        return "active"

    def _money(amount, currency):
        if amount is None or amount == "":
            return "Free"
        try:
            amount = int(amount)
        except Exception:
            return str(amount)
        if amount <= 0:
            return "Free"
        curr = (currency or DEFAULT_SHARE_PRICE_CURRENCY or "gbp").lower()
        symbols = {"gbp": "£", "usd": "$", "eur": "€", "aed": "د.إ", "jpy": "¥", "krw": "₩"}
        zero_decimal = {"jpy", "krw"}
        symbol = symbols.get(curr, curr.upper() + " ")
        return f"{symbol}{amount:,.0f}" if curr in zero_decimal else f"{symbol}{amount / 100:,.2f}"

    def _rate(completed, started):
        completed = int(completed or 0)
        started = int(started or 0)
        return round((completed / started) * 100, 1) if started else 0

    single_rows = db.execute(text("""
        WITH event_stats AS (
            SELECT
                link_token,
                COUNT(*) FILTER (WHERE event_type = 'client_view') AS client_views,
                COUNT(*) FILTER (WHERE event_type IN ('payment_started', 'checkout_started')) AS payment_started,
                MAX(created_at) FILTER (WHERE event_type = 'client_view') AS last_client_viewed_at
            FROM share_link_events
            GROUP BY link_token
        )
        SELECT
            s.id,
            s.token,
            COALESCE(s.share_type, 'report') AS share_type,
            s.created_at,
            s.paid_at,
            s.expires_at,
            s.is_active,
            COALESCE(s.payment_status, 'not_required') AS payment_status,
            COALESCE(s.price_amount, s.price_pence) AS price_amount,
            COALESCE(s.price_currency, :default_currency) AS price_currency,
            p.full_name AS patient_name,
            CASE
                WHEN rv.report_type = 'trend'
                  OR rv.report_json->>'report_type' = 'trend'
                  OR rv.trend_payload_json IS NOT NULL
                  OR (jsonb_typeof(rv.source_report_ids) = 'array' AND jsonb_array_length(rv.source_report_ids) > 0)
                THEN 'trend'
                ELSE 'assessment'
            END AS report_type,
            COALESCE(rv.report_json->>'display_name', c.title, 'Shared report') AS title,
            COALESCE(es.client_views, 0) AS client_views,
            COALESCE(es.payment_started, 0) AS payment_started,
            CASE WHEN COALESCE(s.payment_status, 'not_required') = 'paid' THEN 1 ELSE 0 END AS payment_completed,
            es.last_client_viewed_at
        FROM share_links s
        JOIN report_versions rv ON rv.id = s.report_version_id
        JOIN cases c ON c.id = rv.case_id
        LEFT JOIN patients p ON p.id = c.patient_id
        LEFT JOIN event_stats es ON es.link_token = s.token
        WHERE c.user_id = :user_id
          AND COALESCE(s.share_type, 'report') <> 'bundle_item'
        ORDER BY s.created_at DESC
    """), {"user_id": user.id, "default_currency": DEFAULT_SHARE_PRICE_CURRENCY}).mappings().all()

    bundle_rows = db.execute(text("""
        WITH item_stats AS (
            SELECT
                i.share_bundle_id,
                COUNT(DISTINCT i.id) AS item_count,
                COUNT(DISTINCT rv.id) FILTER (WHERE NOT (
                    rv.report_type = 'trend'
                    OR rv.report_json->>'report_type' = 'trend'
                    OR rv.trend_payload_json IS NOT NULL
                    OR (jsonb_typeof(rv.source_report_ids) = 'array' AND jsonb_array_length(rv.source_report_ids) > 0)
                )) AS assessment_count,
                COUNT(DISTINCT rv.id) FILTER (WHERE
                    rv.report_type = 'trend'
                    OR rv.report_json->>'report_type' = 'trend'
                    OR rv.trend_payload_json IS NOT NULL
                    OR (jsonb_typeof(rv.source_report_ids) = 'array' AND jsonb_array_length(rv.source_report_ids) > 0)
                ) AS trend_count
            FROM share_bundle_items i
            LEFT JOIN report_versions rv ON rv.id = i.report_version_id
            GROUP BY i.share_bundle_id
        ),
        event_stats AS (
            SELECT
                link_token,
                COUNT(*) FILTER (WHERE event_type = 'client_view') AS client_views,
                COUNT(*) FILTER (WHERE event_type IN ('payment_started', 'checkout_started')) AS payment_started,
                MAX(created_at) FILTER (WHERE event_type = 'client_view') AS last_client_viewed_at
            FROM share_link_events
            GROUP BY link_token
        )
        SELECT
            b.id,
            b.token,
            b.title,
            b.access_label,
            b.created_at,
            b.paid_at,
            b.expires_at,
            b.is_active,
            COALESCE(b.payment_status, 'not_required') AS payment_status,
            b.price_amount,
            COALESCE(b.price_currency, :default_currency) AS price_currency,
            p.full_name AS patient_name,
            COALESCE(ins.item_count, 0) AS item_count,
            COALESCE(ins.assessment_count, 0) AS assessment_count,
            COALESCE(ins.trend_count, 0) AS trend_count,
            COALESCE(es.client_views, 0) AS client_views,
            COALESCE(es.payment_started, 0) AS payment_started,
            CASE WHEN COALESCE(b.payment_status, 'not_required') = 'paid' THEN 1 ELSE 0 END AS payment_completed,
            es.last_client_viewed_at
        FROM share_bundles b
        LEFT JOIN patients p ON p.id = b.patient_id
        LEFT JOIN item_stats ins ON ins.share_bundle_id = b.id
        LEFT JOIN event_stats es ON es.link_token = b.token
        WHERE b.created_by_user_id = :user_id
        ORDER BY b.created_at DESC
    """), {"user_id": user.id, "default_currency": DEFAULT_SHARE_PRICE_CURRENCY}).mappings().all()

    results = []

    for r in single_rows:
        report_type = r["report_type"] or "assessment"
        share_type = r["share_type"] or report_type
        is_trend = report_type == "trend" or share_type == "trend"
        type_key = "trend" if is_trend else "assessment"
        url = f"{base}/share/{r['token']}/trend" if is_trend else f"{base}/share/{r['token']}"
        preview_url = f"{url}?preview=1"
        client_views = int(r["client_views"] or 0)
        payment_started = int(r["payment_started"] or 0)
        payment_completed = int(r["payment_completed"] or 0)
        access_status = _access_status(bool(r["is_active"]), r["expires_at"])
        results.append({
            "id": str(r["id"]),
            "token": r["token"],
            "patient": r["patient_name"] or "Client",
            "title": r["title"] or "Shared report",
            "type": "Trend share" if type_key == "trend" else "Assessment share",
            "type_key": type_key,
            "kind": "single",
            "url": url,
            "preview_url": preview_url,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "created_display": _dt(r["created_at"]),
            "paid_at": r["paid_at"].isoformat() if r.get("paid_at") else None,
            "paid_display": _dt(r.get("paid_at")),
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "expires_display": _dt(r["expires_at"]),
            "is_active": bool(r["is_active"]),
            "access_status": access_status,
            "payment_status": r["payment_status"] or "not_required",
            "price_amount": r["price_amount"],
            "price_currency": r["price_currency"],
            "price": _money(r["price_amount"], r["price_currency"]),
            "items": "1 report",
            "item_count": 1,
            "assessment_count": 0 if type_key == "trend" else 1,
            "trend_count": 1 if type_key == "trend" else 0,
            "client_views": client_views,
            "views": client_views,
            "payment_started": payment_started,
            "checkouts": payment_started,
            "payment_completed": payment_completed,
            "conversions": payment_completed,
            "payment_conversion_rate": _rate(payment_completed, payment_started),
            "conversion_rate": _rate(payment_completed, payment_started),
            "last_client_viewed_at": r["last_client_viewed_at"].isoformat() if r["last_client_viewed_at"] else None,
            "last_viewed_at": r["last_client_viewed_at"].isoformat() if r["last_client_viewed_at"] else None,
            "last_viewed_display": _dt(r["last_client_viewed_at"]),
        })

    for r in bundle_rows:
        item_count = int(r["item_count"] or 0)
        trend_count = int(r["trend_count"] or 0)
        # Treat the backend item total as authoritative. Some legacy reports have
        # inconsistent report_type metadata, so derive assessment count from total - trends.
        assessment_count = max(0, item_count - trend_count)
        client_views = int(r["client_views"] or 0)
        payment_started = int(r["payment_started"] or 0)
        payment_completed = int(r["payment_completed"] or 0)
        access_status = _access_status(bool(r["is_active"]), r["expires_at"])
        url = f"{base}/share/bundle/{r['token']}"
        results.append({
            "id": str(r["id"]),
            "token": r["token"],
            "patient": r["patient_name"] or "Client",
            "title": r["title"] or r["access_label"] or "Report bundle",
            "type": "Bundle",
            "type_key": "bundle",
            "kind": "bundle",
            "url": url,
            "preview_url": f"{url}?preview=1",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "created_display": _dt(r["created_at"]),
            "paid_at": r["paid_at"].isoformat() if r.get("paid_at") else None,
            "paid_display": _dt(r.get("paid_at")),
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "expires_display": _dt(r["expires_at"]),
            "is_active": bool(r["is_active"]),
            "access_status": access_status,
            "payment_status": r["payment_status"] or "not_required",
            "price_amount": r["price_amount"],
            "price_currency": r["price_currency"],
            "price": _money(r["price_amount"], r["price_currency"]),
            "item_count": item_count,
            "assessment_count": assessment_count,
            "trend_count": trend_count,
            "items": f"{item_count} report{'s' if item_count != 1 else ''} · {assessment_count} assessment{'s' if assessment_count != 1 else ''} · {trend_count} trend{'s' if trend_count != 1 else ''}",
            "client_views": client_views,
            "views": client_views,
            "payment_started": payment_started,
            "checkouts": payment_started,
            "payment_completed": payment_completed,
            "conversions": payment_completed,
            "payment_conversion_rate": _rate(payment_completed, payment_started),
            "conversion_rate": _rate(payment_completed, payment_started),
            "last_client_viewed_at": r["last_client_viewed_at"].isoformat() if r["last_client_viewed_at"] else None,
            "last_viewed_at": r["last_client_viewed_at"].isoformat() if r["last_client_viewed_at"] else None,
            "last_viewed_display": _dt(r["last_client_viewed_at"]),
        })

    results.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    counts = {
        "all": len(results),
        "single": len([x for x in results if x["kind"] == "single"]),
        "bundle": len([x for x in results if x["kind"] == "bundle"]),
        "active": len([x for x in results if x["access_status"] == "active"]),
        "paid": len([x for x in results if x["payment_status"] == "paid"]),
        "unpaid": len([x for x in results if x["payment_status"] in {"unpaid", "checkout_started"}]),
        "views": sum(int(x.get("client_views") or 0) for x in results),
        "client_views": sum(int(x.get("client_views") or 0) for x in results),
        "payment_started": sum(int(x.get("payment_started") or 0) for x in results),
        "checkouts": sum(int(x.get("payment_started") or 0) for x in results),
        "payment_completed": sum(int(x.get("payment_completed") or 0) for x in results),
        "conversions": sum(int(x.get("payment_completed") or 0) for x in results),
    }

    return {"items": results, "counts": counts}


@router.get("/api/share-links")
def list_share_links_modern(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return list_share_links(db=db, user=user)


@router.post("/api/share/revoke/{share_id}")
def revoke_share(share_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    db.execute(text("""
        UPDATE share_links
        SET is_active = false
        WHERE id = :id
    """), {"id": share_id})
    db.commit()
    return {"success": True}


@router.post("/api/share-links/{share_id}/revoke")
def revoke_share_link_alias(share_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return revoke_share(share_id=share_id, db=db, user=user)


@router.post("/api/share-links/{share_id}/extend-expiry")
def extend_share_link_expiry(share_id: str, payload: dict = Body(...), db: Session = Depends(get_db), user=Depends(get_current_user)):
    link = (
        db.query(ShareLink)
        .join(ReportVersion, ReportVersion.id == ShareLink.report_version_id)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ShareLink.id == share_id, Case.user_id == user.id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found")

    remove_expiry = bool(payload.get("remove_expiry"))
    days = payload.get("days")
    if remove_expiry:
        link.expires_at = None
    else:
        try:
            days_int = int(days if days is not None else 7)
        except Exception:
            days_int = 7
        if days_int <= 0:
            link.expires_at = None
        else:
            base_dt = link.expires_at or datetime.utcnow()
            link.expires_at = base_dt + timedelta(days=days_int)

    db.commit()
    return {"success": True, "expires_at": link.expires_at.isoformat() if link.expires_at else None}


class ShareBundleCreate(BaseModel):
    report_version_ids: list[UUID] = Field(min_length=1)
    patient_id: UUID | None = None
    bundle_label: str | None = None
    requires_payment: bool = False
    price_amount: int | None = None
    price_currency: str = "gbp"
    expires_at: datetime | None = None
    password: str | None = None


@router.post("/api/share/bundles")
def create_share_bundle(
    payload: ShareBundleCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_subscription_feature(db, current_user, "share_bundle")

    reports = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            ReportVersion.id.in_(payload.report_version_ids),
            Case.user_id == current_user.id,
        )
        .all()
    )

    found = {r.id for r in reports}
    missing = [str(x) for x in payload.report_version_ids if x not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"Report(s) not found: {', '.join(missing)}")

    anchor_report = reports[0]

    link = create_share_link(
        db=db,
        report_version=anchor_report,
        patient_id=payload.patient_id,
        share_type="report_bundle",
        password=payload.password,
        expires_days=None,
    )

    if payload.expires_at:
        link.expires_at = payload.expires_at

    db.execute(
        text("""
        UPDATE share_links
        SET share_type = 'report_bundle',
            bundle_label = :bundle_label,
            requires_payment = :requires_payment,
            payment_status = :payment_status,
            price_amount = :price_amount,
            price_currency = :price_currency,
            access_label = :access_label
        WHERE id = :id
        """),
        {
            "id": link.id,
            "bundle_label": payload.bundle_label or "Client report bundle",
            "requires_payment": payload.requires_payment,
            "payment_status": "unpaid" if payload.requires_payment else "not_required",
            "price_amount": payload.price_amount if payload.requires_payment else None,
            "price_currency": payload.price_currency or DEFAULT_SHARE_PRICE_CURRENCY,
            "access_label": payload.bundle_label or "Client report bundle",
        },
    )

    for idx, report_id in enumerate(payload.report_version_ids):
        db.execute(
            text("""
            INSERT INTO share_link_items (share_link_id, report_version_id, item_order)
            VALUES (:share_link_id, :report_version_id, :item_order)
            ON CONFLICT (share_link_id, report_version_id) DO NOTHING
            """),
            {"share_link_id": link.id, "report_version_id": report_id, "item_order": idx},
        )

    db.commit()

    base_url = str(request.base_url).rstrip("/")
    return {
        "success": True,
        "token": link.token,
        "share_url": f"{base_url}/share/{link.token}/bundle",
        "report_count": len(payload.report_version_ids),
        "requires_payment": payload.requires_payment,
    }

@router.get("/share/{token}/bundle", response_class=HTMLResponse)
def access_shared_bundle(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response

    if link.password_hash and not _has_valid_share_cookie(request, token):
        response = templates.TemplateResponse(
            request=request,
            name="patient_trend_password_required.html",
            context={"request": request, "token": token},
        )
        _clear_share_cookie(response)
        return response

    payment = _share_payment_state(db, link)
    if payment.get("requires_payment") and not payment.get("is_unlocked"):
        report = _get_report_for_share_or_404(db, link)
        tenant = _tenant_theme_from_report(db, report)
        return templates.TemplateResponse(
            request=request,
            name="patient_trend_paywall.html",
            context={"request": request, "token": token, "tenant": tenant, "payment": payment},
        )

    return templates.TemplateResponse(
        request=request,
        name="share_bundle_view.html",
        context={"request": request, "token": token},
    )


@router.get("/share/{token}/bundle-data")
def get_shared_bundle_data(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response

    _enforce_password_gate(request, link)
    payment = _enforce_share_payment_or_402(db, link)

    rows = db.execute(
        text("""
        SELECT r.id, r.case_id, r.report_type, r.generated_at, r.version_number, sli.item_order
        FROM share_link_items sli
        JOIN report_versions r ON r.id = sli.report_version_id
        WHERE sli.share_link_id = :share_link_id
        ORDER BY sli.item_order ASC, r.generated_at DESC
        """),
        {"share_link_id": link.id},
    ).mappings().all()

    items = []
    for row in rows:
        report = db.query(ReportVersion).filter(ReportVersion.id == row["id"]).first()
        if not report:
            continue

        if (row["report_type"] or "assessment") == "trend":
            items.append({
                "id": str(report.id),
                "type": "trend",
                "generated_at": report.generated_at.isoformat() if report.generated_at else None,
                "trend": getattr(report, "trend_payload_json", None) or (report.report_json or {}).get("trend"),
            })
        else:
            items.append({
                "id": str(report.id),
                "type": "assessment",
                "generated_at": report.generated_at.isoformat() if report.generated_at else None,
                "report": _public_report_payload(db, report, token),
            })

    return {"viewer_mode": "patient", "share_type": "report_bundle", "items": items, "payment": payment}