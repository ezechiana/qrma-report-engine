"""Provider subjects identify accounts. Email matching never grants a binding."""
from secrets import token_urlsafe
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.db.models import ExternalIdentity, User, AuditLog, RecommendationMode
from app.utils.security import hash_password
from app.services.personal_record_service import ensure_personal_record
from app.services.user_capability_service import grant_capability, PRACTITIONER


def resolve_social_user(db, *, provider, provider_subject, email, full_name=None):
    provider = (provider or "").strip().lower()
    subject = (provider_subject or "").strip()
    email = (email or "").strip().lower()
    if provider not in {"google", "microsoft"} or not subject or len(subject) > 255 or not email:
        raise HTTPException(400, "A verified provider identity is required.")
    for attempt in range(3):
        try:
            binding = db.scalar(select(ExternalIdentity).where(
                ExternalIdentity.provider == provider, ExternalIdentity.subject == subject))
            if binding:
                user = db.get(User, binding.user_id)
                if not user or not user.is_active:
                    raise HTTPException(403, "User account is inactive.")
                user.last_login_at = datetime.now(timezone.utc)
                db.commit()
                return user
            if db.scalar(select(User.id).where(User.email == email)) is not None:
                raise HTTPException(409, "Sign in with your existing account. This provider is not linked.")
            now = datetime.now(timezone.utc)
            user = User(email=email, password_hash=hash_password(token_urlsafe(48)),
                full_name=(full_name or email.split("@")[0]).strip()[:255],
                role="practitioner", is_active=True, email_verified_at=now,
                last_login_at=now, timezone="Europe/London", support_email=email,
                recommendation_mode_default=RecommendationMode.natural_approaches_clinical)
            db.add(user)
            db.flush()
            db.add(ExternalIdentity(provider=provider, subject=subject, user_id=user.id))
            ensure_personal_record(db, user, commit=False)
            grant_capability(db, user, PRACTITIONER, commit=False)
            db.add(AuditLog(user_id=user.id, action="identity.created", actor_type="user",
                           outcome="success", metadata_json={"provider": provider}))
            db.commit()
            return user
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise HTTPException(409, "Sign-in conflicted with another request. Please retry.")
        except Exception:
            db.rollback()
            raise
