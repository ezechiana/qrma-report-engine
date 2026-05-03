from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.services.revenue_model import calculate_revenue_split, get_revenue_fee_model
from app.services.fx_service import ensure_current_month_fx_snapshot, build_revenue_goal_payload

router = APIRouter(tags=["revenue"])
templates = Jinja2Templates(directory="app/templates")


def _money(amount: int | None, currency: str | None) -> str:
    if amount is None:
        return "—"
    curr = (currency or "gbp").lower()
    symbols = {"gbp": "£", "usd": "$", "eur": "€", "aed": "د.إ", "jpy": "¥", "krw": "₩"}
    symbol = symbols.get(curr, curr.upper() + " ")
    zero_decimal = {"jpy", "krw"}
    if curr in zero_decimal:
        return f"{symbol}{amount:,.0f}"
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

    return {
        "preferred_currency": (row["preferred_currency"] if row and row.get("preferred_currency") else "USD"),
        "monthly_goal_minor": int(row["monthly_goal_minor"] if row and row.get("monthly_goal_minor") else 200000),
    }


def _paid_bundle_rows_for_goal(db: Session, user_id) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                id,
                paid_at,
                created_at,
                payment_status,
                price_amount,
                COALESCE(price_currency, 'gbp') AS price_currency
            FROM share_bundles
            WHERE created_by_user_id = :user_id
              AND requires_payment = true
              AND payment_status = 'paid'
              AND price_amount IS NOT NULL
            ORDER BY COALESCE(paid_at, created_at) DESC
            LIMIT 1000
            """
        ),
        {"user_id": str(user_id)},
    ).mappings().all()
    return [dict(r) for r in rows]



@router.get("/app/revenue", response_class=HTMLResponse)
def revenue_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="revenue.html",
        context={
            "request": request,
            "user": current_user,
            "fee_model": get_revenue_fee_model(),
        },
    )


@router.get("/api/revenue/summary")
def revenue_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        text(
            """
            SELECT
                COALESCE(price_currency, 'gbp') AS currency,
                COUNT(*) FILTER (WHERE payment_status = 'paid') AS paid_count,
                COUNT(*) FILTER (WHERE requires_payment = true AND payment_status <> 'paid') AS unpaid_count,
                COALESCE(SUM(price_amount) FILTER (WHERE payment_status = 'paid'), 0) AS gross_amount,
                COALESCE(SUM(platform_fee_amount) FILTER (WHERE payment_status = 'paid'), 0) AS platform_fee_amount,
                COALESCE(SUM(practitioner_payout_amount) FILTER (WHERE payment_status = 'paid'), 0) AS stored_practitioner_payout_amount,
                COALESCE(SUM(stripe_fee_amount) FILTER (WHERE payment_status = 'paid'), 0) AS stripe_fee_amount
            FROM share_bundles
            WHERE created_by_user_id = :user_id
              AND requires_payment = true
            GROUP BY COALESCE(price_currency, 'gbp')
            ORDER BY COALESCE(price_currency, 'gbp')
            """
        ),
        {"user_id": str(current_user.id)},
    ).fetchall()

    fee_model = get_revenue_fee_model()
    currencies = []
    total_paid_count = 0
    total_unpaid_count = 0

    for row in rows:
        r = _row_to_dict(row)
        curr = r["currency"]
        gross = int(r.get("gross_amount") or 0)
        platform_fee = int(r.get("platform_fee_amount") or 0)
        stripe_fee = int(r.get("stripe_fee_amount") or 0)
        split = calculate_revenue_split(
            gross_minor=gross,
            commission_minor=platform_fee,
            stripe_fee_minor=stripe_fee,
            fee_model=fee_model,
        )

        paid_count = int(r.get("paid_count") or 0)
        unpaid_count = int(r.get("unpaid_count") or 0)
        total_paid_count += paid_count
        total_unpaid_count += unpaid_count

        currencies.append(
            {
                "currency": curr.upper(),
                "paid_count": paid_count,
                "unpaid_count": unpaid_count,
                "gross": _money(gross, curr),
                "platform_fee": _money(platform_fee, curr),
                "platform_net": _money(split["platform_net_minor"], curr),
                "practitioner_payout": _money(split["practitioner_payout_minor"], curr),
                "stripe_fee": _money(stripe_fee, curr) if stripe_fee else "—",
                "gross_minor": gross,
                "platform_fee_minor": platform_fee,
                "platform_net_minor": split["platform_net_minor"],
                "practitioner_payout_minor": split["practitioner_payout_minor"],
                "stripe_fee_minor": stripe_fee,
            }
        )

    goal_config = _settings_goal_config(db, current_user.id)
    fx_snapshot = ensure_current_month_fx_snapshot(db)
    revenue_goal = build_revenue_goal_payload(
        payments=_paid_bundle_rows_for_goal(db, current_user.id),
        preferred_currency=goal_config["preferred_currency"],
        monthly_goal_minor=goal_config["monthly_goal_minor"],
        snapshot=fx_snapshot,
        now=datetime.now(timezone.utc),
    )

    return {
        "fee_model": fee_model,
        "paid_count": total_paid_count,
        "unpaid_count": total_unpaid_count,
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
        {"user_id": str(current_user.id)},
    ).fetchall()

    fee_model = get_revenue_fee_model()
    items = []

    for row in rows:
        r = _row_to_dict(row)
        curr = r.get("price_currency") or "gbp"
        gross = int(r.get("price_amount") or 0)
        platform_fee = int(r.get("platform_fee_amount") or 0)
        stripe_fee = int(r.get("stripe_fee_amount") or 0)
        split = calculate_revenue_split(
            gross_minor=gross,
            commission_minor=platform_fee,
            stripe_fee_minor=stripe_fee,
            fee_model=fee_model,
        )

        items.append(
            {
                "id": str(r["id"]),
                "token": r["token"],
                "title": r.get("title") or r.get("access_label") or "Report bundle",
                "patient": r.get("patient_name") or "—",
                "created_at": r.get("created_at").isoformat() if r.get("created_at") else None,
                "created_display": _dt(r.get("created_at")),
                "paid_at": r.get("paid_at").isoformat() if r.get("paid_at") else None,
                "paid_display": _dt(r.get("paid_at")),
                "payment_status": r.get("payment_status") or "unknown",
                "currency": curr.upper(),
                "fee_model": fee_model,
                "gross": _money(gross, curr),
                "platform_fee": _money(platform_fee, curr),
                "platform_net": _money(split["platform_net_minor"], curr),
                "practitioner_payout": _money(split["practitioner_payout_minor"], curr),
                "stripe_fee": _money(stripe_fee, r.get("stripe_fee_currency") or curr) if stripe_fee else "—",
                "gross_minor": gross,
                "platform_fee_minor": platform_fee,
                "platform_net_minor": split["platform_net_minor"],
                "practitioner_payout_minor": split["practitioner_payout_minor"],
                "stripe_fee_minor": stripe_fee,
                "connect_mode": r.get("stripe_connect_mode") or "platform_only",
                "connected_account": r.get("stripe_connect_account_id") or "—",
                "stripe_session_id": r.get("stripe_session_id") or "—",
                "stripe_payment_intent_id": r.get("stripe_payment_intent_id") or "—",
                "stripe_charge_id": r.get("stripe_charge_id") or "—",
                "stripe_transfer_id": r.get("stripe_transfer_id") or "—",
                "item_summary": f"{int(r.get('item_count') or 0)} items · {int(r.get('assessment_count') or 0)} assessments · {int(r.get('trend_count') or 0)} trends",
                "bundle_url": f"/share/bundle/{r['token']}",
            }
        )

    return {"fee_model": fee_model, "items": items}
