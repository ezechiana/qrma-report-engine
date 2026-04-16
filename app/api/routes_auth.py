from __future__ import annotations

from fastapi import APIRouter, Depends

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
):
    return register_and_login_user(db, payload)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: AuthLoginRequest,
    db: CurrentDB,
):
    return login_user(db, payload)


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
def logout():
    """
    Stateless JWT logout placeholder.

    For v1, logout is handled client-side by discarding tokens.
    Later, this can be upgraded with token blacklisting / revocation.
    """
    return AuthMessageResponse(message="Logged out successfully.")