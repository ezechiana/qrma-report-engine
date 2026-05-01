#app/services/audit_service.py
from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session

from app.db.models import AuditLog


def log_action(
    db: Session,
    action: str,
    user_id: Optional[UUID] = None,
    case_id: Optional[UUID] = None,
    report_version_id: Optional[UUID] = None,
    metadata_json: Optional[dict] = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        user_id=user_id,
        case_id=case_id,
        report_version_id=report_version_id,
        metadata_json=metadata_json,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
