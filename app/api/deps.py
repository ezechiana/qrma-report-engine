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

    return user




CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_user(current_user: CurrentUser) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )
    return current_user


CurrentActiveUser = Annotated[User, Depends(get_current_active_user)]