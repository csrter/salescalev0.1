from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin
from ..models.core import Client, PlatformConnection, User
from ..models.crm import LeadFormConfig
from ..schemas import (
    ClientCreate,
    ClientOutPublic,
    ClientOutTeam,
    ConnectionOut,
    ExternalSyncConfigIn,
    GuaranteeConfigIn,
    LeadFormConfigIn,
    LeadFormConfigOut,
)
from ..services.metrics import GUARANTEE_METRICS

router = APIRouter(prefix="/api/clients", tags=["clients"])


def _serialize_client(client: Client, scope: TenantScope):
    # Role decides the schema: internal fields only exist in the team shape.
    if scope.is_team:
        return ClientOutTeam.model_validate(client)
    return ClientOutPublic.model_validate(client)


@router.get("")
def list_clients(
    scope: TenantScope = Depends(get_scope), db: Session = Depends(get_db)
):
    stmt = select(Client).where(Client.organization_id == scope.organization_id)
    if not scope.is_team:
        stmt = stmt.where(Client.id == scope.client_id)
    clients = db.execute(stmt).scalars().all()
    return [_serialize_client(c, scope) for c in clients]


def _get_client_or_404(db: Session, scope: TenantScope, client_id: str) -> Client:
    # Client's own tenant keys are organization_id + its own id, so it can't
    # go through scope.get_or_404 (which reads obj.client_id).
    scope.check_client_id(client_id)
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Not found")
    scope.check_organization_id(client.organization_id)
    return client


@router.get("/{client_id}")
def get_client(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _get_client_or_404(db, scope, client_id)
    return _serialize_client(client, scope)


@router.post("", response_model=ClientOutTeam, status_code=201)
def create_client(
    body: ClientCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = Client(
        organization_id=user.organization_id,
        name=body.name,
        internal_notes=body.internal_notes,
    )
    db.add(client)
    db.commit()
    return ClientOutTeam.model_validate(client)


# Guarantee terms are client management (Organization-configured tenant
# data), not day-to-day campaign work — hence admin, not member.
@router.put("/{client_id}/guarantee")
def set_guarantee(
    client_id: str,
    body: GuaranteeConfigIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    if body.metric not in GUARANTEE_METRICS:
        raise HTTPException(
            400, f"metric must be one of {', '.join(sorted(GUARANTEE_METRICS))}"
        )
    config = body.model_dump()
    if config["start_date"] is not None:
        config["start_date"] = config["start_date"].isoformat()
    # Reassign (not mutate) so SQLAlchemy sees the JSON column change.
    client.metric_settings = {
        **(client.metric_settings or {}),
        "guarantee": config,
    }
    db.commit()
    return {"guarantee": config}


@router.delete("/{client_id}/guarantee", status_code=204)
def clear_guarantee(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    settings = dict(client.metric_settings or {})
    settings.pop("guarantee", None)
    client.metric_settings = settings
    db.commit()


@router.get("/{client_id}/guarantee")
def get_guarantee(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """The configured terms (not progress — that's /api/metrics/guarantee).
    Client-role readable: a client can see the guarantee they were sold."""
    client = _get_client_or_404(db, scope, client_id)
    return {"guarantee": (client.metric_settings or {}).get("guarantee")}


# --- Phase 6: native lead-form routing (admin — client setup, like the
# conversion configs) ---


@router.get("/{client_id}/lead-forms", response_model=List[LeadFormConfigOut])
def list_lead_form_configs(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    return (
        db.execute(select(LeadFormConfig).where(LeadFormConfig.client_id == client.id))
        .scalars()
        .all()
    )


@router.put("/{client_id}/lead-forms/{platform}", response_model=LeadFormConfigOut)
def set_lead_form_config(
    client_id: str,
    platform: str,
    body: LeadFormConfigIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if platform not in ("meta", "google"):
        raise HTTPException(400, "platform must be meta or google")
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    # The (platform, external_key) pair routes public webhooks, so a key
    # already claimed by any client (any tenant) can't be claimed again.
    clash = db.execute(
        select(LeadFormConfig).where(
            LeadFormConfig.platform == platform,
            LeadFormConfig.external_key == body.external_key,
            LeadFormConfig.client_id != client.id,
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(409, "This key is already routed to another client")
    config = db.execute(
        select(LeadFormConfig).where(
            LeadFormConfig.client_id == client.id,
            LeadFormConfig.platform == platform,
        )
    ).scalar_one_or_none()
    if config is None:
        config = LeadFormConfig(
            organization_id=client.organization_id,
            client_id=client.id,
            platform=platform,
            external_key=body.external_key,
            enabled=body.enabled,
        )
        db.add(config)
    else:
        config.external_key = body.external_key
        config.enabled = body.enabled
    db.commit()
    return config


# --- Phase 6: optional external CRM sync (admin, opt-in per client) ---


@router.get("/{client_id}/external-sync")
def get_external_sync(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    config = (client.metric_settings or {}).get("external_sync")
    if not config:
        return {"configured": False}
    # Never echo the shared secret back out.
    return {
        "configured": True,
        "enabled": bool(config.get("enabled")),
        "url": config.get("url"),
    }


@router.put("/{client_id}/external-sync")
def set_external_sync(
    client_id: str,
    body: ExternalSyncConfigIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    client.metric_settings = {
        **(client.metric_settings or {}),
        "external_sync": body.model_dump(),
    }
    db.commit()
    return {"configured": True, "enabled": body.enabled, "url": body.url}


@router.delete("/{client_id}/external-sync", status_code=204)
def clear_external_sync(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    settings = dict(client.metric_settings or {})
    settings.pop("external_sync", None)
    client.metric_settings = settings
    db.commit()


@router.get("/{client_id}/connections", response_model=List[ConnectionOut])
def list_connections(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    _get_client_or_404(db, scope, client_id)
    conns = (
        db.execute(
            select(PlatformConnection).where(
                PlatformConnection.client_id == client_id
            )
        )
        .scalars()
        .all()
    )
    return conns
