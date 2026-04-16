from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Case, ReportOverride, ReportStatus, ReportVersion, User
from app.schemas.reports import ReportOverrideUpdate, ReportVersionRead
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/reports", tags=["reports"])


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
    return (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(Case.user_id == current_user.id)
        .order_by(ReportVersion.generated_at.desc())
        .all()
    )


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

    return (
        db.query(ReportVersion)
        .filter(ReportVersion.case_id == case.id)
        .order_by(ReportVersion.version_number.desc())
        .all()
    )


@router.get("/{report_version_id}", response_model=ReportVersionRead)
def get_report_metadata(
    report_version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = _get_owned_report(db, current_user, report_version_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


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

    return FileResponse(
        report.pdf_path,
        media_type="application/pdf",
        filename=f"report_{report.id}.pdf",
    )


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

    return FileResponse(
        path=report.html_path,
        media_type="text/html",
        filename=f"report_{report.id}.html",
    )


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