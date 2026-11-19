"""Administrative registry API; organisation ownership is not clinical access."""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.services.clinic_registry import create_registry_entry, list_clinics, list_organisations

router = APIRouter(prefix="/api/organisations", tags=["clinic-registry"])


class RegistryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=255)

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Enter a display name without control characters.")
        return value


class OrganisationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_name: str


class ClinicView(OrganisationView):
    organisation_id: UUID


@router.get("", response_model=list[OrganisationView])
def read_organisations(db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    return list_organisations(db, actor)


@router.post("", response_model=OrganisationView)
def create_organisation(payload: RegistryCreate, response: Response,
                        idempotency_key: UUID = Header(), db: Session = Depends(get_db),
                        actor: User = Depends(get_current_user)):
    row, created = create_registry_entry(db, actor, display_name=payload.display_name,
                                        request_key=str(idempotency_key))
    response.status_code = 201 if created else 200
    return row


@router.post("/{organisation_id}/clinics", response_model=ClinicView)
def create_clinic(organisation_id: UUID, payload: RegistryCreate, response: Response,
                  idempotency_key: UUID = Header(), db: Session = Depends(get_db),
                  actor: User = Depends(get_current_user)):
    row, created = create_registry_entry(db, actor, display_name=payload.display_name,
                                        request_key=str(idempotency_key), organisation_id=organisation_id)
    response.status_code = 201 if created else 200
    return row


@router.get("/{organisation_id}/clinics", response_model=list[ClinicView])
def read_clinics(organisation_id: UUID, db: Session = Depends(get_db),
                 actor: User = Depends(get_current_user)):
    return list_clinics(db, actor, organisation_id)
