"""CLINIC-owned administrative registry. No memberships or clinical grants."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Clinic, ClinicRegistryEvent, Organisation, User, UserCapability
from app.services.platform_settings_service import is_platform_admin_user


def event(db, actor, action, outcome, organisation_id=None, clinic_id=None):
    db.add(ClinicRegistryEvent(actor_user_id=actor.id, action=action, outcome=outcome,
                              organisation_id=organisation_id, clinic_id=clinic_id))


def refuse(db, actor, action, code, detail):
    # No resource or request payload is copied to denial evidence.
    event(db, actor, action, "denied")
    db.commit()
    raise HTTPException(code, detail)


def authorize(db: Session, actor: User, action: str):
    if not actor.is_active or not actor.email_verified_at:
        refuse(db, actor, action, 403, "An active, verified account is required.")
    admin = is_platform_admin_user(db, actor)
    practitioner = actor.role == "practitioner" or db.scalar(select(UserCapability.id).where(
        UserCapability.user_id == actor.id, UserCapability.capability == "practitioner")) is not None
    if not (admin or practitioner):
        refuse(db, actor, action, 403, "Clinic administration access required.")
    return admin


def organisation_for_actor(db, actor, organisation_id, action, admin):
    row = db.get(Organisation, organisation_id)
    if row is None or (not admin and row.created_by_user_id != actor.id):
        refuse(db, actor, action, 404, "Organisation not found.")
    return row


def create_registry_entry(db: Session, actor: User, *, display_name: str, request_key: str,
                          organisation_id: UUID | None = None):
    action = "clinic.create" if organisation_id else "organisation.create"
    try:
        # Serialize provisioning by actor, including concurrent retries. Unique
        # constraints remain a second line of defence. Lock lives until commit.
        db.execute(select(User.id).where(User.id == actor.id).with_for_update()).scalar_one()
        admin = authorize(db, actor, action)
        if organisation_id:
            organisation_for_actor(db, actor, organisation_id, action, admin)
        model = Clinic if organisation_id else Organisation
        existing = db.scalar(select(model).where(model.created_by_user_id == actor.id,
                                                  model.request_key == request_key))
        if existing:
            if existing.display_name != display_name or (organisation_id and existing.organisation_id != organisation_id):
                refuse(db, actor, action, 409, "Creation key already used with different input.")
            row, outcome = existing, "replayed"
        else:
            fields = dict(display_name=display_name, request_key=request_key, created_by_user_id=actor.id)
            if organisation_id:
                fields["organisation_id"] = organisation_id
            row = model(**fields)
            db.add(row)
            db.flush()
            outcome = "created"
        event(db, actor, action, outcome,
              organisation_id=organisation_id or row.id,
              clinic_id=row.id if organisation_id else None)
        db.commit()
        db.refresh(row)
        return row, outcome == "created"
    except Exception:
        db.rollback()
        raise


def list_clinics(db: Session, actor: User, organisation_id: UUID):
    admin = authorize(db, actor, "clinic.list")
    organisation_for_actor(db, actor, organisation_id, "clinic.list", admin)
    rows = list(db.scalars(select(Clinic).where(Clinic.organisation_id == organisation_id).order_by(Clinic.id)))
    event(db, actor, "clinic.list", "allowed", organisation_id=organisation_id)
    db.commit()
    return rows


def list_organisations(db: Session, actor: User):
    admin = authorize(db, actor, "organisation.list")
    query = select(Organisation).order_by(Organisation.id)
    if not admin:
        query = query.where(Organisation.created_by_user_id == actor.id)
    rows = list(db.scalars(query))
    event(db, actor, "organisation.list", "allowed")
    db.commit()
    return rows
