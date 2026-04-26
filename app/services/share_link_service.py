# app/services/share_link_service.py

from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from app.db.models import ReportVersion, ShareLink
from app.utils.security import generate_token, hash_password, verify_password
import uuid


def _safe_password_for_bcrypt(password: str) -> str:
    """
    bcrypt only supports up to 72 bytes.
    Truncate safely at the byte level and decode back to UTF-8.
    """
    password_bytes = password.encode("utf-8")
    if len(password_bytes) <= 72:
        return password
    return password_bytes[:72].decode("utf-8", errors="ignore")

def validate_share_link_password(link: ShareLink, password: str) -> bool:
    if not link.password_hash:
        return True
    return verify_password(_safe_password_for_bcrypt(password), link.password_hash)


def is_share_link_valid(link: ShareLink) -> bool:
    if not link.is_active:
        return False

    if link.expires_at:
        now = datetime.utcnow()
        expires = link.expires_at

        # Normalize timezone
        if expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)

        if expires < now:
            return False

    return True

def create_share_link(
    db: Session,
    report_version: ReportVersion | None = None,
    report_version_id=None,
    patient_id=None,
    share_type="report",
    price=None,
    expires_days=7,
    password=None,
):
    """
    Unified share link creator.

    Supports:
    - report share (existing flow)
    - trend share (new flow)
    - paywall (price)
    """

    token = generate_token(24)

    expires_at = None
    if expires_days:
        expires_at = datetime.utcnow() + timedelta(days=expires_days)

    # Normalize password
    password_hash = None
    if password:
        safe_pw = _safe_password_for_bcrypt(password)
        password_hash = hash_password(safe_pw)

    # Resolve report_version_id safely
    if report_version:
        report_version_id = report_version.id

    link = ShareLink(
        report_version_id=report_version_id,
        patient_id=patient_id,
        token=token,
        expires_at=expires_at,
        is_active=True,
        password_hash=password_hash,

        # NEW FIELDS
        share_type=share_type,
        price_pence=int(price * 100) if price else None,
    )

    db.add(link)
    db.commit()
    db.refresh(link)

    return link