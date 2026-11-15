# app/api/routes_patient_claim_invites.py

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Patient, User
from app.schemas.patient_claims import (
    ClaimAccessRead,
    ClaimInviteCreate,
    ClaimInviteCreated,
    ClaimInviteRead,
)
from app.services.patient_claim_service import (
    cancel_claim_invite,
    claim_access_state,
    create_claim_invite,
    display_status,
    end_claim_access,
    list_claim_invites,
)

router = APIRouter(prefix="/api/patients", tags=["patients"])


def _owned_patient(db: Session, patient_id: UUID, current_user: User) -> Patient:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


def _to_read(invite) -> dict:
    return {
        "id": invite.id,
        "patient_id": invite.patient_id,
        "email": invite.email,
        "status": display_status(invite),
        "expires_at": invite.expires_at,
        "created_at": invite.created_at,
        "cancelled_at": invite.cancelled_at,
        "accepted_at": invite.accepted_at,
    }


@router.post("/{patient_id}/claim-invites", response_model=ClaimInviteCreated)
def send_claim_invite(
    patient_id: UUID,
    payload: ClaimInviteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a one-time claim invite for this patient record.

    Handles patient identity data. The raw token is returned only in this response.
    """
    _owned_patient(db, patient_id, current_user)
    invite, claim_url, email_sent = create_claim_invite(
        db,
        current_user,
        patient_id,
        str(payload.email),
    )
    body = _to_read(invite)
    body["claim_url"] = claim_url
    body["email_sent"] = email_sent
    return body


@router.get("/{patient_id}/claim-invites", response_model=list[ClaimInviteRead])
def get_claim_invites(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List claim-invite status for one patient. Tokens are not included."""
    _owned_patient(db, patient_id, current_user)
    return [_to_read(invite) for invite in list_claim_invites(db, current_user.id, patient_id)]


@router.post("/{patient_id}/claim-invites/{invite_id}/cancel", response_model=ClaimInviteRead)
def cancel_patient_claim_invite(
    patient_id: UUID,
    invite_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_patient(db, patient_id, current_user)
    invite = cancel_claim_invite(db, current_user.id, patient_id, invite_id)
    return _to_read(invite)


@router.get("/{patient_id}/claim-access", response_model=ClaimAccessRead)
def get_claim_access(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Show whether a client account can open this record, and when access last ended.

    Handles patient identity. The client account id is not included.
    """
    patient = _owned_patient(db, patient_id, current_user)
    return claim_access_state(patient)


@router.post("/{patient_id}/claim-access/unlink", response_model=ClaimAccessRead)
def unlink_claim_access(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove the claimed client from this record. Cases, reports, and share links stay."""
    patient = _owned_patient(db, patient_id, current_user)
    patient = end_claim_access(db, patient, "practitioner")
    return claim_access_state(patient)
