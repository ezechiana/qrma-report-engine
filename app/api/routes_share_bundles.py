from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.db.models import Case, ReportVersion, ShareBundle, ShareBundleItem, ShareLink, User
from app.services.revenue_model import calculate_revenue_split
from app.services.share_analytics import log_share_event
from app.services.subscription_service import require_subscription_feature
from app.services.referral_service import record_referral_revenue
from app.utils.security import generate_token, hash_password

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["share-bundles"])

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

STRIPE_CONNECT_ENABLED = os.getenv("STRIPE_CONNECT_ENABLED", "true").lower() == "true"
STRIPE_CONNECT_FALLBACK_TO_PLATFORM = os.getenv("STRIPE_CONNECT_FALLBACK_TO_PLATFORM", "true").lower() == "true"
STRIPE_PLATFORM_FEE_PERCENT = float(os.getenv("STRIPE_PLATFORM_FEE_PERCENT", "15"))
STRIPE_PLATFORM_FEE_FIXED_AMOUNT = int(os.getenv("STRIPE_PLATFORM_FEE_FIXED_AMOUNT", "0"))

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


def _track_share_event(db: Session, token: str, event_type: str, metadata: dict | None = None) -> None:
    """Best-effort analytics logging for public bundle interactions."""
    try:
        log_share_event(db, token, event_type, metadata or {})
    except Exception as exc:
        db.rollback()
        print(f"[share-analytics] failed to log {event_type} for bundle {token}: {exc}")


class ShareBundleCreate(BaseModel):
    report_version_ids: list[UUID] = Field(min_length=1)
    title: Optional[str] = None
    access_label: Optional[str] = None
    price_amount: Optional[int] = None  # Stripe minor units, e.g. 2500 = £25.00
    price_currency: str = "gbp"
    expires_days: Optional[int] = 7
    password: Optional[str] = None


def _safe_password_for_bcrypt(password: str) -> str:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) <= 72:
        return password
    return password_bytes[:72].decode("utf-8", errors="ignore")


def _owned_reports(db: Session, current_user: User, ids: list[UUID]) -> list[ReportVersion]:
    requested = list(dict.fromkeys(ids))
    reports = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ReportVersion.id.in_(requested), Case.user_id == current_user.id)
        .all()
    )
    found = {r.id for r in reports}
    missing = [str(rid) for rid in requested if rid not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"Report(s) not found: {', '.join(missing)}")
    report_map = {r.id: r for r in reports}
    return [report_map[rid] for rid in requested]


def _bundle_url(token: str) -> str:
    return f"{BASE_URL.rstrip('/')}/share/bundle/{token}"

def _bundle_unavailable_reason(bundle: ShareBundle | None) -> str:
    if not bundle:
        return "missing"

    if not getattr(bundle, "is_active", False):
        return "revoked"

    expires_at = getattr(bundle, "expires_at", None)
    if expires_at:
        expires = expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            return "expired"

    return ""


def _bundle_unavailable_response(request: Request, reason: str):
    return templates.TemplateResponse(
        request=request,
        name="share_link_unavailable.html",
        context={
            "request": request,
            "reason": reason or "missing",
        },
        status_code=404,
    )


def _bundle_is_valid(bundle: ShareBundle) -> bool:
    if not bundle.is_active:
        return False
    if bundle.expires_at:
        expires = bundle.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            return False
    return True


def _report_type(report: ReportVersion) -> str:
    report_json = getattr(report, "report_json", None) or {}
    if isinstance(report_json, dict) and report_json.get("report_type") == "trend":
        return "trend"
    if getattr(report, "trend_payload_json", None):
        return "trend"
    source_ids = getattr(report, "source_report_ids", None)
    if isinstance(source_ids, list) and len(source_ids) > 0:
        return "trend"
    return "trend" if (getattr(report, "report_type", "assessment") == "trend") else "assessment"


def _report_title(report: ReportVersion) -> str:
    report_json = report.report_json or {}
    if isinstance(report_json, dict):
        display_name = report_json.get("display_name") or (report_json.get("viewer") or {}).get("case_title")
        if display_name:
            return display_name
    case = getattr(report, "case", None)
    patient = getattr(case, "patient", None) if case else None
    patient_name = getattr(patient, "full_name", None)
    case_title = getattr(case, "title", None)
    if _report_type(report) == "trend":
        return f"{patient_name or 'Client'} — Progress trend report"
    return case_title or f"{patient_name or 'Client'} — Assessment report"


def _item_viewer_url(link: ShareLink, report: ReportVersion) -> str:
    return f"/share/{link.token}/trend" if _report_type(report) == "trend" else f"/share/{link.token}"


def _format_minor_amount(amount: int | None, currency: str | None) -> str:
    if not amount:
        return "Free"
    currency = (currency or "gbp").lower()
    zero_decimal = {"jpy", "krw"}
    symbols = {"gbp": "£", "usd": "$", "eur": "€", "aed": "د.إ", "jpy": "¥", "krw": "₩"}
    symbol = symbols.get(currency, currency.upper())
    return f"{symbol}{amount}" if currency in zero_decimal else f"{symbol}{amount / 100:.2f}"


def _create_child_share_link(
    db: Session,
    *,
    report: ReportVersion,
    patient_id,
    title: str,
    expires_at,
    password_hash: str | None,
) -> ShareLink:
    link = ShareLink(
        report_version_id=report.id,
        patient_id=patient_id,
        token=generate_token(24),
        password_hash=password_hash,
        expires_at=expires_at,
        is_active=True,
        share_type="bundle_item",
        bundle_label=title,
        price_pence=None,
    )
    db.add(link)
    db.flush()
    return link


def _get_bundle_by_token_or_404(db: Session, token: str) -> ShareBundle:
    bundle = (
        db.query(ShareBundle)
        .options(
            joinedload(ShareBundle.items).joinedload(ShareBundleItem.report_version).joinedload(ReportVersion.case),
            joinedload(ShareBundle.items).joinedload(ShareBundleItem.share_link),
        )
        .filter(ShareBundle.token == token)
        .first()
    )
    if not bundle or not _bundle_is_valid(bundle):
        raise HTTPException(status_code=404, detail="Link not found or expired.")
    return bundle


def _calculate_platform_fee(amount_minor: int) -> int:
    percent_fee = int(round(amount_minor * (STRIPE_PLATFORM_FEE_PERCENT / 100.0)))
    fee = percent_fee + int(STRIPE_PLATFORM_FEE_FIXED_AMOUNT or 0)
    return max(0, min(fee, amount_minor))


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


def _connect_destination_for_bundle(db: Session, bundle: ShareBundle) -> dict:
    if not STRIPE_CONNECT_ENABLED:
        return {"mode": "platform", "account_id": None, "reason": "connect_disabled", "platform_fee_amount": 0}

    owner_id = getattr(bundle, "created_by_user_id", None)
    if not owner_id:
        return {"mode": "platform_fallback", "account_id": None, "reason": "missing_bundle_owner", "platform_fee_amount": 0}

    state = _get_practitioner_connect_state(db, owner_id)
    account_id = state.get("account_id")

    if account_id and state.get("charges_enabled"):
        return {
            "mode": "destination_charge",
            "account_id": account_id,
            "reason": "connect_ready",
            "platform_fee_amount": _calculate_platform_fee(int(bundle.price_amount or 0)),
        }

    if STRIPE_CONNECT_FALLBACK_TO_PLATFORM:
        return {"mode": "platform_fallback", "account_id": account_id, "reason": "connect_not_ready", "platform_fee_amount": 0}

    raise HTTPException(status_code=409, detail="Practitioner Stripe account is not ready to receive payments yet.")


def _ensure_webhook_events_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    db.commit()


def _webhook_event_seen(db: Session, event_id: str | None) -> bool:
    if not event_id:
        return False
    _ensure_webhook_events_table(db)
    row = db.execute(
        text("SELECT 1 FROM stripe_webhook_events WHERE event_id = :event_id"),
        {"event_id": event_id},
    ).first()
    return bool(row)


def _record_webhook_event(db: Session, event_id: str | None, event_type: str | None) -> None:
    if not event_id:
        return
    _ensure_webhook_events_table(db)
    db.execute(
        text(
            """
            INSERT INTO stripe_webhook_events (event_id, event_type)
            VALUES (:event_id, :event_type)
            ON CONFLICT (event_id) DO NOTHING
            """
        ),
        {"event_id": event_id, "event_type": event_type or "unknown"},
    )
    db.commit()


def _get_bundle_financial_snapshot(db: Session, bundle_id) -> dict:
    row = db.execute(
        text(
            """
            SELECT
                price_amount,
                COALESCE(price_currency, 'gbp') AS price_currency,
                platform_fee_amount,
                platform_fee_currency,
                practitioner_payout_amount,
                stripe_fee_amount,
                stripe_fee_currency,
                stripe_connect_mode,
                stripe_connect_account_id
            FROM share_bundles
            WHERE id = :bundle_id
            """
        ),
        {"bundle_id": str(bundle_id)},
    ).mappings().first()
    return dict(row or {})


def _record_checkout_routing(
    db: Session,
    bundle: ShareBundle,
    *,
    session_id: str,
    connect_mode: str,
    connect_account_id: str | None,
    platform_fee_amount: int | None,
    platform_fee_currency: str | None,
) -> None:
    gross = int(getattr(bundle, "price_amount", 0) or 0)
    platform_fee = int(platform_fee_amount or 0)
    split = calculate_revenue_split(gross_minor=gross, commission_minor=platform_fee, stripe_fee_minor=0)
    practitioner_payout = split["practitioner_payout_minor"] if gross else None

    db.execute(
        text(
            """
            UPDATE share_bundles
            SET stripe_session_id = :session_id,
                payment_status = 'checkout_started',
                stripe_connect_mode = :connect_mode,
                stripe_connect_account_id = :connect_account_id,
                platform_fee_amount = :platform_fee_amount,
                platform_fee_currency = :platform_fee_currency,
                practitioner_payout_amount = :practitioner_payout_amount,
                updated_at = NOW()
            WHERE id = :bundle_id
            """
        ),
        {
            "bundle_id": str(bundle.id),
            "session_id": session_id,
            "connect_mode": connect_mode,
            "connect_account_id": connect_account_id,
            "platform_fee_amount": platform_fee,
            "platform_fee_currency": platform_fee_currency,
            "practitioner_payout_amount": practitioner_payout,
        },
    )
    db.commit()


def _extract_stripe_financials(payment_intent_id: str | None) -> dict:
    result = {"charge_id": None, "transfer_id": None, "stripe_fee_amount": None, "stripe_fee_currency": None}

    if not payment_intent_id or not STRIPE_SECRET_KEY:
        return result

    try:
        pi = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["latest_charge.balance_transaction"])
        charge = pi.get("latest_charge") if hasattr(pi, "get") else getattr(pi, "latest_charge", None)

        if isinstance(charge, str):
            result["charge_id"] = charge
            charge = stripe.Charge.retrieve(charge, expand=["balance_transaction"])

        if isinstance(charge, dict) or hasattr(charge, "get"):
            result["charge_id"] = charge.get("id")
            transfer = charge.get("transfer")
            if isinstance(transfer, dict):
                result["transfer_id"] = transfer.get("id")
            elif isinstance(transfer, str):
                result["transfer_id"] = transfer

            balance_txn = charge.get("balance_transaction")
            if isinstance(balance_txn, dict) or hasattr(balance_txn, "get"):
                result["stripe_fee_amount"] = balance_txn.get("fee")
                result["stripe_fee_currency"] = balance_txn.get("currency")

        if not result["stripe_fee_amount"] and result["charge_id"]:
            charge = stripe.Charge.retrieve(result["charge_id"], expand=["balance_transaction"])
            balance_txn = charge.get("balance_transaction")
            if isinstance(balance_txn, dict) or hasattr(balance_txn, "get"):
                result["stripe_fee_amount"] = balance_txn.get("fee")
                result["stripe_fee_currency"] = balance_txn.get("currency")
    except Exception as exc:
        print(f"[stripe] could not extract financials for PI {payment_intent_id}: {exc}")

    return result


def _find_bundle_by_payment_intent(db: Session, payment_intent_id: str | None) -> ShareBundle | None:
    if not payment_intent_id:
        return None

    row = db.execute(
        text(
            """
            SELECT id
            FROM share_bundles
            WHERE stripe_payment_intent_id = :payment_intent_id
            LIMIT 1
            """
        ),
        {"payment_intent_id": payment_intent_id},
    ).mappings().first()

    if not row:
        return None

    return db.query(ShareBundle).filter(ShareBundle.id == row["id"]).first()


def _mark_bundle_paid(
    db: Session,
    bundle: ShareBundle,
    session_id: str | None = None,
    *,
    payment_intent_id: str | None = None,
    charge_id: str | None = None,
    transfer_id: str | None = None,
    connect_account_id: str | None = None,
    connect_mode: str | None = None,
    platform_fee_amount: int | None = None,
    platform_fee_currency: str | None = None,
    stripe_fee_amount: int | None = None,
    stripe_fee_currency: str | None = None,
):
    snapshot = _get_bundle_financial_snapshot(db, bundle.id)
    gross = int(snapshot.get("price_amount") or getattr(bundle, "price_amount", 0) or 0)
    platform_fee = int(platform_fee_amount if platform_fee_amount is not None else snapshot.get("platform_fee_amount") or 0)

    effective_stripe_fee = stripe_fee_amount
    effective_stripe_fee_currency = stripe_fee_currency
    if effective_stripe_fee is None:
        effective_stripe_fee = snapshot.get("stripe_fee_amount")
    if effective_stripe_fee_currency is None:
        effective_stripe_fee_currency = snapshot.get("stripe_fee_currency")

    split = calculate_revenue_split(
        gross_minor=gross,
        commission_minor=platform_fee,
        stripe_fee_minor=effective_stripe_fee or 0,
    )
    practitioner_payout = split["practitioner_payout_minor"] if gross else None

    db.execute(
        text(
            """
            UPDATE share_bundles
            SET payment_status = 'paid',
                stripe_session_id = COALESCE(:session_id, stripe_session_id),
                stripe_payment_intent_id = COALESCE(:payment_intent_id, stripe_payment_intent_id),
                stripe_charge_id = COALESCE(:charge_id, stripe_charge_id),
                stripe_transfer_id = COALESCE(:transfer_id, stripe_transfer_id),
                stripe_connect_account_id = COALESCE(:connect_account_id, stripe_connect_account_id),
                stripe_connect_mode = COALESCE(:connect_mode, stripe_connect_mode, 'platform_only'),
                platform_fee_amount = COALESCE(:platform_fee_amount, platform_fee_amount, 0),
                platform_fee_currency = COALESCE(:platform_fee_currency, platform_fee_currency, price_currency, 'gbp'),
                stripe_fee_amount = COALESCE(:stripe_fee_amount, stripe_fee_amount),
                stripe_fee_currency = COALESCE(:stripe_fee_currency, stripe_fee_currency, price_currency, 'gbp'),
                practitioner_payout_amount = :practitioner_payout,
                paid_at = COALESCE(paid_at, NOW()),
                updated_at = NOW()
            WHERE id = :bundle_id
            """
        ),
        {
            "bundle_id": str(bundle.id),
            "session_id": session_id,
            "payment_intent_id": payment_intent_id,
            "charge_id": charge_id,
            "transfer_id": transfer_id,
            "connect_account_id": connect_account_id,
            "connect_mode": connect_mode,
            "platform_fee_amount": platform_fee,
            "platform_fee_currency": platform_fee_currency,
            "stripe_fee_amount": stripe_fee_amount,
            "stripe_fee_currency": stripe_fee_currency,
            "practitioner_payout": practitioner_payout,
        },
    )
    db.commit()
    db.refresh(bundle)

    # V4 referral revenue attribution. This is deliberately best-effort and
    # must never block payment completion or bundle unlocking.
    try:
        record_referral_revenue(
            db,
            referred_user_id=getattr(bundle, "created_by_user_id", None),
            amount_minor=gross,
            currency=(snapshot.get("price_currency") or getattr(bundle, "price_currency", None) or "gbp"),
            source_id=getattr(bundle, "id", None),
            source_type="share_bundle",
            commission_minor=platform_fee,
            commission_rate=0,
        )
    except Exception as exc:
        db.rollback()
        print(f"[referral] revenue attribution skipped for bundle {getattr(bundle, 'id', None)}: {exc}")

    return bundle


@router.post("/api/share-bundles")
def create_share_bundle(
    payload: ShareBundleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_subscription_feature(db, current_user, "share_bundle")

    reports = _owned_reports(db, current_user, payload.report_version_ids)
    patient_ids = {str(r.case.patient_id) for r in reports if getattr(r, "case", None)}
    patient_id = reports[0].case.patient_id if len(patient_ids) == 1 else None

    expires_at = datetime.now(timezone.utc) + timedelta(days=int(payload.expires_days or 7)) if payload.expires_days else None
    requires_payment = bool(payload.price_amount and payload.price_amount > 0)
    password_hash = hash_password(_safe_password_for_bcrypt(payload.password)) if payload.password else None

    title = payload.title
    if not title:
        if len(reports) == 1:
            title = "Progress trend report" if _report_type(reports[0]) == "trend" else "Assessment report"
        else:
            title = f"Report bundle ({len(reports)} items)"

    bundle = ShareBundle(
        token=generate_token(32),
        created_by_user_id=current_user.id,
        patient_id=patient_id,
        title=title,
        access_label=payload.access_label or title,
        password_hash=password_hash,
        expires_at=expires_at,
        is_active=True,
        requires_payment=requires_payment,
        payment_status="unpaid" if requires_payment else "not_required",
        price_amount=int(payload.price_amount) if requires_payment else None,
        price_currency=(payload.price_currency or "gbp").lower(),
    )
    db.add(bundle)
    db.flush()

    for idx, report in enumerate(reports):
        child_link = _create_child_share_link(
            db,
            report=report,
            patient_id=patient_id,
            title=title,
            expires_at=expires_at,
            password_hash=password_hash,
        )
        db.add(
            ShareBundleItem(
                share_bundle_id=bundle.id,
                report_version_id=report.id,
                share_link_id=child_link.id,
                position=idx,
            )
        )

    db.commit()
    db.refresh(bundle)

    return {
        "success": True,
        "id": str(bundle.id),
        "token": bundle.token,
        "url": _bundle_url(bundle.token),
        "title": bundle.title,
        "requires_payment": bundle.requires_payment,
        "payment_status": bundle.payment_status,
        "price_amount": bundle.price_amount,
        "price_currency": bundle.price_currency,
    }


@router.post("/api/share-bundles/{bundle_id}/revoke")
def revoke_share_bundle(
    bundle_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bundle = (
        db.query(ShareBundle)
        .options(joinedload(ShareBundle.items))
        .filter(ShareBundle.id == bundle_id, ShareBundle.created_by_user_id == current_user.id)
        .first()
    )
    if not bundle:
        raise HTTPException(status_code=404, detail="Share bundle not found.")

    bundle.is_active = False
    child_ids = [item.share_link_id for item in bundle.items if getattr(item, "share_link_id", None)]
    if child_ids:
        db.query(ShareLink).filter(ShareLink.id.in_(child_ids)).update(
            {ShareLink.is_active: False},
            synchronize_session=False,
        )
    db.commit()
    return {"success": True}


@router.post("/api/share-bundles/{bundle_id}/extend-expiry")
def extend_share_bundle_expiry(
    bundle_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bundle = (
        db.query(ShareBundle)
        .options(joinedload(ShareBundle.items))
        .filter(ShareBundle.id == bundle_id, ShareBundle.created_by_user_id == current_user.id)
        .first()
    )
    if not bundle:
        raise HTTPException(status_code=404, detail="Share bundle not found.")

    remove_expiry = bool(payload.get("remove_expiry"))
    days = payload.get("days")

    if remove_expiry:
        new_expires_at = None
    else:
        try:
            days_int = int(days if days is not None else 7)
        except Exception:
            days_int = 7
        if days_int <= 0:
            new_expires_at = None
        else:
            base_dt = bundle.expires_at or datetime.now(timezone.utc)
            if base_dt.tzinfo is None:
                base_dt = base_dt.replace(tzinfo=timezone.utc)
            new_expires_at = base_dt + timedelta(days=days_int)

    bundle.expires_at = new_expires_at

    child_ids = [item.share_link_id for item in bundle.items if getattr(item, "share_link_id", None)]
    if child_ids:
        db.query(ShareLink).filter(ShareLink.id.in_(child_ids)).update(
            {ShareLink.expires_at: new_expires_at},
            synchronize_session=False,
        )

    db.commit()
    return {"success": True, "expires_at": new_expires_at.isoformat() if new_expires_at else None}



@router.get("/share/bundle/{token}", response_class=HTMLResponse)
def view_share_bundle(token: str, request: Request, db: Session = Depends(get_db)):
    bundle = (
        db.query(ShareBundle)
        .options(
            joinedload(ShareBundle.items).joinedload(ShareBundleItem.report_version).joinedload(ReportVersion.case),
            joinedload(ShareBundle.items).joinedload(ShareBundleItem.share_link),
        )
        .filter(ShareBundle.token == token)
        .first()
    )

    unavailable_reason = _bundle_unavailable_reason(bundle)
    if unavailable_reason:
        return _bundle_unavailable_response(request, unavailable_reason)

    if bundle.requires_payment and bundle.payment_status != "paid":
        _track_share_event(db, token, "paywall_view", {"share_type": "bundle", "payment_status": bundle.payment_status})
        return templates.TemplateResponse(
            request=request,
            name="share_bundle_paywall.html",
            context={
                "request": request,
                "bundle": bundle,
                "amount_label": _format_minor_amount(bundle.price_amount, bundle.price_currency),
            },
        )

    
    if request.query_params.get("preview") != "1":
        _track_share_event(db, token, "client_view", {"share_type": "bundle", "payment_status": bundle.payment_status, "actor": "client"})

    reports = []
    for item in bundle.items:
        report = item.report_version
        link = getattr(item, "share_link", None)
        if not report or not link or not link.is_active:
            continue
        reports.append(
            {
                "id": str(report.id),
                "title": _report_title(report),
                "report_type": _report_type(report),
                "generated_at": report.generated_at,
                "viewer_url": (_item_viewer_url(link, report) + ("?preview=1" if request.query_params.get("preview") == "1" else "")),
                "share_token": link.token,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="share_bundle_view.html",
        context={"request": request, "bundle": bundle, "reports": reports},
    )


@router.post("/share/bundle/{token}/checkout")
def create_bundle_checkout_session(token: str, request: Request, db: Session = Depends(get_db)):
    bundle = (
        db.query(ShareBundle)
        .filter(ShareBundle.token == token)
        .first()
    )

    unavailable_reason = _bundle_unavailable_reason(bundle)
    if unavailable_reason:
        return _bundle_unavailable_response(request, unavailable_reason)

    if not bundle.requires_payment:
        return RedirectResponse(url=f"/share/bundle/{token}", status_code=303)
    if bundle.payment_status == "paid":
        return RedirectResponse(url=f"/share/bundle/{token}", status_code=303)
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe is not configured. Set STRIPE_SECRET_KEY.")
    if not bundle.price_amount or bundle.price_amount <= 0:
        raise HTTPException(status_code=400, detail="This bundle has no valid payment amount.")

    success_url = f"{BASE_URL.rstrip('/')}/share/bundle/{token}/payment-success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{BASE_URL.rstrip('/')}/share/bundle/{token}"

    destination = _connect_destination_for_bundle(db, bundle)

    payment_intent_metadata = {
        "kind": "share_bundle",
        "bundle_id": str(bundle.id),
        "bundle_token": bundle.token,
        "connect_mode": destination["mode"],
        "stripe_connect_account_id": destination.get("account_id") or "",
        "platform_fee_amount": str(destination.get("platform_fee_amount") or 0),
    }

    payment_intent_data = {"metadata": payment_intent_metadata}

    if destination["mode"] == "destination_charge":
        payment_intent_data["application_fee_amount"] = int(destination["platform_fee_amount"] or 0)
        payment_intent_data["transfer_data"] = {"destination": destination["account_id"]}

    session = stripe.checkout.Session.create(
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(bundle.id),
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": (bundle.price_currency or "gbp").lower(),
                    "unit_amount": int(bundle.price_amount),
                    "product_data": {
                        "name": bundle.title or "Shared health report bundle",
                        "description": bundle.access_label or "Secure client access to shared health reports",
                    },
                },
            }
        ],
        metadata=payment_intent_metadata,
        payment_intent_data=payment_intent_data,
    )

    _record_checkout_routing(
        db,
        bundle,
        session_id=session.id,
        connect_mode=destination["mode"],
        connect_account_id=destination.get("account_id"),
        platform_fee_amount=int(destination.get("platform_fee_amount") or 0),
        platform_fee_currency=(bundle.price_currency or "gbp").lower(),
    )
    _track_share_event(db, token, "payment_started", {
        "share_type": "bundle",
        "stripe_session_id": session.id,
        "amount": int(bundle.price_amount or 0),
        "currency": (bundle.price_currency or "gbp").lower(),
        "stripe_connect_mode": destination["mode"],
    })

    return RedirectResponse(url=session.url, status_code=303)


@router.get("/share/bundle/{token}/payment-success")
def bundle_payment_success(token: str, request: Request, db: Session = Depends(get_db)):
    """
    UX redirect only. The webhook is authoritative for unlocking paid bundles.
    """
    bundle = db.query(ShareBundle).filter(ShareBundle.token == token).first()
    unavailable_reason = _bundle_unavailable_reason(bundle)
    if unavailable_reason:
        return _bundle_unavailable_response(request, unavailable_reason)

    return RedirectResponse(url=f"/share/bundle/{token}", status_code=303)


@router.post("/api/share-bundles/stripe-webhook")
async def share_bundle_stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Stripe webhook secret is not configured.")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Stripe webhook: {exc}")

    event_id = event.get("id")
    event_type = event.get("type")

    if _webhook_event_seen(db, event_id):
        return {"received": True, "duplicate": True}

    try:
        obj = event["data"]["object"]

        if event_type == "checkout.session.completed":
            session = obj
            metadata = session.get("metadata") or {}

            if metadata.get("kind") == "share_bundle":
                bundle_id = metadata.get("bundle_id")
                bundle = db.query(ShareBundle).filter(ShareBundle.id == bundle_id).first() if bundle_id else None

                if bundle:
                    payment_intent_id = session.get("payment_intent")
                    refs = _extract_stripe_financials(payment_intent_id)
                    _mark_bundle_paid(
                        db,
                        bundle,
                        session_id=session.get("id"),
                        payment_intent_id=payment_intent_id,
                        charge_id=refs.get("charge_id"),
                        transfer_id=refs.get("transfer_id"),
                        connect_account_id=metadata.get("stripe_connect_account_id") or None,
                        connect_mode=metadata.get("connect_mode") or None,
                        platform_fee_amount=int(metadata.get("platform_fee_amount") or 0),
                        platform_fee_currency=(bundle.price_currency or "gbp").lower(),
                        stripe_fee_amount=refs.get("stripe_fee_amount"),
                        stripe_fee_currency=refs.get("stripe_fee_currency"),
                    )
                    _track_share_event(db, bundle.token, "paid", {
                        "share_type": "bundle",
                        "stripe_session_id": session.get("id"),
                        "stripe_payment_intent_id": payment_intent_id,
                        "source": "checkout.session.completed",
                    })

        elif event_type in {"payment_intent.succeeded", "charge.succeeded", "charge.updated"}:
            payment_intent_id = obj.get("id") if event_type == "payment_intent.succeeded" else obj.get("payment_intent")
            charge_id = obj.get("id") if event_type in {"charge.succeeded", "charge.updated"} else None

            bundle = _find_bundle_by_payment_intent(db, payment_intent_id)
            if bundle:
                refs = _extract_stripe_financials(payment_intent_id)
                _mark_bundle_paid(
                    db,
                    bundle,
                    payment_intent_id=payment_intent_id,
                    charge_id=refs.get("charge_id") or charge_id,
                    transfer_id=refs.get("transfer_id"),
                    stripe_fee_amount=refs.get("stripe_fee_amount"),
                    stripe_fee_currency=refs.get("stripe_fee_currency"),
                )
                _track_share_event(db, bundle.token, "paid", {
                    "share_type": "bundle",
                    "stripe_payment_intent_id": payment_intent_id,
                    "source": event_type,
                })

        _record_webhook_event(db, event_id, event_type)

    except Exception:
        # Do not record failed events as processed; Stripe should retry.
        raise

    return {"received": True}



@router.get("/share/bundle/{token}/reports/{report_id}", response_class=HTMLResponse)
def view_bundle_report(token: str, report_id: UUID, request: Request, db: Session = Depends(get_db)):
    bundle = (
        db.query(ShareBundle)
        .options(
            joinedload(ShareBundle.items).joinedload(ShareBundleItem.report_version),
            joinedload(ShareBundle.items).joinedload(ShareBundleItem.share_link),
        )
        .filter(ShareBundle.token == token)
        .first()
    )

    unavailable_reason = _bundle_unavailable_reason(bundle)
    if unavailable_reason:
        return _bundle_unavailable_response(request, unavailable_reason)

    if bundle.requires_payment and bundle.payment_status != "paid":
        return RedirectResponse(f"/share/bundle/{token}", status_code=302)

    for item in bundle.items:
        if item.report_version_id == report_id and item.share_link and item.share_link.is_active:
            return RedirectResponse(_item_viewer_url(item.share_link, item.report_version), status_code=302)

    return _bundle_unavailable_response(request, "missing")
