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

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.core import Client, Organization
from ..models.team import INVITE_PENDING, OrganizationInvite, OrganizationMembership

# Subscription tier limits, enforced server-side (not just hidden in the UI).
# `None` means unlimited. Kept generous on Starter deliberately; tune against
# real cost once billing is live.
# `custom_fields` is the per-Organization cap on ACTIVE custom field
# definitions (Phase 14, task 9). Even the top tier keeps a hard ceiling
# (services/custom_fields.MAX_ACTIVE_DEFINITIONS) to bound query/UI complexity;
# a tier value of None means "top out at that hard ceiling", not "unlimited".
TIER_LIMITS: dict[str, dict[str, int | None]] = {
    # lead_finder_searches / email_verifications are MONTHLY caps (calendar
    # month, UTC) on metered external calls that cost real money per unit
    # (Google Places ~$35/1k searches, verification ~$8/1k emails). Unlike
    # seats/clients, even the agency tier keeps a finite number — an
    # unmetered tenant here is direct margin loss, not just oversubscription.
    "starter": {
        "clients": 5,
        "seats": 5,
        "custom_fields": 20,
        "lead_finder_searches": 40,
        "email_verifications": 250,
        "email_sends": 1000,
    },
    "pro": {
        "clients": 25,
        "seats": 15,
        "custom_fields": 50,
        "lead_finder_searches": 200,
        "email_verifications": 2000,
        "email_sends": 10000,
    },
    "agency": {
        "clients": None,
        "seats": None,
        "custom_fields": None,
        "lead_finder_searches": 1000,
        "email_verifications": 10000,
        "email_sends": 100000,
    },
}


def _limits(org: Organization) -> dict[str, int | None]:
    return TIER_LIMITS.get(org.plan, TIER_LIMITS["starter"])


def enforce_can_add_client(db: Session, org: Organization) -> None:
    """402 when the org is at its plan's client limit."""
    cap = _limits(org)["clients"]
    if cap is None:
        return
    count = db.execute(
        select(func.count())
        .select_from(Client)
        # The house client is the org's own prospect pipeline, not a billed
        # client — it must never consume a plan slot.
        .where(Client.organization_id == org.id, Client.is_house.is_(False))
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


def custom_field_limit(org: Organization) -> int:
    """Effective cap on active custom field definitions: the tier's number, but
    never above the absolute hard ceiling (even on unlimited tiers)."""
    from .custom_fields import MAX_ACTIVE_DEFINITIONS

    cap = _limits(org).get("custom_fields")
    if cap is None:
        return MAX_ACTIVE_DEFINITIONS
    return min(cap, MAX_ACTIVE_DEFINITIONS)


def custom_field_usage(db: Session, org: Organization) -> dict:
    """Self-service "X of Y used" for custom fields (guardrail 5). Counts only
    active (non-archived) definitions — archived ones don't consume the cap."""
    from .custom_fields import active_count

    return {"used": active_count(db, org.id), "limit": custom_field_limit(org)}


def enforce_can_add_custom_field(db: Session, org: Organization) -> None:
    """402 when the org is at its active-custom-field cap. Archiving frees the
    cap; hard delete does too."""
    usage = custom_field_usage(db, org)
    if usage["used"] >= usage["limit"]:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Your {org.plan} plan allows {usage['limit']} active custom "
            "fields. Archive one or upgrade to add more.",
        )


def _month_count(db: Session, model, organization_id: str) -> int:
    """Rows this org created in the current calendar month (UTC) — the
    metering rule shared by every per-month ledger (same as ai_insights
    .month_usage)."""
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    month_start = dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc)
    return db.execute(
        select(func.count())
        .select_from(model)
        .where(
            model.organization_id == organization_id,
            model.created_at >= month_start,
        )
    ).scalar_one()


def lead_finder_usage(db: Session, org: Organization) -> dict:
    """Self-service "X of Y used" for Lead Finder searches this month.

    Google bills every pagination page as its own Text Search request, so
    "used" sums pages_fetched rather than counting ledger rows — a 60-result
    search honestly consumes 3 of the monthly quota."""
    import datetime as dt

    from ..models.lead_finder import LeadFinderSearch

    now = dt.datetime.now(dt.timezone.utc)
    month_start = dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc)
    used = db.execute(
        select(func.coalesce(func.sum(LeadFinderSearch.pages_fetched), 0)).where(
            LeadFinderSearch.organization_id == org.id,
            LeadFinderSearch.created_at >= month_start,
        )
    ).scalar_one()
    return {
        "used": int(used),
        "limit": _limits(org)["lead_finder_searches"],
    }


def enforce_can_search_leads(db: Session, org: Organization) -> None:
    """402 when the org has used its monthly Lead Finder search quota."""
    usage = lead_finder_usage(db, org)
    cap = usage["limit"]
    if cap is not None and usage["used"] >= cap:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Your {org.plan} plan allows {cap} Lead Finder searches per "
            "month. Upgrade for more.",
        )


def lead_finder_pages_remaining(db: Session, org: Organization) -> Optional[int]:
    """How many more billed Places pages this org may fetch this month
    (None = unlimited). The search endpoint clamps multi-page requests to
    this instead of 402-ing a search that could still return one page."""
    usage = lead_finder_usage(db, org)
    if usage["limit"] is None:
        return None
    return max(0, usage["limit"] - usage["used"])


def email_verification_usage(db: Session, org: Organization) -> dict:
    """Self-service "X of Y used" for email verifications this month."""
    from ..models.lead_finder import EmailVerificationRecord

    return {
        "used": _month_count(db, EmailVerificationRecord, org.id),
        "limit": _limits(org)["email_verifications"],
    }


def enforce_can_verify_emails(db: Session, org: Organization, count: int = 1) -> None:
    """402 when verifying `count` more addresses would exceed the monthly
    quota — batch-aware so a bulk request is atomically in or out, never
    silently truncated."""
    usage = email_verification_usage(db, org)
    cap = usage["limit"]
    if cap is not None and usage["used"] + count > cap:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Your {org.plan} plan allows {cap} email verifications per "
            f"month ({max(cap - usage['used'], 0)} remaining). Upgrade for more.",
        )


def email_outreach_usage(db: Session, org: Organization) -> dict:
    """Self-service "X of Y used" for cold-email sends this calendar month
    (UTC). Counts real prospect sends only — outbound EmailMessages with
    status="sent" that are campaign/manual (kind != "warmup"); warmup traffic
    is mailbox-reputation noise, not billable outreach, so it never meters."""
    from ..models.email_outreach import DIR_OUT, KIND_WARMUP, MSG_SENT, EmailMessage
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    month_start = dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc)
    used = db.execute(
        select(func.count())
        .select_from(EmailMessage)
        .where(
            EmailMessage.organization_id == org.id,
            EmailMessage.direction == DIR_OUT,
            EmailMessage.status == MSG_SENT,
            EmailMessage.kind != KIND_WARMUP,
            EmailMessage.created_at >= month_start,
        )
    ).scalar_one()
    return {"used": used, "limit": _limits(org)["email_sends"]}


def enforce_can_send_email(db: Session, org: Organization) -> None:
    """402 when the org has exhausted its monthly cold-email send quota. Gates
    ENROLLING contacts (enrollment implies future sends) as well as any direct
    send path, so an org can't queue a campaign it has no send budget for."""
    usage = email_outreach_usage(db, org)
    cap = usage["limit"]
    if cap is not None and usage["used"] >= cap:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Your {org.plan} plan allows {cap} cold-email sends per month. "
            "Upgrade for more.",
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
