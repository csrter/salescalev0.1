"""Phase 9 entitlement seams — the single place Phase 8's subscription
tiers will plug in.

Every feature gate introduced in Phase 9 goes through exactly one function
here, so wiring real tier data later means editing this file, not hunting
call sites. Until Phase 8 exists:

- white-labeling is available to every Organization (industry-wide, custom
  domains are usually an Agency-tier feature and logo/colors sometimes sit
  lower — that split is a Phase 8 decision to confirm with the user);
- AI insights get one global default monthly cap (config.ai_monthly_query_limit)
  applied to every Organization.
"""

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.core import Client, Organization
from ..models.team import INVITE_PENDING, OrganizationInvite, OrganizationMembership

# Subscription tier limits, enforced server-side (not just hidden in the UI).
# `None` means unlimited. Kept generous on Starter deliberately; tune against
# real cost once billing is live.
TIER_LIMITS: dict[str, dict[str, int | None]] = {
    "starter": {"clients": 5, "seats": 5},
    "pro": {"clients": 25, "seats": 15},
    "agency": {"clients": None, "seats": None},
}


def _limits(org: Organization) -> dict[str, int | None]:
    return TIER_LIMITS.get(org.plan, TIER_LIMITS["starter"])


def enforce_can_add_client(db: Session, org: Organization) -> None:
    """402 when the org is at its plan's client limit."""
    cap = _limits(org)["clients"]
    if cap is None:
        return
    count = db.execute(
        select(func.count()).select_from(Client).where(Client.organization_id == org.id)
    ).scalar_one()
    if count >= cap:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Your {org.plan} plan allows {cap} clients. Upgrade to add more.",
        )


def seat_usage(db: Session, org: Organization) -> dict:
    """Self-service usage visibility: seats occupied by team memberships plus
    pending invites (an invite reserves a seat so accepts can't oversubscribe).
    Client-role logins don't count as seats. limit=None means unlimited."""
    used = db.execute(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(OrganizationMembership.organization_id == org.id)
    ).scalar_one()
    pending = db.execute(
        select(func.count())
        .select_from(OrganizationInvite)
        .where(
            OrganizationInvite.organization_id == org.id,
            OrganizationInvite.status == INVITE_PENDING,
        )
    ).scalar_one()
    return {"used": used, "pending_invites": pending, "limit": _limits(org)["seats"]}


def enforce_can_add_seat(db: Session, org: Organization) -> None:
    """402 when the org is at its plan's team-seat limit. Gates inviting AND
    directly adding a member; pending invites count against the cap so an
    org can't oversubscribe by sending invites it has no seats for."""
    usage = seat_usage(db, org)
    cap = usage["limit"]
    if cap is None:
        return
    if usage["used"] + usage["pending_invites"] >= cap:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Your {org.plan} plan allows {cap} team seats. Upgrade to add more.",
        )


def enforce_can_accept_seat(db: Session, org: Organization) -> None:
    """Accept-time seat gate (seats can fill between send and accept, e.g. a
    plan downgrade). Only occupied seats count here — the accepting invite
    frees its own pending reservation as it converts to a membership."""
    usage = seat_usage(db, org)
    cap = usage["limit"]
    if cap is None:
        return
    if usage["used"] >= cap:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "This organization has no seats available — contact its admin to "
            "free a seat or upgrade the plan.",
        )


def can_use_white_labeling(org: Organization) -> bool:
    """May this Organization configure branding, custom domains, and branded
    email? Phase 8: return based on the org's subscription tier."""
    return True


def can_use_ai_insights(org: Organization) -> bool:
    """May this Organization use AI explanations/summaries at all?
    Phase 8: return based on the org's subscription tier."""
    return True


def ai_monthly_query_limit(org: Organization) -> int:
    """Monthly cap on AI queries (explanations + summaries) for this
    Organization. Phase 8: read the org's tier limit instead of the global
    default. NOTE for whoever prices the tiers: check actual cost per org in
    the ai_usage table before committing to a number — a cap that lets an
    org spend more on Claude API calls than its subscription price is a
    loss-maker, and this ledger exists so that's checkable, not guessed.
    """
    return get_settings().ai_monthly_query_limit
