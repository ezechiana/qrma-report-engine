from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.api.deps import CurrentActiveUser, CurrentDB
from app.services import practitioner_profile as service
from app.services.auth_service import restore_admin_from_token

router = APIRouter(prefix="/api/practitioner/profile", tags=["practitioner-profile"])


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=0, strict=True)
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA timezone, for example Europe/London.")
        return value

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value):
        return value or None


def support_actor(request, db):
    token = request.cookies.get("admin_restore_token")
    if not token:
        return None
    # Invalid restore context also fails closed for writes.
    return str(restore_admin_from_token(db, token).id)


@router.get("/me")
def read_profile(request: Request, db: CurrentDB, actor: CurrentActiveUser):
    service.authorize(db, actor)
    original = support_actor(request, db)
    service.event(db, actor, "profile.read", "success", original_admin_id=original)
    db.commit()
    return service.payload(actor, read_only=bool(request.cookies.get("admin_restore_token")))


@router.patch("/me")
def write_profile(command: ProfileUpdate, request: Request, db: CurrentDB, actor: CurrentActiveUser):
    service.authorize(db, actor)
    if request.cookies.get("admin_restore_token"):
        original = support_actor(request, db)
        service.refuse(db, actor, "Profile editing is unavailable in support mode.", original_admin_id=original)
    return service.update_profile(db, actor, command)
