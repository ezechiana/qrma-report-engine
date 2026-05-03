from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

ZERO_DECIMAL_CURRENCIES = {"jpy", "krw"}
DEFAULT_BASE = "USD"
DEFAULT_RATES = {
    "USD": 1.0,
    "GBP": 0.79,
    "EUR": 0.92,
    "AED": 3.6725,
    "JPY": 155.0,
    "KRW": 1350.0,
}
DISPLAY_TZ = ZoneInfo(os.getenv("PLATFORM_DISPLAY_TIMEZONE", "Europe/London"))


@dataclass
class FxSnapshot:
    effective_month: date
    base_currency: str
    rates: dict[str, float]
    source: str = "fallback_monthly_snapshot"


def _currency(value: str | None, fallback: str = "USD") -> str:
    return (value or fallback).strip().upper()


def _factor(currency: str | None) -> int:
    return 1 if _currency(currency).lower() in ZERO_DECIMAL_CURRENCIES else 100


def _to_display_time(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TZ)


def month_start(value: datetime | None = None) -> date:
    value = _to_display_time(value)
    return date(value.year, value.month, 1)


def add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, 28)
    return date(year, month, day)


def _fx_config(db: Session | None = None) -> dict[str, Any]:
    if db is None:
        return {}
    try:
        from app.services.platform_settings_service import get_platform_settings
        return get_platform_settings(db).get("fx", {})
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {}


def _load_fallback_rates(db: Session | None = None) -> dict[str, float]:
    raw = _fx_config(db).get("default_rates_json") or os.getenv("FX_DEFAULT_RATES_JSON")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                rates = {str(k).upper(): float(v) for k, v in parsed.items() if v}
                rates.setdefault("USD", 1.0)
                return rates
        except Exception:
            pass
    return dict(DEFAULT_RATES)


def _fetch_rates_from_provider(db: Session | None = None) -> tuple[dict[str, float], str]:
    config = _fx_config(db)
    url = config.get("provider_url") or os.getenv("FX_PROVIDER_URL", "https://open.er-api.com/v6/latest/USD")
    timeout = float(config.get("provider_timeout_seconds") or os.getenv("FX_PROVIDER_TIMEOUT_SECONDS", "6"))
    with urllib.request.urlopen(url, timeout=timeout) as response:  # nosec B310 - configurable public FX source
        payload = json.loads(response.read().decode("utf-8"))
    rates = payload.get("rates") or payload.get("conversion_rates") or {}
    if not isinstance(rates, dict) or not rates:
        raise RuntimeError("FX provider did not return rates.")
    normalised = {str(k).upper(): float(v) for k, v in rates.items() if v}
    normalised.setdefault("USD", 1.0)
    return normalised, url


def _snapshot_from_row(row) -> FxSnapshot:
    rates = row["rates"] or {}
    if isinstance(rates, str):
        rates = json.loads(rates)
    return FxSnapshot(
        effective_month=row["effective_month"],
        base_currency=_currency(row["base_currency"], DEFAULT_BASE),
        rates={str(k).upper(): float(v) for k, v in dict(rates).items()},
        source=row.get("source") or "database_monthly_snapshot",
    )


def _latest_snapshot(db: Session) -> FxSnapshot | None:
    try:
        row = db.execute(
            text("""
                SELECT effective_month, base_currency, rates, source
                FROM fx_rates_monthly
                ORDER BY effective_month DESC
                LIMIT 1
            """)
        ).mappings().first()
    except Exception:
        db.rollback()
        return None
    return _snapshot_from_row(row) if row else None


def ensure_current_month_fx_snapshot(db: Session, now: datetime | None = None) -> FxSnapshot:
    effective_month = month_start(now)

    try:
        row = db.execute(
            text("""
                SELECT effective_month, base_currency, rates, source
                FROM fx_rates_monthly
                WHERE effective_month = :effective_month
                LIMIT 1
            """),
            {"effective_month": effective_month},
        ).mappings().first()
    except Exception:
        db.rollback()
        previous = _latest_snapshot(db)
        if previous:
            return previous
        return FxSnapshot(effective_month=effective_month, base_currency=DEFAULT_BASE, rates=_load_fallback_rates(db))

    if row:
        return _snapshot_from_row(row)

    try:
        rates, source = _fetch_rates_from_provider(db)
    except Exception:
        previous = _latest_snapshot(db)
        if previous:
            return previous
        rates, source = _load_fallback_rates(db), "fallback_default_rates"

    try:
        db.execute(
            text("""
                INSERT INTO fx_rates_monthly (effective_month, base_currency, rates, source, created_at)
                VALUES (:effective_month, 'USD', CAST(:rates AS JSONB), :source, NOW())
                ON CONFLICT (effective_month)
                DO UPDATE SET rates = EXCLUDED.rates, source = EXCLUDED.source, created_at = NOW()
            """),
            {
                "effective_month": effective_month,
                "rates": json.dumps(rates),
                "source": source,
            },
        )
        db.commit()
    except Exception:
        db.rollback()

    return FxSnapshot(effective_month=effective_month, base_currency=DEFAULT_BASE, rates=rates, source=source)


def refresh_current_month_fx_snapshot(db: Session, now: datetime | None = None) -> FxSnapshot:
    effective_month = month_start(now)
    try:
        rates, source = _fetch_rates_from_provider(db)
    except Exception:
        previous = _latest_snapshot(db)
        if previous:
            rates, source = previous.rates, f"manual_refresh_previous:{previous.source}"
        else:
            rates, source = _load_fallback_rates(db), "manual_refresh_fallback_default_rates"

    try:
        db.execute(
            text("""
                INSERT INTO fx_rates_monthly (effective_month, base_currency, rates, source, created_at)
                VALUES (:effective_month, 'USD', CAST(:rates AS JSONB), :source, NOW())
                ON CONFLICT (effective_month)
                DO UPDATE SET rates = EXCLUDED.rates, source = EXCLUDED.source, created_at = NOW()
            """),
            {
                "effective_month": effective_month,
                "rates": json.dumps(rates),
                "source": source,
            },
        )
        db.commit()
    except Exception:
        db.rollback()

    return FxSnapshot(effective_month=effective_month, base_currency=DEFAULT_BASE, rates=rates, source=source)


def convert_minor(amount_minor: int | float | None, from_currency: str | None, to_currency: str | None, snapshot: FxSnapshot) -> int:
    amount_minor = int(amount_minor or 0)
    source = _currency(from_currency)
    target = _currency(to_currency)
    if amount_minor == 0 or source == target:
        return amount_minor

    rates = {str(k).upper(): float(v) for k, v in (snapshot.rates or {}).items()}
    source_rate = rates.get(source)
    target_rate = rates.get(target)
    if not source_rate or not target_rate:
        return 0

    source_major = amount_minor / _factor(source)
    usd_major = source_major / source_rate
    target_major = usd_major * target_rate
    return int(round(target_major * _factor(target)))


def _as_aware(dt: datetime | None) -> datetime | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _display_dt(dt: datetime) -> datetime:
    return _to_display_time(dt)


def _start_of_week(dt: datetime) -> datetime:
    local = _display_dt(dt)
    base = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return base - timedelta(days=base.weekday())


def _start_of_month(dt: datetime) -> datetime:
    local = _display_dt(dt)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _month_label(dt: datetime) -> str:
    return _display_dt(dt).strftime("%B")


def _range_label(start: datetime, end: datetime, *, end_exclusive: bool = False) -> str:
    display_end = _display_dt(end)
    if end_exclusive:
        display_end = display_end - timedelta(days=1)
    return f"{_display_dt(start).strftime('%d %b')} → {display_end.strftime('%d %b')}"


def _period_contains(period: dict[str, Any], paid_at: datetime) -> bool:
    paid_at = _display_dt(paid_at)
    start = _display_dt(period["start"])
    end = _display_dt(period["end"])
    return start <= paid_at < end


def build_revenue_goal_payload(
    *,
    payments: list[dict[str, Any]],
    preferred_currency: str,
    monthly_goal_minor: int,
    snapshot: FxSnapshot,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = _to_display_time(now)
    preferred_currency = _currency(preferred_currency, "USD")
    monthly_goal_minor = int(monthly_goal_minor or 200000)

    week_start = _start_of_week(now)
    last_week_start = week_start - timedelta(days=7)
    month_start_dt = _start_of_month(now)
    last_month_end = month_start_dt
    last_month_start = _start_of_month(month_start_dt - timedelta(days=1))
    three_month_start = now - timedelta(days=90)

    # Completed periods use half-open backend ranges [start, end), while their
    # labels show the final included calendar day. Current periods end at now.
    periods = [
        {"key": "this_week", "label": "This week", "short_label": "This week", "start": week_start, "end": now, "end_exclusive_label": False},
        {"key": "last_week", "label": "Last week", "short_label": "Last week", "start": last_week_start, "end": week_start, "end_exclusive_label": True},
        {"key": "this_month", "label": _month_label(now), "short_label": "This month", "start": month_start_dt, "end": now, "end_exclusive_label": False},
        {"key": "last_month", "label": "Last month", "short_label": "Last month", "start": last_month_start, "end": last_month_end, "end_exclusive_label": True},
        {"key": "last_3_months", "label": "Last 3 months", "short_label": "3 months", "start": three_month_start, "end": now, "end_exclusive_label": False},
    ]

    paid = []
    for p in payments:
        status = str(p.get("payment_status") or p.get("status") or "").lower()
        if status != "paid":
            continue
        paid_at = _as_aware(p.get("paid_at") or p.get("created_at"))
        if not paid_at:
            continue
        paid.append({**p, "_paid_at": paid_at})

    output_periods = []
    for period in periods:
        total = 0
        for payment in paid:
            if _period_contains(period, payment["_paid_at"]):
                total += convert_minor(
                    payment.get("price_amount") or payment.get("gross_minor") or 0,
                    payment.get("price_currency") or payment.get("currency"),
                    preferred_currency,
                    snapshot,
                )
        display_end = _display_dt(period["end"])
        if period.get("end_exclusive_label"):
            display_end = display_end - timedelta(days=1)
        output_periods.append({
            "key": period["key"],
            "label": period["label"],
            "short_label": period["short_label"],
            "value_minor": int(total),
            "range_label": _range_label(period["start"], period["end"], end_exclusive=bool(period.get("end_exclusive_label"))),
            "start_date": _display_dt(period["start"]).date().isoformat(),
            "end_date": display_end.date().isoformat(),
        })

    return {
        "currency": preferred_currency,
        "monthly_goal_minor": monthly_goal_minor,
        "month_label": _month_label(now),
        "periods": output_periods,
        "fx_effective_month": snapshot.effective_month.isoformat(),
        "fx_base_currency": snapshot.base_currency,
        "fx_source": snapshot.source,
        "fx_rates": {k: snapshot.rates[k] for k in sorted(snapshot.rates) if k in {"USD", "GBP", "EUR", "AED", "JPY", "KRW", preferred_currency}},
        "period_note": "Periods use Europe/London calendar boundaries. Completed periods display their inclusive final day.",
        "note": "Converted for performance tracking only. Actual earnings remain in original transaction currencies.",
    }
