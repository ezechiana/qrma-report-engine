from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.schemas.settings import PractitionerSettingsRead, PractitionerSettingsUpdate
from app.services.settings_service import get_or_create_settings, update_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=PractitionerSettingsRead)
def read_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_or_create_settings(db, current_user)


@router.put("", response_model=PractitionerSettingsRead)
def save_settings(
    payload: PractitionerSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    settings = get_or_create_settings(db, current_user)
    return update_settings(db, settings, payload.model_dump(exclude_unset=True))