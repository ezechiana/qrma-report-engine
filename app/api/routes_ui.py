from uuid import UUID

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Case, PractitionerSettings, ReportVersion, User


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _get_tenant_theme_for_report(report: ReportVersion) -> dict:
    report_json = report.report_json or {}
    stored_theme = report_json.get("tenant_theme", {}) if isinstance(report_json, dict) else {}
    settings = getattr(report, "_practitioner_settings", None)

    return {
        "tenant_name": stored_theme.get("tenant_name") or (settings.clinic_name if settings and settings.clinic_name else "Health Portal"),
        "tagline": stored_theme.get("tagline") or (settings.report_subtitle if settings and settings.report_subtitle else "Secure wellness report viewer"),
        "logo_url": stored_theme.get("logo_url") or (settings.logo_url if settings and settings.logo_url else ""),
        "cover_image_url": stored_theme.get("cover_image_url") or (settings.cover_image_url if settings and settings.cover_image_url else ""),
        "primary_color": stored_theme.get("primary_color") or (settings.accent_color if settings and settings.accent_color else "#2f4f2f"),
        "accent_color": stored_theme.get("accent_color") or (settings.accent_color if settings and settings.accent_color else "#d97706"),
        "text_color": stored_theme.get("text_color") or "#183028",
        "background_color": stored_theme.get("background_color") or "#f6f8f5",
        "surface_color": stored_theme.get("surface_color") or "#ffffff",
        "surface_soft_color": stored_theme.get("surface_soft_color") or "#f9fbf8",
        "border_color": stored_theme.get("border_color") or "#dfe7e1",
        "muted_color": stored_theme.get("muted_color") or "#5f7269",
        "support_email": stored_theme.get("support_email") or (settings.support_email if settings and settings.support_email else ""),
        "website_url": stored_theme.get("website_url") or (settings.website_url if settings and settings.website_url else ""),
    }


def _get_viewer_payload_for_report(report: ReportVersion) -> dict:
    report_json = report.report_json or {}
    viewer = dict(report_json.get("viewer") or {})
    tenant = dict(viewer.get("tenant") or {})

    settings = getattr(report, "_practitioner_settings", None)
    if settings:
        if settings.report_title:
            tenant["report_title"] = settings.report_title
        if settings.report_subtitle:
            tenant["subtitle"] = settings.report_subtitle

    viewer["tenant"] = tenant

    return {
        "id": str(report.id),
        "case_id": str(report.case_id),
        "version_number": report.version_number,
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "recommendation_mode": report.recommendation_mode.value if hasattr(report.recommendation_mode, "value") else str(report.recommendation_mode),
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "pdf_url": f"/api/reports/{report.id}/pdf",
        "html_url": f"/api/reports/{report.id}/html",
        "data_url": f"/api/reports/{report.id}/json",
        "viewer": viewer,
    }


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "title": "Login"})


@router.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="register.html", context={"request": request, "title": "Register"})


@router.get("/logout")
def logout():
    return RedirectResponse("/login", status_code=302)


@router.get("/app")
def dashboard_page(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"request": request, "title": "Dashboard"})


@router.get("/app/reports")
def reports_page(request: Request):
    return templates.TemplateResponse(request=request, name="reports.html", context={"request": request, "title": "Reports"})


@router.get("/app/cases/new")
def new_case_page(request: Request):
    return templates.TemplateResponse(request=request, name="cases_new.html", context={"request": request, "title": "New Case"})


@router.get("/app/cases/{case_id}")
def case_detail_page(case_id: str, request: Request):
    return templates.TemplateResponse(request=request, name="case_detail.html", context={"request": request, "title": "Case Detail", "case_id": case_id})


@router.get("/app/patients")
def patients_page(request: Request):
    return templates.TemplateResponse(request=request, name="patients.html", context={"request": request, "title": "Patients"})


@router.get("/app/patients/{patient_id}")
def patient_detail_page(patient_id: str, request: Request):
    return templates.TemplateResponse(request=request, name="patient_detail.html", context={"request": request, "title": "Patient Detail", "patient_id": patient_id})


@router.get("/app/cases")
def cases_page(request: Request):
    return templates.TemplateResponse(request=request, name="cases.html", context={"request": request, "title": "Cases"})


@router.get("/app/reports/{report_id}/view")
def report_view_router(
    report_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ReportVersion.id == UUID(report_id), Case.user_id == current_user.id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    report_type = getattr(report, "report_type", None) or (report.report_json or {}).get("report_type") or "assessment"
    if report_type == "trend":
        return RedirectResponse(f"/trend-reports/{report_id}", status_code=302)

    settings = db.query(PractitionerSettings).filter(PractitionerSettings.user_id == current_user.id).first()
    report._practitioner_settings = settings

    payload = _get_viewer_payload_for_report(report)
    tenant = _get_tenant_theme_for_report(report)

    return templates.TemplateResponse(
        request=request,
        name="report_full.html",
        context={
            "request": request,
            "title": "",
            "report_id": report_id,
            "report": payload,
            "tenant": tenant,
            "viewer_mode": "practitioner",
        },
    )


@router.get("/app/reports/{report_id}")
def report_detail_page(report_id: str, request: Request):
    return templates.TemplateResponse(request=request, name="report_detail.html", context={"request": request, "title": "Report Detail", "report_id": report_id})


@router.get("/trend-reports/{trend_report_id}", response_class=HTMLResponse)
def trend_report_view(request: Request, trend_report_id: str):
    return templates.TemplateResponse(
        request=request,
        name="trend_report_view.html",
        context={
            "request": request,
            "title": "Trend Report",
            "trend_report_id": trend_report_id,
        },
    )


@router.get("/app/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(request=request, name="settings.html", context={"request": request, "title": "Settings"})


@router.get("/app/billing")
def billing_page(request: Request):
    return templates.TemplateResponse(request=request, name="billing.html", context={"request": request, "title": "Billing"})


@router.get("/share-dashboard", response_class=HTMLResponse)
def share_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="share_dashboard.html", context={"request": request})
