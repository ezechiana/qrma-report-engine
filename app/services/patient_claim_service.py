"""Practitioner-owned claim invites for a single patient record.

The raw token is emailed and returned once. Only its hash is stored.
A client account can claim the record only after email verification.
An accepted invite cannot be used again, and the practitioner remains
the owner of the patient record.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Patient, PatientClaimInvite, RecommendationMode, User
from app.services.client_entry_service import clear_client_entry_releases
from app.services.content_release_service import clear_progress_release, clear_report_releases
from app.services.patient_source_service import clear_clinic_note_releases
from app.services.email_service import send_email, send_verification_email
from app.utils.security import hash_password

CLAIM_INVITE_DAYS = 7
CLAIM_CONSENT_VERSION = "client-claim-v1"
CLAIM_CONSENT_TEXT = (
    "I agree to create a client account so I can view the record prepared for me "
    "by this clinic. This does not give me access to anyone else's record."
)
INVITE_UNUSABLE = "This invite can no longer be used."


def hash_claim_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def display_status(invite: PatientClaimInvite, now: datetime | None = None) -> str:
    if invite.status == "cancelled":
        return "cancelled"
    if invite.status == "accepted":
        return "accepted"
    current = now or datetime.now(timezone.utc)
    if _as_utc(invite.expires_at) <= current:
        return "expired"
    return "pending"


def _claim_url(raw_token: str) -> str:
    base = (os.getenv("BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/claim/{raw_token}"


def _send_invite(invite: PatientClaimInvite, practitioner: User, raw_token: str) -> bool:
    clinic = (practitioner.clinic_name or practitioner.full_name or "Your clinic").strip()
    url = _claim_url(raw_token)
    expires = _as_utc(invite.expires_at).strftime("%d %b %Y %H:%M UTC")
    text = (
        f"{clinic} has invited you to claim your GO360 record.\n\n"
        f"Open this link to continue:\n{url}\n\n"
        f"This link expires at {expires} and can be used once.\n"
        "If you were not expecting this invitation, you can ignore this email.\n"
    )
    html = (
        f"<p>{escape(clinic)} has invited you to claim your GO360 record.</p>"
        f"<p><a href=\"{escape(url, quote=True)}\">Claim your record</a></p>"
        f"<p>This link expires at {escape(expires)} and can be used once.</p>"
        "<p>If you were not expecting this invitation, you can ignore this email.</p>"
    )
    try:
        result = send_email(
            to_email=invite.email,
            subject=f"{clinic} invited you to view your GO360 record",
            html=html,
            text=text,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="The invite email could not be sent. No invite was saved.",
        ) from exc
    return bool(result.get("sent"))


def create_claim_invite(
    db: Session,
    practitioner: User,
    patient_id,
    email: str,
) -> tuple[PatientClaimInvite, str, bool]:
    """Create one live invite for this patient and retire older pending ones."""
    now = datetime.now(timezone.utc)
    normalised = email.strip().lower()
    pending = (
        db.query(PatientClaimInvite)
        .filter(
            PatientClaimInvite.patient_id == patient_id,
            PatientClaimInvite.practitioner_user_id == practitioner.id,
            PatientClaimInvite.status == "pending",
        )
        .all()
    )
    for previous in pending:
        previous.status = "cancelled"
        previous.cancelled_at = now

    raw_token = secrets.token_urlsafe(32)
    invite = PatientClaimInvite(
        patient_id=patient_id,
        practitioner_user_id=practitioner.id,
        email=normalised,
        token_hash=hash_claim_token(raw_token),
        status="pending",
        expires_at=now + timedelta(days=CLAIM_INVITE_DAYS),
        created_at=now,
    )
    db.add(invite)
    db.flush()
    try:
        email_sent = _send_invite(invite, practitioner, raw_token)
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    db.refresh(invite)
    return invite, _claim_url(raw_token), email_sent


def list_claim_invites(db: Session, practitioner_id, patient_id) -> list[PatientClaimInvite]:
    return (
        db.query(PatientClaimInvite)
        .filter(
            PatientClaimInvite.patient_id == patient_id,
            PatientClaimInvite.practitioner_user_id == practitioner_id,
        )
        .order_by(PatientClaimInvite.created_at.desc())
        .all()
    )


def cancel_claim_invite(db: Session, practitioner_id, patient_id, invite_id) -> PatientClaimInvite:
    invite = (
        db.query(PatientClaimInvite)
        .filter(
            PatientClaimInvite.id == invite_id,
            PatientClaimInvite.patient_id == patient_id,
            PatientClaimInvite.practitioner_user_id == practitioner_id,
        )
        .first()
    )
    if invite is None:
        raise HTTPException(status_code=404, detail="Claim invite not found")
    if invite.status == "accepted":
        raise HTTPException(status_code=409, detail="This invite has already been used.")
    if invite.status == "cancelled":
        raise HTTPException(status_code=409, detail="This invite is already cancelled.")
    invite.status = "cancelled"
    invite.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(invite)
    return invite


def _invite_for_token(db: Session, raw_token: str) -> PatientClaimInvite:
    invite = (
        db.query(PatientClaimInvite)
        .filter(PatientClaimInvite.token_hash == hash_claim_token(raw_token))
        .first()
    )
    if invite is None:
        raise HTTPException(status_code=404, detail="This invite is no longer available.")
    return invite


def _ensure_usable(invite: PatientClaimInvite) -> None:
    if invite.status in {"cancelled", "accepted"} or display_status(invite) != "pending":
        raise HTTPException(status_code=409, detail=INVITE_UNUSABLE)


def preview_claim_invite(db: Session, raw_token: str) -> dict:
    """Show the clinic and the client's own name before an account is created."""
    invite = _invite_for_token(db, raw_token)
    _ensure_usable(invite)
    patient = db.query(Patient).filter(Patient.id == invite.patient_id).first()
    practitioner = db.query(User).filter(User.id == invite.practitioner_user_id).first()
    if patient is None or practitioner is None:
        raise HTTPException(status_code=404, detail="This invite is no longer available.")
    clinic = (practitioner.clinic_name or practitioner.full_name or "Your clinic").strip()
    return {
        "clinic_name": clinic,
        "client_name": patient.full_name,
        "email": invite.email,
        "expires_at": invite.expires_at,
        "consent_text": CLAIM_CONSENT_TEXT,
        "consent_version": CLAIM_CONSENT_VERSION,
    }


def begin_client_claim(
    db: Session,
    raw_token: str,
    email: str,
    password: str,
    confirm_password: str,
    consent: bool,
) -> tuple[User, str]:
    """Create a client account and reserve this invite until the email is verified."""
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    if consent is not True:
        raise HTTPException(
            status_code=400,
            detail="Consent is required to create an account and claim this record.",
        )
    from app.services.auth_service import (
        _validate_password_strength,
        create_email_verification_token,
        get_user_by_email,
    )

    _validate_password_strength(password)
    invite = _invite_for_token(db, raw_token)
    _ensure_usable(invite)
    normalised = email.strip().lower()
    if normalised != invite.email:
        raise HTTPException(status_code=400, detail="Use the email address that received this invite.")

    existing = get_user_by_email(db, normalised)
    now = datetime.now(timezone.utc)
    if existing is not None:
        same_reservation = invite.reserved_by_user_id == existing.id and existing.role == "client"
        if not same_reservation:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        user = existing
    else:
        if invite.reserved_by_user_id is not None:
            raise HTTPException(status_code=409, detail=INVITE_UNUSABLE)
        patient = db.query(Patient).filter(Patient.id == invite.patient_id).first()
        if patient is None:
            raise HTTPException(status_code=404, detail="This invite is no longer available.")
        user = User(
            email=normalised,
            password_hash=hash_password(password),
            full_name=patient.full_name,
            clinic_name=None,
            role="client",
            is_active=True,
            email_verified_at=None,
            recommendation_mode_default=RecommendationMode.natural_approaches_clinical,
        )
        db.add(user)
        db.flush()

    invite.reserved_by_user_id = user.id
    invite.reserved_at = invite.reserved_at or now
    invite.consent_at = now
    invite.consent_version = CLAIM_CONSENT_VERSION
    db.commit()
    db.refresh(user)
    verification_token = create_email_verification_token(db, user)
    try:
        send_verification_email(to_email=user.email, full_name=user.full_name, token=verification_token)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="The account was created, but the verification email could not be sent.",
        ) from exc
    return user, verification_token


def complete_reserved_claim(db: Session, user: User) -> PatientClaimInvite | None:
    """Link a verified client to the one record reserved by their invite.

    Does not commit. The practitioner user_id on the patient stays unchanged.
    Earlier releases stay withdrawn.
    """
    if str(getattr(user, "role", "") or "").lower() != "client" or not user.email_verified_at:
        return None
    invite = (
        db.query(PatientClaimInvite)
        .filter(
            PatientClaimInvite.reserved_by_user_id == user.id,
            PatientClaimInvite.status == "pending",
        )
        .order_by(PatientClaimInvite.created_at.desc())
        .first()
    )
    if invite is None or display_status(invite) != "pending":
        return None
    patient = db.query(Patient).filter(Patient.id == invite.patient_id).first()
    if patient is None or patient.user_id == user.id:
        return None
    if patient.claimed_by_user_id and patient.claimed_by_user_id != user.id:
        return None
    now = datetime.now(timezone.utc)
    invite.status = "accepted"
    invite.accepted_at = now
    invite.claimed_by_user_id = user.id
    patient.claimed_by_user_id = user.id
    patient.claimed_at = now
    patient.claim_access_ended_at = None
    patient.claim_access_ended_by = None
    return invite


def claim_access_state(patient: Patient) -> dict:
    """Clinic-facing access state. Does not include the client account id."""
    return {
        "patient_id": patient.id,
        "claimed": patient.claimed_by_user_id is not None,
        "claimed_at": patient.claimed_at,
        "access_ended_at": patient.claim_access_ended_at,
        "access_ended_by": patient.claim_access_ended_by,
    }


def end_claim_access(db: Session, patient: Patient, ended_by: str) -> Patient:
    """Remove a client's account access without deleting the clinical record.

    Pending invites for this patient stop working. Progress, report, note, lab-panel,
    and client-entry releases are withdrawn in this same save. The notes, panels,
    marker values, and client entries stay. Share links are left as they are.
    """
    if ended_by not in {"practitioner", "client"}:
        raise HTTPException(status_code=400, detail="Access can only be ended by the clinic or the client.")
    if patient.claimed_by_user_id is None:
        raise HTTPException(status_code=409, detail="This record is not claimed.")
    now = datetime.now(timezone.utc)
    patient.claimed_by_user_id = None
    patient.claim_access_ended_at = now
    patient.claim_access_ended_by = ended_by
    pending = (
        db.query(PatientClaimInvite)
        .filter(
            PatientClaimInvite.patient_id == patient.id,
            PatientClaimInvite.status == "pending",
        )
        .all()
    )
    for invite in pending:
        invite.status = "cancelled"
        invite.cancelled_at = now
    clear_progress_release(db, patient, now)
    clear_report_releases(db, patient, now)
    clear_clinic_note_releases(db, patient, now)
    clear_client_entry_releases(db, patient, now)
    db.commit()
    db.refresh(patient)
    return patient
