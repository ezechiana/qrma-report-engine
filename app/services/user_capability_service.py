"""Capabilities on an existing login.

Practitioner and admin are capabilities. Granting one does not create a user,
and granting practitioner keeps the account's single personal record.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import User, UserCapability
from app.services.personal_record_service import ensure_personal_record

PRACTITIONER = "practitioner"
ADMIN = "admin"
ALLOWED = {PRACTITIONER, ADMIN}


def grant_capability(db: Session, user: User, capability: str, *, commit: bool = True) -> UserCapability:
    """Add a capability to this account. A second call keeps the same row and the same login."""
    cleaned = (capability or "").strip().lower()
    if cleaned not in ALLOWED:
        raise HTTPException(status_code=400, detail="Unknown capability.")

    row = (
        db.query(UserCapability)
        .filter(UserCapability.user_id == user.id, UserCapability.capability == cleaned)
        .one_or_none()
    )
    if row is None:
        row = UserCapability(user_id=user.id, capability=cleaned)
        db.add(row)
        if commit:
            db.commit()
            db.refresh(row)
        else:
            db.flush()

    if cleaned == PRACTITIONER:
        ensure_personal_record(db, user, commit=commit)
    return row
