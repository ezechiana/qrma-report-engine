"""Administrative registry API; organisation ownership is not clinical access."""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, Request, HTTPException
from fastapi.routing import APIRoute
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import quote
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.services.clinic_registry import authorize, create_registry_entry, list_clinics, list_organisations

api_router = APIRouter(prefix="/api/organisations", tags=["clinic-registry"])
templates = Jinja2Templates(directory="app/templates")


class RegistryPageRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def page(request: Request):
            try:
                return await handler(request)
            except HTTPException as exc:
                if exc.status_code not in {401, 403}:
                    raise
                # Main's existing navigation middleware redirects HTML 401 to
                # login. Standalone routers still receive a useful 401 page.
                return templates.TemplateResponse(request=request, name="clinic_registry_access.html",
                    context={"request": request, "title": "Organisations & clinics", "signed_out": exc.status_code == 401,
                             "login_url": "/login?next=" + quote(request.url.path + ("?" + request.url.query if request.url.query else ""), safe="")},
                    status_code=exc.status_code, headers={"Cache-Control": "no-store"})
        return page


pages_router = APIRouter(route_class=RegistryPageRoute)


@pages_router.api_route("/app/organisations", methods=["GET", "HEAD"], response_class=HTMLResponse)
def registry_page(request: Request, actor: User = Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="clinic_registry.html",
        context={"request": request, "title": "Organisations & clinics"}, headers={"Cache-Control": "no-store"})


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


@api_router.get("/access")
def registry_access(response: Response, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    """Fresh navigation eligibility without returning organisation data."""
    admin = authorize(db, actor, "registry.access")
    response.headers["Cache-Control"] = "no-store"
    return {"can_manage": True, "is_platform_admin": admin}


@api_router.get("", response_model=list[OrganisationView])
def read_organisations(response: Response, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return list_organisations(db, actor)


@api_router.post("", response_model=OrganisationView)
def create_organisation(payload: RegistryCreate, response: Response,
                        idempotency_key: UUID = Header(), db: Session = Depends(get_db),
                        actor: User = Depends(get_current_user)):
    row, created = create_registry_entry(db, actor, display_name=payload.display_name,
                                        request_key=str(idempotency_key))
    response.status_code = 201 if created else 200
    return row


@api_router.post("/{organisation_id}/clinics", response_model=ClinicView)
def create_clinic(organisation_id: UUID, payload: RegistryCreate, response: Response,
                  idempotency_key: UUID = Header(), db: Session = Depends(get_db),
                  actor: User = Depends(get_current_user)):
    row, created = create_registry_entry(db, actor, display_name=payload.display_name,
                                        request_key=str(idempotency_key), organisation_id=organisation_id)
    response.status_code = 201 if created else 200
    return row


@api_router.get("/{organisation_id}/clinics", response_model=list[ClinicView])
def read_clinics(organisation_id: UUID, response: Response, db: Session = Depends(get_db),
                 actor: User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return list_clinics(db, actor, organisation_id)


router = APIRouter()
router.include_router(api_router)
router.include_router(pages_router)
