from __future__ import annotations

from fastapi import APIRouter, Response

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
    payload: TokenRefreshRequest,
    db: CurrentDB,
):
    refreshed = refresh_user_tokens(db, payload.refresh_token)
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


