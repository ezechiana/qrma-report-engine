# app/api/routes_share_links.py

from __future__ import annotations

import os
from uuid import UUID
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.share_helpers import DEFAULT_SHARE_PRICE_CURRENCY, _ensure_share_link_events_table
from app.db.models import Case, Patient, ReportVersion, ShareLink, User
from app.schemas.share import ReportShareLinkCreate, ShareLinkRead
from app.services.audit_service import log_action
from app.services.share_link_service import create_share_link
from app.services.subscription_service import require_subscription_feature

router = APIRouter(tags=["share"])


@router.post("/api/reports/{report_version_id}/share-links", response_model=ShareLinkRead)
def create_report_share_link(
    report_version_id: UUID,
    payload: ReportShareLinkCreate,
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
        expires_days=payload.expires_in_days,
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


@router.post("/api/share/create")
def create_share(payload: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_subscription_feature(db, user, "share_link")
    if payload.get("price"):
        require_subscription_feature(db, user, "sell_shares")

    try:
        report_id = UUID(str(payload.get("report_version_id")))
        patient_id = UUID(str(payload["patient_id"])) if payload.get("patient_id") else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid report or patient ID") from exc

    report = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ReportVersion.id == report_id, Case.user_id == user.id)
        .first()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if patient_id and not db.query(Patient.id).filter(Patient.id == patient_id, Patient.user_id == user.id).first():
        raise HTTPException(status_code=404, detail="Patient not found")

    share = create_share_link(
        db=db,
        report_version=report,
        patient_id=patient_id,
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

    # The share analytics table was introduced after the original sharing MVP.
    # Ensure it exists so the dashboard does not 500 on freshly deployed/staging DBs.
    _ensure_share_link_events_table(db)

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
