"""CLINIC administrative participation and live policy. No clinical authority."""
from dataclasses import dataclass
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select
from app.db.models import Clinic, Organisation, User, UserCapability, ClinicMembership, ClinicMembershipEvent
from app.services.platform_settings_service import is_platform_admin_user

POLICY_REVISION = "membership-admin-v1"


def event(db, actor, action, outcome, row=None, *, prior_status=None, original_admin_id=None):
    db.add(ClinicMembershipEvent(actor_user_id=actor.id, action=action, outcome=outcome,
        membership_id=row.id if row else None, clinic_id=row.clinic_id if row else None,
        prior_status=prior_status, new_status=row.status if row else None,
        revision=row.revision if row else None, original_admin_user_id=original_admin_id))


def refuse(db, actor, action, code, message, row=None, *, original_admin_id=None):
    event(db, actor, action, "denied", row, original_admin_id=original_admin_id)
    db.commit()
    raise HTTPException(code, message, headers={"Cache-Control": "no-store"})


def eligible(db, user):
    return bool(user and user.is_active and user.email_verified_at and (
        user.role in {"practitioner", "admin"} or
        db.scalar(select(UserCapability.id).where(UserCapability.user_id == user.id,
                  UserCapability.capability == "practitioner")) is not None or
        is_platform_admin_user(db, user)))


def managed_clinic(db, actor, clinic_id, action):
    if not eligible(db, actor):
        refuse(db, actor, action, 403, "An active, verified practitioner account is required.")
    clinic = db.get(Clinic, clinic_id)
    organisation = db.get(Organisation, clinic.organisation_id) if clinic else None
    if not clinic or not organisation or not (organisation.created_by_user_id == actor.id or is_platform_admin_user(db, actor)):
        refuse(db, actor, action, 404, "Clinic not found.")
    return clinic


def view(db, row):
    principal = db.get(User, row.practitioner_user_id)
    return dict(id=str(row.id), clinic_id=str(row.clinic_id),
        practitioner_user_id=str(row.practitioner_user_id), full_name=principal.full_name,
        status=row.status, revision=row.revision, clinical_approval="not_granted",
        clinical_access=False, seat_status="unassigned")


def list_memberships(db, actor, clinic_id):
    clinic = managed_clinic(db, actor, clinic_id, "membership.list")
    rows = list(db.scalars(select(ClinicMembership).where(ClinicMembership.clinic_id == clinic_id).order_by(ClinicMembership.id)))
    event(db, actor, "membership.list", "allowed")
    db.commit()
    return dict(clinic_id=str(clinic.id), clinic_name=clinic.display_name,
                items=[view(db, row) for row in rows])


def create_membership(db, actor, clinic_id, principal_id, request_key):
    try:
        # Actor lock serializes retries across clinics; clinic lock serializes adds
        # to the same clinic even when different administrators issue them.
        # NO KEY UPDATE still serializes this actor's retries but permits the
        # foreign-key key-share lock when two administrators add each other.
        db.execute(select(User.id).where(User.id == actor.id).with_for_update(key_share=True)).scalar_one()
        managed_clinic(db, actor, clinic_id, "membership.create")
        db.execute(select(Clinic.id).where(Clinic.id == clinic_id).with_for_update()).scalar_one()
        existing = db.scalar(select(ClinicMembership).where(
            ClinicMembership.created_by_user_id == actor.id, ClinicMembership.request_key == request_key))
        if existing:
            if existing.clinic_id != clinic_id or existing.practitioner_user_id != principal_id:
                refuse(db, actor, "membership.create", 409, "Creation key already used with different input.")
            result = view(db, existing)
            db.commit()
            return result, False
        target = db.get(User, principal_id)
        if not eligible(db, target):
            refuse(db, actor, "membership.create", 404, "That practitioner cannot be added.")
        if db.scalar(select(ClinicMembership.id).where(ClinicMembership.clinic_id == clinic_id,
                            ClinicMembership.practitioner_user_id == principal_id)) is not None:
            refuse(db, actor, "membership.create", 409, "This practitioner already has a membership. Update its status instead.")
        row = ClinicMembership(clinic_id=clinic_id, practitioner_user_id=principal_id,
                              created_by_user_id=actor.id, request_key=request_key)
        db.add(row); db.flush()
        event(db, actor, "membership.created", "success", row)
        result = view(db, row)
        db.commit()
        return result, True
    except Exception:
        db.rollback()
        raise


def transition(db, actor, clinic_id, membership_id, desired_status, revision):
    try:
        managed_clinic(db, actor, clinic_id, "membership.transition")
        row = db.scalar(select(ClinicMembership).where(ClinicMembership.id == membership_id,
            ClinicMembership.clinic_id == clinic_id).with_for_update().execution_options(populate_existing=True))
        if row is None:
            refuse(db, actor, "membership.transition", 404, "Membership not found.")
        if row.status == desired_status:
            result = view(db, row)
            db.commit()
            return result
        if row.revision != revision:
            refuse(db, actor, "membership.transition", 409, "Membership changed. Reload before trying again.", row)
        prior = row.status
        row.status = desired_status; row.revision += 1
        db.flush()
        event(db, actor, "membership.status_changed", "success", row, prior_status=prior)
        result = view(db, row)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@dataclass(frozen=True)
class MembershipDecision:
    allowed: bool
    reason: str
    membership_id: UUID | None = None
    revision: int | None = None
    policy_revision: str = POLICY_REVISION
    clinical_access: bool = False


def membership_decision(db, principal_id, clinic_id, action="membership.context"):
    """Fresh database decision, not a cached claim or a clinical access grant.

    Call at execution time. Caller owns its transaction and audit; this query
    never commits a caller's business work. Only the published action is allowed.
    """
    actor = db.get(User, principal_id, populate_existing=True)
    row = db.scalar(select(ClinicMembership).where(ClinicMembership.clinic_id == clinic_id,
        ClinicMembership.practitioner_user_id == principal_id).execution_options(populate_existing=True))
    if not eligible(db, actor) or row is None:
        return MembershipDecision(False, "membership_unavailable")
    if row.status != "active":
        return MembershipDecision(False, "membership_inactive", row.id, row.revision)
    if action != "membership.context":
        return MembershipDecision(False, "clinical_or_unknown_action_not_granted", row.id, row.revision)
    return MembershipDecision(True, "active_membership", row.id, row.revision)
