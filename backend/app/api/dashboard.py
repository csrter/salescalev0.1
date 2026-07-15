"""Phase 4: persisted dashboard layouts.

A layout belongs to one (user, client view) pair — every user, including
client-role users, can arrange their own dashboard for any client view
they're allowed to see. Reading another user's layout is not a thing; the
row is always looked up by the authenticated user's id, so there is no
user_id parameter to tamper with.
"""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from pydantic import BaseModel

from ..db import get_db
from ..deps import TenantScope, get_scope
from ..models.core import Client
from ..models.dashboard import CrmListPreference, DashboardLayout
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


# --- Dashboard timeframe + account/campaign filter (same row as widgets) ---


class DashboardFiltersIn(BaseModel):
    preset: str | None = None  # "today" | "7d" | "30d" | "90d" | "custom"
    since: dt.date | None = None
    until: dt.date | None = None
    account_ids: list[str] = []
    campaign_ids: list[str] = []


@router.get("/filters")
def get_filters(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """The saved timeframe + account/campaign selection for this user's view
    of this client's dashboard. null = no saved choice (role default: last
    30 days, every connected account)."""
    _client_for(db, scope, client_id)
    row = _layout_row(db, scope, client_id)
    return {"client_id": client_id, "filters": row.filters if row else None}


@router.put("/filters")
def save_filters(
    client_id: str,
    body: DashboardFiltersIn,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    _client_for(db, scope, client_id)
    filters = body.model_dump(mode="json")
    row = _layout_row(db, scope, client_id)
    if row is None:
        row = DashboardLayout(
            organization_id=scope.organization_id,
            user_id=scope.user.id,
            client_id=client_id,
            filters=filters,
        )
        db.add(row)
    else:
        row.filters = filters
    db.commit()
    return {"client_id": client_id, "filters": row.filters}


# --- Phase 14: CRM lead-list column choice (same per-user preference pattern) ---


class CrmColumnsIn(BaseModel):
    columns: list[str]


def _cols_row(
    db: Session, scope: TenantScope, client_id: str
) -> CrmListPreference | None:
    return db.execute(
        select(CrmListPreference).where(
            CrmListPreference.organization_id == scope.organization_id,
            CrmListPreference.user_id == scope.user.id,
            CrmListPreference.client_id == client_id,
        )
    ).scalar_one_or_none()


@router.get("/crm-columns")
def get_crm_columns(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """The custom-field column keys this user has chosen to show in the lead
    list for this client view. null = no saved choice (show none by default)."""
    _client_for(db, scope, client_id)
    row = _cols_row(db, scope, client_id)
    return {"client_id": client_id, "columns": row.columns if row else None}


@router.put("/crm-columns")
def save_crm_columns(
    client_id: str,
    body: CrmColumnsIn,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    _client_for(db, scope, client_id)
    columns = [str(c) for c in body.columns]
    row = _cols_row(db, scope, client_id)
    if row is None:
        row = CrmListPreference(
            organization_id=scope.organization_id,
            user_id=scope.user.id,
            client_id=client_id,
            columns=columns,
        )
        db.add(row)
    else:
        row.columns = columns
    db.commit()
    return {"client_id": client_id, "columns": row.columns}
