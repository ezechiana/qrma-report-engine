from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.saas_v1 import ReportVersion, ReportVersionStatus
from app.services.report_service import generate_report  # <-- your existing function


def _utcnow():
    return datetime.now(timezone.utc)


def run_report_generation(db: Session, report: ReportVersion):
    """
    Orchestrates report generation using existing engine.
    """

    try:
        # mark processing
        report.status = ReportVersionStatus.processing
        report.started_at = _utcnow()
        db.commit()

        # 🔥 CALL YOUR EXISTING PIPELINE
        result = generate_report(
            case_id=report.case_id,
            recommendation_mode=report.recommendation_mode,
        )

        # result should contain:
        # - report_json
        # - html_path
        # - pdf_path

        report.report_json = result.get("report_json")
        report.html_path = result.get("html_path")
        report.pdf_path = result.get("pdf_path")

        report.status = ReportVersionStatus.ready
        report.completed_at = _utcnow()

        db.commit()

    except Exception as e:
        report.status = ReportVersionStatus.failed
        report.failed_at = _utcnow()
        report.error_message = str(e)

        db.commit()
        raise