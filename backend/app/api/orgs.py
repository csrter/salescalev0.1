"""Organization lifecycle: self-serve signup and team membership.

Signup is the single generic tenant-creation path — Atlas Reach (tenant #1)
is created through this exact flow (see scripts/seed.py), with no
special-casing and no access another Organization wouldn't get.

Role semantics (Phase 1 definition):
  owner  — everything, including team membership changes (billing in Phase 8)
  admin  — manage clients, platform connections, and team members
  member — day-to-day campaign work; no client or team management
Admins may add members; only the Owner may add admins.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin, require_team
from ..models.core import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    Organization,
    User,
)
from ..schemas import OrganizationOut, OrgSignupRequest, TeamMemberCreate, TokenResponse, UserOut
from ..security import create_access_token, hash_password

router = APIRouter(prefix="/api/orgs", tags=["orgs"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
def signup(body: OrgSignupRequest, db: Session = Depends(get_db)):
    """Public: create an Organization and its first user (the Owner), and
    log them in. This inserts only into the new tenant — it can neither read
    nor touch any other Organization's rows."""
    email = body.email.lower()
    existing = db.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, "A user with this email already exists")

    org = Organization(name=body.organization_name)
    db.add(org)
    db.flush()
    owner = User(
        organization_id=org.id,
        email=email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=ROLE_OWNER,
    )
    db.add(owner)
    db.commit()

    token = create_access_token(owner.id, owner.role, org.id, None)
    return TokenResponse(
        access_token=token,
        role=owner.role,
        organization_id=org.id,
        organization_name=org.name,
        client_id=None,
        full_name=owner.full_name,
    )


@router.get("/me", response_model=OrganizationOut)
def get_my_org(user: User = Depends(require_team), db: Session = Depends(get_db)):
    return db.get(Organization, user.organization_id)


@router.get("/me/members", response_model=List[UserOut])
def list_members(user: User = Depends(require_team), db: Session = Depends(get_db)):
    return (
        db.execute(
            select(User)
            .where(User.organization_id == user.organization_id)
            .order_by(User.created_at)
        )
        .scalars()
        .all()
    )


@router.post("/me/members", response_model=UserOut, status_code=201)
def add_member(
    body: TeamMemberCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if body.role not in (ROLE_ADMIN, ROLE_MEMBER):
        raise HTTPException(400, "Role must be admin or member")
    if body.role == ROLE_ADMIN and user.role != ROLE_OWNER:
        raise HTTPException(403, "Only the Owner can add admins")
    email = body.email.lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(409, "A user with this email already exists")
    member = User(
        organization_id=user.organization_id,
        email=email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(member)
    db.commit()
    return member
