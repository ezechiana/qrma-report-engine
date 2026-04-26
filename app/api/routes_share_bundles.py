from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.db.models import Case, ReportVersion, ShareBundle, ShareBundleItem, User
from app.utils.security import generate_token, hash_password

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["share-bundles"])
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")


def _safe_password_for_bcrypt(password: str) -> str:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) <= 72:
        return password
    return password_bytes[:72].decode("utf-8", errors="ignore")


class ShareBundleCreate(BaseModel):
    report_version_ids: list[UUID] = Field(min_length=1)
    title: Optional[str] = None
    access_label: Optional[str] = None
    price_amount: Optional[int] = None
    price_currency: str = "gbp"
    expires_days: Optional[int] = 7
    password: Optional[str] = None


def _owned_reports(db: Session, current_user: User, ids: list[UUID]) -> list[ReportVersion]:
    reports = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ReportVersion.id.in_(ids), Case.user_id == current_user.id)
        .order_by(ReportVersion.generated_at.asc())
        .all()
    )
    if len(reports) != len(set(ids)):
        raise HTTPException(status_code=404, detail="One or more reports were not found.")
    return reports


def _bundle_url(token: str) -> str:
    return f"{BASE_URL.rstrip('/')}/share/bundle/{token}"


@router.post("/api/share-bundles")
def create_share_bundle(payload: ShareBundleCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reports = _owned_reports(db, current_user, payload.report_version_ids)
    patient_ids = {str(r.case.patient_id) for r in reports if getattr(r, "case", None)}
    patient_id = reports[0].case.patient_id if len(patient_ids) == 1 else None

    expires_at = datetime.now(timezone.utc) + timedelta(days=int(payload.expires_days or 7)) if payload.expires_days else None
    requires_payment = bool(payload.price_amount and payload.price_amount > 0)
    password_hash = hash_password(_safe_password_for_bcrypt(payload.password)) if payload.password else None

    title = payload.title
    if not title:
        if len(reports) == 1:
            title = "Trend report" if (getattr(reports[0], "report_type", "assessment") == "trend") else "Assessment report"
        else:
            title = f"Report bundle ({len(reports)} reports)"

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
        db.add(ShareBundleItem(share_bundle_id=bundle.id, report_version_id=report.id, position=idx))

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
def revoke_share_bundle(bundle_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    bundle = db.query(ShareBundle).filter(ShareBundle.id == bundle_id, ShareBundle.created_by_user_id == current_user.id).first()
    if not bundle:
        raise HTTPException(status_code=404, detail="Share bundle not found.")
    bundle.is_active = False
    db.commit()
    return {"success": True}


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


@router.get("/share/bundle/{token}", response_class=HTMLResponse)
def view_share_bundle(token: str, request: Request, db: Session = Depends(get_db)):
    bundle = (
        db.query(ShareBundle)
        .options(joinedload(ShareBundle.items).joinedload(ShareBundleItem.report_version).joinedload(ReportVersion.case))
        .filter(ShareBundle.token == token)
        .first()
    )
    if not bundle or not _bundle_is_valid(bundle):
        raise HTTPException(status_code=404, detail="Link not found or expired.")

    if bundle.requires_payment and bundle.payment_status != "paid":
        return templates.TemplateResponse("share_bundle_paywall.html", {"request": request, "bundle": bundle})

    reports = []
    for item in bundle.items:
        report = item.report_version
        if not report:
            continue
        reports.append({
            "id": str(report.id),
            "title": getattr(report.case, "title", None) or ("Trend report" if getattr(report, "report_type", "assessment") == "trend" else "Assessment report"),
            "report_type": getattr(report, "report_type", "assessment") or "assessment",
            "generated_at": report.generated_at,
            "viewer_url": f"/share/bundle/{token}/reports/{report.id}",
        })

    return templates.TemplateResponse("share_bundle_view_v2.html", {"request": request, "bundle": bundle, "reports": reports})


@router.get("/share/bundle/{token}/reports/{report_id}", response_class=HTMLResponse)
def view_bundle_report(token: str, report_id: UUID, request: Request, db: Session = Depends(get_db)):
    bundle = db.query(ShareBundle).options(joinedload(ShareBundle.items)).filter(ShareBundle.token == token).first()
    if not bundle or not _bundle_is_valid(bundle):
        raise HTTPException(status_code=404, detail="Link not found or expired.")
    if bundle.requires_payment and bundle.payment_status != "paid":
        return RedirectResponse(f"/share/bundle/{token}")

    allowed = {item.report_version_id for item in bundle.items}
    if report_id not in allowed:
        raise HTTPException(status_code=404, detail="Report not included in this bundle.")

    report = db.query(ReportVersion).filter(ReportVersion.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    if getattr(report, "report_type", "assessment") == "trend":
        return templates.TemplateResponse("stored_trend_report_view_v2.html", {"request": request, "report": report, "trend": report.trend_payload_json or {}})

    return templates.TemplateResponse("report_viewer.html", {"request": request, "report_id": str(report.id), "viewer_payload": (report.report_json or {}).get("viewer", {})})
