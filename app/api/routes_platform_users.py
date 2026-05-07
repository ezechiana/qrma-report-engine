from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_platform_admin
from app.db.models import User
from app.services.platform_settings_service import is_platform_admin_email

router = APIRouter(tags=["platform-users"])


class UserEmailPayload(BaseModel):
    email: EmailStr


def _ensure_audit_table(db: Session) -> None:
    """Create a lightweight admin audit log table if migrations have not done so yet."""
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS platform_admin_audit_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                actor_user_id UUID NULL,
                actor_email TEXT NULL,
                action TEXT NOT NULL,
                target_user_id UUID NULL,
                target_email TEXT NULL,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_platform_admin_audit_created_at
            ON platform_admin_audit_log (created_at DESC)
            """
        )
    )
    db.commit()


def _audit(
    db: Session,
    *,
    actor: User,
    action: str,
    target: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    _ensure_audit_table(db)
    db.execute(
        text(
            """
            INSERT INTO platform_admin_audit_log (
                actor_user_id,
                actor_email,
                action,
                target_user_id,
                target_email,
                details,
                created_at
            ) VALUES (
                :actor_user_id,
                :actor_email,
                :action,
                :target_user_id,
                :target_email,
                CAST(:details AS JSONB),
                NOW()
            )
            """
        ),
        {
            "actor_user_id": str(actor.id) if getattr(actor, "id", None) else None,
            "actor_email": getattr(actor, "email", None),
            "action": action,
            "target_user_id": str(target.get("id")) if target and target.get("id") else None,
            "target_email": target.get("email") if target else None,
            "details": json.dumps(details or {}),
        },
    )
    db.commit()


def _user_by_email(db: Session, email: str) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, email, full_name, role, is_active, created_at, updated_at
            FROM users
            WHERE LOWER(email) = LOWER(:email)
            LIMIT 1
            """
        ),
        {"email": email},
    ).mappings().first()
    return dict(row) if row else None


def _admin_source(db_role: str, allowlist_admin: bool) -> str | None:
    db_admin = db_role == "admin"
    if db_admin and allowlist_admin:
        return "db_role+allowlist"
    if db_admin:
        return "db_role"
    if allowlist_admin:
        return "allowlist"
    return None


def _normalise_user(db: Session, row: dict[str, Any]) -> dict[str, Any]:
    db_role = str(row.get("role") or "practitioner").lower()
    email = row.get("email")
    allowlist_admin = is_platform_admin_email(email, db=db)
    source = _admin_source(db_role, allowlist_admin)
    effective_role = "admin" if source else "practitioner"
    return {
        "id": str(row.get("id")) if row.get("id") else None,
        "email": email,
        "full_name": row.get("full_name"),
        "role": db_role,
        "effective_role": effective_role,
        "is_platform_admin": effective_role == "admin",
        "admin_source": source,
        "is_active": bool(row.get("is_active")),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }


def _all_users(db: Session, *, q: str = "", limit: int = 500) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    where = ""
    if q.strip():
        where = "WHERE email ILIKE :search OR full_name ILIKE :search"
        params["search"] = f"%{q.strip()}%"
    rows = db.execute(
        text(
            f"""
            SELECT id, email, full_name, role, is_active, created_at, updated_at
            FROM users
            {where}
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    return [_normalise_user(db, dict(row)) for row in rows]


def _active_admin_count(db: Session) -> int:
    users = _all_users(db, limit=5000)
    return sum(1 for user in users if user.get("is_active") and user.get("effective_role") == "admin")


def _summary(users: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(users),
        "active": sum(1 for user in users if user.get("is_active")),
        "inactive": sum(1 for user in users if not user.get("is_active")),
        "admins": sum(1 for user in users if user.get("effective_role") == "admin"),
        "practitioners": sum(1 for user in users if user.get("effective_role") != "admin"),
        "allowlist_admins": sum(1 for user in users if user.get("admin_source") in {"allowlist", "db_role+allowlist"}),
    }


@router.get("/api/platform-users")
def list_platform_users(
    q: str = Query("", description="Optional email/name search"),
    role: str = Query("all", pattern="^(all|admin|practitioner)$"),
    status: str = Query("all", pattern="^(all|active|inactive)$"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    all_matching = _all_users(db, q=q, limit=500)
    filtered = all_matching
    if role != "all":
        filtered = [u for u in filtered if u.get("effective_role") == role]
    if status == "active":
        filtered = [u for u in filtered if u.get("is_active")]
    elif status == "inactive":
        filtered = [u for u in filtered if not u.get("is_active")]
    return {"items": filtered[:limit], "summary": _summary(all_matching)}


@router.get("/api/platform-users/audit-log")
def platform_audit_log(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    _ensure_audit_table(db)
    rows = db.execute(
        text(
            """
            SELECT id, actor_email, action, target_email, details, created_at
            FROM platform_admin_audit_log
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return {
        "items": [
            {
                "id": str(row.get("id")),
                "actor_email": row.get("actor_email"),
                "action": row.get("action"),
                "target_email": row.get("target_email"),
                "details": row.get("details") or {},
                "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
            }
            for row in rows
        ]
    }


def _set_user_state(db: Session, *, email: str, role: str | None = None, is_active: bool | None = None) -> dict[str, Any]:
    existing = _user_by_email(db, email)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found.")

    if role is not None:
        db.execute(
            text("UPDATE users SET role = :role, updated_at = NOW() WHERE id = :id"),
            {"role": role, "id": existing["id"]},
        )
    if is_active is not None:
        db.execute(
            text("UPDATE users SET is_active = :is_active, updated_at = NOW() WHERE id = :id"),
            {"is_active": is_active, "id": existing["id"]},
        )
    db.commit()
    updated = _user_by_email(db, email)
    return _normalise_user(db, updated)


@router.post("/api/platform-users/promote")
def promote_user_to_admin(
    payload: UserEmailPayload,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    existing = _user_by_email(db, str(payload.email))
    if not existing:
        raise HTTPException(status_code=404, detail="User not found.")
    before = _normalise_user(db, existing)
    user = _set_user_state(db, email=str(payload.email), role="admin")
    _audit(db, actor=admin, action="admin_promoted_user", target=user, details={"previous_role": before.get("role"), "new_role": "admin", "admin_source": user.get("admin_source")})
    return {"message": "User promoted to platform admin.", "user": user}


@router.post("/api/platform-users/demote")
def demote_user_to_practitioner(
    payload: UserEmailPayload,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    email = str(payload.email)
    if email.lower() == str(getattr(admin, "email", "")).lower():
        raise HTTPException(status_code=400, detail="You cannot demote your own active admin account from this screen.")
    existing = _user_by_email(db, email)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found.")
    before = _normalise_user(db, existing)
    if before.get("effective_role") == "admin" and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last active platform admin.")
    if before.get("admin_source") == "allowlist":
        raise HTTPException(status_code=400, detail="This user is a platform admin through the Admin Emails allowlist. Remove the email from Platform access settings to remove admin access.")
    user = _set_user_state(db, email=email, role="practitioner")
    detail_message = "User demoted to practitioner."
    if user.get("is_platform_admin"):
        detail_message = "Database role demoted, but the user still has platform admin access through the Admin Emails allowlist."
    _audit(db, actor=admin, action="admin_demoted_user", target=user, details={"previous_role": before.get("role"), "new_role": "practitioner", "effective_role_after": user.get("effective_role"), "admin_source_after": user.get("admin_source")})
    return {"message": detail_message, "user": user}


@router.post("/api/platform-users/deactivate")
def deactivate_user(
    payload: UserEmailPayload,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    email = str(payload.email)
    if email.lower() == str(getattr(admin, "email", "")).lower():
        raise HTTPException(status_code=400, detail="You cannot deactivate your own active admin account.")
    existing = _user_by_email(db, email)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found.")
    before = _normalise_user(db, existing)
    if before.get("effective_role") == "admin" and before.get("is_active") and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot deactivate the last active platform admin.")
    user = _set_user_state(db, email=email, is_active=False)
    _audit(db, actor=admin, action="admin_deactivated_user", target=user, details={"was_active": before.get("is_active"), "is_active": False, "admin_source": before.get("admin_source")})
    return {"message": "User deactivated.", "user": user}


@router.post("/api/platform-users/reactivate")
def reactivate_user(
    payload: UserEmailPayload,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    existing = _user_by_email(db, str(payload.email))
    if not existing:
        raise HTTPException(status_code=404, detail="User not found.")
    before = _normalise_user(db, existing)
    user = _set_user_state(db, email=str(payload.email), is_active=True)
    _audit(db, actor=admin, action="admin_reactivated_user", target=user, details={"was_active": before.get("is_active"), "is_active": True, "admin_source": user.get("admin_source")})
    return {"message": "User reactivated.", "user": user}
