from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import User
from app.schemas.auth import (
    AuthLoginRequest,
    AuthRegisterRequest,
)
from app.utils.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    get_token_subject,
    require_refresh_token,
    hash_password,
    verify_password,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.lower().strip()).first()


def get_user_by_id(db: Session, user_id: UUID | str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def register_user(db: Session, payload: AuthRegisterRequest) -> User:
    existing = get_user_by_email(db, payload.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        email=payload.email.lower().strip(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        clinic_name=payload.clinic_name.strip() if payload.clinic_name else None,
        phone=payload.phone.strip() if payload.phone else None,
        is_active=True,
        email_verified_at=None,
        last_login_at=None,
        recommendation_mode_default="natural_approaches_clinical",
        logo_url=None,
        primary_color=None,
        accent_color=None,
        support_email=payload.email.lower().strip(),
        website_url=None,
        timezone="Europe/London",
    )

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, payload: AuthLoginRequest) -> User:
    user = get_user_by_email(db, payload.email)
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    user.last_login_at = utcnow()
    db.commit()
    db.refresh(user)
    return user


def build_auth_response(user: User) -> dict:
    subject = str(user.id)

    access_token = create_access_token(
        subject=subject,
        extra_claims={
            "email": user.email,
            "full_name": user.full_name,
        },
    )
    refresh_token = create_refresh_token(
        subject=subject,
        extra_claims={
            "email": user.email,
        },
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user,
    }


def login_user(db: Session, payload: AuthLoginRequest) -> dict:
    user = authenticate_user(db, payload)
    return build_auth_response(user)


def register_and_login_user(db: Session, payload: AuthRegisterRequest) -> dict:
    user = register_user(db, payload)
    return build_auth_response(user)


def refresh_user_tokens(db: Session, refresh_token: str) -> dict:
    try:
        require_refresh_token(refresh_token)
        subject = get_token_subject(refresh_token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    user = get_user_by_id(db, subject)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    return build_auth_response(user)


def get_current_user_from_token(db: Session, token: str) -> User:
    from app.utils.security import require_access_token

    try:
        require_access_token(token)
        subject = get_token_subject(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    user = get_user_by_id(db, subject)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    return user


def change_password(
    db: Session,
    user: User,
    current_password: str,
    new_password: str,
) -> User:
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    user.password_hash = hash_password(new_password)
    db.commit()
    db.refresh(user)
    return user