#app/api/routes_reports.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Case, ReportOverride, ReportVersion, User
from app.schemas.reports import ReportOverrideUpdate, ReportVersionRead
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/{report_version_id}", response_model=ReportVersionRead)
def get_report_metadata(
    report_version_id: UUID,
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
    return report


@router.get("/{report_version_id}/json")
def get_report_json(
    report_version_id: UUID,
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
    return JSONResponse(report.report_json)


@router.get("/{report_version_id}/pdf")
def get_report_pdf(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ReportVersion.id == report_version_id, Case.user_id == current_user.id)
        .first()
    )
    if not report or not report.pdf_path:
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(report.pdf_path, media_type="application/pdf", filename=f"report_{report.id}.pdf")

@router.get("/{report_version_id}/html")
def get_report_html(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ReportVersion.id == report_version_id, Case.user_id == current_user.id)
        .first()
    )
    if not report or not report.html_path:
        raise HTTPException(status_code=404, detail="HTML not found")

    return FileResponse(
        path=report.html_path,
        media_type="text/html",
        filename=f"report_{report.id}.html"
    )


@router.patch("/{report_version_id}/overrides")
def update_report_overrides(
    report_version_id: UUID,
    payload: ReportOverrideUpdate,
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
    report = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(ReportVersion.id == report_version_id, Case.user_id == current_user.id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    report.status = "final"
    report.case.status = "final"
    db.commit()

    log_action(
        db,
        "report_finalised",
        user_id=current_user.id,
        case_id=report.case_id,
        report_version_id=report.id,
    )

    return {"ok": True}