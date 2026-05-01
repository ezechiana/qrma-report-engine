from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.db.models import Case, ReportVersion, ShareBundle, ShareBundleItem, ShareLink, User

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["share-dashboard"])
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _now():
    return datetime.now(timezone.utc)


def _dt_iso(dt):
    return dt.isoformat() if dt else None


def _fmt_dt(dt):
    return dt.strftime("%d/%m/%Y, %H:%M") if dt else "—"


def _is_expired(expires_at):
    if not expires_at:
        return False
    dt = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return dt < _now()


def _report_type(report):
    return "trend" if report and getattr(report, "report_type", "assessment") == "trend" else "assessment"


def _patient_name(report):
    if not report or not getattr(report, "case", None):
        return "—"
    patient = getattr(report.case, "patient", None)
    return getattr(patient, "full_name", None) or "—"


def _title(report):
    if not report:
        return "Untitled report"
    payload = report.report_json or report.trend_payload_json or {}
    if isinstance(payload, dict):
        name = payload.get("display_name") or payload.get("patient_display_name")
        if name:
            return str(name)
        viewer = payload.get("viewer") or {}
        if isinstance(viewer, dict) and viewer.get("case_title"):
            return str(viewer.get("case_title"))
    case = getattr(report, "case", None)
    patient = getattr(case, "patient", None) if case else None
    patient_name = getattr(patient, "full_name", None) or "Client"
    if _report_type(report) == "trend":
        return f"{patient_name} — Trend report"
    scan = getattr(case, "scan_datetime", None) if case else None
    return f"{patient_name} — {_fmt_dt(scan)}"


def _share_url(link):
    suffix = "/trend" if _report_type(getattr(link, "report_version", None)) == "trend" else ""
    return f"{BASE_URL}/share/{link.token}{suffix}"


def _bundle_url(bundle):
    return f"{BASE_URL}/share/bundle/{bundle.token}"


def _access_status(is_active, expires_at):
    if not is_active:
        return "revoked"
    if _is_expired(expires_at):
        return "expired"
    return "active"


def _price(amount, currency="gbp"):
    if not amount:
        return "Free"
    return f"{(currency or 'gbp').upper()} {int(amount) / 100:.2f}"


def _single_row(link):
    report = getattr(link, "report_version", None)
    rtype = _report_type(report)
    return {
        "kind": "single",
        "id": str(link.id),
        "type": "Trend share" if rtype == "trend" else "Assessment share",
        "type_key": rtype,
        "title": _title(report),
        "patient": _patient_name(report),
        "items": "1 item",
        "created_at": _dt_iso(link.created_at),
        "created_display": _fmt_dt(link.created_at),
        "expires_at": _dt_iso(link.expires_at),
        "expires_display": _fmt_dt(link.expires_at),
        "price": _price(getattr(link, "price_pence", None), "gbp"),
        "payment_status": "unpaid" if getattr(link, "price_pence", None) else "not_required",
        "access_status": _access_status(link.is_active, link.expires_at),
        "url": _share_url(link),
        "token": link.token,
    }


def _bundle_counts(bundle):
    total = len(bundle.items or [])
    trends = sum(1 for i in (bundle.items or []) if _report_type(getattr(i, "report_version", None)) == "trend")
    return total, total - trends, trends


def _bundle_patient(bundle):
    for item in bundle.items or []:
        name = _patient_name(getattr(item, "report_version", None))
        if name != "—":
            return name
    return "—"


def _bundle_row(bundle):
    total, assessments, trends = _bundle_counts(bundle)
    return {
        "kind": "bundle",
        "id": str(bundle.id),
        "type": "Bundle",
        "type_key": "bundle",
        "title": bundle.title or bundle.access_label or f"Report bundle ({total} items)",
        "patient": _bundle_patient(bundle),
        "items": f"{total} item{'s' if total != 1 else ''} · {assessments} assessment{'s' if assessments != 1 else ''} · {trends} trend{'s' if trends != 1 else ''}",
        "created_at": _dt_iso(bundle.created_at),
        "created_display": _fmt_dt(bundle.created_at),
        "expires_at": _dt_iso(bundle.expires_at),
        "expires_display": _fmt_dt(bundle.expires_at),
        "price": _price(bundle.price_amount, bundle.price_currency),
        "payment_status": bundle.payment_status or ("unpaid" if bundle.requires_payment else "not_required"),
        "access_status": _access_status(bundle.is_active, bundle.expires_at),
        "url": _bundle_url(bundle),
        "token": bundle.token,
    }


def _rows(db: Session, current_user: User):
    links = (
        db.query(ShareLink)
        .join(ReportVersion, ReportVersion.id == ShareLink.report_version_id)
        .join(Case, Case.id == ReportVersion.case_id)
        .options(joinedload(ShareLink.report_version).joinedload(ReportVersion.case).joinedload(Case.patient))
        .filter(Case.user_id == current_user.id)
        .order_by(ShareLink.created_at.desc())
        .all()
    )
    bundles = (
        db.query(ShareBundle)
        .options(
            joinedload(ShareBundle.items).joinedload(ShareBundleItem.report_version).joinedload(ReportVersion.case).joinedload(Case.patient),
            joinedload(ShareBundle.items).joinedload(ShareBundleItem.share_link),
        )
        .filter(ShareBundle.created_by_user_id == current_user.id)
        .order_by(ShareBundle.created_at.desc())
        .all()
    )
    out = [_single_row(l) for l in links if getattr(l, "share_type", None) != "bundle_item"]
    out += [_bundle_row(b) for b in bundles]
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


@router.get("/api/share-links")
def list_share_links(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    items = _rows(db, current_user)
    return {
        "items": items,
        "counts": {
            "all": len(items),
            "single": sum(1 for r in items if r["kind"] == "single"),
            "bundle": sum(1 for r in items if r["kind"] == "bundle"),
            "paid": sum(1 for r in items if r["payment_status"] == "paid"),
            "unpaid": sum(1 for r in items if r["payment_status"] == "unpaid"),
        },
    }


@router.get("/app/share-links", response_class=HTMLResponse)
def share_links_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="share_links.html",
        context={"request": request, "user": current_user, "items": _rows(db, current_user), "base_url": BASE_URL},
    )


@router.post("/api/share-links/{share_link_id}/revoke")
def revoke_share_link(share_link_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    link = (
        db.query(ShareLink)
        .join(ReportVersion, ReportVersion.id == ShareLink.report_version_id)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ShareLink.id == share_link_id, Case.user_id == current_user.id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found.")
    link.is_active = False
    db.commit()
    return {"success": True}


@router.post("/api/share-bundles/{bundle_id}/revoke")
def revoke_share_bundle_from_dashboard(bundle_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
        db.query(ShareLink).filter(ShareLink.id.in_(child_ids)).update({ShareLink.is_active: False}, synchronize_session=False)
    db.commit()
    return {"success": True}
