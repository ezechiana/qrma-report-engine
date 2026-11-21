"""Self-service person profile; clinical authority and billing are separate."""
from fastapi import HTTPException
from sqlalchemy import select, update
from app.db.models import AuditLog, User, UserCapability
from app.services.platform_settings_service import is_platform_admin_user


def event(db, actor, action, outcome, **metadata):
    db.add(AuditLog(user_id=actor.id, action=action, actor_type="user",
                    outcome=outcome, metadata_json=metadata))


def refuse(db, actor, detail, *, original_admin_id=None):
    event(db, actor, "profile.update.refused", "refused",
          reason=detail, original_admin_id=original_admin_id)
    db.commit()
    raise HTTPException(403, detail)


def authorize(db, actor):
    capable = actor.role in {"practitioner", "admin"} or db.scalar(select(UserCapability.id).where(
        UserCapability.user_id == actor.id, UserCapability.capability == "practitioner")) is not None
    if not actor.is_active or not actor.email_verified_at or not (capable or is_platform_admin_user(db, actor)):
        refuse(db, actor, "An active, verified practitioner account is required.")


def payload(actor, *, read_only=False):
    return dict(id=str(actor.id), email=actor.email, full_name=actor.full_name,
                phone=actor.phone, timezone=actor.timezone,
                revision=actor.profile_revision, read_only=read_only)


def update_profile(db, actor, command):
    authorize(db, actor)
    values = command.model_dump(exclude={"revision"})
    # Conditional UPDATE is the concurrency boundary, rather than a stale ORM read.
    try:
        changed = db.execute(update(User).where(User.id == actor.id,
            User.profile_revision == command.revision).values(
                **values, profile_revision=User.profile_revision + 1),
            execution_options={"synchronize_session": False}).rowcount
        if not changed:
            db.rollback()
            current = db.get(User, actor.id, populate_existing=True)
            if all(getattr(current, k) == v for k, v in values.items()):
                return payload(current)
            event(db, actor, "profile.update.refused", "refused", reason="revision_conflict")
            db.commit()
            raise HTTPException(409, "Your profile changed. Reload before saving again.")
        event(db, actor, "profile.updated", "success", fields=sorted(values),
              revision=command.revision + 1)
        db.commit()
        db.refresh(actor)
        return payload(actor)
    except Exception:
        db.rollback()
        raise
