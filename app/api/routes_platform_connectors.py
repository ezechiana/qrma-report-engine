"""DEV-only platform administrator connector projections; no fleet mutations."""
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_db, require_platform_admin
from app.api.routes_connector import enabled
from app.services import connector_audit as audit, connector_observability as service
from app.services.connector_limits import require
from app.services.connector_privacy import ConnectorRoute


def telemetry_enabled():
    if not audit.enabled():
        raise HTTPException(404)


def administrator(user=Depends(require_platform_admin)):
    from app.utils.security import JWT_SECRET_KEY
    if not user.is_active:
        raise HTTPException(403, 'User account is inactive.')
    if not JWT_SECRET_KEY or JWT_SECRET_KEY == 'change-me-in-production':
        raise HTTPException(503, 'Configure application authentication before enabling connector monitoring.')
    return user


router = APIRouter(tags=['platform-connectors'], route_class=ConnectorRoute,
                   dependencies=[Depends(enabled), Depends(telemetry_enabled)])


def access(db, request, user, action):
    require('admin-read', str(user.id), 60)
    audit.record(db, action, actor_id=user.id, actor_type='admin', observation=audit.context(request))
    # Query completed first. Required access audit must persist before data leaves.
    db.commit()


@router.get('/api/platform/connectors')
def inventory(request: Request, user=Depends(administrator), db=Depends(get_db),
              limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None, max_length=256),
              owner_id: uuid.UUID | None = None,
              status: Literal['revoked', 'expired', 'owner_inactive', 'unknown', 'online', 'stale'] | None = None,
              version: str | None = Query(None, min_length=1, max_length=64, pattern=r'^[0-9][0-9A-Za-z.+_-]*$')):
    result = service.inventory(db, limit=limit, cursor=cursor, owner_id=owner_id, status=status, version=version)
    access(db, request, user, 'inventory_read')
    return result


@router.get('/api/platform/connectors/stats')
def stats(request: Request, user=Depends(administrator), db=Depends(get_db),
          window_hours: int = Query(24, ge=1, le=2160)):
    result = service.stats(db, window_hours)
    access(db, request, user, 'stats_read')
    return result


@router.get('/api/platform/connector-events')
def events(request: Request, user=Depends(administrator), db=Depends(get_db),
           limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None, max_length=256),
           connector_id: uuid.UUID | None = None):
    result = service.events(db, limit=limit, cursor=cursor, connector_id=connector_id)
    access(db, request, user, 'events_read')
    return result
