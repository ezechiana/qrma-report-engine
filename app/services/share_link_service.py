# app/services/share_link_service.py

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.db.models import ReportVersion, ShareLink
from app.utils.security import generate_token, hash_password, verify_password



def _safe_password_for_bcrypt(password: str) -> str:
    """
    bcrypt only supports up to 72 bytes.
    Truncate safely at the byte level and decode back to UTF-8.
    """
    password_bytes = password.encode("utf-8")
    if len(password_bytes) <= 72:
        return password
    return password_bytes[:72].decode("utf-8", errors="ignore")


def create_share_link(
    db: Session,
    report_version: ReportVersion,
    password: str | None = None,
    expires_at: datetime | None = None,
) -> ShareLink:
    normalized_password = None
    if password:
        normalized_password = _safe_password_for_bcrypt(password)

    link = ShareLink(
        report_version_id=report_version.id,
        token=generate_token(24),
        password_hash=hash_password(normalized_password) if normalized_password else None,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


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

        # Normalize timezone differences
        if expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)

        if expires < now:
            return False

    return True

    return True