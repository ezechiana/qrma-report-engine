from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import RecommendationMode, User
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


def _validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 10 characters.",
        )

    if not any(ch.isupper() for ch in password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one uppercase letter.",
        )

    if not any(ch.islower() for ch in password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one lowercase letter.",
        )

    if not any(ch.isdigit() for ch in password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one number.",
        )


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

    _validate_password_strength(payload.password)

    user = User(
        email=payload.email.lower().strip(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        clinic_name=payload.clinic_name.strip() if payload.clinic_name else None,
        phone=payload.phone.strip() if payload.phone else None,
        is_active=True,
        email_verified_at=None,
        last_login_at=None,
        recommendation_mode_default=RecommendationMode.natural_approaches_clinical,
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

    _validate_password_strength(new_password)

    user.password_hash = hash_password(new_password)
    db.commit()
    db.refresh(user)
    return user

def build_admin_restore_token(admin_user: User) -> str:
    """Create a short-lived restore token used only to return from impersonation."""
    return create_refresh_token(
        subject=str(admin_user.id),
        extra_claims={
            "email": admin_user.email,
            "purpose": "admin_impersonation_restore",
        },
    )


def restore_admin_from_token(db: Session, restore_token: str) -> User:
    """Resolve the original admin from an impersonation restore token."""
    try:
        require_refresh_token(restore_token)
        subject = get_token_subject(restore_token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    admin = get_user_by_id(db, subject)
    if not admin or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin restore session is invalid.")

    from app.services.platform_settings_service import is_platform_admin_user

    if not is_platform_admin_user(db, admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Restored user is not a platform admin.")

    return admin


def build_impersonation_response(db: Session, admin_user: User, target_user_id: str) -> dict:
    """Build auth tokens for a target user after validating platform-admin access."""
    from app.services.platform_settings_service import is_platform_admin_user

    if not is_platform_admin_user(db, admin_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform administrator access required.")

    target = get_user_by_id(db, target_user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found.")
    if not target.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot impersonate an inactive user.")
    if str(target.id) == str(admin_user.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You are already signed in as this admin user.")

    auth = build_auth_response(target)
    auth["admin_restore_token"] = build_admin_restore_token(admin_user)
    auth["admin_user"] = admin_user
    auth["impersonated_user"] = target
    return auth
