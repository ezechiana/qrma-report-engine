from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

MICROSOFT_AUTH_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
MICROSOFT_USERINFO_URL = "https://graph.microsoft.com/oidc/userinfo"

SUPPORTED_SOCIAL_PROVIDERS = {"google", "microsoft"}


def _base_url() -> str:
    return (os.getenv("BASE_URL") or "http://127.0.0.1:8000").rstrip("/")


def _state_secret() -> bytes:
    secret = (
        os.getenv("SOCIAL_AUTH_STATE_SECRET")
        or os.getenv("SECRET_KEY")
        or os.getenv("JWT_SECRET")
        or "dev-social-auth-state-secret-change-me"
    )
    return secret.encode("utf-8")


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def safe_next_path(value: str | None, default: str = "/app") -> str:
    if not value:
        return default
    value = str(value).strip()
    if not value.startswith("/") or value.startswith("//"):
        return default
    if value.startswith("/login") or value.startswith("/register") or value.startswith("/auth/"):
        return default
    return value


def social_login_enabled() -> bool:
    return os.getenv("SOCIAL_LOGIN_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def build_social_state(*, provider: str, next_path: str | None = None) -> str:
    provider = provider.lower().strip()
    payload = {
        "provider": provider,
        "next": safe_next_path(next_path),
        "iat": int(time.time()),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = _b64_encode(raw)
    sig = hmac.new(_state_secret(), body.encode("utf-8"), hashlib.sha256).digest()
    return f"{body}.{_b64_encode(sig)}"


def verify_social_state(state: str | None, *, max_age_seconds: int = 600) -> dict[str, Any]:
    if not state or "." not in state:
        return {"provider": None, "next": "/app", "valid": False}

    body, sig = state.split(".", 1)
    expected = _b64_encode(hmac.new(_state_secret(), body.encode("utf-8"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return {"provider": None, "next": "/app", "valid": False}

    try:
        payload = json.loads(_b64_decode(body).decode("utf-8"))
    except Exception:
        return {"provider": None, "next": "/app", "valid": False}

    issued_at = int(payload.get("iat") or 0)
    if issued_at <= 0 or int(time.time()) - issued_at > max_age_seconds:
        return {"provider": payload.get("provider"), "next": safe_next_path(payload.get("next")), "valid": False}

    provider = str(payload.get("provider") or "").lower().strip()
    if provider not in SUPPORTED_SOCIAL_PROVIDERS:
        return {"provider": None, "next": safe_next_path(payload.get("next")), "valid": False}

    return {"provider": provider, "next": safe_next_path(payload.get("next")), "valid": True}


def _redirect_uri(provider: str) -> str:
    provider = provider.lower().strip()
    if provider == "google":
        return os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or f"{_base_url()}/auth/social/google/callback"
    if provider == "microsoft":
        return os.getenv("MICROSOFT_OAUTH_REDIRECT_URI") or f"{_base_url()}/auth/social/microsoft/callback"
    raise ValueError(f"Unsupported social provider: {provider}")


def _client_id(provider: str) -> str | None:
    if provider == "google":
        return os.getenv("GOOGLE_CLIENT_ID")
    if provider == "microsoft":
        return os.getenv("MICROSOFT_CLIENT_ID")
    return None


def _client_secret(provider: str) -> str | None:
    if provider == "google":
        return os.getenv("GOOGLE_CLIENT_SECRET")
    if provider == "microsoft":
        return os.getenv("MICROSOFT_CLIENT_SECRET")
    return None


def provider_is_configured(provider: str) -> bool:
    provider = provider.lower().strip()
    return bool(_client_id(provider) and _client_secret(provider) and _redirect_uri(provider))


def build_oauth_authorization_url(provider: str, *, state: str) -> str:
    if not social_login_enabled():
        raise RuntimeError("Social login is disabled.")

    provider = provider.lower().strip()
    if provider not in SUPPORTED_SOCIAL_PROVIDERS:
        raise RuntimeError("Unsupported social provider.")
    if not provider_is_configured(provider):
        raise RuntimeError(f"{provider.title()} login is not configured.")

    if provider == "google":
        params = {
            "client_id": _client_id("google"),
            "redirect_uri": _redirect_uri("google"),
            "response_type": "code",
            "scope": "openid email profile",
            "prompt": "select_account",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    tenant = os.getenv("MICROSOFT_TENANT", "common").strip() or "common"
    params = {
        "client_id": _client_id("microsoft"),
        "redirect_uri": _redirect_uri("microsoft"),
        "response_type": "code",
        "response_mode": "query",
        "scope": "openid email profile",
        "prompt": "select_account",
        "state": state,
    }
    return f"{MICROSOFT_AUTH_BASE.format(tenant=tenant)}?{urlencode(params)}"


# Backwards-compatible helper name for earlier Google-only implementation.
def build_google_oauth_url(state: str | None = None) -> str:
    return build_oauth_authorization_url("google", state=state or build_social_state(provider="google"))


def exchange_code_for_token(provider: str, code: str) -> dict[str, Any]:
    provider = provider.lower().strip()
    if provider not in SUPPORTED_SOCIAL_PROVIDERS:
        raise RuntimeError("Unsupported social provider.")

    if provider == "google":
        url = GOOGLE_TOKEN_URL
        data = {
            "code": code,
            "client_id": _client_id("google"),
            "client_secret": _client_secret("google"),
            "redirect_uri": _redirect_uri("google"),
            "grant_type": "authorization_code",
        }
    else:
        tenant = os.getenv("MICROSOFT_TENANT", "common").strip() or "common"
        url = MICROSOFT_TOKEN_BASE.format(tenant=tenant)
        data = {
            "code": code,
            "client_id": _client_id("microsoft"),
            "client_secret": _client_secret("microsoft"),
            "redirect_uri": _redirect_uri("microsoft"),
            "grant_type": "authorization_code",
        }

    response = requests.post(url, data=data, timeout=15)
    response.raise_for_status()
    return response.json()


# Backwards-compatible helper name for earlier Google-only implementation.
def exchange_google_code_for_token(code: str) -> dict[str, Any]:
    return exchange_code_for_token("google", code)


def get_provider_user_info(provider: str, access_token: str) -> dict[str, Any]:
    provider = provider.lower().strip()
    if provider == "google":
        url = GOOGLE_USERINFO_URL
    elif provider == "microsoft":
        url = MICROSOFT_USERINFO_URL
    else:
        raise RuntimeError("Unsupported social provider.")

    response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    response.raise_for_status()
    return response.json()


# Backwards-compatible helper name for earlier Google-only implementation.
def get_google_user_info(access_token: str) -> dict[str, Any]:
    return get_provider_user_info("google", access_token)


def normalise_social_profile(provider: str, user_info: dict[str, Any]) -> dict[str, Any]:
    provider = provider.lower().strip()
    subject = str(user_info.get("sub") or user_info.get("id") or "").strip() or None

    if provider == "google":
        email = user_info.get("email")
        full_name = user_info.get("name") or " ".join(
            part for part in [user_info.get("given_name"), user_info.get("family_name")] if part
        )
        email_verified = bool(user_info.get("email_verified", True))
    elif provider == "microsoft":
        email = user_info.get("email") or user_info.get("preferred_username") or user_info.get("upn")
        full_name = user_info.get("name") or " ".join(
            part for part in [user_info.get("given_name"), user_info.get("family_name")] if part
        )
        # Microsoft Entra ID userinfo generally returns an account controlled by Microsoft/tenant.
        email_verified = True
    else:
        raise RuntimeError("Unsupported social provider.")

    return {
        "provider": provider,
        "provider_subject": subject,
        "email": str(email).lower().strip() if email else None,
        "full_name": (full_name or "").strip() or None,
        "email_verified": email_verified,
    }
