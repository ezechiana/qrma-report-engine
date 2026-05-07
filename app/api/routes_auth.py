from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr

from app.api.deps import CurrentActiveUser, CurrentDB
from app.schemas.auth import (
    AuthLoginRequest,
    AuthMeResponse,
    AuthMessageResponse,
    AuthRegisterRequest,
    AuthResponse,
    PasswordChangeRequest,
    TokenPairResponse,
    TokenRefreshRequest,
)
from app.services.auth_service import (
    build_auth_response,
    build_impersonation_response,
    change_password,
    create_email_verification_token,
    get_user_by_email,
    login_user,
    refresh_user_tokens,
    register_user,
    restore_admin_from_token,
    verify_email_token,
)
from app.services.email_service import send_verification_email, verification_url

router = APIRouter(prefix="/auth", tags=["auth"])


class ResendVerificationRequest(BaseModel):
    email: EmailStr


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
            target_payload = {"id": getattr(target, "id", None), "email": getattr(target, "email", None)}
        _audit(db, actor=actor, action=action, target=target_payload, details=details or {})
    except Exception as exc:
        db.rollback()
        print(f"[admin-audit] failed to record {action}: {exc}")


def _send_verification_for_user(db: CurrentDB, user) -> dict:
    token = create_email_verification_token(db, user)
    result = send_verification_email(to_email=user.email, full_name=user.full_name, token=token)
    return {"token": token, "email_result": result}


@router.post("/register")
def register(payload: AuthRegisterRequest, db: CurrentDB, request: Request):
    """Create an unverified user and send a verification email.

    Deliberately does not log the user in. This prevents fake/unreachable emails
    from immediately accessing the platform.
    """
    user = register_user(db, payload)

    referral_code = request.query_params.get("ref") or request.query_params.get("referral_code") or payload.referral_code
    if referral_code:
        try:
            from app.services.referral_service import register_referral_signup
            register_referral_signup(db, referred_user=user, referral_code=referral_code)
        except Exception as exc:
            db.rollback()
            print(f"[referrals] failed to register referral signup: {exc}")

    email_payload = _send_verification_for_user(db, user)
    response = {
        "ok": True,
        "message": "Account created. Please check your email to verify your account before signing in.",
        "email": user.email,
        "requires_email_verification": True,
    }

    # Helpful in local/dev when SMTP is not configured. Do not expose this in production.
    import os
    if os.getenv("APP_ENV", "development").lower() != "production":
        response["dev_verification_url"] = verification_url(email_payload["token"])

    return response


@router.get("/verify-email")
def verify_email(token: str, db: CurrentDB):
    verify_email_token(db, token)
    return RedirectResponse(url="/login?verified=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/resend-verification")
def resend_verification(payload: ResendVerificationRequest, db: CurrentDB):
    # Always return a generic success shape to avoid account enumeration.
    user = get_user_by_email(db, str(payload.email))
    if not user:
        return {"ok": True, "message": "If an unverified account exists for this email, a verification email has been sent."}
    if user.email_verified_at:
        return {"ok": True, "message": "This email is already verified. You can sign in."}

    email_payload = _send_verification_for_user(db, user)
    response = {"ok": True, "message": "If an unverified account exists for this email, a verification email has been sent."}

    import os
    if os.getenv("APP_ENV", "development").lower() != "production":
        response["dev_verification_url"] = verification_url(email_payload["token"])

    return response


@router.post("/login", response_model=AuthResponse)
def login(payload: AuthLoginRequest, db: CurrentDB, response: Response):
    auth = login_user(db, payload)
    _set_session_cookies(response, auth)
    return auth


@router.post("/refresh", response_model=TokenPairResponse)
def refresh_tokens(
    request: Request,
    response: Response,
    db: CurrentDB,
    payload: TokenRefreshRequest | None = Body(default=None),
):
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
    _set_session_cookies(response, refreshed)
    return TokenPairResponse(
        access_token=refreshed["access_token"],
        refresh_token=refreshed["refresh_token"],
        token_type=refreshed["token_type"],
    )


@router.get("/me", response_model=AuthMeResponse)
def get_me(current_user: CurrentActiveUser):
    return AuthMeResponse(user=current_user)


@router.get("/impersonation-status")
def impersonation_status(request: Request, db: CurrentDB, current_user: CurrentActiveUser):
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
def exit_impersonation(request: Request, response: Response, db: CurrentDB, current_user: CurrentActiveUser):
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
def update_password(payload: PasswordChangeRequest, db: CurrentDB, current_user: CurrentActiveUser):
    change_password(db=db, user=current_user, current_password=payload.current_password, new_password=payload.new_password)
    return AuthMessageResponse(message="Password updated successfully.")


@router.post("/logout", response_model=AuthMessageResponse)
def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    response.delete_cookie("admin_restore_token", path="/")
    response.delete_cookie("impersonated_user_id", path="/")
    return AuthMessageResponse(message="Logged out successfully.")
