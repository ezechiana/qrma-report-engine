# app/api/routes_share.py

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Case, ReportVersion, ShareLink, User
from app.schemas.share import ShareLinkCreate, ShareLinkRead
from app.services.audit_service import log_action
from app.services.share_link_service import (
    create_share_link,
    is_share_link_valid,
    validate_share_link_password,
)

router = APIRouter(tags=["share"])
templates = Jinja2Templates(directory="app/templates")

SHARE_ACCESS_COOKIE_NAME = "qrma_share_access"
SHARE_ACCESS_COOKIE_MAX_AGE = int(os.getenv("SHARE_ACCESS_COOKIE_MAX_AGE", "43200"))  # 12 hours
SHARE_COOKIE_SECURE = os.getenv("SHARE_COOKIE_SECURE", "false").lower() == "true"
SHARE_COOKIE_SAMESITE = os.getenv("SHARE_COOKIE_SAMESITE", "lax")
SHARE_COOKIE_SECRET = os.getenv("SHARE_COOKIE_SECRET", os.getenv("OPENAI_API_KEY", "dev-share-secret"))


def _get_share_link_or_404(db: Session, token: str) -> ShareLink:
    link = db.query(ShareLink).filter(ShareLink.token == token).first()
    if not link or not is_share_link_valid(link):
        raise HTTPException(status_code=404, detail="Link not found or expired")
    return link


def _get_report_for_share_or_404(db: Session, link: ShareLink) -> ReportVersion:
    report = db.query(ReportVersion).filter(
        ReportVersion.id == link.report_version_id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


def _tenant_theme_from_report(report: ReportVersion) -> dict:
    """
    Temporary multi-tenant theme source.
    For now this is driven by environment variables with sensible fallbacks.
    Later this should come from a tenant/clinic branding table.
    """
    report_json = report.report_json or {}
    stored_theme = report_json.get("tenant_theme", {}) if isinstance(report_json, dict) else {}

    return {
        "tenant_name": stored_theme.get("tenant_name") or os.getenv("VIEWER_TENANT_NAME", "Health Portal"),
        "tagline": stored_theme.get("tagline") or os.getenv(
            "VIEWER_TAGLINE",
            "Secure wellness report viewer"
        ),
        "logo_url": stored_theme.get("logo_url") or os.getenv("VIEWER_LOGO_URL", ""),
        "primary_color": stored_theme.get("primary_color") or os.getenv("VIEWER_PRIMARY_COLOR", "#2f4f2f"),
        "accent_color": stored_theme.get("accent_color") or os.getenv("VIEWER_ACCENT_COLOR", "#d97706"),
        "text_color": stored_theme.get("text_color") or os.getenv("VIEWER_TEXT_COLOR", "#183028"),
        "background_color": stored_theme.get("background_color") or os.getenv("VIEWER_BACKGROUND_COLOR", "#f6f8f5"),
        "surface_color": stored_theme.get("surface_color") or os.getenv("VIEWER_SURFACE_COLOR", "#ffffff"),
        "surface_soft_color": stored_theme.get("surface_soft_color") or os.getenv("VIEWER_SURFACE_SOFT_COLOR", "#f9fbf8"),
        "border_color": stored_theme.get("border_color") or os.getenv("VIEWER_BORDER_COLOR", "#dfe7e1"),
        "muted_color": stored_theme.get("muted_color") or os.getenv("VIEWER_MUTED_COLOR", "#5f7269"),
        "support_email": stored_theme.get("support_email") or os.getenv("VIEWER_SUPPORT_EMAIL", ""),
        "website_url": stored_theme.get("website_url") or os.getenv("VIEWER_WEBSITE_URL", ""),
    }


def _public_report_payload(report: ReportVersion, token: str) -> dict:
    report_json = report.report_json or {}
    viewer = report_json.get("viewer") or {}

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
    link = _get_share_link_or_404(db, token)
    report = _get_report_for_share_or_404(db, link)

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

    payload = _public_report_payload(report, token)
    tenant = _tenant_theme_from_report(report)

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
    link = _get_share_link_or_404(db, token)

    if not link.password_hash:
        report = _get_report_for_share_or_404(db, link)
        payload = _public_report_payload(report, token)
        tenant = _tenant_theme_from_report(report)
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

    payload = _public_report_payload(report, token)
    tenant = _tenant_theme_from_report(report)

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
    link = _get_share_link_or_404(db, token)
    _enforce_password_gate(request, link)

    report = _get_report_for_share_or_404(db, link)
    payload = _public_report_payload(report, token)
    tenant = _tenant_theme_from_report(report)

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
    link = _get_share_link_or_404(db, token)
    _enforce_password_gate(request, link)

    report = _get_report_for_share_or_404(db, link)

    if not report.pdf_path:
        raise HTTPException(status_code=404, detail="PDF not found")

    log_action(
        db,
        "share_link_pdf_downloaded",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )

    return FileResponse(
        path=report.pdf_path,
        media_type="application/pdf",
        filename=f"report_{report.id}.pdf",
    )


@router.get("/share/{token}/html")
def get_shared_report_html(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link = _get_share_link_or_404(db, token)
    _enforce_password_gate(request, link)

    report = _get_report_for_share_or_404(db, link)

    if not report.html_path:
        raise HTTPException(status_code=404, detail="HTML not found")

    log_action(
        db,
        "share_link_html_downloaded",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )

    return FileResponse(
        path=report.html_path,
        media_type="text/html",
        filename=f"report_{report.id}.html",
    )