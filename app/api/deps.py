from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import SessionLocal
from app.services.auth_service import get_current_user_from_token


bearer_scheme = HTTPBearer(auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


CurrentDB = Annotated[Session, Depends(get_db)]



def get_current_user(
    request: Request,
    db: CurrentDB,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    """
    Resolve current user from:
    1. Bearer token (Authorization header)
    2. OR access_token cookie
    """

    token = None

    # 1️⃣ Try header first (existing behaviour)
    if credentials and credentials.credentials:
        token = credentials.credentials

    # 2️⃣ Fallback to cookie (NEW)
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user = get_current_user_from_token(db, token)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if request.method in {"GET", "HEAD"} and request.url.path == "/app/organisations":
        # Administrative registry only. Use the same live capability/active/
        # verification gate as its API; do not open other practitioner paths
        # to a client-role login that also has a practitioner capability.
        from app.services.clinic_registry import authorize

        authorize(db, user, "registry.page")
    elif request.method in {"GET", "HEAD"} and request.url.path == "/app/profile":
        from app.services.practitioner_profile import authorize
        authorize(db, user)
    elif request.method in {"GET", "HEAD"} and (request.url.path == "/app/memberships" or (
        request.url.path.startswith("/app/clinics/") and request.url.path.endswith("/members")
        and len(request.url.path.split("/")) == 5)):
        from app.services.clinic_membership import eligible, refuse
        if not eligible(db, user):
            refuse(db, user, "membership.page", 403, "Practitioner access required.")
    elif _client_blocked_from_practitioner_path(user, request.url.path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client accounts cannot use practitioner tools.",
        )

    return user


def _client_blocked_from_practitioner_path(user: User, path: str) -> bool:
    """Keep a claimed client out of practitioner patient, report, and share tools."""
    if str(getattr(user, "role", "") or "").lower() != "client":
        return False
    prefixes = (
        "/api/patients",
        "/api/cases",
        "/api/reports",
        "/api/share",
        "/api/billing",
        "/app",
    )
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "-"):
            return True
    return False




CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_user(current_user: CurrentUser) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )
    return current_user


CurrentActiveUser = Annotated[User, Depends(get_current_active_user)]

def require_platform_admin(
    db: CurrentDB,
    current_user: CurrentUser,
) -> User:
    """Require platform administrator access.

    Access is granted when either:
    - users.role = 'admin', or
    - the user's email is present in Platform Settings / PLATFORM_ADMIN_EMAILS.
    """
    from app.services.platform_settings_service import is_platform_admin_user

    if not is_platform_admin_user(db, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator access required.",
        )
    return current_user


def require_feature_enabled(feature_key: str):
    """FastAPI dependency factory for feature-flag enforcement."""
    def _dependency(db: CurrentDB) -> bool:
        from app.services.platform_settings_service import is_feature_enabled

        if not is_feature_enabled(db, feature_key, default=True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Feature disabled: {feature_key}",
            )
        return True

    return _dependency
