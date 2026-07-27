"""SMS outreach API — framework surface: accounts (BYO Twilio, creds
write-only), suppression, usage. Campaign/step/enrollment routes are built by
the campaign-engine work on top of this file — mirror api/email_outreach.py's
shapes exactly (same paths under /api/sms, same status codes, 1-indexed step
positions, PUT /steps upsert-in-place with stable ids, activate guard,
archive endpoint).

Gates mirror email: require_team reads, require_admin config, client role
locked out entirely (require_team refuses it). Isolation is the org-scoped
_scoped_get pattern (404-not-403).
"""

import datetime as dt
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin, require_team
from ..models.base import utcnow
from ..models.core import Client, Organization, User
from ..models.crm import Contact, ContactList, ContactListMember
from ..models.sms_outreach import (
    SMS_ACCOUNT_ACTIVE,
    SMS_ACCOUNT_ERROR,
    SMS_CAMPAIGN_ACTIVE,
    SMS_CAMPAIGN_ARCHIVED,
    SMS_CAMPAIGN_DRAFT,
    SMS_CAMPAIGN_PAUSED,
    SMS_DIR_IN,
    SMS_DIR_OUT,
    SMS_ENROLL_ACTIVE,
    SMS_KIND_MANUAL,
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_READ,
    SMS_MSG_SENT,
    SMS_SUPPRESS_MANUAL,
    SMS_TRIGGER_REPLY,
    SmsAccount,
    SmsCampaign,
    SmsEnrollment,
    SmsMessage,
    SmsStep,
    SmsSuppression,
)
from ..schemas import (
    SmsCampaignIn,
    SmsCampaignPatch,
    SmsComposeIn,
    SmsEnrollIn,
    SmsMarkReadIn,
    SmsPreviewIn,
    SmsStepsIn,
)
from ..security import encrypt_secret
from ..services import custom_fields as custom_fields_svc
from ..services import (
    ai_provider,
    entitlements,
    feature_flags,
    research as research_svc,
    sms_campaigns,
    sms_consent,
    sms_send,
    timezones,
)

router = APIRouter(prefix="/api/sms", tags=["sms-outreach"])


def _org(db: Session, user: User) -> Organization:
    return db.get(Organization, user.organization_id)


def _scoped_get(db: Session, scope: TenantScope, model, object_id: str):
    obj = db.get(model, object_id)
    if obj is None or obj.organization_id != scope.organization_id:
        raise HTTPException(404, "Not found")
    return obj


# --- serialization (auth token never included) ---

# Below this many lifetime outbound sends on a non-Twilio account we don't warn
# about a missing inbound webhook — a brand-new account legitimately has no
# replies yet.
_INBOUND_STALE_MIN_SENDS = 20


def _account_out(db: Session, a: SmsAccount) -> dict:
    # Inbound-webhook liveness. Sendblue/BlueBubbles have no Twilio-21610-style
    # send-time opt-out self-heal, so if their inbound webhook was never
    # registered (or is misconfigured) STOP is silently never captured — a TCPA
    # exposure. Surface it: last time an inbound message landed, plus a derived
    # warning when a non-Twilio active account has sent a meaningful volume yet
    # has never received a single inbound message.
    last_inbound_at = db.execute(
        select(func.max(SmsMessage.created_at)).where(
            SmsMessage.account_id == a.id,
            SmsMessage.direction == SMS_DIR_IN,
        )
    ).scalar_one_or_none()
    inbound_webhook_stale = False
    if a.provider != "twilio" and a.status == SMS_ACCOUNT_ACTIVE and last_inbound_at is None:
        outbound_total = db.execute(
            select(func.count(SmsMessage.id)).where(
                SmsMessage.account_id == a.id,
                SmsMessage.direction == SMS_DIR_OUT,
            )
        ).scalar_one() or 0
        inbound_webhook_stale = outbound_total >= _INBOUND_STALE_MIN_SENDS
    return {
        "id": a.id,
        "name": a.name,
        "provider": a.provider,
        "account_sid": a.account_sid,
        "from_number": a.from_number,
        "messaging_service_sid": a.messaging_service_sid,
        "status": a.status,
        "error_detail": a.error_detail,
        "daily_send_cap": a.daily_send_cap,
        "sends_today": sms_send.sends_today(db, a),
        # Webhook-URL secret for unsigned-webhook providers — the org admin
        # pastes the tokened URL into the provider dashboard, so it must be
        # readable here (admin/team-gated routes only).
        "webhook_token": a.webhook_token,
        "relay_url": a.relay_url,
        "min_send_spacing_seconds": a.min_send_spacing_seconds,
        "max_send_spacing_seconds": a.max_send_spacing_seconds,
        "bluebubbles_force_sms": a.bluebubbles_force_sms,
        "channel_health": sms_send.channel_health(db, a),
        "last_inbound_at": last_inbound_at.isoformat() if last_inbound_at else None,
        "inbound_webhook_stale": inbound_webhook_stale,
        "created_at": a.created_at.isoformat(),
    }


# --- accounts ---


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # twilio: account_sid = Account SID, auth_token = Auth Token.
    # sendblue: account_sid = API Key ID, auth_token = API Secret Key.
    # bluebubbles: account_sid unused (defaults to "bluebubbles"), auth_token
    # = the BlueBubbles server password, relay_url = the VPS relay base URL.
    provider: str = Field(default="twilio", pattern="^(twilio|sendblue|bluebubbles)$")
    account_sid: Optional[str] = Field(default=None, max_length=64)
    auth_token: str = Field(min_length=8, max_length=200)
    from_number: Optional[str] = Field(default=None, max_length=20)
    messaging_service_sid: Optional[str] = Field(default=None, max_length=64)
    daily_send_cap: int = Field(default=200, ge=1, le=5000)
    relay_url: Optional[str] = Field(default=None, max_length=500)
    min_send_spacing_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    max_send_spacing_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    bluebubbles_force_sms: bool = False


class AccountPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    auth_token: Optional[str] = Field(default=None, min_length=8, max_length=200)
    from_number: Optional[str] = Field(default=None, max_length=20)
    messaging_service_sid: Optional[str] = Field(default=None, max_length=64)
    daily_send_cap: Optional[int] = Field(default=None, ge=1, le=5000)
    relay_url: Optional[str] = Field(default=None, max_length=500)
    min_send_spacing_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    max_send_spacing_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    bluebubbles_force_sms: Optional[bool] = None


@router.get("/accounts")
def list_accounts(
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(SmsAccount).where(
            SmsAccount.organization_id == scope.organization_id
        )
    ).scalars()
    return [_account_out(db, a) for a in rows]


@router.post("/accounts", status_code=201)
def create_account(
    body: AccountIn,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    if body.provider == "bluebubbles":
        # Operator allowlist (services/feature_flags): the self-hosted
        # BlueBubbles path needs a Mac + Apple ID the operator controls —
        # not offered to external orgs. UI hiding is never load-bearing.
        if not feature_flags.bluebubbles_allowed(scope.organization_id):
            raise HTTPException(
                403, "The BlueBubbles provider is not enabled for this "
                "organization — connect Twilio or Sendblue instead."
            )
        if not body.relay_url:
            raise HTTPException(422, "Provide the BlueBubbles relay URL.")
        if not body.from_number:
            raise HTTPException(422, "Provide the iMessage sending number/handle.")
        if body.messaging_service_sid:
            raise HTTPException(422, "Messaging Service SID is a Twilio concept.")
    elif body.provider == "sendblue":
        if not body.account_sid:
            raise HTTPException(422, "Provide the Account SID / API Key ID.")
        if not body.from_number:
            raise HTTPException(422, "Provide your Sendblue sending number.")
        if body.messaging_service_sid:
            raise HTTPException(
                422, "Messaging Service SID is a Twilio concept — not used "
                "with Sendblue."
            )
    else:
        if not body.account_sid:
            raise HTTPException(422, "Provide the Account SID / API Key ID.")
        if not body.from_number and not body.messaging_service_sid:
            raise HTTPException(
                422, "Provide a from number or a Messaging Service SID."
            )
    if (
        body.min_send_spacing_seconds is not None
        and body.max_send_spacing_seconds is not None
        and body.max_send_spacing_seconds < body.min_send_spacing_seconds
    ):
        raise HTTPException(422, "Max seconds between sends must be >= the minimum.")
    # BlueBubbles sends through a real Mac/Apple ID — default to a conservative
    # randomized pacing RANGE so it's not machine-gun-detectable out of the box
    # (the operator can still set their own, incl. 0 to disable). Only applied
    # when NEITHER bound is given — an explicit min with no max keeps the
    # older floor*jitter behavior instead of silently gaining a range.
    _spacing_min = body.min_send_spacing_seconds
    _spacing_max = body.max_send_spacing_seconds
    if (
        body.provider == "bluebubbles"
        and _spacing_min is None
        and _spacing_max is None
    ):
        _spacing_min = sms_send.BLUEBUBBLES_DEFAULT_SPACING_MIN_SECONDS
        _spacing_max = sms_send.BLUEBUBBLES_DEFAULT_SPACING_MAX_SECONDS
    account = SmsAccount(
        organization_id=scope.organization_id,
        name=body.name.strip(),
        provider=body.provider,
        # bluebubbles has no meaningful account_sid — the non-null column is
        # satisfied with a placeholder; auth_token carries the server
        # password for that provider instead.
        account_sid=(body.account_sid or "bluebubbles").strip(),
        auth_token_encrypted=encrypt_secret(body.auth_token.strip()),
        from_number=sms_consent.normalize_phone(body.from_number),
        messaging_service_sid=(body.messaging_service_sid or "").strip() or None,
        daily_send_cap=body.daily_send_cap,
        relay_url=(body.relay_url or "").strip() or None,
        min_send_spacing_seconds=_spacing_min,
        max_send_spacing_seconds=_spacing_max,
        bluebubbles_force_sms=(
            body.bluebubbles_force_sms and body.provider == "bluebubbles"
        ),
        # URL secret for unsigned-webhook providers (Sendblue, BlueBubbles);
        # minted for every account so a later provider switch never leaves a
        # gap.
        webhook_token=secrets.token_urlsafe(24),
    )
    ok, detail = sms_send.verify_credentials(account)
    account.status = SMS_ACCOUNT_ACTIVE if ok else SMS_ACCOUNT_ERROR
    account.error_detail = None if ok else detail
    db.add(account)
    db.commit()
    return _account_out(db, account)


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: str,
    body: AccountPatch,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account = _scoped_get(db, scope, SmsAccount, account_id)
    if body.name is not None:
        account.name = body.name.strip()
    if body.auth_token is not None:
        account.auth_token_encrypted = encrypt_secret(body.auth_token.strip())
    if body.from_number is not None:
        account.from_number = sms_consent.normalize_phone(body.from_number)
    if body.messaging_service_sid is not None:
        account.messaging_service_sid = body.messaging_service_sid.strip() or None
    if body.daily_send_cap is not None:
        account.daily_send_cap = body.daily_send_cap
    if body.relay_url is not None:
        account.relay_url = body.relay_url.strip() or None
    if body.min_send_spacing_seconds is not None:
        account.min_send_spacing_seconds = body.min_send_spacing_seconds
    if body.max_send_spacing_seconds is not None:
        account.max_send_spacing_seconds = body.max_send_spacing_seconds
    if body.bluebubbles_force_sms is not None:
        account.bluebubbles_force_sms = body.bluebubbles_force_sms
    if (
        account.min_send_spacing_seconds is not None
        and account.max_send_spacing_seconds is not None
        and account.max_send_spacing_seconds < account.min_send_spacing_seconds
    ):
        raise HTTPException(422, "Max seconds between sends must be >= the minimum.")
    db.commit()
    return _account_out(db, account)


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(
    account_id: str,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account = _scoped_get(db, scope, SmsAccount, account_id)
    in_use = db.execute(
        select(SmsCampaign.id).where(SmsCampaign.account_id == account.id).limit(1)
    ).scalar_one_or_none()
    if in_use is not None:
        raise HTTPException(
            409, "A campaign still uses this number — archive it first."
        )
    db.delete(account)
    db.commit()


@router.post("/accounts/{account_id}/test")
def test_account(
    account_id: str,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account = _scoped_get(db, scope, SmsAccount, account_id)
    ok, detail = sms_send.verify_credentials(account)
    account.status = SMS_ACCOUNT_ACTIVE if ok else SMS_ACCOUNT_ERROR
    account.error_detail = None if ok else detail
    if ok:
        # The "reconnect flow re-arms" contract: enrollments parked while
        # this account was down get scheduled again.
        sms_campaigns.rearm_account(db, account.id)
    db.commit()
    return {"ok": ok, "detail": detail}


# --- suppression ---


class SuppressIn(BaseModel):
    phone: str = Field(min_length=7, max_length=25)
    detail: Optional[str] = Field(default=None, max_length=300)


@router.get("/suppression")
def list_suppression(
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(SmsSuppression)
        .where(SmsSuppression.organization_id == scope.organization_id)
        .order_by(SmsSuppression.created_at.desc())
    ).scalars()
    return [
        {
            "id": s.id,
            "phone_e164": s.phone_e164,
            "reason": s.reason,
            "detail": s.detail,
            "created_at": s.created_at.isoformat(),
        }
        for s in rows
    ]


@router.post("/suppression", status_code=201)
def add_suppression(
    body: SuppressIn,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    number = sms_consent.normalize_phone(body.phone)
    if not number:
        raise HTTPException(422, "Not a usable phone number.")
    sms_consent.record_opt_out(
        db, scope.organization_id, number, SMS_SUPPRESS_MANUAL, detail=body.detail
    )
    db.commit()
    return {"ok": True, "phone_e164": number}


@router.delete("/suppression/{suppression_id}", status_code=204)
def delete_suppression(
    suppression_id: str,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    row = _scoped_get(db, scope, SmsSuppression, suppression_id)
    db.delete(row)
    db.commit()


# --- usage ---


@router.get("/usage")
def usage(user: User = Depends(require_team), db: Session = Depends(get_db)):
    org = _org(db, user)
    return {"sends": entitlements.sms_outreach_usage(db, org), "plan": org.plan}


# --- campaigns (campaign engine) --------------------------------------------


def _client_or_404(db: Session, scope: TenantScope, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if client is None or client.organization_id != scope.organization_id:
        raise HTTPException(404, "Unknown client")
    return client


def _rate(num: int, den: int) -> Optional[float]:
    """0-1 float, or None when the denominator is 0 (undefined, not zero)."""
    return round(num / den, 4) if den else None


def _count(db: Session, stmt) -> int:
    return db.execute(stmt).scalar_one() or 0


def _contact_stub(contact: Optional[Contact]) -> Optional[dict]:
    if contact is None:
        return None
    return {
        "id": contact.id,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "phone": contact.mobile_phone or contact.phone,
    }


# A delivery receipt moves a row sent → delivered, and an iMessage read
# receipt moves it delivered → read. Each status therefore INCLUDES its
# successors when counting the funnel — otherwise a read receipt would
# silently remove a message from the sent/delivered counts (the bug this
# replaced; sms_send._COUNTED_SENT_STATUSES fixed the same thing for caps).
_SENT_STATUSES = (SMS_MSG_SENT, SMS_MSG_DELIVERED, SMS_MSG_READ)
_DELIVERED_STATUSES = (SMS_MSG_DELIVERED, SMS_MSG_READ)


def _campaign_stats(db: Session, campaign: SmsCampaign) -> dict:
    """Computed funnel for one campaign. Definitions (all campaign-scoped):
      sent      = messages the provider accepted (incl. later receipts)
      delivered = confirmed delivered via the status callback (incl. read)
      read      = iMessage/Sendblue read receipts — the closest thing SMS has
                  to "opened"; plain carrier SMS never reports this
      failed    = messages the provider (or the network) rejected outright
      replied   = enrollments with at least one reply recorded
      replies   = total inbound messages linked to this campaign (a lead
                  texting back three times counts three here, once in replied)
      opted_out = enrollments exited via STOP/opt-out
      awaiting_reply = active enrollments parked at a reply-triggered step
    Rates (None when the denominator is 0): delivery_rate = delivered/sent,
    read_rate = read/delivered, reply_rate = replied/sent,
    opt_out_rate = opted_out/sent."""
    cid = campaign.id
    base = select(func.count(SmsMessage.id)).where(
        SmsMessage.campaign_id == cid, SmsMessage.direction == SMS_DIR_OUT
    )
    sent = _count(db, base.where(SmsMessage.status.in_(_SENT_STATUSES)))
    delivered = _count(db, base.where(SmsMessage.status.in_(_DELIVERED_STATUSES)))
    read = _count(db, base.where(SmsMessage.status == SMS_MSG_READ))
    failed = _count(db, base.where(SmsMessage.status == SMS_MSG_FAILED))
    # Reply counting excludes automated out-of-office auto-responders so the
    # numbers reflect REAL human engagement; auto_replies is surfaced on its own.
    in_base = select(func.count(SmsMessage.id)).where(
        SmsMessage.campaign_id == cid, SmsMessage.direction == SMS_DIR_IN
    )
    replies = _count(db, in_base.where(SmsMessage.is_auto_reply.is_(False)))
    auto_replies = _count(db, in_base.where(SmsMessage.is_auto_reply.is_(True)))
    failure_reasons = _failure_reasons(db, cid)

    enr = select(func.count(SmsEnrollment.id)).where(SmsEnrollment.campaign_id == cid)
    enrolled = _count(db, enr)
    active = _count(db, enr.where(SmsEnrollment.status == SMS_ENROLL_ACTIVE))
    awaiting = _count(
        db,
        enr.where(
            SmsEnrollment.status == SMS_ENROLL_ACTIVE,
            SmsEnrollment.awaiting_reply_since.is_not(None),
        ),
    )
    replied = _count(db, enr.where(SmsEnrollment.replied_at.is_not(None)))
    opted_out = _count(
        db, enr.where(SmsEnrollment.exit_reason == "opted_out")
    )
    steps_count = _count(
        db, select(func.count(SmsStep.id)).where(SmsStep.campaign_id == cid)
    )
    return {
        "steps_count": steps_count,
        "enrolled": enrolled,
        "active_enrollments": active,
        "awaiting_reply": awaiting,
        "sent": sent,
        "delivered": delivered,
        "read": read,
        "failed": failed,
        "failure_reasons": failure_reasons,
        "replied": replied,
        "replies": replies,
        "auto_replies": auto_replies,
        "opted_out": opted_out,
        "delivery_rate": _rate(delivered, sent),
        "read_rate": _rate(read, delivered),
        "reply_rate": _rate(replied, sent),
        "opt_out_rate": _rate(opted_out, sent),
    }


def _failure_reasons(db: Session, campaign_id: str) -> list:
    """Send-tracking diagnostics: failed outbound grouped by reason, most
    common first. Reason is the human error_detail when present, else the
    provider error_code, else 'Unknown'. Lets an operator see WHY sends failed
    (bad number vs carrier reject vs auth) instead of only a failure count."""
    label = func.coalesce(
        func.nullif(SmsMessage.error_detail, ""),
        func.nullif(SmsMessage.error_code, ""),
        "Unknown",
    )
    rows = db.execute(
        select(label.label("reason"), func.count(SmsMessage.id).label("n"))
        .where(
            SmsMessage.campaign_id == campaign_id,
            SmsMessage.direction == SMS_DIR_OUT,
            SmsMessage.status == SMS_MSG_FAILED,
        )
        .group_by(label)
        .order_by(func.count(SmsMessage.id).desc())
        .limit(8)
    ).all()
    return [{"reason": r.reason, "count": r.n} for r in rows]


def _step_stats(db: Session, campaign_id: str) -> dict:
    """Per-step funnel: step_id -> {sent, delivered, read, failed, replies}.
    `replies` counts inbound messages attributed to the step the lead was
    replying to (stamped by the inbound webhook going forward — historical
    inbound rows predate the linkage and simply don't appear here)."""
    out: dict = {}

    def _bucket(step_id):
        return out.setdefault(
            step_id,
            {"sent": 0, "delivered": 0, "read": 0, "failed": 0, "replies": 0},
        )

    rows = db.execute(
        select(SmsMessage.step_id, SmsMessage.direction, SmsMessage.status,
               SmsMessage.is_auto_reply, func.count(SmsMessage.id))
        .where(
            SmsMessage.campaign_id == campaign_id,
            SmsMessage.step_id.is_not(None),
        )
        .group_by(SmsMessage.step_id, SmsMessage.direction, SmsMessage.status,
                  SmsMessage.is_auto_reply)
    ).all()
    for step_id, direction, status, is_auto, n in rows:
        b = _bucket(step_id)
        if direction == SMS_DIR_IN:
            # Auto-responders don't count as real replies in the per-step funnel.
            if not is_auto:
                b["replies"] += n
            continue
        if status in _SENT_STATUSES:
            b["sent"] += n
        if status in _DELIVERED_STATUSES:
            b["delivered"] += n
        if status == SMS_MSG_READ:
            b["read"] += n
        if status == SMS_MSG_FAILED:
            b["failed"] += n
    return out


def _step_out(s: SmsStep, stats: Optional[dict] = None) -> dict:
    return {
        "id": s.id,
        "position": s.position,
        "wait_days": s.wait_days,
        "wait_minutes": s.wait_minutes,
        "trigger": s.trigger or "schedule",
        "body": s.body_template,
        "branches": s.branches or [],
        "ai_branching": s.ai_branching,
        "ai_instructions": s.ai_instructions,
        "stats": stats
        or {"sent": 0, "delivered": 0, "read": 0, "failed": 0, "replies": 0},
    }


def _campaign_out(db: Session, c: SmsCampaign, *, full: bool = False) -> dict:
    out = {
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "account_id": c.account_id,
        "client_id": c.client_id,
        "timezone": c.timezone,
        "send_window_start": c.send_window_start,
        "send_window_end": c.send_window_end,
        "send_days": c.send_days,
        "daily_cap": c.daily_cap,
        "exit_on_reply": c.exit_on_reply,
        "include_compliance_footer": c.include_compliance_footer,
        "auto_enroll_new_leads": c.auto_enroll_new_leads,
        "activated_at": c.activated_at.isoformat() if c.activated_at else None,
        "created_at": c.created_at.isoformat(),
        **_campaign_stats(db, c),
    }
    if full:
        per_step = _step_stats(db, c.id)
        out["steps"] = [
            _step_out(s, per_step.get(s.id))
            for s in db.execute(
                select(SmsStep)
                .where(SmsStep.campaign_id == c.id)
                .order_by(SmsStep.position)
            ).scalars()
        ]
    return out


@router.get("/campaigns")
def list_campaigns(
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(SmsCampaign), SmsCampaign).order_by(
        SmsCampaign.created_at.desc()
    )
    return [_campaign_out(db, c) for c in db.execute(stmt).scalars().all()]


@router.post("/campaigns", status_code=201)
def create_campaign(
    body: SmsCampaignIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    account = _scoped_get(db, scope, SmsAccount, body.account_id)
    if body.send_window_start >= body.send_window_end:
        raise HTTPException(422, "send_window_start must be before send_window_end")
    client = None
    client_id = None
    if body.client_id:
        client = _client_or_404(db, scope, body.client_id)
        client_id = client.id
    if body.auto_enroll_new_leads and client_id is None:
        raise HTTPException(
            422,
            "Auto-enroll needs a client — set client_id so the campaign knows "
            "whose new leads to enroll",
        )
    # Timezone: an explicit value (already normalized by the schema validator)
    # wins; otherwise inherit the client's tz, then the org's, then the SMS
    # default. Drives the send-window / TCPA quiet-hours evaluation.
    org = db.get(Organization, scope.organization_id)
    timezone = body.timezone or timezones.campaign_default(
        client.timezone if client else None,
        org.timezone if org else None,
        "America/New_York",
    )
    campaign = SmsCampaign(
        organization_id=scope.organization_id,
        client_id=client_id,
        name=body.name,
        status=SMS_CAMPAIGN_DRAFT,
        account_id=account.id,
        timezone=timezone,
        send_window_start=body.send_window_start,
        send_window_end=body.send_window_end,
        send_days=body.send_days if body.send_days is not None else [0, 1, 2, 3, 4],
        daily_cap=body.daily_cap,
        exit_on_reply=body.exit_on_reply,
        include_compliance_footer=body.include_compliance_footer,
        auto_enroll_new_leads=body.auto_enroll_new_leads,
    )
    db.add(campaign)
    db.commit()
    return _campaign_out(db, campaign, full=True)


@router.get("/campaigns/{campaign_id}")
def get_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    return _campaign_out(db, campaign, full=True)


@router.patch("/campaigns/{campaign_id}")
def update_campaign(
    campaign_id: str,
    body: SmsCampaignPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    data = body.model_dump(exclude_unset=True)
    if "account_id" in data:
        # Changing the sending number is only safe before the campaign runs.
        if campaign.status == SMS_CAMPAIGN_ACTIVE:
            raise HTTPException(409, "Pause the campaign before changing its number")
        _scoped_get(db, scope, SmsAccount, data["account_id"])
    if "client_id" in data and data["client_id"]:
        _client_or_404(db, scope, data["client_id"])
    start = data.get("send_window_start", campaign.send_window_start)
    end = data.get("send_window_end", campaign.send_window_end)
    if start >= end:
        raise HTTPException(422, "send_window_start must be before send_window_end")
    # Auto-enroll needs a client to scope which leads flow in — check the state
    # this patch WOULD produce, so you can't turn it on without a client_id nor
    # clear the client while it's on.
    resulting_client = data.get("client_id", campaign.client_id)
    resulting_auto = data.get("auto_enroll_new_leads", campaign.auto_enroll_new_leads)
    if resulting_auto and not resulting_client:
        raise HTTPException(
            422,
            "Auto-enroll needs a client — set client_id so the campaign knows "
            "whose new leads to enroll",
        )
    for field, value in data.items():
        setattr(campaign, field, value)
    db.commit()
    # Config edits apply on the next scheduler tick.
    return _campaign_out(db, campaign, full=True)


@router.put("/campaigns/{campaign_id}/steps")
def set_steps(
    campaign_id: str,
    body: SmsStepsIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    positions = sorted(s.position for s in body.steps)
    if positions != list(range(1, len(body.steps) + 1)):
        raise HTTPException(
            422, "step positions must be contiguous starting at 1 (1..n)"
        )
    # A typo'd or email-only token would silently render as "" in every sent
    # text — reject it here, where the author can still see it.
    custom_keys = set(
        custom_fields_svc.definitions_by_key(db, campaign.organization_id)
    )
    research_keys = research_svc.active_keys(db, campaign.organization_id)
    bad: list = []
    for s in body.steps:
        templates = [s.body] + [b.body for b in (s.branches or [])]
        for template in templates:
            for tok in sms_campaigns.unknown_tokens(template, custom_keys, research_keys):
                if tok not in bad:
                    bad.append(tok)
    if bad:
        raise HTTPException(
            422,
            "Unknown personalization token(s): "
            + ", ".join("{{%s}}" % t for t in bad)
            + ". Valid: "
            + ", ".join(sorted(sms_campaigns.SMS_KNOWN_TOKENS))
            + ", custom.<field key>",
        )
    # Upsert in place — editable while ACTIVE. Existing ids keep their row;
    # ids not in the payload are deleted; id-less entries are new steps.
    # Edits only ever affect FUTURE sends — already-sent texts are the
    # SmsMessage ledger.
    existing = {
        s.id: s
        for s in db.execute(
            select(SmsStep).where(SmsStep.campaign_id == campaign.id)
        ).scalars()
    }
    keep_ids = {s.id for s in body.steps if s.id}
    unknown_ids = keep_ids - set(existing)
    if unknown_ids:
        raise HTTPException(422, "Unknown step id(s) for this campaign")
    for step_id, row in existing.items():
        if step_id not in keep_ids:
            db.delete(row)
    # Two passes so the per-campaign unique(position) constraint never sees a
    # transient duplicate while rows swap positions: park survivors on
    # negative positions, then assign the real ones.
    for i, (step_id, row) in enumerate(existing.items()):
        if step_id in keep_ids:
            row.position = -(i + 1)
    db.flush()
    for s in body.steps:
        branches = (
            [b.model_dump() for b in s.branches] if s.branches else None
        )
        if s.id:
            row = existing[s.id]
            row.position = s.position
            row.wait_days = s.wait_days
            row.wait_minutes = s.wait_minutes
            row.trigger = s.trigger
            row.body_template = s.body
            row.branches = branches
            row.ai_branching = s.ai_branching
            row.ai_instructions = s.ai_instructions
        else:
            db.add(
                SmsStep(
                    organization_id=campaign.organization_id,
                    campaign_id=campaign.id,
                    position=s.position,
                    wait_days=s.wait_days,
                    wait_minutes=s.wait_minutes,
                    trigger=s.trigger,
                    body_template=s.body,
                    branches=branches,
                    ai_branching=s.ai_branching,
                    ai_instructions=s.ai_instructions,
                )
            )
    db.commit()
    return _campaign_out(db, campaign, full=True)


@router.post("/campaigns/{campaign_id}/activate")
def activate_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    if campaign.status not in (SMS_CAMPAIGN_DRAFT, SMS_CAMPAIGN_PAUSED):
        raise HTTPException(409, "Only a draft or paused campaign can activate")
    steps = _count(
        db, select(func.count(SmsStep.id)).where(SmsStep.campaign_id == campaign.id)
    )
    if steps == 0:
        raise HTTPException(422, "Add at least one step before activating")
    account = db.get(SmsAccount, campaign.account_id)
    if account is None or account.status != SMS_ACCOUNT_ACTIVE:
        raise HTTPException(422, "The campaign's Twilio number is not connected")
    campaign.status = SMS_CAMPAIGN_ACTIVE
    campaign.activated_at = utcnow()
    # Enrollments a tick parked while paused/disconnected stay dormant
    # otherwise — run_due only scans non-NULL next_run_at.
    sms_campaigns.rearm_parked(db, campaign)
    db.commit()
    return _campaign_out(db, campaign, full=True)


@router.post("/campaigns/{campaign_id}/pause")
def pause_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    campaign.status = SMS_CAMPAIGN_PAUSED
    db.commit()
    return _campaign_out(db, campaign, full=True)


@router.post("/campaigns/{campaign_id}/archive")
def archive_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    """Terminal state: enrollments self-park (the engine skips non-active
    campaigns) and the number becomes deletable. Un-archiving isn't offered —
    start a new campaign instead."""
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    campaign.status = SMS_CAMPAIGN_ARCHIVED
    db.commit()
    return _campaign_out(db, campaign, full=True)


@router.post("/campaigns/{campaign_id}/enroll")
def enroll_campaign(
    campaign_id: str,
    body: SmsEnrollIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    org = db.get(Organization, scope.organization_id)
    # Enrollment implies future sends — gate on the monthly send quota up
    # front (402 when already exhausted) so an org can't queue what it can't
    # send.
    entitlements.enforce_can_send_sms(db, org)
    if body.list_id:
        contact_list = scope.get_or_404(db, ContactList, body.list_id)
        contact_ids = list(
            db.execute(
                select(ContactListMember.contact_id).where(
                    ContactListMember.list_id == contact_list.id
                )
            ).scalars()
        )
        source, source_detail = "list", contact_list.name
    elif body.client_id:
        client = _client_or_404(db, scope, body.client_id)
        contact_ids = [
            cid
            for (cid,) in db.execute(
                select(Contact.id).where(
                    Contact.organization_id == scope.organization_id,
                    Contact.client_id == client.id,
                )
            ).all()
        ]
        source, source_detail = "client", client.name
    elif body.contact_ids:
        contact_ids = body.contact_ids
        source, source_detail = "manual", None
    else:
        raise HTTPException(422, "Provide contact_ids, client_id, or list_id")
    # >500 members enroll in slices through the same function, merged into
    # one receipt — enroll_contacts itself is unchanged.
    result = {"enrolled": 0, "skipped": []}
    for i in range(0, len(contact_ids), 500):
        chunk = sms_campaigns.enroll_contacts(
            db,
            campaign,
            contact_ids[i : i + 500],
            enrolled_by=user.id,
            source=source,
            source_detail=source_detail,
        )
        result["enrolled"] += chunk["enrolled"]
        result["skipped"].extend(chunk["skipped"])
    db.commit()
    return result


class CatchUpIn(BaseModel):
    # True computes the receipt without queuing anything — the UI shows the
    # real counts in a confirm step before the admin commits to sending.
    dry_run: bool = False


@router.post("/campaigns/{campaign_id}/catch-up-replies")
def catch_up_replies(
    campaign_id: str,
    body: CatchUpIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    """Queue the campaign's reply step for leads who replied before the
    campaign had reply handling (enrollments that exited "replied" under the
    old stop-on-reply behavior, or completed and then replied). Explicit and
    admin-only by design — texting past repliers is a deliberate action with
    a visible receipt, never a side effect of saving steps. Idempotent; the
    consent gate applies per lead here and again at send time."""
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    org = db.get(Organization, scope.organization_id)
    if not body.dry_run:
        # Queuing implies future sends — same monthly-quota gate as enroll.
        entitlements.enforce_can_send_sms(db, org)
    result = sms_campaigns.catch_up_past_replies(
        db, campaign, dry_run=body.dry_run
    )
    if not body.dry_run:
        db.commit()
    return result


@router.post("/campaigns/{campaign_id}/resume-completed")
def resume_completed(
    campaign_id: str,
    body: CatchUpIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    """Resume completed enrollments through steps added after they finished —
    how a newly written parting-message step reaches leads who already went
    through the sequence. Explicit + admin-only with a dry-run confirm, same
    posture as catch-up-replies; consent re-checked per lead."""
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    org = db.get(Organization, scope.organization_id)
    if not body.dry_run:
        entitlements.enforce_can_send_sms(db, org)
    result = sms_campaigns.resume_completed(db, campaign, dry_run=body.dry_run)
    if not body.dry_run:
        db.commit()
    return result


@router.get("/campaigns/{campaign_id}/enrollments")
def list_enrollments(
    campaign_id: str,
    status: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    stmt = select(SmsEnrollment).where(SmsEnrollment.campaign_id == campaign.id)
    if status:
        stmt = stmt.where(SmsEnrollment.status == status)
    stmt = stmt.order_by(SmsEnrollment.created_at.desc()).limit(min(limit, 1000))
    rows = db.execute(stmt).scalars().all()
    contacts = {
        c.id: c
        for c in db.execute(
            select(Contact).where(
                Contact.id.in_([e.contact_id for e in rows] or [""])
            )
        ).scalars()
    }
    return [
        {
            "id": e.id,
            "contact_id": e.contact_id,
            "status": e.status,
            "exit_reason": e.exit_reason,
            "current_position": e.current_position,
            "next_run_at": e.next_run_at.isoformat() if e.next_run_at else None,
            "replied_at": e.replied_at.isoformat() if e.replied_at else None,
            "awaiting_reply": e.awaiting_reply_since is not None,
            "last_reply_at": e.last_reply_at.isoformat() if e.last_reply_at else None,
            "last_reply_body": e.last_reply_body,
            "source": e.source,
            "source_detail": e.source_detail,
            "created_at": e.created_at.isoformat(),
            "contact": _contact_stub(contacts.get(e.contact_id)),
        }
        for e in rows
    ]


@router.delete("/campaigns/{campaign_id}/enrollments/{enrollment_id}")
def unenroll(
    campaign_id: str,
    enrollment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    enrollment = db.get(SmsEnrollment, enrollment_id)
    if enrollment is None or enrollment.campaign_id != campaign.id:
        raise HTTPException(404, "Not found")
    sms_campaigns.exit_manual(db, enrollment)
    db.commit()
    return {"status": "exited", "exit_reason": "manual"}


@router.post("/campaigns/{campaign_id}/preview")
def preview_campaign(
    campaign_id: str,
    body: SmsPreviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, SmsCampaign, campaign_id)
    contact = scope.get_or_404(db, Contact, body.contact_id)
    step = db.execute(
        select(SmsStep).where(
            SmsStep.campaign_id == campaign.id,
            SmsStep.position == body.position,
        )
    ).scalar_one_or_none()
    if step is None:
        raise HTTPException(404, "No step at that position")
    org = db.get(Organization, scope.organization_id)
    # enrollment=None: preview generates the ai_snippet fresh, never cached.
    # Generate it explicitly (instead of via render_full) so we can surface
    # whether an AI-instructed step produced an empty snippet — the fail-open
    # signal the org needs to know its AI provider isn't actually configured.
    snippet = ""
    if (step.ai_instructions or "").strip():
        # None = transient AI failure (unconfigured key, cap, timeout) —
        # renders as empty here, same as the send path's `or ""`.
        snippet = sms_campaigns.generate_ai_snippet(db, org, contact, step) or ""
    ai_snippet_empty = bool((step.ai_instructions or "").strip()) and not snippet.strip()
    # Reply steps: match the caller's sample reply against the response
    # branches — the same select_branch the engine uses at send time (incl.
    # the AI fallback, so the preview is honest about what would fire).
    branch_body = None
    branch_label = None
    if (step.trigger or "schedule") == SMS_TRIGGER_REPLY:
        branch_body, branch_label = sms_campaigns.select_branch(
            db, org, step, body.sample_reply
        )
    rendered = sms_campaigns.render_body(
        db, contact, step, ai_snippet=snippet, body_template=branch_body
    )
    # Show the compliance suffix too — it's what actually goes out.
    final = sms_send.apply_compliance_suffix(
        rendered,
        org.name if org else "",
        first_step=(step.position == 1),
        include_footer=campaign.include_compliance_footer,
    )
    return {
        "body": final,
        "ai_snippet_empty": ai_snippet_empty,
        "trigger": step.trigger or "schedule",
        # None = default response (no branch matched / no branches defined).
        "branch_label": branch_label,
    }


# --- messages (conversation view — SMS has no threads, just a contact-number
# keyed message list) --------------------------------------------------------


def _message_out(m: SmsMessage, contact: Optional[Contact] = None) -> dict:
    sent_at = m.created_at.isoformat() if m.direction == SMS_DIR_OUT else None
    received_at = m.created_at.isoformat() if m.direction != SMS_DIR_OUT else None
    return {
        "id": m.id,
        "account_id": m.account_id,
        "campaign_id": m.campaign_id,
        "step_id": m.step_id,
        "enrollment_id": m.enrollment_id,
        "contact_id": m.contact_id,
        "contact": _contact_stub(contact),
        "direction": m.direction,
        "kind": m.kind,
        "to_number": m.to_number,
        "from_number": m.from_number,
        "body": m.body,
        "status": m.status,
        "error_code": m.error_code,
        "error_detail": m.error_detail,
        "sent_at": sent_at,
        "received_at": received_at,
        "read_at": m.read_at.isoformat() if m.read_at else None,
        "created_at": m.created_at.isoformat(),
    }


def _raise_for_sms_code(code: str) -> None:
    if code == sms_send.SENT:
        return
    if code == sms_send.SUPPRESSED:
        raise HTTPException(409, "This number is on the suppression list")
    if code == sms_send.BLOCKED:
        raise HTTPException(409, "No recorded SMS consent for this contact")
    if code == sms_send.CAP_REACHED:
        raise HTTPException(429, "Daily send cap reached for this number")
    # FAILED
    raise HTTPException(502, "Send failed — check the number's connection")


@router.post("/compose")
def compose(
    body: SmsComposeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    """One-off manual text to a single contact — a live 1:1 conversation, not
    a campaign. Goes through the same consent/suppression/cap guards as every
    other send; skips only the campaign-specific send window (mirrors how a
    human reply behaves in the email module) and never carries the CTIA
    sender-id/opt-out footer (see sms_send.send)."""
    account = _scoped_get(db, scope, SmsAccount, body.account_id)
    contact = scope.get_or_404(db, Contact, body.contact_id)
    if sms_campaigns.segment_count(body.body) > sms_campaigns.MAX_RENDERED_SEGMENTS:
        raise HTTPException(
            422,
            f"Message is too long ({sms_campaigns.segment_count(body.body)} "
            f"segments, max {sms_campaigns.MAX_RENDERED_SEGMENTS}).",
        )
    org = _org(db, user)
    code, msg = sms_send.send(
        db,
        account,
        contact,
        body.body,
        kind=SMS_KIND_MANUAL,
        org_name=org.name if org else "",
    )
    db.commit()
    _raise_for_sms_code(code)
    return {"status": code, "message_id": msg.id if msg else None}


@router.get("/messages")
def list_messages(
    account_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    contact_id: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(SmsMessage), SmsMessage)
    if account_id:
        stmt = stmt.where(SmsMessage.account_id == account_id)
    if campaign_id:
        stmt = stmt.where(SmsMessage.campaign_id == campaign_id)
    if contact_id:
        stmt = stmt.where(SmsMessage.contact_id == contact_id)
    stmt = stmt.order_by(SmsMessage.created_at.desc()).limit(min(limit, 1000))
    rows = db.execute(stmt).scalars().all()
    contacts = {
        c.id: c
        for c in db.execute(
            select(Contact).where(
                Contact.id.in_([r.contact_id for r in rows if r.contact_id] or [""])
            )
        ).scalars()
    }
    return [_message_out(m, contacts.get(m.contact_id)) for m in rows]


@router.post("/messages/mark-read")
def mark_read(
    body: SmsMarkReadIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    """Marks every unread INBOUND message with this contact as read — SMS has
    no thread id, so the contact is the conversation key (mirrors the email
    module's per-thread mark-read, keyed differently since SMS is threadless)."""
    contact = scope.get_or_404(db, Contact, body.contact_id)
    rows = db.execute(
        select(SmsMessage).where(
            SmsMessage.organization_id == scope.organization_id,
            SmsMessage.contact_id == contact.id,
            SmsMessage.direction == SMS_DIR_IN,
            SmsMessage.read_at.is_(None),
        )
    ).scalars().all()
    now = utcnow()
    for row in rows:
        row.read_at = now
    db.commit()
    return {"marked": len(rows)}


@router.get("/conversations")
def list_conversations(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    """Inbox-lite: the latest message per contact phone number (both
    directions), newest activity first — SMS has no thread concept, so a
    contact's number is the conversation key."""
    stmt = scope.filter(select(SmsMessage), SmsMessage).order_by(
        SmsMessage.created_at.desc()
    )
    rows = db.execute(stmt.limit(2000)).scalars().all()  # recent-window scan
    by_number: dict = {}
    for m in rows:
        number = m.to_number if m.direction == SMS_DIR_OUT else m.from_number
        if not number or number in by_number:
            continue
        by_number[number] = m
    contacts = {
        c.id: c
        for c in db.execute(
            select(Contact).where(
                Contact.id.in_([m.contact_id for m in by_number.values() if m.contact_id] or [""])
            )
        ).scalars()
    }
    out = [
        {
            "phone_number": number,
            "contact": _contact_stub(contacts.get(m.contact_id)),
            "last_message": _message_out(m, contacts.get(m.contact_id)),
        }
        for number, m in by_number.items()
    ]
    out.sort(key=lambda r: r["last_message"]["created_at"], reverse=True)
    return out[: min(limit, 500)]


# --- analytics ---------------------------------------------------------------


@router.get("/analytics")
def analytics(
    campaign_id: Optional[str] = None,
    days: int = 30,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    days = max(1, min(days, 365))
    campaigns = db.execute(
        scope.filter(select(SmsCampaign), SmsCampaign)
    ).scalars().all()
    if campaign_id is not None:
        campaigns = [c for c in campaigns if c.id == campaign_id]
        if not campaigns:
            raise HTTPException(404, "Not found")
    cids = [c.id for c in campaigns]

    agg = {
        k: 0
        for k in (
            "sent", "delivered", "read", "failed", "replied", "replies",
            "auto_replies", "opted_out", "enrolled", "active_enrollments",
            "awaiting_reply",
        )
    }
    by_campaign = []
    for c in campaigns:
        st = _campaign_stats(db, c)
        for k in agg:
            agg[k] += st.get(k, 0)
        by_campaign.append({"campaign_id": c.id, "name": c.name, **st})
    totals = {
        **agg,
        "delivery_rate": _rate(agg["delivered"], agg["sent"]),
        "read_rate": _rate(agg["read"], agg["delivered"]),
        "reply_rate": _rate(agg["replied"], agg["sent"]),
        "opt_out_rate": _rate(agg["opted_out"], agg["sent"]),
    }

    since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(
        days=days - 1
    )
    by_day = _analytics_by_day(db, cids, since)
    accounts = _analytics_accounts(db, scope)
    org = db.get(Organization, scope.organization_id)
    return {
        "totals": totals,
        "by_day": by_day,
        "by_campaign": by_campaign,
        "accounts": accounts,
        # Whether the active AI provider has a resolvable key for this org.
        # AI personalization fails open to "" when unconfigured (never blocks a
        # send), so the Dashboard uses this to warn that {{ai_snippet}} is inert.
        "ai_configured": ai_provider.resolve(db, org).configured,
    }


def _day_key(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    v = value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return v.astimezone(dt.timezone.utc).date().isoformat()


def _analytics_by_day(db: Session, cids: list, since: dt.datetime) -> list:
    if not cids:
        return []
    buckets: dict = {}

    def _b(day: str) -> dict:
        return buckets.setdefault(
            day,
            {
                "date": day,
                "sent": 0,
                "delivered": 0,
                "read": 0,
                "failed": 0,
                "replied": 0,
            },
        )

    msgs = db.execute(
        select(SmsMessage.created_at, SmsMessage.status, SmsMessage.read_at).where(
            SmsMessage.campaign_id.in_(cids),
            SmsMessage.direction == SMS_DIR_OUT,
        )
    ).all()
    for created_at, status, read_at in msgs:
        aware = (
            created_at
            if created_at.tzinfo
            else created_at.replace(tzinfo=dt.timezone.utc)
        )
        if aware >= since:
            if status in _SENT_STATUSES:
                _b(_day_key(aware))["sent"] += 1
            if status in _DELIVERED_STATUSES:
                _b(_day_key(aware))["delivered"] += 1
            if status == SMS_MSG_FAILED:
                _b(_day_key(aware))["failed"] += 1
        # Reads bucket on the day the RECIPIENT read it, not the send day.
        if status == SMS_MSG_READ and read_at is not None:
            read_aware = (
                read_at if read_at.tzinfo else read_at.replace(tzinfo=dt.timezone.utc)
            )
            if read_aware >= since:
                _b(_day_key(read_aware))["read"] += 1
    replies = db.execute(
        select(SmsEnrollment.replied_at).where(
            SmsEnrollment.campaign_id.in_(cids),
            SmsEnrollment.replied_at.is_not(None),
        )
    ).all()
    for (replied_at,) in replies:
        if replied_at is None:
            continue
        aware = (
            replied_at
            if replied_at.tzinfo
            else replied_at.replace(tzinfo=dt.timezone.utc)
        )
        if aware >= since:
            _b(_day_key(aware))["replied"] += 1
    return [buckets[k] for k in sorted(buckets)]


def _analytics_accounts(db: Session, scope: TenantScope) -> list:
    accounts = db.execute(
        scope.filter(select(SmsAccount), SmsAccount)
    ).scalars().all()
    seven_days_ago = utcnow() - dt.timedelta(days=7)
    out = []
    for a in accounts:
        base = select(func.count(SmsMessage.id)).where(
            SmsMessage.account_id == a.id,
            SmsMessage.direction == SMS_DIR_OUT,
            SmsMessage.created_at >= seven_days_ago,
        )
        sent_7d = _count(db, base.where(SmsMessage.status.in_(_SENT_STATUSES)))
        failed_7d = _count(db, base.where(SmsMessage.status == SMS_MSG_FAILED))
        out.append(
            {
                "account_id": a.id,
                "from_number": a.from_number,
                "status": a.status,
                "sends_today": sms_send.sends_today(db, a),
                "daily_send_cap": a.daily_send_cap,
                "failure_rate_7d": _rate(failed_7d, sent_7d),
            }
        )
    return out
