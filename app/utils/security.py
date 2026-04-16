from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext


pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))


class TokenError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def generate_token(length: int = 48) -> str:
    return secrets.token_urlsafe(length)


def _build_token_payload(
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = utcnow()
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return payload


def create_access_token(
    subject: str,
    extra_claims: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
) -> str:
    expire_minutes = expires_minutes or ACCESS_TOKEN_EXPIRE_MINUTES
    payload = _build_token_payload(
        subject=subject,
        token_type="access",
        expires_delta=timedelta(minutes=expire_minutes),
        extra_claims=extra_claims,
    )
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(
    subject: str,
    extra_claims: dict[str, Any] | None = None,
    expires_days: int | None = None,
) -> str:
    expire_days = expires_days or REFRESH_TOKEN_EXPIRE_DAYS
    payload = _build_token_payload(
        subject=subject,
        token_type="refresh",
        expires_delta=timedelta(days=expire_days),
        extra_claims=extra_claims,
    )
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError as exc:
        raise TokenError("Invalid or expired token") from exc


def get_token_subject(token: str) -> str:
    payload = decode_token(token)
    subject = payload.get("sub")
    if not subject:
        raise TokenError("Token missing subject")
    return str(subject)


def get_token_type(token: str) -> str:
    payload = decode_token(token)
    token_type = payload.get("type")
    if token_type not in {"access", "refresh"}:
        raise TokenError("Invalid token type")
    return str(token_type)


def require_access_token(token: str) -> dict[str, Any]:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise TokenError("Expected access token")
    return payload


def require_refresh_token(token: str) -> dict[str, Any]:
    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise TokenError("Expected refresh token")
    return payload