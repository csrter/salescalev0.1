"""Phase 13 team operations: membership truth, the active-org mirror, and
the membership audit trail.

The invariant everything here protects: OrganizationMembership rows are the
source of truth for org access, and User.organization_id/role always equal
one of the user's memberships (the *active* one). Every code path that
creates, changes, or removes a membership goes through these helpers so the
mirror can never drift — a drifted mirror is a tenant-isolation bug, because
TenantScope and the role gates read the mirror.
"""
import datetime as dt
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import ROLE_OWNER, TEAM_ROLES, Organization, User
from ..models.team import (
    INVITE_EXPIRED,
    INVITE_PENDING,
    MembershipAuditEntry,
    OrganizationInvite,
    OrganizationMembership,
)
from . import sessions


def record_event(
    db: Session,
    organization_id: str,
    actor: User,
    action: str,
    target_user: Optional[User] = None,
    target_email: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    """Append one membership audit entry. Caller owns the commit — the entry
    rides the same transaction as the change it records, so the log can't
    show an event whose change rolled back (or vice versa)."""
    db.add(
        MembershipAuditEntry(
            organization_id=organization_id,
            actor_user_id=actor.id,
            actor_email=actor.email,
            actor_name=actor.full_name,
            action=action,
            target_user_id=target_user.id if target_user is not None else None,
            target_email=(
                target_user.email if target_user is not None else target_email
            ),
            detail=detail,
        )
    )


def get_membership(
    db: Session, organization_id: str, user_id: str
) -> Optional[OrganizationMembership]:
    return db.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    ).scalar_one_or_none()


def memberships_for_user(db: Session, user_id: str) -> list[OrganizationMembership]:
    return (
        db.execute(
            select(OrganizationMembership)
            .where(OrganizationMembership.user_id == user_id)
            .order_by(OrganizationMembership.created_at)
        )
        .scalars()
        .all()
    )


def add_membership(
    db: Session, organization_id: str, user: User, role: str
) -> OrganizationMembership:
    membership = OrganizationMembership(
        organization_id=organization_id, user_id=user.id, role=role
    )
    db.add(membership)
    return membership


def sync_active_org(user: User, membership: OrganizationMembership) -> None:
    """Point the User-row mirror at this membership. client_id is a
    client-portal concept; team memberships never carry one."""
    user.organization_id = membership.organization_id
    user.role = membership.role
    user.client_id = None


def sync_mirror_if_active(user: User, membership: OrganizationMembership) -> None:
    """After a membership role change: refresh the mirror only when this
    membership is the user's active one."""
    if user.organization_id == membership.organization_id:
        user.role = membership.role


def owner_count(db: Session, organization_id: str) -> int:
    return db.execute(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.role == ROLE_OWNER,
        )
    ).scalar_one()


def assert_not_last_owner(db: Session, membership: OrganizationMembership) -> None:
    """Guard for demote/remove/deactivate paths: an Organization must always
    have at least one Owner (acceptance check: no path may violate this)."""
    if membership.role == ROLE_OWNER and owner_count(db, membership.organization_id) <= 1:
        raise HTTPException(
            400,
            "This is the Organization's only Owner — transfer ownership first.",
        )


def sole_owner_orgs(db: Session, user_id: str) -> list[str]:
    """Organization ids where this user is the only Owner. Used to block
    account-wide deactivation that would leave an org ownerless."""
    result = []
    for m in memberships_for_user(db, user_id):
        if m.role == ROLE_OWNER and owner_count(db, m.organization_id) <= 1:
            result.append(m.organization_id)
    return result


def revoke_org_access(db: Session, user: User, organization_id: str) -> None:
    """Post-removal cleanup: kill every live session/token immediately, then
    repoint the active-org mirror away from the org they were removed from.
    A user left with no memberships keeps their account row but can't log in
    (is_active=False) — records referencing them stay intact."""
    user.token_version += 1
    sessions.revoke_all(db, user.id)
    if user.organization_id != organization_id:
        return
    remaining = memberships_for_user(db, user.id)
    if remaining:
        sync_active_org(user, remaining[0])
    else:
        user.is_active = False


def pending_invite_for(
    db: Session, organization_id: str, email: str
) -> Optional[OrganizationInvite]:
    return db.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.organization_id == organization_id,
            OrganizationInvite.email == email,
            OrganizationInvite.status == INVITE_PENDING,
        )
    ).scalar_one_or_none()


def _aware(value: dt.datetime) -> dt.datetime:
    # SQLite hands back naive datetimes (same normalization as sessions.touch).
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


def expire_if_due(db: Session, invite: OrganizationInvite) -> bool:
    """Lazily flip a pending invite past its expiry to `expired`. Returns True
    when the invite is no longer redeemable for that reason."""
    if invite.status != INVITE_PENDING:
        return False
    if _aware(invite.expires_at) < utcnow():
        invite.status = INVITE_EXPIRED
        return True
    return False


def team_members_with_roles(db: Session, organization_id: str) -> list[tuple[User, str]]:
    """(User, role-in-this-org) for every team member, via memberships — a
    user's mirror role may belong to a different active org, so the
    membership row is what a member list must show."""
    rows = db.execute(
        select(User, OrganizationMembership.role)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(OrganizationMembership.organization_id == organization_id)
        .order_by(User.created_at)
    ).all()
    return [(user, role) for user, role in rows]
