# app/api/routes_patient_claims.py

import os

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.schemas.patient_claims import ClaimAccountCreate, ClaimAccountStarted, ClaimInvitePreview, ClaimInviteAccept
from app.services.email_service import verification_url
from app.services.patient_claim_service import accept_claim_invite, begin_client_claim, preview_claim_invite

router = APIRouter(tags=["client-claim"])


@router.post("/api/claim/{token}/accept")
def accept_patient_claim(
    token: str,
    payload: ClaimInviteAccept,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    invite = accept_claim_invite(db, user, token, payload.consent)
    return {"ok": True, "patient_id": str(invite.patient_id)}


@router.get("/api/claim/{token}", response_model=ClaimInvitePreview)
def preview_patient_claim(
    token: str,
    db: Session = Depends(get_db),
):
    """Show the clinic name, the client's name, and the consent text for one invite.

    Handles patient identity data. The token is the secret from the invite email.
    """
    return preview_claim_invite(db, token)


@router.post("/claim/{token}", response_model=ClaimAccountStarted)
def start_patient_claim(
    token: str,
    payload: ClaimAccountCreate,
    db: Session = Depends(get_db),
):
    """Create a client account for this invite. The record is claimed after email verification."""
    user, verification_token = begin_client_claim(
        db,
        token,
        str(payload.email),
        payload.password,
        payload.confirm_password,
        payload.consent,
    )
    body = {
        "ok": True,
        "email": user.email,
        "requires_email_verification": True,
        "message": "Account created. Verify your email to claim this record.",
        "dev_verification_url": None,
    }
    if os.getenv("APP_ENV", "development").lower() != "production":
        body["dev_verification_url"] = verification_url(verification_token)
    return body
