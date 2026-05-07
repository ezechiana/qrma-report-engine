from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.services.fx_service import build_revenue_goal_payload, ensure_current_month_fx_snapshot
from app.services.platform_settings_service import get_platform_settings, get_revenue_fee_model_from_settings, is_feature_enabled
from app.services.revenue_model import calculate_revenue_split

router = APIRouter(tags=["revenue"])
templates = Jinja2Templates(directory="app/templates")

ZERO_DECIMAL_CURRENCIES = {"jpy", "krw"}


def _money(amount: int | None, currency: str | None) -> str:
    if amount is None:
        return "—"
    curr = (currency or "gbp").lower()
    symbols = {"gbp": "£", "usd": "$", "eur": "€", "aed": "د.إ", "jpy": "¥", "krw": "₩"}
    symbol = symbols.get(curr, curr.upper() + " ")
    if curr in ZERO_DECIMAL_CURRENCIES:
        return f"{symbol}{int(amount):,}"
    return f"{symbol}{amount / 100:,.2f}"


def _dt(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        return value
    try:
        return value.strftime("%d/%m/%Y, %H:%M")
    except Exception:
        return str(value)


def _row_to_dict(row) -> dict[str, Any]:
    return dict(row._mapping)


def _settings_goal_config(db: Session, user_id) -> dict[str, Any]:
    """Resolve practitioner goal settings with platform defaults as fallback.

    This is deliberately defensive so a partially migrated database does not
    break the revenue dashboard. Practitioner columns are preferred when they
    exist; otherwise platform defaults are used.
    """
    platform = get_platform_settings(db).get("revenue", {})
    fallback_currency = str(platform.get("default_currency") or "USD").upper()
    fallback_goal = int(platform.get("default_monthly_goal_minor") or 200000)

    try:
        row = db.execute(
            text(
                """
                SELECT preferred_currency, monthly_goal_minor
                FROM practitioner_settings
                WHERE user_id = :user_id
                LIMIT 1
                """
            ),
            {"user_id": str(user_id)},
        ).mappings().first()
    except Exception:
        db.rollback()
        row = None

    preferred_currency = fallback_currency
    monthly_goal_minor = fallback_goal

    if row:
        preferred_currency = str(row.get("preferred_currency") or fallback_currency).upper()
        monthly_goal_minor = int(row.get("monthly_goal_minor") or fallback_goal)

    return {
        "preferred_currency": preferred_currency,
        "monthly_goal_minor": monthly_goal_minor,
    }


def _fetch_payment_rows(db: Session, user_id) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                b.id,
                b.token,
                b.title,
                b.access_label,
                b.created_at,
                b.paid_at,
                b.payment_status,
                b.requires_payment,
                b.price_amount,
                COALESCE(b.price_currency, 'gbp') AS price_currency,
                b.platform_fee_amount,
                b.practitioner_payout_amount,
                b.stripe_fee_amount,
                b.stripe_fee_currency,
                b.stripe_connect_mode,
                b.stripe_connect_account_id,
                b.stripe_session_id,
                b.stripe_payment_intent_id,
                b.stripe_charge_id,
                b.stripe_transfer_id,
                p.full_name AS patient_name,
                COUNT(i.id) AS item_count,
                COUNT(rv.id) FILTER (WHERE COALESCE(rv.report_type, 'assessment') = 'assessment') AS assessment_count,
                COUNT(rv.id) FILTER (WHERE rv.report_type = 'trend') AS trend_count
            FROM share_bundles b
            LEFT JOIN patients p ON p.id = b.patient_id
            LEFT JOIN share_bundle_items i ON i.share_bundle_id = b.id
            LEFT JOIN report_versions rv ON rv.id = i.report_version_id
            WHERE b.created_by_user_id = :user_id
              AND b.requires_payment = true
            GROUP BY
                b.id, b.token, b.title, b.access_label, b.created_at, b.paid_at,
                b.payment_status, b.requires_payment, b.price_amount, b.price_currency,
                b.platform_fee_amount, b.practitioner_payout_amount, b.stripe_fee_amount,
                b.stripe_fee_currency, b.stripe_connect_mode, b.stripe_connect_account_id,
                b.stripe_session_id, b.stripe_payment_intent_id, b.stripe_charge_id,
                b.stripe_transfer_id, p.full_name
            ORDER BY COALESCE(b.paid_at, b.created_at) DESC
            LIMIT 250
            """
        ),
        {"user_id": str(user_id)},
    ).fetchall()
    return [_row_to_dict(row) for row in rows]



def _connect_mode(value: Any) -> str:
    return str(value or "platform_only").lower()


def _is_platform_collected_mode(mode: str | None) -> bool:
    return _connect_mode(mode) in {"platform_only", "platform_fallback", "platform"}


def _safe_financials(*, gross: int, platform_fee: int, stripe_fee: int, fee_model: str, connect_mode: str | None) -> dict[str, int | bool | str]:
    """Return revenue figures that separate operational revenue from reconciliation.

    Stripe Connect destination charges can legitimately use platform commission less
    Stripe fees as operational platform net. For platform-only/fallback payments,
    negative platform net is a reconciliation position only: the platform collected
    the payment because Connect was unavailable/not routed, so the UI must not show
    a negative value as operating profit/loss.
    """
    split = calculate_revenue_split(
        gross_minor=gross,
        commission_minor=platform_fee,
        stripe_fee_minor=stripe_fee,
        fee_model=fee_model,
    )

    raw_platform_net = int(split.get("platform_net_minor") or 0)
    practitioner_allocation = int(split.get("practitioner_payout_minor") or 0)
    platform_collected = _is_platform_collected_mode(connect_mode)

    # For fallback/platform-only payments, the business meaning is allocation +
    # reconciliation, not platform profit. Never display a negative number as net.
    if platform_collected and raw_platform_net < 0:
        display_platform_net = 0
        reconciliation_adjustment = raw_platform_net
        platform_net_kind = "reconciliation_only"
    else:
        display_platform_net = raw_platform_net
        reconciliation_adjustment = 0
        platform_net_kind = "operational"

    return {
        "platform_net_raw_minor": raw_platform_net,
        "platform_net_display_minor": display_platform_net,
        "platform_net_reconciliation_minor": reconciliation_adjustment,
        "platform_net_kind": platform_net_kind,
        "is_platform_collected": platform_collected,
        "practitioner_allocation_minor": practitioner_allocation,
    }

def _serialise_payment_row(r: dict[str, Any], fee_model: str) -> dict[str, Any]:
    curr = r.get("price_currency") or "gbp"
    gross = int(r.get("price_amount") or 0)
    platform_fee = int(r.get("platform_fee_amount") or 0)
    stripe_fee = int(r.get("stripe_fee_amount") or 0)
    connect_mode = _connect_mode(r.get("stripe_connect_mode"))
    figures = _safe_financials(
        gross=gross,
        platform_fee=platform_fee,
        stripe_fee=stripe_fee,
        fee_model=fee_model,
        connect_mode=connect_mode,
    )

    platform_net_note = None
    if figures["platform_net_kind"] == "reconciliation_only":
        platform_net_note = "Stripe fee absorbed by platform for reconciliation; not operating profit/loss."
    elif figures["is_platform_collected"]:
        platform_net_note = "Platform-collected payment; reconcile allocation separately."

    return {
        "id": str(r["id"]),
        "token": r["token"],
        "title": r.get("title") or r.get("access_label") or "Report bundle",
        "patient": r.get("patient_name") or "—",
        "patient_name": r.get("patient_name") or "—",
        "created_at": r.get("created_at").isoformat() if r.get("created_at") else None,
        "created_display": _dt(r.get("created_at")),
        "paid_at": r.get("paid_at").isoformat() if r.get("paid_at") else None,
        "paid_display": _dt(r.get("paid_at")),
        "payment_status": r.get("payment_status") or "unknown",
        "currency": curr.upper(),
        "price_currency": curr.upper(),
        "fee_model": fee_model,
        "gross": _money(gross, curr),
        "platform_fee": _money(platform_fee, curr),
        "platform_net": _money(int(figures["platform_net_display_minor"]), curr),
        "platform_net_raw": _money(int(figures["platform_net_raw_minor"]), curr),
        "platform_net_reconciliation": _money(int(figures["platform_net_reconciliation_minor"]), curr) if int(figures["platform_net_reconciliation_minor"]) else "—",
        "platform_net_kind": figures["platform_net_kind"],
        "platform_net_note": platform_net_note,
        "practitioner_payout": _money(int(figures["practitioner_allocation_minor"]), curr),
        "practitioner_allocation": _money(int(figures["practitioner_allocation_minor"]), curr),
        "stripe_fee": _money(stripe_fee, r.get("stripe_fee_currency") or curr) if stripe_fee else "—",
        "gross_minor": gross,
        "price_amount": gross,
        "platform_fee_minor": platform_fee,
        "platform_net_minor": int(figures["platform_net_display_minor"]),
        "platform_net_raw_minor": int(figures["platform_net_raw_minor"]),
        "platform_net_reconciliation_minor": int(figures["platform_net_reconciliation_minor"]),
        "practitioner_payout_minor": int(figures["practitioner_allocation_minor"]),
        "practitioner_allocation_minor": int(figures["practitioner_allocation_minor"]),
        "stripe_fee_minor": stripe_fee,
        "connect_mode": connect_mode,
        "is_platform_collected": bool(figures["is_platform_collected"]),
        "connected_account": r.get("stripe_connect_account_id") or "—",
        "stripe_session_id": r.get("stripe_session_id") or "—",
        "stripe_payment_intent_id": r.get("stripe_payment_intent_id") or "—",
        "stripe_charge_id": r.get("stripe_charge_id") or "—",
        "stripe_transfer_id": r.get("stripe_transfer_id") or "—",
        "item_summary": f"{int(r.get('item_count') or 0)} items · {int(r.get('assessment_count') or 0)} assessments · {int(r.get('trend_count') or 0)} trends",
        "bundle_url": f"/share/bundle/{r['token']}",
    }

@router.get("/app/revenue", response_class=HTMLResponse)
def revenue_page(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not is_feature_enabled(db, "revenue_dashboard_enabled", default=True):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Revenue dashboard is disabled by platform settings.")
    return templates.TemplateResponse(
        request=request,
        name="revenue.html",
        context={
            "request": request,
            "user": current_user,
            "fee_model": "platform_absorbs",
        },
    )


@router.get("/api/revenue/summary")
def revenue_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_feature_enabled(db, "revenue_dashboard_enabled", default=True):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Revenue dashboard is disabled by platform settings.")

    fee_model = get_revenue_fee_model_from_settings(db)
    payment_rows = _fetch_payment_rows(db, current_user.id)
    items = [_serialise_payment_row(row, fee_model) for row in payment_rows]

    paid_items = [p for p in items if str(p.get("payment_status") or "").lower() == "paid"]
    unpaid_items = [p for p in items if str(p.get("payment_status") or "").lower() != "paid"]

    by_currency: dict[str, dict[str, Any]] = {}
    for p in items:
        curr = str(p.get("currency") or "GBP").upper()
        bucket = by_currency.setdefault(curr, {
            "currency": curr,
            "paid_count": 0,
            "unpaid_count": 0,
            "gross_minor": 0,
            "platform_fee_minor": 0,
            "platform_net_minor": 0,
            "platform_net_raw_minor": 0,
            "platform_net_reconciliation_minor": 0,
            "practitioner_payout_minor": 0,
            "stripe_fee_minor": 0,
        })
        if str(p.get("payment_status") or "").lower() == "paid":
            bucket["paid_count"] += 1
            bucket["gross_minor"] += int(p.get("gross_minor") or 0)
            bucket["platform_fee_minor"] += int(p.get("platform_fee_minor") or 0)
            bucket["platform_net_minor"] += int(p.get("platform_net_minor") or 0)
            bucket["platform_net_raw_minor"] += int(p.get("platform_net_raw_minor") or 0)
            bucket["platform_net_reconciliation_minor"] += int(p.get("platform_net_reconciliation_minor") or 0)
            bucket["practitioner_payout_minor"] += int(p.get("practitioner_payout_minor") or 0)
            bucket["stripe_fee_minor"] += int(p.get("stripe_fee_minor") or 0)
        else:
            bucket["unpaid_count"] += 1

    currencies = []
    for curr in sorted(by_currency):
        c = by_currency[curr]
        currencies.append({
            **c,
            "gross": _money(c["gross_minor"], curr),
            "platform_fee": _money(c["platform_fee_minor"], curr),
            "platform_net": _money(c["platform_net_minor"], curr),
            "platform_net_raw": _money(c["platform_net_raw_minor"], curr),
            "platform_net_reconciliation": _money(c["platform_net_reconciliation_minor"], curr) if c["platform_net_reconciliation_minor"] else "—",
            "platform_net_has_reconciliation_adjustment": c["platform_net_reconciliation_minor"] < 0,
            "practitioner_payout": _money(c["practitioner_payout_minor"], curr),
            "practitioner_allocation": _money(c["practitioner_payout_minor"], curr),
            "stripe_fee": _money(c["stripe_fee_minor"], curr) if c["stripe_fee_minor"] else "—",
        })

    goal_config = _settings_goal_config(db, current_user.id)
    snapshot = ensure_current_month_fx_snapshot(db)
    revenue_goal = build_revenue_goal_payload(
        payments=payment_rows,
        preferred_currency=goal_config["preferred_currency"],
        monthly_goal_minor=goal_config["monthly_goal_minor"],
        snapshot=snapshot,
        now=None,
    )

    return {
        "fee_model": fee_model,
        "paid_count": len(paid_items),
        "unpaid_count": len(unpaid_items),
        "currencies": currencies,
        "preferred_currency": goal_config["preferred_currency"],
        "monthly_goal_minor": goal_config["monthly_goal_minor"],
        "revenue_goal": revenue_goal,
    }

@router.get("/api/revenue/payments")
def revenue_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_feature_enabled(db, "revenue_dashboard_enabled", default=True):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Revenue dashboard is disabled by platform settings.")
    fee_model = get_revenue_fee_model_from_settings(db)
    items = [_serialise_payment_row(row, fee_model) for row in _fetch_payment_rows(db, current_user.id)]
    return {"fee_model": fee_model, "items": items}
