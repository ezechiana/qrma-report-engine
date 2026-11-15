"""Practitioner-owned claim invites for a single patient record.

The raw token is emailed and returned once. Only its hash is stored.
Accepting the invite is a later story; this module will not revive an
invite that has already been accepted.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import PatientClaimInvite, User
from app.services.email_service import send_email

CLAIM_INVITE_DAYS = 7


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
