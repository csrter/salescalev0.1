from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin
from ..models.core import Client, PlatformConnection, User
from ..schemas import ClientCreate, ClientOutPublic, ClientOutTeam, ConnectionOut

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
