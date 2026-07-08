"""Platform super-admin surface — the ONE place that reads across every
Organization.

Everything here is Salescale-operator tooling, gated by `require_superadmin`
(the SUPERADMIN_EMAILS allowlist). It intentionally does NOT go through
TenantScope: these queries span all tenants by design. No tenant user can
reach any of it.
"""

import datetime as dt
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_superadmin
from ..pagination import Page, paginator
from ..models.core import (
    CONN_ACTIVE,
    ORG_ACTIVE,
    ORG_PLANS,
    ORG_SUSPENDED,
    Client,
    Organization,
    PlatformConnection,
    User,
)
from ..models.crm import Contact
from ..schemas import (
    AdminOrgDetail,
    AdminOrgRow,
    AdminOrgUpdate,
    AdminSignupPoint,
    AdminStats,
    PasswordResetResult,
    UserOut,
)
from ..security import hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _counts_by_org(db: Session, model) -> dict:
    return dict(
        db.execute(
            select(model.organization_id, func.count()).group_by(
                model.organization_id
            )
        ).all()
    )


@router.get("/stats", response_model=AdminStats)
def platform_stats(_: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    scalar = lambda stmt: db.execute(stmt).scalar_one()
    return AdminStats(
        organizations=scalar(select(func.count()).select_from(Organization)),
        users=scalar(select(func.count()).select_from(User)),
        clients=scalar(select(func.count()).select_from(Client)),
        active_connections=scalar(
            select(func.count())
            .select_from(PlatformConnection)
            .where(PlatformConnection.status == CONN_ACTIVE)
        ),
        signups_last_30d=scalar(
            select(func.count())
            .select_from(Organization)
            .where(Organization.created_at >= cutoff)
        ),
    )


@router.get("/signups", response_model=list[AdminSignupPoint])
def signups_series(
    days: int = 30,
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Signups per day for the last `days` days, zero-filled so the chart has a
    continuous x-axis even on days with no signups."""
    days = max(1, min(days, 365))
    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=days - 1)
    rows = db.execute(
        select(func.date(Organization.created_at), func.count())
        .where(Organization.created_at >= dt.datetime(start.year, start.month, start.day, tzinfo=dt.timezone.utc))
        .group_by(func.date(Organization.created_at))
    ).all()
    # func.date may return a date or an ISO string depending on backend.
    counts = {str(d): c for d, c in rows}
    return [
        AdminSignupPoint(
            date=(start + dt.timedelta(days=i)).isoformat(),
            count=counts.get((start + dt.timedelta(days=i)).isoformat(), 0),
        )
        for i in range(days)
    ]


@router.get("/organizations", response_model=list[AdminOrgRow])
def list_organizations(
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
    page: Page = paginator(default=50, maximum=200),
):
    # Aggregate each usage count once, then stitch onto the org list — avoids an
    # N+1 over every organization.
    users = _counts_by_org(db, User)
    clients = _counts_by_org(db, Client)
    conns = _counts_by_org(db, PlatformConnection)
    contacts = _counts_by_org(db, Contact)
    orgs = (
        db.execute(
            select(Organization)
            .order_by(Organization.created_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        .scalars()
        .all()
    )
    return [
        AdminOrgRow(
            id=o.id,
            name=o.name,
            created_at=o.created_at,
            status=o.status,
            plan=o.plan,
            user_count=users.get(o.id, 0),
            client_count=clients.get(o.id, 0),
            connection_count=conns.get(o.id, 0),
            contact_count=contacts.get(o.id, 0),
        )
        for o in orgs
    ]


@router.get("/organizations/{org_id}", response_model=AdminOrgDetail)
def organization_detail(
    org_id: str,
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(404, "Organization not found")
    users = (
        db.execute(
            select(User).where(User.organization_id == org_id).order_by(User.created_at)
        )
        .scalars()
        .all()
    )
    clients = (
        db.execute(
            select(Client)
            .where(Client.organization_id == org_id)
            .order_by(Client.created_at)
        )
        .scalars()
        .all()
    )
    return AdminOrgDetail(
        id=org.id,
        name=org.name,
        created_at=org.created_at,
        status=org.status,
        plan=org.plan,
        users=[UserOut.model_validate(u) for u in users],
        clients=[{"id": c.id, "name": c.name, "status": c.status} for c in clients],
    )


@router.patch("/organizations/{org_id}", response_model=AdminOrgDetail)
def update_organization(
    org_id: str,
    body: AdminOrgUpdate,
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Suspend/reactivate an org or change its plan. Suspending blocks login
    for all of the org's users (super-admins excepted)."""
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(404, "Organization not found")
    if body.status is not None:
        if body.status not in (ORG_ACTIVE, ORG_SUSPENDED):
            raise HTTPException(400, "status must be active or suspended")
        org.status = body.status
        org.suspended_at = (
            dt.datetime.now(dt.timezone.utc) if body.status == ORG_SUSPENDED else None
        )
    if body.plan is not None:
        if body.plan not in ORG_PLANS:
            raise HTTPException(400, f"plan must be one of {', '.join(ORG_PLANS)}")
        org.plan = body.plan
    db.commit()
    return organization_detail(org_id, _, db)


@router.post("/users/{user_id}/reset-password", response_model=PasswordResetResult)
def reset_user_password(
    user_id: str,
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Generate a new temporary password for any user and return it once. With
    no email delivery wired up, the operator relays it to the user out-of-band.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    temp = secrets.token_urlsafe(9)
    user.hashed_password = hash_password(temp)
    db.commit()
    return PasswordResetResult(
        user_id=user.id, email=user.email, temporary_password=temp
    )
