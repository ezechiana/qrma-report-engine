from __future__ import annotations

from fastapi import APIRouter, Response, Request, Body, HTTPException, status

from app.api.deps import CurrentActiveUser, CurrentDB
from app.schemas.auth import (
    AuthLoginRequest,
    AuthMeResponse,
    AuthMessageResponse,
    AuthRegisterRequest,
    AuthResponse,
    PasswordChangeRequest,
    TokenRefreshRequest,
    TokenPairResponse,
)
from app.services.auth_service import (
    build_auth_response,
    build_impersonation_response,
    change_password,
    login_user,
    refresh_user_tokens,
    register_and_login_user,
    restore_admin_from_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_secure() -> bool:
    import os
    return os.getenv("APP_ENV", "development").lower() == "production"


def _set_session_cookies(response: Response, auth: dict) -> None:
    response.set_cookie(
        key="access_token",
        value=auth["access_token"],
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=60 * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=auth["refresh_token"],
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


def _audit_admin_action(db, *, actor, action: str, target=None, details=None) -> None:
    try:
        from app.api.routes_platform_users import _audit
        target_payload = None
        if target is not None:
            target_payload = {
                "id": getattr(target, "id", None),
                "email": getattr(target, "email", None),
            }
        _audit(db, actor=actor, action=action, target=target_payload, details=details or {})
    except Exception as exc:
        db.rollback()
        print(f"[admin-audit] failed to record {action}: {exc}")


@router.post("/register", response_model=AuthResponse)
def register(
    payload: AuthRegisterRequest,
    db: CurrentDB,
    response: Response,
    request: Request,
):
    auth = register_and_login_user(db, payload)

    # Referral V2: capture /register?ref=CODE without changing the auth schema.
    # Best-effort only; registration must never fail because referral tracking failed.
    referral_code = request.query_params.get("ref") or request.query_params.get("referral_code")
    if referral_code:
        try:
            from app.services.referral_service import register_referral_signup

            register_referral_signup(db, referred_user=auth.get("user"), referral_code=referral_code)
        except Exception as exc:
            db.rollback()
            print(f"[referrals] failed to register referral signup: {exc}")

    response.set_cookie(
        key="access_token",
        value=auth["access_token"],
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60,
        path="/",
    )

    response.set_cookie(
        key="refresh_token",
        value=auth["refresh_token"],
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )

    return auth


@router.post("/login", response_model=AuthResponse)
def login(
    payload: AuthLoginRequest,
    db: CurrentDB,
    response: Response,
):
    auth = login_user(db, payload)

    response.set_cookie(
        key="access_token",
        value=auth["access_token"],
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60,
        path="/",
    )

    response.set_cookie(
        key="refresh_token",
        value=auth["refresh_token"],
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )

    return auth


@router.post("/refresh", response_model=TokenPairResponse)
def refresh_tokens(
    request: Request,
    response: Response,
    db: CurrentDB,
    payload: TokenRefreshRequest | None = Body(default=None),
):
    """
    Refresh the browser session without forcing a login.

    Supports both:
    1. JSON body refresh token, for backwards-compatible API clients.
    2. HttpOnly refresh_token cookie, for normal browser use.
    """
    refresh_token = payload.refresh_token if payload and payload.refresh_token else None
    if not refresh_token:
        refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    refreshed = refresh_user_tokens(db, refresh_token)

    response.set_cookie(
        key="access_token",
        value=refreshed["access_token"],
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60,
        path="/",
    )

    response.set_cookie(
        key="refresh_token",
        value=refreshed["refresh_token"],
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )

    return TokenPairResponse(
        access_token=refreshed["access_token"],
        refresh_token=refreshed["refresh_token"],
        token_type=refreshed["token_type"],
    )


@router.get("/me", response_model=AuthMeResponse)
def get_me(
    current_user: CurrentActiveUser,
):
    return AuthMeResponse(user=current_user)




@router.get("/impersonation-status")
def impersonation_status(
    request: Request,
    db: CurrentDB,
    current_user: CurrentActiveUser,
):
    restore_token = request.cookies.get("admin_restore_token")
    if not restore_token:
        return {"impersonating": False}

    try:
        admin_user = restore_admin_from_token(db, restore_token)
    except Exception:
        return {"impersonating": False}

    return {
        "impersonating": True,
        "admin_email": admin_user.email,
        "admin_user_id": str(admin_user.id),
        "impersonated_email": current_user.email,
        "impersonated_user_id": str(current_user.id),
    }


@router.post("/impersonate/exit")
def exit_impersonation(
    request: Request,
    response: Response,
    db: CurrentDB,
    current_user: CurrentActiveUser,
):
    restore_token = request.cookies.get("admin_restore_token")
    if not restore_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No admin impersonation session found.")

    admin_user = restore_admin_from_token(db, restore_token)
    auth = build_auth_response(admin_user)
    _set_session_cookies(response, auth)
    response.delete_cookie("admin_restore_token", path="/")
    response.delete_cookie("impersonated_user_id", path="/")

    _audit_admin_action(
        db,
        actor=admin_user,
        action="admin_exited_impersonation",
        target=current_user,
        details={"impersonated_email": current_user.email},
    )

    return {"message": "Returned to admin session.", "user": admin_user}


@router.post("/impersonate/{user_id}")
def start_impersonation(
    user_id: str,
    request: Request,
    response: Response,
    db: CurrentDB,
    current_user: CurrentActiveUser,
):
    auth = build_impersonation_response(db, current_user, user_id)
    target = auth["impersonated_user"]

    _set_session_cookies(response, auth)
    response.set_cookie(
        key="admin_restore_token",
        value=auth["admin_restore_token"],
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=60 * 60 * 2,
        path="/",
    )
    response.set_cookie(
        key="impersonated_user_id",
        value=str(target.id),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=60 * 60 * 2,
        path="/",
    )

    _audit_admin_action(
        db,
        actor=current_user,
        action="admin_started_impersonation",
        target=target,
        details={"target_email": target.email, "ip": request.client.host if request.client else None},
    )

    return {"message": "Impersonation started.", "user": target}


@router.post("/change-password", response_model=AuthMessageResponse)
def update_password(
    payload: PasswordChangeRequest,
    db: CurrentDB,
    current_user: CurrentActiveUser,
):
    change_password(
        db=db,
        user=current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return AuthMessageResponse(message="Password updated successfully.")


@router.post("/logout", response_model=AuthMessageResponse)
def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    response.delete_cookie("admin_restore_token", path="/")
    response.delete_cookie("impersonated_user_id", path="/")
    return AuthMessageResponse(message="Logged out successfully.")


