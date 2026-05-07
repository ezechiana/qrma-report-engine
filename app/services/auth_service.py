from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import RecommendationMode, User
from app.schemas.auth import AuthLoginRequest, AuthRegisterRequest
from app.utils.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    get_token_subject,
    hash_password,
    require_refresh_token,
    verify_password,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_auth_events_table(db: Session) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS auth_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NULL,
            email TEXT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            ip_address TEXT NULL,
            user_agent TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            meta JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_auth_events_created_at ON auth_events (created_at DESC)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_auth_events_type_status ON auth_events (event_type, status, created_at DESC)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_auth_events_email ON auth_events (LOWER(email))"))
    db.commit()


def log_auth_event(db: Session, *, event_type: str, status: str, user=None, email=None, request=None, meta=None) -> None:
    try:
        _ensure_auth_events_table(db)
        db.execute(text("""
            INSERT INTO auth_events (user_id, email, event_type, status, ip_address, user_agent, meta)
            VALUES (:user_id, :email, :event_type, :status, :ip, :ua, CAST(:meta AS JSONB))
        """), {
            "user_id": str(user.id) if user else None,
            "email": (str(email).lower().strip() if email else getattr(user, "email", None)),
            "event_type": event_type,
            "status": status,
            "ip": request.client.host if request and request.client else None,
            "ua": request.headers.get("user-agent") if request else None,
            "meta": json.dumps(meta or {}),
        })
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[auth-events] failed to record {event_type}/{status}: {exc}")


def _validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 10 characters.")
    if not any(ch.isupper() for ch in password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must contain at least one uppercase letter.")
    if not any(ch.islower() for ch in password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must contain at least one lowercase letter.")
    if not any(ch.isdigit() for ch in password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must contain at least one number.")


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.lower().strip()).first()


def get_user_by_id(db: Session, user_id: UUID | str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def _ensure_email_verification_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS email_verification_tokens (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token TEXT NOT NULL UNIQUE,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                used_at TIMESTAMPTZ NULL
            )
            """
        )
    )
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_email_verification_tokens_user_id ON email_verification_tokens (user_id)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_email_verification_tokens_expires_at ON email_verification_tokens (expires_at)"))
    db.commit()


def create_email_verification_token(db: Session, user: User, *, expires_hours: int = 24) -> str:
    _ensure_email_verification_table(db)
    token = token_urlsafe(32)
    expires_at = utcnow() + timedelta(hours=expires_hours)

    # Keep only the latest active token per user to reduce confusion.
    db.execute(
        text("DELETE FROM email_verification_tokens WHERE user_id = :user_id AND used_at IS NULL"),
        {"user_id": str(user.id)},
    )
    db.execute(
        text(
            """
            INSERT INTO email_verification_tokens (user_id, token, expires_at)
            VALUES (:user_id, :token, :expires_at)
            """
        ),
        {"user_id": str(user.id), "token": token, "expires_at": expires_at},
    )
    db.commit()
    return token


def verify_email_token(db: Session, token: str) -> User:
    _ensure_email_verification_table(db)
    row = db.execute(
        text(
            """
            SELECT user_id, expires_at, used_at
            FROM email_verification_tokens
            WHERE token = :token
            LIMIT 1
            """
        ),
        {"token": token},
    ).mappings().first()

    if not row or row.get("used_at"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This verification link is invalid or has already been used.")

    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This verification link has expired. Please request a new one.")

    user = get_user_by_id(db, row["user_id"])
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if not user.email_verified_at:
        user.email_verified_at = utcnow()
        user.updated_at = utcnow()

    db.execute(text("UPDATE email_verification_tokens SET used_at = NOW() WHERE token = :token"), {"token": token})
    db.commit()
    db.refresh(user)
    log_auth_event(db, event_type="email_verify", status="success", user=user, email=user.email)
    return user


def register_user(db: Session, payload: AuthRegisterRequest) -> User:
    existing = get_user_by_email(db, payload.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")

    _validate_password_strength(payload.password)

    user = User(
        email=payload.email.lower().strip(),
        password_hash=hash_password(payload.password),
        full_name=(payload.full_name or f"{payload.first_name} {payload.last_name}").strip(),
        clinic_name=payload.clinic_name.strip() if payload.clinic_name else None,
        phone=payload.phone.strip() if payload.phone else None,
        role="practitioner",
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
    log_auth_event(db, event_type="register", status="success", user=user, email=user.email)
    return user


def get_or_create_social_user(
    db: Session,
    *,
    provider: str,
    provider_subject: str | None,
    email: str,
    full_name: str | None = None,
) -> User:
    """Return an existing user or create a verified practitioner account from a trusted OAuth provider.

    Social login must never grant platform-admin access by itself. Admin access remains
    controlled by the role/platform-admin checks.
    """
    clean_email = email.lower().strip()
    user = get_user_by_email(db, clean_email)

    if user:
        if not user.is_active:
            log_auth_event(
                db,
                event_type="social_login",
                status="failed",
                user=user,
                email=clean_email,
                meta={"provider": provider, "reason": "inactive_user"},
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive.")

        # Provider-verified email is sufficient to mark an existing account verified.
        if not user.email_verified_at:
            user.email_verified_at = utcnow()
        user.last_login_at = utcnow()
        user.updated_at = utcnow()
        db.commit()
        db.refresh(user)
        return user

    generated_password = token_urlsafe(48)
    display_name = (full_name or clean_email.split("@")[0]).strip()

    user = User(
        email=clean_email,
        password_hash=hash_password(generated_password),
        full_name=display_name,
        clinic_name=None,
        phone=None,
        role="practitioner",
        is_active=True,
        email_verified_at=utcnow(),
        last_login_at=utcnow(),
        recommendation_mode_default=RecommendationMode.natural_approaches_clinical,
        logo_url=None,
        primary_color=None,
        accent_color=None,
        support_email=clean_email,
        website_url=None,
        timezone="Europe/London",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_auth_event(
        db,
        event_type="register",
        status="success",
        user=user,
        email=clean_email,
        meta={"method": "social", "provider": provider, "provider_subject": provider_subject},
    )
    return user


def _is_platform_admin(db: Session, user: User) -> bool:
    try:
        from app.services.platform_settings_service import is_platform_admin_user
        return bool(is_platform_admin_user(db, user))
    except Exception:
        db.rollback()
        return str(getattr(user, "role", "")).lower() == "admin"


def authenticate_user(db: Session, payload: AuthLoginRequest) -> User:
    email = str(payload.email).lower().strip()
    user = get_user_by_email(db, email)
    if not user or not verify_password(payload.password, user.password_hash):
        log_auth_event(db, event_type="login", status="failed", email=email, meta={"reason": "invalid_credentials"})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    if not user.is_active:
        log_auth_event(db, event_type="login", status="failed", user=user, email=email, meta={"reason": "inactive_user"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive.")

    # Platform admins may need emergency access before email verification, especially
    # when founder/admin access is configured through the env/UI allowlist.
    if not user.email_verified_at and not _is_platform_admin(db, user):
        log_auth_event(db, event_type="login", status="blocked_unverified", user=user, email=email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before signing in. Check your inbox or request a new verification email.",
        )

    user.last_login_at = utcnow()
    db.commit()
    db.refresh(user)
    return user


def build_auth_response(user: User) -> dict:
    subject = str(user.id)
    access_token = create_access_token(subject=subject, extra_claims={"email": user.email, "full_name": user.full_name})
    refresh_token = create_refresh_token(subject=subject, extra_claims={"email": user.email})
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer", "user": user}


def login_user(db: Session, payload: AuthLoginRequest) -> dict:
    user = authenticate_user(db, payload)
    log_auth_event(db, event_type="login", status="success", user=user, email=user.email)
    return build_auth_response(user)


def register_and_login_user(db: Session, payload: AuthRegisterRequest) -> dict:
    # Backwards-compatible helper; new browser registration flow should use
    # register_user + email verification before login.
    user = register_user(db, payload)
    return build_auth_response(user)


def refresh_user_tokens(db: Session, refresh_token: str) -> dict:
    try:
        require_refresh_token(refresh_token)
        subject = get_token_subject(refresh_token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = get_user_by_id(db, subject)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive.")
    if not user.email_verified_at and not _is_platform_admin(db, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Please verify your email before continuing.")
    return build_auth_response(user)


def get_current_user_from_token(db: Session, token: str) -> User:
    from app.utils.security import require_access_token

    try:
        require_access_token(token)
        subject = get_token_subject(token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = get_user_by_id(db, subject)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive.")
    return user


def change_password(db: Session, user: User, current_password: str, new_password: str) -> User:
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")
    _validate_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    db.commit()
    db.refresh(user)
    return user


def build_admin_restore_token(admin_user: User) -> str:
    return create_refresh_token(
        subject=str(admin_user.id),
        extra_claims={"email": admin_user.email, "purpose": "admin_impersonation_restore"},
    )


def restore_admin_from_token(db: Session, restore_token: str) -> User:
    try:
        require_refresh_token(restore_token)
        subject = get_token_subject(restore_token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    admin = get_user_by_id(db, subject)
    if not admin or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin restore session is invalid.")
    if not _is_platform_admin(db, admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Restored user is not a platform admin.")
    return admin


def build_impersonation_response(db: Session, admin_user: User, target_user_id: str) -> dict:
    if not _is_platform_admin(db, admin_user):
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
