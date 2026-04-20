from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Case, ReportOverride, ReportStatus, ReportVersion, User, PractitionerSettings
from app.schemas.reports import ReportOverrideUpdate, ReportVersionRead
from app.services.audit_service import log_action
from app.services.storage_service import object_exists, generate_presigned_url


templates = Jinja2Templates(directory="app/templates")
router = APIRouter(prefix="/api/reports", tags=["reports"])


def _patient_display_name_from_case(case: Case | None) -> str | None:
    if not case:
        return None

    patient = getattr(case, "patient", None)
    if patient:
        full_name = getattr(patient, "full_name", None)
        if full_name:
            return full_name

        first_name = getattr(patient, "first_name", None)
        last_name = getattr(patient, "last_name", None)
        joined = " ".join(part for part in [first_name, last_name] if part)
        if joined:
            return joined

    source_data = case.source_patient_data_json or {}
    full_name = source_data.get("full_name")
    if full_name:
        return full_name

    first_name = source_data.get("first_name")
    last_name = source_data.get("last_name")
    joined = " ".join(part for part in [first_name, last_name] if part)
    return joined or None


def _case_display_name(case: Case | None) -> str | None:
    if not case:
        return None

    patient_name = _patient_display_name_from_case(case)
    if case.scan_datetime:
        date_text = case.scan_datetime.strftime("%d %b %Y")
    else:
        date_text = case.created_at.strftime("%d %b %Y") if case.created_at else None

    if patient_name and date_text:
        return f"{patient_name} — {date_text}"
    if patient_name:
        return patient_name
    if case.title:
        return case.title
    return f"Case {str(case.id)[:8]}"


def _serialise_report(report: ReportVersion) -> dict:
    case = getattr(report, "case", None)

    return {
        "id": report.id,
        "case_id": report.case_id,
        "version_number": report.version_number,
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "build_version": getattr(report, "build_version", None),
        "recommendation_mode": (
            report.recommendation_mode.value
            if hasattr(report.recommendation_mode, "value")
            else str(report.recommendation_mode)
        ),
        "generated_at": report.generated_at,
        "display_name": _case_display_name(case),
        "patient_display_name": _patient_display_name_from_case(case),
        "case_title": case.title if case else None,
        "scan_datetime": case.scan_datetime if case else None,
    }


def _get_tenant_theme_for_report(report: ReportVersion) -> dict:
    report_json = report.report_json or {}
    stored_theme = report_json.get("tenant_theme", {}) if isinstance(report_json, dict) else {}

    settings = None
    if getattr(report, "created_by_user_id", None):
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
        "recommendation_mode": (
            report.recommendation_mode.value
            if hasattr(report.recommendation_mode, "value")
            else str(report.recommendation_mode)
        ),
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "pdf_url": f"/api/reports/{report.id}/pdf",
        "html_url": f"/api/reports/{report.id}/html",
        "data_url": f"/api/reports/{report.id}/json",
        "viewer": viewer,
    }


def _get_owned_report(
    db: Session,
    current_user: User,
    report_version_id: UUID,
) -> ReportVersion | None:
    return (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            ReportVersion.id == report_version_id,
            Case.user_id == current_user.id,
        )
        .first()
    )


@router.get("", response_model=list[ReportVersionRead])
def list_reports(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reports = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(Case.user_id == current_user.id)
        .order_by(ReportVersion.generated_at.desc())
        .all()
    )
    return [_serialise_report(report) for report in reports]


@router.get("/case/{case_id}", response_model=list[ReportVersionRead])
def list_case_reports(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = (
        db.query(Case)
        .filter(Case.id == case_id, Case.user_id == current_user.id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    reports = (
        db.query(ReportVersion)
        .filter(ReportVersion.case_id == case.id)
        .order_by(ReportVersion.version_number.desc())
        .all()
    )
    return [_serialise_report(report) for report in reports]


@router.get("/{report_version_id}", response_model=ReportVersionRead)
def get_report_metadata(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_owned_report(db, current_user, report_version_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _serialise_report(report)


@router.get("/{report_version_id}/viewer", response_class=HTMLResponse)
def get_report_viewer(
    report_version_id: UUID,
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

    settings = (
        db.query(PractitionerSettings)
        .filter(PractitionerSettings.user_id == current_user.id)
        .first()
    )
    report._practitioner_settings = settings

    payload = _get_viewer_payload_for_report(report)
    tenant = _get_tenant_theme_for_report(report)

    return templates.TemplateResponse(
        request=request,
        name="report_viewer.html",
        context={
            "request": request,
            "report": payload,
            "tenant": tenant,
            "viewer_mode": "practitioner",
        },
    )


@router.get("/{report_version_id}/status")
def get_report_status(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_owned_report(db, current_user, report_version_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return {
        "report_version_id": str(report.id),
        "case_id": str(report.case_id),
        "display_name": _case_display_name(getattr(report, "case", None)),
        "version_number": report.version_number,
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "recommendation_mode": (
            report.recommendation_mode.value
            if hasattr(report.recommendation_mode, "value")
            else str(report.recommendation_mode)
        ),
        "started_at": getattr(report, "started_at", None),
        "completed_at": getattr(report, "completed_at", None),
        "failed_at": getattr(report, "failed_at", None),
        "error_message": getattr(report, "error_message", None),
        "generated_at": report.generated_at,
    }


@router.get("/{report_version_id}/json")
def get_report_json(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_owned_report(db, current_user, report_version_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.report_json is None:
        raise HTTPException(
            status_code=409,
            detail=f"Report is not ready yet. Current status: {report.status.value if hasattr(report.status, 'value') else report.status}",
        )

    return JSONResponse(report.report_json)


@router.get("/{report_version_id}/viewer")
def get_report_viewer_payload(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_owned_report(db, current_user, report_version_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.report_json is None:
        raise HTTPException(
            status_code=409,
            detail=f"Report is not ready yet. Current status: {report.status.value if hasattr(report.status, 'value') else report.status}",
        )

    report_json = report.report_json or {}

    return {
        "report_version_id": str(report.id),
        "case_id": str(report.case_id),
        "display_name": _case_display_name(getattr(report, "case", None)),
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "recommendation_mode": (
            report.recommendation_mode.value
            if hasattr(report.recommendation_mode, "value")
            else str(report.recommendation_mode)
        ),
        "viewer": report_json.get("viewer"),
        "html_path": report.html_path,
        "pdf_path": report.pdf_path,
    }


@router.get("/{report_version_id}/pdf")
def get_report_pdf(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_owned_report(db, current_user, report_version_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if not report.pdf_path:
        raise HTTPException(
            status_code=409,
            detail=f"PDF is not available yet. Current status: {report.status.value if hasattr(report.status, 'value') else report.status}",
        )

    if not object_exists(report.pdf_path):
        raise HTTPException(
            status_code=404,
            detail="PDF file not found. Please regenerate the report.",
        )

    signed_url = generate_presigned_url(report.pdf_path)
    return RedirectResponse(url=signed_url, status_code=302)


@router.get("/{report_version_id}/html")
def get_report_html(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_owned_report(db, current_user, report_version_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if not report.html_path:
        raise HTTPException(
            status_code=409,
            detail=f"HTML is not available yet. Current status: {report.status.value if hasattr(report.status, 'value') else report.status}",
        )

    if not object_exists(report.html_path):
        raise HTTPException(
            status_code=404,
            detail="HTML file not found. Please regenerate the report.",
        )

    signed_url = generate_presigned_url(report.html_path)
    return RedirectResponse(url=signed_url, status_code=302)


@router.patch("/{report_version_id}/overrides")
def update_report_overrides(
    report_version_id: UUID,
    payload: ReportOverrideUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_owned_report(db, current_user, report_version_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    overrides = report.overrides
    if not overrides:
        overrides = ReportOverride(report_version_id=report.id)
        db.add(overrides)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(overrides, field, value)

    db.commit()
    db.refresh(overrides)

    log_action(
        db,
        "report_overrides_updated",
        user_id=current_user.id,
        case_id=report.case_id,
        report_version_id=report.id,
    )

    return {"ok": True}


@router.post("/{report_version_id}/finalise")
def finalise_report(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_owned_report(db, current_user, report_version_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    current_status = report.status.value if hasattr(report.status, "value") else str(report.status)
    if current_status not in {"ready", "final"}:
        raise HTTPException(
            status_code=409,
            detail=f"Only ready reports can be finalised. Current status: {current_status}",
        )

    report.status = ReportStatus.final
    report.case.status = "final"
    db.commit()
    db.refresh(report)

    log_action(
        db,
        "report_finalised",
        user_id=current_user.id,
        case_id=report.case_id,
        report_version_id=report.id,
    )

    return {
        "ok": True,
        "report_version_id": str(report.id),
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
    }
