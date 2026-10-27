"""Atomic adapter around existing patient/case/report business logic."""
from pathlib import Path
import uuid
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import User, Case, ReportVersion
from app.services import connector_service as service
from app.services import connector_audit as audit


class ConfirmationSession(Session):
    """Existing services flush here; only the connector boundary commits."""
    def commit(self):
        self.flush()


async def confirm(bind, owner_id, assessment_id, payload, observation=None):
    from app.api.routes_cases import create_from_import
    from app.services.scan_import_service import save_temp_html, TEMP_UPLOAD_DIR
    from app.services.report_service import generate_report_version
    # Serialise this account's ingest/confirmation operations, including duplicates.
    with ConfirmationSession(bind=bind, expire_on_commit=False) as tx:
        user = tx.scalar(select(User).where(User.id == owner_id).with_for_update())
        if not user or not user.is_active:
            raise HTTPException(401)
        row = service.owned(tx, owner_id, assessment_id, lock=True)
        if not row.case_id:
            duplicate = service.duplicate_match(tx, owner_id, row.raw_sha256, row.fingerprint, exclude=row.id)
            if duplicate and not payload.allow_duplicate:
                raise HTTPException(409, duplicate)
            # Original server-owned bytes, never a browser-supplied temporary ID.
            temp_id, _ = save_temp_html(row.source_html)
            try:
                request = payload.model_copy(update={'temporary_upload_id':temp_id, 'generate_report':False})
                response = await create_from_import(request, tx, user)
            finally:
                (TEMP_UPLOAD_DIR / (temp_id + '.html')).unlink(missing_ok=True)
            row.patient_id, row.case_id = uuid.UUID(response['patient_id']), uuid.UUID(response['case_id'])
            row.state = 'case_created'
            audit.assessment_event(tx, 'case_linked', row, actor_type='user', observation=observation)
            if duplicate and payload.allow_duplicate:
                audit.assessment_event(tx, 'duplicate_override', row, actor_type='user', observation=observation)
            Session.commit(tx)
        if payload.generate_report and not row.report_id:
            # A second transaction locks the durable case association. A crash
            # during generation rolls back its report version rather than
            # creating another version on retry.
            tx.scalar(select(User).where(User.id == owner_id).with_for_update())
            row = service.owned(tx, owner_id, assessment_id, lock=True)
            if not row.report_id:
                case = tx.get(Case, row.case_id)
                try:
                    report = await generate_report_version(tx, case, user, job_id=str(row.job_id))
                    row.report_id, row.state = report.id, 'completed'
                except Exception:
                    # Existing builder records a failed version. Preserve its ID
                    # so retries of confirmation never create another version.
                    report = tx.scalar(select(ReportVersion).where(ReportVersion.case_id == row.case_id,
                                       ReportVersion.job_id == str(row.job_id)).order_by(ReportVersion.version_number.desc()))
                    if report:
                        row.report_id = report.id
                    row.state = 'needs_attention'
                action = 'report_failed' if row.state == 'needs_attention' else 'report_linked'
                audit.assessment_event(tx, action, row, actor_type='user', observation=observation)
                Session.commit(tx)
        value = service.result(row)
        value['auto_generated'] = row.state == 'completed'
        if row.state == 'needs_attention' and row.case_id:
            value['warning'] = 'Case created. Open the case workspace to review report processing.'
        return value
