"""Cold-email Outreach module API (Phase 1 foundation).

Role mapping mirrors the IG Outreach module:
- team (owner/admin/member): mailbox visibility, the unified inbox, manual
  replies + compose, suppression viewing.
- admin/owner: connecting/editing/removing mailboxes, editing the suppression
  list.
- client role: no access — Outreach is Organization-internal tooling. Every
  query goes through TenantScope; cross-tenant access 404s.

Campaign / sequence / analytics endpoints are Phase 2 and deliberately absent.
Every send routes through the ONE gateway (services/email_outreach_send.send),
so suppression + the verified-email gate can't be bypassed here.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin, require_team
from ..models.core import Organization, User
from ..models.crm import Contact, ContactList, ContactListMember
from ..models.base import utcnow
from ..models.email_outreach import (
    ACCOUNT_ACTIVE,
    ACCOUNT_ERROR,
    CAMPAIGN_ACTIVE,
    CAMPAIGN_ARCHIVED,
    CAMPAIGN_DRAFT,
    CAMPAIGN_PAUSED,
    DIR_OUT,
    ENROLL_ACTIVE,
    ENROLL_EXITED,
    EXIT_MANUAL,
    KIND_CAMPAIGN,
    KIND_MANUAL,
    SUPPRESS_MANUAL,
    EmailAccount,
    EmailCampaign,
    EmailEnrollment,
    EmailMessage,
    EmailStep,
    EmailSuppression,
    EmailThread,
)
from ..schemas import (
    CampaignQaIn,
    EmailAccountIn,
    EmailAccountPatch,
    EmailCampaignIn,
    EmailCampaignPatch,
    EmailComposeIn,
    EmailEnrollIn,
    EmailPreviewBatchIn,
    EmailPreviewIn,
    EmailReplyIn,
    EmailStepsIn,
    EmailSuppressionIn,
    EnrollmentOverrideIn,
)
from ..security import encrypt_secret
from ..services import ai_provider
from ..services import branding, email_campaigns, email_personalize, entitlements
from ..services import custom_fields as custom_fields_svc
from ..services import email_outreach_send as gateway
from ..services import email_transport
from ..services import email_verification
from ..services import email_warmup
from ..services import research as research_svc

router = APIRouter(prefix="/api/email-outreach", tags=["email-outreach"])


def _scoped_get(db: Session, scope: TenantScope, model, object_id: str):
    """Org-scoped single-object fetch. The email tables are org-scoped only (no
    client_id), so TenantScope.get_or_404 — which also checks obj.client_id —
    can't be used; this is the same 404-not-403 isolation rule for these
    tables. Outreach is team-only (require_team), so there is no client pin."""
    obj = db.get(model, object_id)
    if obj is None or obj.organization_id != scope.organization_id:
        raise HTTPException(404, "Not found")
    return obj


# --- serialization (password is never included) ---


def _account_out(db: Session, a: EmailAccount) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "from_name": a.from_name,
        "from_email": a.from_email,
        "smtp_host": a.smtp_host,
        "smtp_port": a.smtp_port,
        "smtp_security": a.smtp_security,
        "imap_host": a.imap_host,
        "imap_port": a.imap_port,
        "imap_security": a.imap_security,
        "smtp_username": a.smtp_username,
        "imap_username": a.imap_username,
        "status": a.status,
        "error_detail": a.error_detail,
        "daily_send_cap": a.daily_send_cap,
        "warmup_enabled": a.warmup_enabled,
        "warmup_started_at": a.warmup_started_at.isoformat() if a.warmup_started_at else None,
        "warmup_target_daily": a.warmup_target_daily,
        "warmup_timezone": a.warmup_timezone,
        "signature": a.signature,
        "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
        "last_sync_error": a.last_sync_error,
        "created_at": a.created_at.isoformat(),
        "sends_today": gateway.sends_today(db, a),
        # Warmup ramps the effective cap up over time; equals daily_send_cap
        # when warmup is off (email_warmup.effective_daily_cap).
        "effective_daily_cap": email_warmup.effective_daily_cap(a, db),
        "warmup_stage": email_warmup.warmup_stage(a),
        # Two separate numbers, per warmup industry convention: progress is
        # the deterministic ramp maturity (0-100, days into the 28-day
        # schedule), health is measured reputation (bounces/junk placement/
        # peer delivery), None until there's enough data.
        "warmup_progress": email_warmup.warmup_progress(a),
        "warmup_health": email_warmup.warmup_health(db, a),
        # Dedicated warmup UI: today's planned synthetic volume vs done, and
        # lifetime engagement counters ({sent, delivered, junk}).
        "warmup_volume_today": email_warmup.warmup_volume_today(a),
        "warmup_sends_today": email_warmup.warmup_sends_today(db, a),
        "warmup_totals": email_warmup.warmup_totals(db, a),
        # Day 10+ of the ramp: low-volume real sends should begin (research:
        # real replies out-signal any synthetic warmup engagement).
        "warmup_blended_ready": email_warmup.warmup_blended_ready(a),
    }


def _contact_stub(contact: Optional[Contact]) -> Optional[dict]:
    if contact is None:
        return None
    return {
        "id": contact.id,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "email": contact.email,
    }


def _thread_out(db: Session, t: EmailThread) -> dict:
    contact = db.get(Contact, t.contact_id) if t.contact_id else None
    return {
        "id": t.id,
        "account_id": t.account_id,
        "contact_id": t.contact_id,
        "subject": t.subject,
        "snippet": t.snippet,
        "unread": t.unread,
        "message_count": t.message_count,
        "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        "last_inbound_at": t.last_inbound_at.isoformat() if t.last_inbound_at else None,
        "created_at": t.created_at.isoformat(),
        "contact": _contact_stub(contact),
    }


def _message_out(m: EmailMessage) -> dict:
    return {
        "id": m.id,
        "thread_id": m.thread_id,
        "direction": m.direction,
        "status": m.status,
        "kind": m.kind,
        "subject": m.subject,
        "body_text": m.body_text,
        "opened_at": m.opened_at.isoformat() if m.opened_at else None,
        "open_count": m.open_count,
        "bounced_at": m.bounced_at.isoformat() if m.bounced_at else None,
        "error_detail": m.error_detail,
        "sent_at": m.sent_at.isoformat() if m.sent_at else None,
        "received_at": m.received_at.isoformat() if m.received_at else None,
        "created_at": m.created_at.isoformat(),
    }


def _suppression_out(s: EmailSuppression) -> dict:
    return {
        "id": s.id,
        "email": s.email,
        "reason": s.reason,
        "contact_id": s.contact_id,
        "created_at": s.created_at.isoformat(),
    }


# --- accounts ---


@router.get("/accounts")
def list_accounts(
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(EmailAccount), EmailAccount).order_by(
        EmailAccount.created_at.desc()
    )
    return [_account_out(db, a) for a in db.execute(stmt).scalars().all()]


def _probe_or_400(account: EmailAccount) -> None:
    result = email_transport.probe(account)
    if not (result["smtp_ok"] and result["imap_ok"]):
        raise HTTPException(
            400, result["detail"] or "Could not connect to the mailbox (SMTP/IMAP)"
        )


@router.post("/accounts", status_code=201)
def create_account(
    body: EmailAccountIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    dupe = db.execute(
        scope.filter(
            select(EmailAccount).where(
                EmailAccount.from_email == body.from_email.lower()
            ),
            EmailAccount,
        )
    ).scalar_one_or_none()
    if dupe is not None:
        raise HTTPException(409, "A mailbox with this from-address already exists")

    account = EmailAccount(
        organization_id=scope.organization_id,
        name=body.name,
        from_name=body.from_name,
        from_email=body.from_email.lower(),
        smtp_host=body.smtp_host,
        smtp_port=body.smtp_port,
        smtp_security=body.smtp_security,
        imap_host=body.imap_host,
        imap_port=body.imap_port,
        imap_security=body.imap_security,
        smtp_username=body.smtp_username,
        smtp_password_encrypted=encrypt_secret(body.smtp_password),
        imap_username=body.imap_username,
        imap_password_encrypted=encrypt_secret(body.imap_password),
        daily_send_cap=body.daily_send_cap,
        signature=body.signature,
        status=ACCOUNT_ACTIVE,
    )
    # Probe before persisting — a mailbox that can't connect is a 400, not a
    # saved-but-broken account.
    _probe_or_400(account)
    db.add(account)
    db.commit()
    return _account_out(db, account)


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: str,
    body: EmailAccountPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    account = _scoped_get(db, scope, EmailAccount, account_id)
    data = body.model_dump(exclude_unset=True)

    # Fields that change the connection re-probe before persisting.
    connection_fields = {
        "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password",
        "imap_host", "imap_port", "imap_security", "imap_username", "imap_password",
    }
    reprobe = any(f in data for f in connection_fields)

    if reprobe:
        probe_account = EmailAccount(
            organization_id=account.organization_id,
            from_email=account.from_email,
            smtp_host=data.get("smtp_host", account.smtp_host),
            smtp_port=data.get("smtp_port", account.smtp_port),
            smtp_security=data.get("smtp_security", account.smtp_security),
            smtp_username=data.get("smtp_username", account.smtp_username),
            smtp_password_encrypted=(
                encrypt_secret(data["smtp_password"])
                if "smtp_password" in data
                else account.smtp_password_encrypted
            ),
            imap_host=data.get("imap_host", account.imap_host),
            imap_port=data.get("imap_port", account.imap_port),
            imap_security=data.get("imap_security", account.imap_security),
            imap_username=data.get("imap_username", account.imap_username),
            imap_password_encrypted=(
                encrypt_secret(data["imap_password"])
                if "imap_password" in data
                else account.imap_password_encrypted
            ),
        )
        _probe_or_400(probe_account)

    # Warmup toggle: enabling starts the 28-day ramp clock; re-enabling after
    # a disable restarts it (a gap in warmup means reputation decayed — the
    # ramp must be earned again). Disabling keeps the timestamp; it's inert
    # while warmup_enabled is False.
    if data.get("warmup_enabled") is True and not account.warmup_enabled:
        account.warmup_started_at = utcnow()

    for field, value in data.items():
        if field == "smtp_password":
            account.smtp_password_encrypted = encrypt_secret(value)
        elif field == "imap_password":
            account.imap_password_encrypted = encrypt_secret(value)
        else:
            setattr(account, field, value)
    if reprobe:
        # A successful re-probe clears a prior error state — and re-arms
        # enrollments parked while the mailbox was down.
        account.status = ACCOUNT_ACTIVE
        account.error_detail = None
        email_campaigns.rearm_account(db, account.id)
    db.commit()
    return _account_out(db, account)


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    account = _scoped_get(db, scope, EmailAccount, account_id)
    live_campaign = db.execute(
        select(EmailCampaign.id).where(
            EmailCampaign.account_id == account.id,
            EmailCampaign.status != CAMPAIGN_ARCHIVED,
        ).limit(1)
    ).scalar_one_or_none()
    if live_campaign is not None:
        raise HTTPException(
            409, "This mailbox is used by an active campaign — archive it first"
        )

    # Remove the mailbox and everything anchored to it, FK-safe order. (In
    # Phase 1 only threads/messages exist; campaigns/steps/enrollments are
    # cleared too for forward-safety once Phase 2 creates them.)
    campaign_ids = [
        c for (c,) in db.execute(
            select(EmailCampaign.id).where(EmailCampaign.account_id == account.id)
        ).all()
    ]
    db.execute(
        EmailMessage.__table__.delete().where(EmailMessage.account_id == account.id)
    )
    if campaign_ids:
        db.execute(
            EmailEnrollment.__table__.delete().where(
                EmailEnrollment.campaign_id.in_(campaign_ids)
            )
        )
    db.execute(
        EmailThread.__table__.delete().where(EmailThread.account_id == account.id)
    )
    if campaign_ids:
        db.execute(
            EmailStep.__table__.delete().where(EmailStep.campaign_id.in_(campaign_ids))
        )
        db.execute(
            EmailCampaign.__table__.delete().where(
                EmailCampaign.account_id == account.id
            )
        )
    db.delete(account)
    db.commit()
    return {"status": "deleted"}


@router.post("/accounts/{account_id}/test")
def test_account(
    account_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    account = _scoped_get(db, scope, EmailAccount, account_id)
    result = email_transport.probe(account)
    ok = result["smtp_ok"] and result["imap_ok"]
    account.status = ACCOUNT_ACTIVE if ok else ACCOUNT_ERROR
    account.error_detail = None if ok else (result["detail"] or "Connection failed")
    if ok:
        # The "reconnect flow re-arms" contract: enrollments parked while
        # this mailbox was down get scheduled again.
        email_campaigns.rearm_account(db, account.id)
    db.commit()
    return {
        "ok": ok,
        "smtp_ok": result["smtp_ok"],
        "imap_ok": result["imap_ok"],
        "detail": result["detail"],
    }


# --- unified inbox ---


@router.get("/inbox")
def inbox(
    account_id: Optional[str] = None,
    unread: Optional[bool] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(EmailThread), EmailThread)
    if account_id:
        stmt = stmt.where(EmailThread.account_id == account_id)
    if unread is not None:
        stmt = stmt.where(EmailThread.unread.is_(unread))
    stmt = stmt.order_by(EmailThread.last_message_at.desc().nulls_last()).limit(
        min(limit, 500)
    )
    return [_thread_out(db, t) for t in db.execute(stmt).scalars().all()]


@router.get("/threads/{thread_id}/messages")
def thread_messages(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    thread = _scoped_get(db, scope, EmailThread, thread_id)
    msgs = db.execute(
        select(EmailMessage)
        .where(EmailMessage.thread_id == thread.id)
        .order_by(EmailMessage.created_at)
    ).scalars().all()
    return {"thread": _thread_out(db, thread), "messages": [_message_out(m) for m in msgs]}


@router.post("/threads/{thread_id}/reply")
def reply(
    thread_id: str,
    body: EmailReplyIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    thread = _scoped_get(db, scope, EmailThread, thread_id)
    account = db.get(EmailAccount, thread.account_id)
    contact = db.get(Contact, thread.contact_id)
    if contact is None:
        raise HTTPException(409, "This thread has no contact to reply to")
    # Thread the reply onto the most recent message in the conversation.
    last = db.execute(
        select(EmailMessage)
        .where(
            EmailMessage.thread_id == thread.id,
            EmailMessage.message_id_header.is_not(None),
        )
        .order_by(EmailMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    code, msg = gateway.send(
        db,
        account,
        to_contact=contact,
        subject=None,  # threaded: derived "Re: …" from the thread subject
        body_text=body.body,
        kind=KIND_MANUAL,
        in_reply_to_message=last,
    )
    db.commit()
    _raise_for_send_code(code, msg)
    return {"status": code, "message_id": msg.id if msg else None}


@router.post("/threads/{thread_id}/mark-read")
def mark_read(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    thread = _scoped_get(db, scope, EmailThread, thread_id)
    thread.unread = False
    db.commit()
    return {"status": "ok"}


@router.post("/compose")
def compose(
    body: EmailComposeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    account = _scoped_get(db, scope, EmailAccount, body.account_id)
    contact = scope.get_or_404(db, Contact, body.contact_id)
    if not contact.email:
        raise HTTPException(400, "This contact has no email address")
    code, msg = gateway.send(
        db,
        account,
        to_contact=contact,
        subject=body.subject,
        body_text=body.body,
        kind=KIND_MANUAL,
    )
    db.commit()
    _raise_for_send_code(code, msg)
    return {
        "status": code,
        "message_id": msg.id if msg else None,
        "thread_id": msg.thread_id if msg else None,
    }


def _raise_for_send_code(code: str, msg: Optional[EmailMessage]) -> None:
    if code == gateway.SENT:
        return
    if code == gateway.SUPPRESSED:
        raise HTTPException(409, "This address is on the suppression list")
    if code == gateway.BLOCKED:
        raise HTTPException(409, "This address is verified invalid — sending is blocked")
    if code == gateway.CAP_REACHED:
        raise HTTPException(429, "Daily send cap reached for this mailbox")
    # FAILED
    raise HTTPException(
        502, (msg.error_detail if msg else None) or "Send failed — check the mailbox connection"
    )


# --- suppression list ---


@router.get("/suppression")
def list_suppression(
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(EmailSuppression), EmailSuppression).order_by(
        EmailSuppression.created_at.desc()
    )
    return [_suppression_out(s) for s in db.execute(stmt).scalars().all()]


@router.post("/suppression", status_code=201)
def add_suppression(
    body: EmailSuppressionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    added = 0
    for addr in body.emails:
        existing = gateway.is_suppressed(db, scope.organization_id, addr)
        gateway.suppress(db, scope.organization_id, addr, SUPPRESS_MANUAL)
        if not existing:
            added += 1
    db.commit()
    return {"added": added, "submitted": len(body.emails)}


@router.delete("/suppression/{suppression_id}")
def delete_suppression(
    suppression_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    row = _scoped_get(db, scope, EmailSuppression, suppression_id)
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


# --- campaigns (Phase 2) ----------------------------------------------------


def _rate(num: int, den: int) -> Optional[float]:
    """0-1 float, or None when the denominator is 0 (undefined, not zero)."""
    return round(num / den, 4) if den else None


def _count(db: Session, stmt) -> int:
    return db.execute(stmt).scalar_one() or 0


def _campaign_stats(db: Session, campaign: EmailCampaign) -> dict:
    """Computed funnel + rates for one campaign. Definitions (all campaign-
    scoped, kind=campaign outbound):
      sent            = messages transmitted (sent_at set)
      bounced         = messages that hard-bounced
      delivered       = sent - bounced
      opened          = messages with an open recorded
      replied         = enrollments with a reply recorded
      unsubscribed    = enrollments exited via opt-out
    Rates (None when the denominator is 0):
      delivery_rate    = delivered / sent
      open_rate        = opened / delivered
      reply_rate       = replied / delivered
      bounce_rate      = bounced / sent
      unsubscribe_rate = unsubscribed / delivered
    """
    cid = campaign.id
    base = select(func.count(EmailMessage.id)).where(
        EmailMessage.campaign_id == cid, EmailMessage.direction == DIR_OUT
    )
    sent = _count(db, base.where(EmailMessage.sent_at.is_not(None)))
    bounced = _count(db, base.where(EmailMessage.bounced_at.is_not(None)))
    opened = _count(db, base.where(EmailMessage.opened_at.is_not(None)))
    delivered = max(0, sent - bounced)

    enr = select(func.count(EmailEnrollment.id)).where(
        EmailEnrollment.campaign_id == cid
    )
    enrolled = _count(db, enr)
    active = _count(db, enr.where(EmailEnrollment.status == ENROLL_ACTIVE))
    replied = _count(db, enr.where(EmailEnrollment.replied_at.is_not(None)))
    unsubscribed = _count(
        db, enr.where(EmailEnrollment.exit_reason == "unsubscribed")
    )
    steps_count = _count(
        db,
        select(func.count(EmailStep.id)).where(EmailStep.campaign_id == cid),
    )
    return {
        "steps_count": steps_count,
        "enrolled": enrolled,
        "active_enrollments": active,
        "sent": sent,
        "delivered": delivered,
        "opened": opened,
        "replied": replied,
        "bounced": bounced,
        "unsubscribed": unsubscribed,
        "delivery_rate": _rate(delivered, sent),
        "open_rate": _rate(opened, delivered),
        "reply_rate": _rate(replied, delivered),
        "bounce_rate": _rate(bounced, sent),
        "unsubscribe_rate": _rate(unsubscribed, delivered),
    }


def _step_out(s: EmailStep) -> dict:
    return {
        "id": s.id,
        "position": s.position,
        "wait_days": s.wait_days,
        "subject": s.subject_template,
        "body": s.body_template,
        "ai_instructions": s.ai_instructions,
    }


def _campaign_out(db: Session, c: EmailCampaign, *, full: bool = False) -> dict:
    out = {
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "account_id": c.account_id,
        "timezone": c.timezone,
        "send_window_start": c.send_window_start,
        "send_window_end": c.send_window_end,
        "send_days": c.send_days,
        "daily_cap": c.daily_cap,
        "open_tracking": c.open_tracking,
        "exit_on_reply": c.exit_on_reply,
        "require_approval": c.require_approval,
        "ai_tone": c.ai_tone,
        "ai_example": c.ai_example,
        "activated_at": c.activated_at.isoformat() if c.activated_at else None,
        "created_at": c.created_at.isoformat(),
        **_campaign_stats(db, c),
    }
    if full:
        out["steps"] = [
            _step_out(s)
            for s in db.execute(
                select(EmailStep)
                .where(EmailStep.campaign_id == c.id)
                .order_by(EmailStep.position)
            ).scalars()
        ]
    return out


@router.get("/campaigns")
def list_campaigns(
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(EmailCampaign), EmailCampaign).order_by(
        EmailCampaign.created_at.desc()
    )
    return [_campaign_out(db, c) for c in db.execute(stmt).scalars().all()]


@router.post("/campaigns", status_code=201)
def create_campaign(
    body: EmailCampaignIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    account = _scoped_get(db, scope, EmailAccount, body.account_id)
    if body.send_window_start >= body.send_window_end:
        raise HTTPException(422, "send_window_start must be before send_window_end")
    campaign = EmailCampaign(
        organization_id=scope.organization_id,
        name=body.name,
        status=CAMPAIGN_DRAFT,
        account_id=account.id,
        timezone=body.timezone,
        send_window_start=body.send_window_start,
        send_window_end=body.send_window_end,
        send_days=body.send_days if body.send_days is not None else [0, 1, 2, 3, 4],
        daily_cap=body.daily_cap,
        open_tracking=body.open_tracking,
        exit_on_reply=body.exit_on_reply,
        require_approval=body.require_approval,
        ai_tone=body.ai_tone,
        ai_example=body.ai_example,
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
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    return _campaign_out(db, campaign, full=True)


@router.patch("/campaigns/{campaign_id}")
def update_campaign(
    campaign_id: str,
    body: EmailCampaignPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    data = body.model_dump(exclude_unset=True)
    if "account_id" in data:
        # Changing the sending mailbox is only safe before the campaign runs.
        if campaign.status == CAMPAIGN_ACTIVE:
            raise HTTPException(409, "Pause the campaign before changing its mailbox")
        _scoped_get(db, scope, EmailAccount, data["account_id"])
    start = data.get("send_window_start", campaign.send_window_start)
    end = data.get("send_window_end", campaign.send_window_end)
    if start >= end:
        raise HTTPException(422, "send_window_start must be before send_window_end")
    for field, value in data.items():
        setattr(campaign, field, value)
    db.commit()
    # Config edits apply on the next scheduler tick.
    return _campaign_out(db, campaign, full=True)


@router.put("/campaigns/{campaign_id}/steps")
def set_steps(
    campaign_id: str,
    body: EmailStepsIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    positions = sorted(s.position for s in body.steps)
    if positions != list(range(1, len(body.steps) + 1)):
        raise HTTPException(
            422, "step positions must be contiguous starting at 1 (1..n)"
        )
    # A typo'd token would silently render as "" in every sent email — reject
    # it here, where the author can still see it.
    custom_keys = set(
        custom_fields_svc.definitions_by_key(db, campaign.organization_id)
    )
    research_keys = research_svc.active_keys(db, campaign.organization_id)
    bad: list = []
    for s in body.steps:
        for tok in email_personalize.unknown_tokens(s.subject, custom_keys, research_keys):
            if tok not in bad:
                bad.append(tok)
        for tok in email_personalize.unknown_tokens(s.body, custom_keys, research_keys):
            if tok not in bad:
                bad.append(tok)
    if bad:
        raise HTTPException(
            422,
            "Unknown personalization token(s): "
            + ", ".join("{{%s}}" % t for t in bad)
            + ". Valid: "
            + ", ".join(sorted(email_personalize.KNOWN_TOKENS))
            + ", custom.<field key>",
        )
    # Upsert in place — editable while ACTIVE. Existing ids keep their row (so
    # enrollment ai_snippet caches stay valid and in-flight enrollments simply
    # continue at their position number against the new step list); ids not in
    # the payload are deleted; id-less entries are new steps. Edits only ever
    # affect FUTURE sends — already-sent messages are the EmailMessage ledger.
    existing = {
        s.id: s
        for s in db.execute(
            select(EmailStep).where(EmailStep.campaign_id == campaign.id)
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
        if s.id:
            row = existing[s.id]
            row.position = s.position
            row.wait_days = s.wait_days
            row.subject_template = s.subject
            row.body_template = s.body
            row.ai_instructions = s.ai_instructions
        else:
            db.add(
                EmailStep(
                    organization_id=campaign.organization_id,
                    campaign_id=campaign.id,
                    position=s.position,
                    wait_days=s.wait_days,
                    subject_template=s.subject,
                    body_template=s.body,
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
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    if campaign.status not in (CAMPAIGN_DRAFT, CAMPAIGN_PAUSED):
        raise HTTPException(409, "Only a draft or paused campaign can activate")
    steps = _count(
        db,
        select(func.count(EmailStep.id)).where(EmailStep.campaign_id == campaign.id),
    )
    if steps == 0:
        raise HTTPException(422, "Add at least one step before activating")
    account = db.get(EmailAccount, campaign.account_id)
    if account is None or account.status != ACCOUNT_ACTIVE:
        raise HTTPException(422, "The campaign's mailbox is not connected")
    org = db.get(Organization, campaign.organization_id)
    if not (branding.merged(org).get("mailing_address") or "").strip():
        raise HTTPException(
            422,
            "Set your organization's mailing address (Branding) before sending "
            "cold email — it's required in the CAN-SPAM footer.",
        )
    campaign.status = CAMPAIGN_ACTIVE
    campaign.activated_at = utcnow()
    # Enrollments a tick parked while paused/disconnected stay dormant
    # otherwise — run_due only scans non-NULL next_run_at.
    email_campaigns.rearm_parked(db, campaign)
    db.commit()
    return _campaign_out(db, campaign, full=True)


@router.post("/campaigns/{campaign_id}/pause")
def pause_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    campaign.status = CAMPAIGN_PAUSED
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
    campaigns) and the mailbox becomes deletable. Un-archiving isn't offered —
    a stopped campaign's history should stay immutable; start a new campaign
    instead."""
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    campaign.status = CAMPAIGN_ARCHIVED
    db.commit()
    return _campaign_out(db, campaign, full=True)


@router.post("/campaigns/{campaign_id}/enroll")
def enroll_campaign(
    campaign_id: str,
    body: EmailEnrollIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    org = db.get(Organization, scope.organization_id)
    # Enrollment implies future sends — gate on the monthly send quota up front
    # (402 when already exhausted) so an org can't queue what it can't send.
    entitlements.enforce_can_send_email(db, org)
    if body.list_id:
        contact_list = scope.get_or_404(db, ContactList, body.list_id)
        contact_ids = list(
            db.execute(
                select(ContactListMember.contact_id).where(
                    ContactListMember.list_id == contact_list.id
                )
            ).scalars()
        )
    else:
        contact_ids = body.contact_ids
    # >500 members enroll in slices through the same function, merged into
    # one receipt — enroll_contacts itself is unchanged.
    result = {"enrolled": 0, "risky": [], "skipped": []}
    for i in range(0, len(contact_ids), 500):
        chunk = email_campaigns.enroll_contacts(
            db, campaign, contact_ids[i : i + 500], enrolled_by=user.id
        )
        result["enrolled"] += chunk["enrolled"]
        result["risky"].extend(chunk["risky"])
        result["skipped"].extend(chunk["skipped"])
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
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    stmt = select(EmailEnrollment).where(EmailEnrollment.campaign_id == campaign.id)
    if status:
        stmt = stmt.where(EmailEnrollment.status == status)
    stmt = stmt.order_by(EmailEnrollment.created_at.desc()).limit(min(limit, 1000))
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
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    enrollment = db.get(EmailEnrollment, enrollment_id)
    if enrollment is None or enrollment.campaign_id != campaign.id:
        raise HTTPException(404, "Not found")
    email_campaigns.exit_manual(db, enrollment)
    db.commit()
    return {"status": "exited", "exit_reason": EXIT_MANUAL}


@router.post("/campaigns/{campaign_id}/preview")
def preview_campaign(
    campaign_id: str,
    body: EmailPreviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    contact = scope.get_or_404(db, Contact, body.contact_id)
    step = db.execute(
        select(EmailStep).where(
            EmailStep.campaign_id == campaign.id,
            EmailStep.position == body.position,
        )
    ).scalar_one_or_none()
    if step is None:
        raise HTTPException(404, "No step at that position")
    org = db.get(Organization, scope.organization_id)
    subject, bodytext = email_personalize.render_full(
        db, org, None, step, campaign, contact=contact
    )
    return {"subject": subject, "body": bodytext}


# --- audience preview + QA table (Feature B) --------------------------------


def _preview_issues(
    db: Session,
    org: Organization,
    contact: Optional[Contact],
    subject,
    body,
    *,
    step: Optional[EmailStep] = None,
    enrollment: Optional[EmailEnrollment] = None,
) -> list:
    if contact is None:
        return ["not_sendable:not_found"]
    issues: list = []
    combined = (subject or "") + (body or "")
    if "{{" in combined.replace("{{unsubscribe_url}}", ""):
        issues.append("leftover_tokens")
    if not (body or "").strip():
        issues.append("blank_body")
    if not (contact.first_name or "").strip():
        issues.append("no_first_name")
    if not contact.email:
        issues.append("not_sendable:no_email")
    elif gateway.is_suppressed(db, org.id, contact.email):
        issues.append("not_sendable:suppressed")
    else:
        _ok, invalid, risky = email_verification.sendable([contact])
        if invalid:
            issues.append("not_sendable:invalid_email")
        elif risky:
            issues.append("not_sendable:risky")
    # A step that ASKS for an AI snippet but rendered without one (unconfigured
    # provider, cap hit, or the output guard discarded the response) — the
    # email still sends, just less personalized; surface it for QA review.
    if (
        step is not None
        and (step.ai_instructions or "").strip()
        and enrollment is not None
        and not ((enrollment.ai_snippets or {}).get(step.id) or "").strip()
    ):
        issues.append("ai_snippet_empty")
    return issues


@router.post("/campaigns/{campaign_id}/preview-batch")
def preview_batch(
    campaign_id: str,
    body: EmailPreviewBatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    step = db.execute(
        select(EmailStep).where(
            EmailStep.campaign_id == campaign.id,
            EmailStep.position == body.position,
        )
    ).scalar_one_or_none()
    if step is None:
        raise HTTPException(404, "No step at that position")
    org = db.get(Organization, scope.organization_id)

    base_where = (
        EmailEnrollment.campaign_id == campaign.id,
        EmailEnrollment.status == ENROLL_ACTIVE,
    )
    total = _count(
        db, select(func.count()).select_from(EmailEnrollment).where(*base_where)
    )
    rows = db.execute(
        select(EmailEnrollment)
        .where(*base_where)
        .order_by(EmailEnrollment.created_at)
        .limit(body.limit)
        .offset(body.offset)
    ).scalars().all()

    contacts = {
        c.id: c
        for c in db.execute(
            select(Contact).where(
                Contact.id.in_([e.contact_id for e in rows] or [""])
            )
        ).scalars()
    }
    out_rows = []
    for e in rows:
        contact = contacts.get(e.contact_id)
        override = (e.overrides or {}).get(step.id)
        if override is not None:
            subject = override.get("subject")
            bodytext = override.get("body") or ""
            overridden = True
        else:
            subject, bodytext = email_personalize.render_full(
                db, org, e, step, campaign, contact=contact
            )
            overridden = False
        out_rows.append(
            {
                "enrollment_id": e.id,
                "contact": _contact_stub(contact),
                "subject": subject,
                "body": bodytext,
                "overridden": overridden,
                "qa_status": e.qa_status,
                "issues": _preview_issues(
                    db, org, contact, subject, bodytext,
                    # A human-edited override is sent verbatim — an empty AI
                    # snippet is irrelevant to it, so skip the check.
                    step=None if overridden else step,
                    enrollment=e,
                ),
            }
        )
    db.commit()  # persist any newly generated+cached ai_snippets from render_full
    return {"total": total, "rows": out_rows}


def _step_at_position(db: Session, campaign_id: str, position: int) -> EmailStep:
    step = db.execute(
        select(EmailStep).where(
            EmailStep.campaign_id == campaign_id,
            EmailStep.position == position,
        )
    ).scalar_one_or_none()
    if step is None:
        raise HTTPException(404, "No step at that position")
    return step


@router.put("/enrollments/{enrollment_id}/override")
def set_override(
    enrollment_id: str,
    body: EnrollmentOverrideIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    enrollment = _scoped_get(db, scope, EmailEnrollment, enrollment_id)
    if not body.body.strip():
        raise HTTPException(422, "body must not be blank")
    step = _step_at_position(db, enrollment.campaign_id, body.position)
    overrides = dict(enrollment.overrides or {})
    overrides[step.id] = {"subject": body.subject, "body": body.body}
    enrollment.overrides = overrides
    db.commit()
    return {"status": "ok"}


@router.delete("/enrollments/{enrollment_id}/override")
def clear_override(
    enrollment_id: str,
    position: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    enrollment = _scoped_get(db, scope, EmailEnrollment, enrollment_id)
    step = _step_at_position(db, enrollment.campaign_id, position)
    overrides = dict(enrollment.overrides or {})
    overrides.pop(step.id, None)
    enrollment.overrides = overrides or None
    db.commit()
    return {"status": "ok"}


@router.post("/campaigns/{campaign_id}/qa")
def campaign_qa(
    campaign_id: str,
    body: CampaignQaIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    campaign = _scoped_get(db, scope, EmailCampaign, campaign_id)
    rows = db.execute(
        select(EmailEnrollment).where(
            EmailEnrollment.campaign_id == campaign.id,
            EmailEnrollment.id.in_(body.enrollment_ids),
        )
    ).scalars().all()
    now = utcnow()
    updated = 0
    for e in rows:
        if e.organization_id != scope.organization_id:
            continue  # cross-org/foreign-campaign ids silently skipped
        if body.action == "approve":
            e.qa_status = "approved"
            if e.next_run_at is None and e.status == ENROLL_ACTIVE:
                valid_at = email_campaigns._next_valid_send_time(now, campaign)
                e.next_run_at = valid_at or now
            updated += 1
        elif body.action == "unapprove":
            e.qa_status = None
            updated += 1
        else:  # exclude
            email_campaigns.exit_qa_excluded(db, e)
            updated += 1
    db.commit()
    return {"updated": updated}


# --- analytics + usage ------------------------------------------------------


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
        scope.filter(select(EmailCampaign), EmailCampaign)
    ).scalars().all()
    if campaign_id is not None:
        campaigns = [c for c in campaigns if c.id == campaign_id]
        if not campaigns:
            raise HTTPException(404, "Not found")
    cids = [c.id for c in campaigns]

    # Totals: aggregate the per-campaign stats (counts sum; rates recomputed).
    agg = {
        k: 0
        for k in (
            "sent", "delivered", "opened", "replied", "bounced",
            "unsubscribed", "enrolled", "active_enrollments",
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
        "open_rate": _rate(agg["opened"], agg["delivered"]),
        "reply_rate": _rate(agg["replied"], agg["delivered"]),
        "bounce_rate": _rate(agg["bounced"], agg["sent"]),
        "unsubscribe_rate": _rate(agg["unsubscribed"], agg["delivered"]),
    }

    # by_day over the window (UTC days) — sent/opened/bounced from messages,
    # replied from enrollment reply timestamps.
    since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(
        days=days - 1
    )
    by_day = _analytics_by_day(db, cids, since)

    by_step = _analytics_by_step(db, campaign_id) if campaign_id else []
    accounts = _analytics_accounts(db, scope)
    org = db.get(Organization, scope.organization_id)
    return {
        "totals": totals,
        "by_day": by_day,
        "by_campaign": by_campaign,
        "by_step": by_step,
        "accounts": accounts,
        # Whether an AI key resolves for this org (BYO or operator) — the
        # frontend banners "AI personalization off" from this on the email
        # dashboard/campaign view instead of users discovering it via empty
        # {{ai_snippet}}s in sent mail.
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
            day, {"date": day, "sent": 0, "opened": 0, "replied": 0, "bounced": 0}
        )

    msgs = db.execute(
        select(
            EmailMessage.sent_at, EmailMessage.opened_at, EmailMessage.bounced_at
        ).where(
            EmailMessage.campaign_id.in_(cids),
            EmailMessage.direction == DIR_OUT,
        )
    ).all()
    for sent_at, opened_at, bounced_at in msgs:
        for value, field in (
            (sent_at, "sent"),
            (opened_at, "opened"),
            (bounced_at, "bounced"),
        ):
            if value is None:
                continue
            aware = value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
            if aware >= since:
                _b(_day_key(aware))[field] += 1
    replies = db.execute(
        select(EmailEnrollment.replied_at).where(
            EmailEnrollment.campaign_id.in_(cids),
            EmailEnrollment.replied_at.is_not(None),
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


def _analytics_by_step(db: Session, campaign_id: str) -> list:
    steps = db.execute(
        select(EmailStep)
        .where(EmailStep.campaign_id == campaign_id)
        .order_by(EmailStep.position)
    ).scalars().all()
    out = []
    for s in steps:
        base = select(func.count(EmailMessage.id)).where(
            EmailMessage.step_id == s.id, EmailMessage.direction == DIR_OUT
        )
        sent = _count(db, base.where(EmailMessage.sent_at.is_not(None)))
        opened = _count(db, base.where(EmailMessage.opened_at.is_not(None)))
        bounced = _count(db, base.where(EmailMessage.bounced_at.is_not(None)))
        out.append(
            {
                "step_id": s.id,
                "position": s.position,
                "sent": sent,
                "opened": opened,
                "bounced": bounced,
            }
        )
    return out


def _analytics_accounts(db: Session, scope: TenantScope) -> list:
    accounts = db.execute(
        scope.filter(select(EmailAccount), EmailAccount)
    ).scalars().all()
    seven_days_ago = utcnow() - dt.timedelta(days=7)
    out = []
    for a in accounts:
        base = select(func.count(EmailMessage.id)).where(
            EmailMessage.account_id == a.id,
            EmailMessage.direction == DIR_OUT,
            EmailMessage.sent_at >= seven_days_ago,
        )
        sent_7d = _count(db, base)
        bounced_7d = _count(
            db,
            select(func.count(EmailMessage.id)).where(
                EmailMessage.account_id == a.id,
                EmailMessage.direction == DIR_OUT,
                EmailMessage.bounced_at >= seven_days_ago,
            ),
        )
        out.append(
            {
                "account_id": a.id,
                "from_email": a.from_email,
                "status": a.status,
                "sends_today": gateway.sends_today(db, a),
                "effective_daily_cap": email_warmup.effective_daily_cap(a, db),
                "warmup_stage": email_warmup.warmup_stage(a),
                "warmup_progress": email_warmup.warmup_progress(a),
                "warmup_health": email_warmup.warmup_health(db, a),
                "bounce_rate_7d": _rate(bounced_7d, sent_7d),
            }
        )
    return out


@router.get("/usage")
def usage(
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    org = db.get(Organization, scope.organization_id)
    u = entitlements.email_outreach_usage(db, org)
    return {"sends": u, "plan": org.plan}
