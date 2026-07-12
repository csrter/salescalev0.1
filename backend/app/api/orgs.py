"""Organization lifecycle: self-serve signup, team membership, invites, and
seats (Phase 13).

Role semantics (permission matrix, enforced server-side in this file and the
role gates in deps.py — UI hiding is never load-bearing):
  owner  — everything: billing, ownership transfer, admin management
  admin  — manage clients, platform connections, members and invites; never
           billing, org deletion, or Owner/Admin role management
  member — day-to-day campaign work; no member management
  client — read-only portal, unchanged by this phase
Admins may add/invite members; only the Owner may add/invite admins, change
roles, or transfer ownership.

Membership truth lives in OrganizationMembership (multi-org capable); the
User row mirrors the *active* membership — see services/team.py for the
invariant. Every membership event lands in the membership audit log.
"""

import datetime as dt
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import (
    get_current_user,
    is_superadmin,
    require_admin,
    require_owner,
    require_team,
    require_verified_email,
)
from ..ratelimit import enforce_bucket, rate_limit
from ..services import auth_email, entitlements, sessions, team
from ..models.base import utcnow
from ..models.core import (
    ORG_SUSPENDED,
    ROLE_ADMIN,
    ROLE_CLIENT,
    ROLE_MEMBER,
    ROLE_OWNER,
    Client,
    Organization,
    User,
)
from ..models.crm import CrmTask
from ..models.team import (
    INVITE_ACCEPTED,
    AUDIT_INVITE_ACCEPTED,
    AUDIT_INVITE_RESENT,
    AUDIT_INVITE_REVOKED,
    AUDIT_INVITE_SENT,
    AUDIT_MEMBER_ADDED,
    AUDIT_MEMBER_DEACTIVATED,
    AUDIT_MEMBER_REACTIVATED,
    AUDIT_MEMBER_REMOVED,
    AUDIT_OWNERSHIP_TRANSFERRED,
    AUDIT_ROLE_CHANGED,
    INVITE_EXPIRED,
    INVITE_PENDING,
    INVITE_REVOKED,
    INVITE_TTL_DAYS,
    MembershipAuditEntry,
    OrganizationInvite,
    hash_invite_token,
    new_invite_token,
)
from ..schemas import (
    InviteAcceptRequest,
    InviteAcceptSignupRequest,
    InviteCreate,
    InviteLookupOut,
    InviteOut,
    MembershipAuditOut,
    MembershipOut,
    OkResponse,
    OrganizationOut,
    OrgRememberDeviceIn,
    OrgSmsOptInDefaultIn,
    OrgSecurityIn,
    OrgSignupRequest,
    QualifiedLeadCriteriaIn,
    RemoveMemberRequest,
    SeatUsageOut,
    SwitchOrgRequest,
    TeamMemberCreate,
    TeamMemberUpdate,
    TokenResponse,
    TransferOwnershipRequest,
    UserOut,
)
from ..security import create_access_token, hash_password

router = APIRouter(prefix="/api/orgs", tags=["orgs"])

# Anti-abuse brake on open signup: 10 new organizations per hour per IP.
_signup_limit = rate_limit("signup", limit=10, window_seconds=3600)
# Invite redemption endpoints are public (token-authenticated); brake probing.
_invite_public_limit = rate_limit("invite_redeem", limit=30, window_seconds=3600)
# Per-Organization cap on invite sends, enforced inside the endpoints once the
# tenant is known (per-IP limiting would let one org exhaust nothing).
_ORG_INVITES_PER_HOUR = 30


def _member_out(user: User, role: str) -> UserOut:
    """UserOut with the role this Organization sees — a multi-org user's
    mirror role may belong to a different active org."""
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=role,
        client_id=user.client_id,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/signup", response_model=TokenResponse, status_code=201)
def signup(
    body: OrgSignupRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = _signup_limit,
):
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
    db.flush()
    team.add_membership(db, org.id, owner, ROLE_OWNER)
    db.commit()

    # Fire off the "confirm your email" message (delivered if SMTP is
    # configured, otherwise recorded in email_log).
    auth_email.send_verification_email(db, org, owner)
    db.commit()

    sid = sessions.create(db, owner, request)
    db.commit()
    token = create_access_token(
        owner.id, owner.role, org.id, None, owner.token_version, sid
    )
    return TokenResponse(
        access_token=token,
        role=owner.role,
        organization_id=org.id,
        organization_name=org.name,
        client_id=None,
        full_name=owner.full_name,
        is_superadmin=is_superadmin(owner),
    )


@router.get("/me", response_model=OrganizationOut)
def get_my_org(user: User = Depends(require_team), db: Session = Depends(get_db)):
    return db.get(Organization, user.organization_id)


@router.get("/me/qualified-lead-criteria")
def get_qualified_lead_criteria(
    user: User = Depends(require_team), db: Session = Depends(get_db)
):
    """The Organization's own qualified-lead checklist (Phase 6). Team-only:
    how an agency defines "qualified" is internal workflow, not something a
    client account needs — clients see the resulting status, not the rubric.
    """
    org = db.get(Organization, user.organization_id)
    return {"criteria": org.qualified_lead_criteria or []}


@router.put("/me/qualified-lead-criteria")
def set_qualified_lead_criteria(
    body: QualifiedLeadCriteriaIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replace the checklist. Organization data through and through —
    Atlas Reach's 14-Day Trial Sprint list and another agency's (or no
    list at all) are just different rows here. Existing contacts keep
    their qualified status; the checklist governs evaluations from now on.
    """
    org = db.get(Organization, user.organization_id)
    org.qualified_lead_criteria = [c.model_dump() for c in body.criteria]
    db.commit()
    return {"criteria": org.qualified_lead_criteria}


@router.put("/me/require-mfa", response_model=OrganizationOut)
def set_require_mfa(
    body: OrgSecurityIn,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Owner policy: require every team member to have 2FA. When turned on,
    members without it are gated to enrollment (mfa_setup_required) on their
    next request; it doesn't retroactively invalidate their sessions."""
    org = db.get(Organization, user.organization_id)
    org.require_mfa = body.require_mfa
    db.commit()
    return org


@router.put("/me/allow-remember-device", response_model=OrganizationOut)
def set_allow_remember_device(
    body: OrgRememberDeviceIn,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Owner policy: whether team members may check "remember this device" at
    a 2FA challenge to skip future challenges on that device. Turning it off
    doesn't retroactively revoke devices already remembered — see
    trusted_devices for the explicit per-device/revoke-all controls."""
    org = db.get(Organization, user.organization_id)
    org.allow_remember_device = body.allow_remember_device
    db.commit()
    return org


@router.put("/me/sms-opt-in-default", response_model=OrganizationOut)
def set_sms_opt_in_default(
    body: OrgSmsOptInDefaultIn,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Owner policy: when true, every newly created contact is stamped with
    the org's standing SMS-consent attestation (services/sms_consent.
    apply_org_default) — for agencies whose own intake funnels already
    collect SMS consent before a lead reaches Salescale. STOP/suppression at
    send time is unaffected."""
    org = db.get(Organization, user.organization_id)
    org.sms_opt_in_default = body.sms_opt_in_default
    db.commit()
    return org


@router.get("/me/house-client")
def get_house_client(
    user: User = Depends(require_team), db: Session = Depends(get_db)
):
    """The Organization's own prospect pipeline — the agency "house" CRM. It's
    one synthetic Client row per org (flagged is_house), hidden from the client
    roster and never counted as a billed client, so the whole existing CRM runs
    against it unchanged. Get-or-create: the first team member to open the house
    CRM materializes the row. Team-only — a house pipeline is internal agency
    workflow, never a client-portal surface."""
    # first() not one-or-none, same rigor as crm.get_or_create_pipeline: the
    # partial unique index caps it at one per org, but "get or create" picks the
    # earliest deterministically rather than assuming exactly one row exists.
    client = (
        db.execute(
            select(Client)
            .where(
                Client.organization_id == user.organization_id,
                Client.is_house.is_(True),
            )
            .order_by(Client.created_at)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if client is None:
        client = Client(
            organization_id=user.organization_id,
            name="House",
            status="active",
            is_house=True,
        )
        db.add(client)
        try:
            db.commit()
        except IntegrityError:
            # Two team members opened the house CRM for the first time at
            # once — the partial unique index let exactly one create through,
            # so read that winner back instead of surfacing a 500.
            db.rollback()
            client = (
                db.execute(
                    select(Client)
                    .where(
                        Client.organization_id == user.organization_id,
                        Client.is_house.is_(True),
                    )
                    .order_by(Client.created_at)
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if client is None:  # pragma: no cover — index fired, row must exist
                raise HTTPException(500, "House client creation raced")
    return {"client_id": client.id}


# --- members ---


@router.get("/me/members", response_model=List[UserOut])
def list_members(user: User = Depends(require_team), db: Session = Depends(get_db)):
    """Team members (via memberships — the org-scoped truth) plus this
    Organization's client portal users, as before."""
    out = [
        _member_out(u, role)
        for u, role in team.team_members_with_roles(db, user.organization_id)
    ]
    clients = (
        db.execute(
            select(User).where(
                User.organization_id == user.organization_id,
                User.role == ROLE_CLIENT,
            )
        )
        .scalars()
        .all()
    )
    out.extend(_member_out(u, ROLE_CLIENT) for u in clients)
    out.sort(key=lambda m: m.created_at)
    return out


@router.post("/me/members", response_model=UserOut, status_code=201)
def add_member(
    body: TeamMemberCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _verified: User = Depends(require_verified_email),
):
    """Direct-create with a temporary password the admin shares out-of-band.
    The email-invite flow below is the primary path; this stays for teams
    that provision accounts hands-on."""
    if body.role not in (ROLE_ADMIN, ROLE_MEMBER):
        raise HTTPException(400, "Role must be admin or member")
    if body.role == ROLE_ADMIN and user.role != ROLE_OWNER:
        raise HTTPException(403, "Only the Owner can add admins")
    entitlements.enforce_can_add_seat(db, db.get(Organization, user.organization_id))
    email = body.email.lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(
            409,
            "A user with this email already exists — send them an invite instead",
        )
    member = User(
        organization_id=user.organization_id,
        email=email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(member)
    db.flush()
    team.add_membership(db, user.organization_id, member, body.role)
    team.record_event(
        db,
        user.organization_id,
        user,
        AUDIT_MEMBER_ADDED,
        target_user=member,
        detail={"role": body.role},
    )
    db.commit()
    return _member_out(member, body.role)


@router.patch("/me/members/{member_id}", response_model=UserOut)
def update_member(
    member_id: str,
    body: TeamMemberUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Change a team member's role (Owner only) or activate/deactivate them.
    Scoped to the actor's own Organization. Guards: no self-changes, no
    ownerless org (last-Owner protection), and no account-wide deactivation
    of a user who also belongs to other Organizations."""
    member = db.get(User, member_id)
    if member is None:
        raise HTTPException(404, "Member not found")
    if member.id == user.id:
        raise HTTPException(400, "You can't change your own membership here")

    membership = team.get_membership(db, user.organization_id, member.id)
    if membership is None:
        # Client portal users have no membership; allow the activate toggle
        # only (they have no team role to change).
        if (
            member.organization_id != user.organization_id
            or member.role != ROLE_CLIENT
        ):
            raise HTTPException(404, "Member not found")
        if body.role is not None:
            raise HTTPException(400, "Client portal accounts have no team role")
        if body.is_active is not None:
            member.is_active = body.is_active
            db.commit()
        return _member_out(member, ROLE_CLIENT)

    if body.role is not None:
        if user.role != ROLE_OWNER:
            raise HTTPException(403, "Only the Owner can change roles")
        if body.role not in (ROLE_ADMIN, ROLE_MEMBER):
            raise HTTPException(400, "Role must be admin or member")
        if membership.role == ROLE_OWNER:
            # Demoting a co-Owner is allowed; demoting the last Owner never is.
            team.assert_not_last_owner(db, membership)
        if body.role != membership.role:
            old = membership.role
            membership.role = body.role
            team.sync_mirror_if_active(member, membership)
            team.record_event(
                db,
                user.organization_id,
                user,
                AUDIT_ROLE_CHANGED,
                target_user=member,
                detail={"from": old, "to": body.role},
            )

    if body.is_active is not None and body.is_active != member.is_active:
        if not body.is_active:
            team.assert_not_last_owner(db, membership)
            others = [
                m
                for m in team.memberships_for_user(db, member.id)
                if m.organization_id != user.organization_id
            ]
            if others:
                raise HTTPException(
                    400,
                    "This user belongs to other organizations — remove them "
                    "from this organization instead of deactivating the "
                    "account.",
                )
        member.is_active = body.is_active
        team.record_event(
            db,
            user.organization_id,
            user,
            AUDIT_MEMBER_REACTIVATED if body.is_active else AUDIT_MEMBER_DEACTIVATED,
            target_user=member,
        )

    db.commit()
    db.refresh(member)
    return _member_out(member, membership.role)


@router.delete("/me/members/{member_id}", response_model=OkResponse)
def remove_member(
    member_id: str,
    body: Optional[RemoveMemberRequest] = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove a member from this Organization: their sessions die immediately,
    their open work is reassigned (never orphaned, never deleted), and the
    removal is audit-logged. Owners are never removable — transfer ownership
    first. Admins can remove members; removing an admin takes the Owner."""
    member = db.get(User, member_id)
    membership = (
        team.get_membership(db, user.organization_id, member_id)
        if member is not None
        else None
    )
    if member is None or membership is None:
        raise HTTPException(404, "Member not found")
    if member.id == user.id:
        raise HTTPException(400, "You can't remove yourself")
    if membership.role == ROLE_OWNER:
        raise HTTPException(400, "Owners can't be removed — transfer ownership first")
    if membership.role == ROLE_ADMIN and user.role != ROLE_OWNER:
        raise HTTPException(403, "Only the Owner can remove admins")

    reassign_to_id = (body.reassign_to_user_id if body else None) or user.id
    if reassign_to_id == member.id:
        raise HTTPException(400, "Can't reassign records to the removed member")
    if team.get_membership(db, user.organization_id, reassign_to_id) is None:
        raise HTTPException(400, "Reassignment target must be a team member")

    # Open work moves to the designated member (default: the remover).
    # Historical rows (activities, audit entries, executed changes) keep the
    # leaver's id — they're a record of what happened, not live ownership.
    open_tasks = (
        db.execute(
            select(CrmTask).where(
                CrmTask.organization_id == user.organization_id,
                CrmTask.assigned_to_user_id == member.id,
                CrmTask.completed_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for task in open_tasks:
        task.assigned_to_user_id = reassign_to_id

    db.delete(membership)
    # Flush so revoke_org_access's membership query sees the deletion
    # (sessions run with autoflush=False).
    db.flush()
    team.revoke_org_access(db, member, user.organization_id)
    team.record_event(
        db,
        user.organization_id,
        user,
        AUDIT_MEMBER_REMOVED,
        target_user=member,
        detail={"reassigned_to": reassign_to_id, "reassigned_tasks": len(open_tasks)},
    )
    db.commit()
    return OkResponse()


@router.post("/me/transfer-ownership", response_model=OkResponse)
def transfer_ownership(
    body: TransferOwnershipRequest,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Explicit ownership transfer: promote a team member to Owner and (by
    default) step down to Admin. This is the only path that changes who owns
    the Organization — ownership never moves as a side effect."""
    if body.member_id == user.id:
        raise HTTPException(400, "You already own this organization")
    target = db.get(User, body.member_id)
    membership = (
        team.get_membership(db, user.organization_id, body.member_id)
        if target is not None
        else None
    )
    if target is None or membership is None:
        raise HTTPException(404, "Member not found")
    if not target.is_active:
        raise HTTPException(400, "Can't transfer ownership to a deactivated member")

    old_role = membership.role
    membership.role = ROLE_OWNER
    team.sync_mirror_if_active(target, membership)
    if body.demote_self:
        own = team.get_membership(db, user.organization_id, user.id)
        own.role = ROLE_ADMIN
        team.sync_mirror_if_active(user, own)
    team.record_event(
        db,
        user.organization_id,
        user,
        AUDIT_OWNERSHIP_TRANSFERRED,
        target_user=target,
        detail={"from_role": old_role, "demoted_self": body.demote_self},
    )
    db.commit()
    return OkResponse()


@router.get("/me/seats", response_model=SeatUsageOut)
def seat_usage(user: User = Depends(require_team), db: Session = Depends(get_db)):
    """Self-service "X of Y seats used" (same metering pattern as the other
    entitlement surfaces)."""
    org = db.get(Organization, user.organization_id)
    usage = entitlements.seat_usage(db, org)
    return SeatUsageOut(**usage, plan=org.plan)


@router.get("/me/membership-audit", response_model=List[MembershipAuditOut])
def membership_audit(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return (
        db.execute(
            select(MembershipAuditEntry)
            .where(MembershipAuditEntry.organization_id == user.organization_id)
            .order_by(MembershipAuditEntry.created_at.desc())
            .limit(min(max(limit, 1), 200))
            .offset(max(offset, 0))
        )
        .scalars()
        .all()
    )


# --- invites ---


@router.get("/me/invites", response_model=List[InviteOut])
def list_invites(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    invites = (
        db.execute(
            select(OrganizationInvite)
            .where(OrganizationInvite.organization_id == user.organization_id)
            .order_by(OrganizationInvite.created_at.desc())
        )
        .scalars()
        .all()
    )
    # Lazily settle expiries so the list never shows a redeemable-looking
    # invite that would bounce. (List comprehension: evaluate for EVERY
    # invite — any() alone would stop at the first expired one.)
    if any([team.expire_if_due(db, i) for i in invites]):
        db.commit()
    return invites


@router.post("/me/invites", response_model=InviteOut, status_code=201)
def send_invite(
    body: InviteCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _verified: User = Depends(require_verified_email),
):
    """Email an invite. The mail carries the only copy of the token; the DB
    stores its hash. A pending invite reserves a seat (see entitlements)."""
    if body.role not in (ROLE_ADMIN, ROLE_MEMBER):
        raise HTTPException(400, "Role must be admin or member")
    if body.role == ROLE_ADMIN and user.role != ROLE_OWNER:
        raise HTTPException(403, "Only the Owner can invite admins")
    enforce_bucket(
        f"org_invites:{user.organization_id}", _ORG_INVITES_PER_HOUR, 3600
    )
    org = db.get(Organization, user.organization_id)
    email = body.email.lower()

    existing_user = db.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()
    if existing_user is not None:
        if team.get_membership(db, org.id, existing_user.id) is not None:
            raise HTTPException(
                409, "That person is already a member of this organization"
            )
        if existing_user.role == ROLE_CLIENT:
            raise HTTPException(
                409,
                "That email belongs to a client portal account — it can't "
                "join the team",
            )

    # Re-inviting supersedes: the old invite dies with its token, the new one
    # takes its reserved seat.
    superseded = team.pending_invite_for(db, org.id, email)
    if superseded is not None:
        superseded.status = INVITE_REVOKED

    entitlements.enforce_can_add_seat(db, org)

    raw, token_hash = new_invite_token()
    invite = OrganizationInvite(
        organization_id=org.id,
        email=email,
        role=body.role,
        invited_by_user_id=user.id,
        token_hash=token_hash,
        expires_at=utcnow() + dt.timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    delivered, link = auth_email.send_invite_email(
        db, org, email, raw, user.full_name, body.role
    )
    team.record_event(
        db,
        org.id,
        user,
        AUDIT_INVITE_SENT,
        target_email=email,
        detail={"role": body.role, "superseded": superseded is not None},
    )
    db.commit()
    out = InviteOut.model_validate(invite)
    if not delivered:
        # No email transport (dev/desktop) or delivery failed: hand the link
        # to the inviting Admin to share out-of-band — same posture as the
        # temp-password surface on member password reset. Never stored, never
        # in list responses (the DB keeps only the token hash).
        out.invite_link = link
    return out


@router.post("/me/invites/{invite_id}/resend", response_model=InviteOut)
def resend_invite(
    invite_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Regenerate the token (killing the old link) and send a fresh email.
    Also revives an expired invite with a new 7-day window."""
    invite = db.get(OrganizationInvite, invite_id)
    if invite is None or invite.organization_id != user.organization_id:
        raise HTTPException(404, "Invite not found")
    team.expire_if_due(db, invite)
    if invite.status not in (INVITE_PENDING, INVITE_EXPIRED):
        raise HTTPException(400, f"This invite was already {invite.status}")
    enforce_bucket(
        f"org_invites:{user.organization_id}", _ORG_INVITES_PER_HOUR, 3600
    )
    org = db.get(Organization, user.organization_id)

    raw, token_hash = new_invite_token()
    invite.token_hash = token_hash
    invite.status = INVITE_PENDING
    invite.expires_at = utcnow() + dt.timedelta(days=INVITE_TTL_DAYS)
    delivered, link = auth_email.send_invite_email(
        db, org, invite.email, raw, user.full_name, invite.role
    )
    team.record_event(
        db, org.id, user, AUDIT_INVITE_RESENT, target_email=invite.email
    )
    db.commit()
    out = InviteOut.model_validate(invite)
    if not delivered:
        out.invite_link = link  # same out-of-band fallback as send_invite
    return out


@router.delete("/me/invites/{invite_id}", response_model=InviteOut)
def revoke_invite(
    invite_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    invite = db.get(OrganizationInvite, invite_id)
    if invite is None or invite.organization_id != user.organization_id:
        raise HTTPException(404, "Invite not found")
    if invite.status != INVITE_PENDING:
        raise HTTPException(400, f"This invite was already {invite.status}")
    invite.status = INVITE_REVOKED
    team.record_event(
        db, user.organization_id, user, AUDIT_INVITE_REVOKED, target_email=invite.email
    )
    db.commit()
    return invite


# --- invite redemption (public: the token is the credential) ---


def _redeemable_invite(db: Session, token: str) -> OrganizationInvite:
    invite = db.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.token_hash == hash_invite_token(token)
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(400, "Invalid invite link")
    if team.expire_if_due(db, invite):
        db.commit()
        raise HTTPException(400, "This invite has expired — ask for a new one")
    if invite.status != INVITE_PENDING:
        raise HTTPException(400, f"This invite was {invite.status}")
    return invite


@router.get("/invites/lookup", response_model=InviteLookupOut)
def lookup_invite(
    token: str,
    db: Session = Depends(get_db),
    _: None = _invite_public_limit,
):
    """Pre-accept preview for the invite page: which org, which email, and
    whether that email already has an account (login vs signup path)."""
    invite = db.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.token_hash == hash_invite_token(token)
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(400, "Invalid invite link")
    if team.expire_if_due(db, invite):
        db.commit()
    org = db.get(Organization, invite.organization_id)
    account_exists = (
        db.execute(
            select(User).where(User.email == invite.email)
        ).scalar_one_or_none()
        is not None
    )
    return InviteLookupOut(
        organization_name=org.name,
        email=invite.email,
        role=invite.role,
        status=invite.status,
        account_exists=account_exists,
    )


@router.post("/invites/accept", response_model=TokenResponse)
def accept_invite(
    body: InviteAcceptRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Existing, logged-in user joins the inviting Organization. The invite
    email must match the account email — a forwarded link is worthless to
    anyone else."""
    invite = _redeemable_invite(db, body.token)
    if invite.email != user.email.lower():
        raise HTTPException(
            403,
            f"This invite was sent to {invite.email} — log in with that "
            "account to accept it",
        )
    if user.role == ROLE_CLIENT:
        raise HTTPException(403, "Client portal accounts can't join a team")
    if team.get_membership(db, invite.organization_id, user.id) is not None:
        raise HTTPException(409, "You're already a member of this organization")

    org = db.get(Organization, invite.organization_id)
    # Seats can fill between send and accept (or the plan can shrink) —
    # re-check at accept time rather than silently over-provisioning.
    entitlements.enforce_can_accept_seat(db, org)

    membership = team.add_membership(db, org.id, user, invite.role)
    team.sync_active_org(user, membership)
    invite.status = INVITE_ACCEPTED
    invite.accepted_by_user_id = user.id
    invite.accepted_at = utcnow()
    team.record_event(db, org.id, user, AUDIT_INVITE_ACCEPTED, target_user=user)
    db.commit()

    from .auth import _token_response

    return _token_response(user, org, getattr(request.state, "session_id", None))


@router.post("/invites/accept-signup", response_model=TokenResponse, status_code=201)
def accept_invite_signup(
    body: InviteAcceptSignupRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = _invite_public_limit,
):
    """New user: the invite doubles as signup. The account email is the
    invited address by construction, and possession of the token proves
    control of that inbox — the same proof the verification email would
    collect — so the account starts verified."""
    invite = _redeemable_invite(db, body.token)
    if (
        db.execute(
            select(User).where(User.email == invite.email)
        ).scalar_one_or_none()
        is not None
    ):
        raise HTTPException(
            409,
            "An account with this email already exists — log in, then open "
            "the invite link again",
        )
    org = db.get(Organization, invite.organization_id)
    entitlements.enforce_can_accept_seat(db, org)

    user = User(
        organization_id=org.id,
        email=invite.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=invite.role,
        email_verified=True,
    )
    db.add(user)
    db.flush()
    team.add_membership(db, org.id, user, invite.role)
    invite.status = INVITE_ACCEPTED
    invite.accepted_by_user_id = user.id
    invite.accepted_at = utcnow()
    team.record_event(db, org.id, user, AUDIT_INVITE_ACCEPTED, target_user=user)
    db.commit()

    sid = sessions.create(db, user, request)
    db.commit()

    from .auth import _token_response

    return _token_response(user, org, sid)


# --- multi-org: my memberships & the org switcher ---


@router.get("/mine", response_model=List[MembershipOut])
def my_organizations(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Every Organization this account belongs to. Client users are pinned to
    one org by construction and get that single entry."""
    if user.role == ROLE_CLIENT:
        org = db.get(Organization, user.organization_id)
        return [
            MembershipOut(
                organization_id=org.id,
                organization_name=org.name,
                role=ROLE_CLIENT,
                is_active_org=True,
            )
        ]
    memberships = team.memberships_for_user(db, user.id)
    orgs = {
        o.id: o
        for o in db.execute(
            select(Organization).where(
                Organization.id.in_([m.organization_id for m in memberships])
            )
        ).scalars()
    }
    return [
        MembershipOut(
            organization_id=m.organization_id,
            organization_name=orgs[m.organization_id].name,
            role=m.role,
            is_active_org=m.organization_id == user.organization_id,
        )
        for m in memberships
    ]


@router.post("/switch", response_model=TokenResponse)
def switch_organization(
    body: SwitchOrgRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Point this account at another of its Organizations. The active org is
    per-account (the User-row mirror), so every device follows — the same
    semantics as a workspace switch in comparable products."""
    membership = team.get_membership(db, body.organization_id, user.id)
    if membership is None:
        raise HTTPException(404, "Not found")
    org = db.get(Organization, membership.organization_id)
    # Switching INTO a suspended org would brick the account (every request
    # 403s on the active org) — keep them where they are instead.
    if org.status == ORG_SUSPENDED:
        raise HTTPException(403, "That organization is suspended")
    team.sync_active_org(user, membership)
    db.commit()
    from .auth import _token_response

    return _token_response(user, org, getattr(request.state, "session_id", None))
