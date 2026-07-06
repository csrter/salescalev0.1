"""Phase 4: persisted dashboard layouts.

A layout belongs to one (user, client view) pair — every user, including
client-role users, can arrange their own dashboard for any client view
they're allowed to see. Reading another user's layout is not a thing; the
row is always looked up by the authenticated user's id, so there is no
user_id parameter to tamper with.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope
from ..models.core import Client
from ..models.dashboard import DashboardLayout
from ..schemas import DashboardLayoutIn

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _client_for(db: Session, scope: TenantScope, client_id: str) -> Client:
    scope.check_client_id(client_id)
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Not found")
    scope.check_organization_id(client.organization_id)
    return client


def _layout_row(
    db: Session, scope: TenantScope, client_id: str
) -> DashboardLayout | None:
    return db.execute(
        select(DashboardLayout).where(
            DashboardLayout.organization_id == scope.organization_id,
            DashboardLayout.user_id == scope.user.id,
            DashboardLayout.client_id == client_id,
        )
    ).scalar_one_or_none()


@router.get("/layout")
def get_layout(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    _client_for(db, scope, client_id)
    row = _layout_row(db, scope, client_id)
    # widgets: null tells the frontend "no saved layout — use the role
    # default", which is different from a deliberately emptied dashboard.
    return {"client_id": client_id, "widgets": row.widgets if row else None}


@router.put("/layout")
def save_layout(
    client_id: str,
    body: DashboardLayoutIn,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    _client_for(db, scope, client_id)
    widgets = [w.model_dump() for w in body.widgets]
    row = _layout_row(db, scope, client_id)
    if row is None:
        row = DashboardLayout(
            organization_id=scope.organization_id,
            user_id=scope.user.id,
            client_id=client_id,
            widgets=widgets,
        )
        db.add(row)
    else:
        row.widgets = widgets
    db.commit()
    return {"client_id": client_id, "widgets": row.widgets}
