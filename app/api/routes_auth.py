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
    change_password,
    login_user,
    refresh_user_tokens,
    register_and_login_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse)
def register(
    payload: AuthRegisterRequest,
    db: CurrentDB,
    response: Response,
):
    auth = register_and_login_user(db, payload)

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
    return AuthMessageResponse(message="Logged out successfully.")


