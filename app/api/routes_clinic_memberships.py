from uuid import UUID
from dataclasses import asdict
from typing import Literal
from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.api.deps import CurrentActiveUser, CurrentDB
from app.db.models import ClinicMembership, Clinic
from app.services import clinic_membership as service
from app.services.auth_service import restore_admin_from_token
from app.api.routes_clinics import RegistryPageRoute, templates

router = APIRouter(tags=["clinic-memberships"])
pages_router = APIRouter(route_class=RegistryPageRoute)


class MembershipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    practitioner_user_id: UUID


class MembershipTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "suspended", "revoked"]
    revision: int = Field(ge=0, strict=True)


def refuse_support(request, db, actor, expected_actor):
    if actor.id != expected_actor:
        service.refuse(db, actor, "membership.write", 403, "Your account changed. Reload before continuing.")
    token = request.cookies.get("admin_restore_token")
    if token:
        original = restore_admin_from_token(db, token)
        service.refuse(db, actor, "membership.write", 403,
            "Membership editing is unavailable in support mode.", original_admin_id=original.id)


@router.get("/api/clinics/{clinic_id}/memberships")
def read_members(clinic_id: UUID, request: Request, response: Response, db: CurrentDB, actor: CurrentActiveUser):
    response.headers["Cache-Control"] = "no-store"
    result = service.list_memberships(db, actor, clinic_id)
    result["can_edit"] = not bool(request.cookies.get("admin_restore_token"))
    return result


@router.post("/api/clinics/{clinic_id}/memberships")
def add_member(clinic_id: UUID, payload: MembershipCreate, request: Request, response: Response,
               db: CurrentDB, actor: CurrentActiveUser, idempotency_key: UUID = Header(),
               x_expected_actor: UUID = Header()):
    refuse_support(request, db, actor, x_expected_actor)
    result, created = service.create_membership(db, actor, clinic_id,
        payload.practitioner_user_id, str(idempotency_key))
    response.status_code = 201 if created else 200
    response.headers["Cache-Control"] = "no-store"
    return result


@router.patch("/api/clinics/{clinic_id}/memberships/{membership_id}")
def change_member(clinic_id: UUID, membership_id: UUID, payload: MembershipTransition,
                  request: Request, response: Response, db: CurrentDB, actor: CurrentActiveUser,
                  x_expected_actor: UUID = Header()):
    refuse_support(request, db, actor, x_expected_actor)
    response.headers["Cache-Control"] = "no-store"
    return service.transition(db, actor, clinic_id, membership_id, payload.status, payload.revision)


@router.get("/api/practitioner/memberships/me")
def own_memberships(response: Response, db: CurrentDB, actor: CurrentActiveUser):
    if not service.eligible(db, actor):
        service.refuse(db, actor, "membership.own_list", 403, "Practitioner access required.")
    rows = list(db.scalars(select(ClinicMembership).where(
        ClinicMembership.practitioner_user_id == actor.id).order_by(ClinicMembership.id)))
    result = [{**service.view(db, row), "clinic_name": db.get(Clinic, row.clinic_id).display_name} for row in rows]
    service.event(db, actor, "membership.own_list", "allowed")
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"items": result, "can_edit": False}


@router.get("/api/clinics/{clinic_id}/membership-context")
def membership_context(clinic_id: UUID, response: Response, db: CurrentDB, actor: CurrentActiveUser):
    decision = service.membership_decision(db, actor.id, clinic_id)
    row = db.get(ClinicMembership, decision.membership_id) if decision.membership_id else None
    if not decision.allowed:
        service.refuse(db, actor, "membership.context", 403, "An active clinic membership is required.", row)
    service.event(db, actor, "membership.context", "allowed", row)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return asdict(decision)


@pages_router.get("/app/clinics/{clinic_id}/members")
def membership_page(clinic_id: UUID, request: Request, db: CurrentDB, actor: CurrentActiveUser):
    clinic = service.managed_clinic(db, actor, clinic_id, "membership.page")
    return templates.TemplateResponse(request=request, name="clinic_memberships.html",
        context={"request": request, "title": "Clinic members", "clinic_id": str(clinic.id),
                 "clinic_name": clinic.display_name, "own_view": False}, headers={"Cache-Control":"no-store"})


@pages_router.get("/app/memberships")
def own_memberships_page(request: Request, db: CurrentDB, actor: CurrentActiveUser):
    if not service.eligible(db, actor):
        service.refuse(db, actor, "membership.page", 403, "Practitioner access required.")
    return templates.TemplateResponse(request=request, name="clinic_memberships.html",
        context={"request": request, "title": "My clinic memberships", "clinic_id": None,
                 "clinic_name": None, "own_view": True}, headers={"Cache-Control":"no-store"})


router.include_router(pages_router)
