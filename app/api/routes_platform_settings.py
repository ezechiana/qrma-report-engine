from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_platform_admin
from app.db.models import User
from app.services.fx_service import ensure_current_month_fx_snapshot, refresh_current_month_fx_snapshot
from app.services.platform_settings_service import (
    get_platform_settings,
    get_feature_flags,
    is_platform_admin_user,
    save_platform_settings,
)

router = APIRouter(tags=["platform-settings"])
templates = Jinja2Templates(directory="app/templates")



def _latest_fx_row(db: Session) -> dict[str, Any] | None:
    try:
        row = db.execute(
            text(
                """
                SELECT effective_month, base_currency, rates, source, created_at
                FROM fx_rates_monthly
                ORDER BY effective_month DESC
                LIMIT 1
                """
            )
        ).mappings().first()
    except Exception:
        db.rollback()
        return None
    if not row:
        return None
    rates = row["rates"] or {}
    return {
        "effective_month": row["effective_month"].isoformat() if row.get("effective_month") else None,
        "base_currency": row.get("base_currency") or "USD",
        "source": row.get("source") or "—",
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "rates": {k: rates[k] for k in sorted(rates) if k in {"USD", "GBP", "EUR", "AED", "JPY", "KRW"}},
    }


@router.get("/app/platform-settings", response_class=HTMLResponse)
def platform_settings_page(request: Request, current_user: User = Depends(require_platform_admin)):
    return templates.TemplateResponse(
        request=request,
        name="platform_settings.html",
        context={"request": request, "title": "Platform Settings", "user": current_user},
    )


@router.get("/api/platform-settings/me")
def platform_settings_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = str(getattr(current_user, "role", None) or "practitioner").lower()
    return {
        "is_admin": is_platform_admin_user(db, current_user),
        "role": role,
        "features": get_feature_flags(db),
    }


@router.get("/api/platform-settings")
def read_platform_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_platform_admin),
):
    settings = get_platform_settings(db)
    settings["fx"]["latest_snapshot"] = _latest_fx_row(db)
    settings["meta"] = {
        "admin_email": current_user.email,
        "note": "Environment variables remain the boot-time fallback. Values saved here are stored in app_settings.",
    }
    return settings


@router.put("/api/platform-settings")
def update_platform_settings(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_platform_admin),
):
    settings = save_platform_settings(db, payload)
    settings["fx"]["latest_snapshot"] = _latest_fx_row(db)
    return settings


@router.post("/api/platform-settings/fx/ensure-current")
def ensure_fx_snapshot(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_platform_admin),
):
    snapshot = ensure_current_month_fx_snapshot(db)
    return {
        "effective_month": snapshot.effective_month.isoformat(),
        "base_currency": snapshot.base_currency,
        "source": snapshot.source,
        "rates": {k: snapshot.rates[k] for k in sorted(snapshot.rates) if k in {"USD", "GBP", "EUR", "AED", "JPY", "KRW"}},
    }


@router.post("/api/platform-settings/fx/refresh-current")
def refresh_fx_snapshot(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_platform_admin),
):
    snapshot = refresh_current_month_fx_snapshot(db)
    return {
        "effective_month": snapshot.effective_month.isoformat(),
        "base_currency": snapshot.base_currency,
        "source": snapshot.source,
        "rates": {k: snapshot.rates[k] for k in sorted(snapshot.rates) if k in {"USD", "GBP", "EUR", "AED", "JPY", "KRW"}},
    }
