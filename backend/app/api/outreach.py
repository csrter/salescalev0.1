"""Outreach module API (Instagram DM automation).

Role mapping (spec Owner/Manager/Rep → existing roles):
- owner/admin  = "Manager": connect accounts, build rules/sequences, manage
  prospects, view analytics, approve pending-review sends.
- member       = "Rep": inbox visibility + manual replies only.
- client role  = no access (Outreach is Organization-internal tooling).
Every query goes through TenantScope; cross-tenant access 404s.
"""

import csv
import datetime as dt
import io
from typing import List, Optional

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin, require_team
from ..models.base import utcnow
from ..models.core import Client, User
from ..models.crm import Contact, Deal
from ..models.outreach import (
    ENROLL_ACTIVE,
    IG_ACTIVE,
    IG_DISCONNECTED,
    KIND_MANUAL,
    MSG_DISCARDED,
    MSG_PENDING_REVIEW,
    MSG_SENT,
    SEQ_ACTIVE,
    SEQ_PAUSED,
    STEP_CONDITION,
    STEP_MESSAGE,
    STEP_WAIT,
    TRIGGER_TYPES,
    InstagramAccount,
    OutreachConversation,
    OutreachEnrollment,
    OutreachMessage,
    OutreachProspect,
    OutreachSequence,
    OutreachStep,
    OutreachTriggerRule,
)
from ..security import create_state_token, decode_state_token, encrypt_secret
from ..services import instagram_api, integration_creds, meta_api
from ..services import outreach_send, outreach_sequences
from .connect_common import post_connect_response

router = APIRouter(prefix="/api/outreach", tags=["outreach"])


def _client_or_404(db: Session, scope: TenantScope, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if client is None or client.organization_id != scope.organization_id:
        raise HTTPException(404, "Unknown client")
    return client


# --- Instagram account connect / management ---


def _account_out(a: InstagramAccount) -> dict:
    return {
        "id": a.id,
        "client_id": a.client_id,
        "ig_user_id": a.ig_user_id,
        "username": a.username,
        "name": a.name,
        "status": a.status,
        "error_detail": a.error_detail,
        "daily_send_cap": a.daily_send_cap,
        "automation_paused": a.automation_paused,
        "connected_at": a.connected_at.isoformat() if a.connected_at else None,
    }


@router.get("/accounts")
def list_accounts(
    client_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(InstagramAccount), InstagramAccount)
    if client_id:
        stmt = stmt.where(InstagramAccount.client_id == client_id)
    return [_account_out(a) for a in db.execute(stmt).scalars().all()]


@router.get("/accounts/connect/start")
def connect_start(
    client_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Admin-only: begin the IG-messaging OAuth grant for one client. Signed
    state binds the callback to this org + client (connect_meta pattern)."""
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Unknown client")
    creds = integration_creds.resolve_meta(db, user.organization_id)
    if not creds.configured:
        raise HTTPException(
            503, "Meta isn't connected — add your Meta app credentials in Integrations"
        )
    integration_creds.bind(db, user.organization_id)
    state = create_state_token("ig_oauth", user.organization_id, client_id)
    return {"url": instagram_api.build_ig_oauth_url(state)}


@router.get("/accounts/callback")
def connect_callback(code: str, state: str, db: Session = Depends(get_db)):
    """Unauthenticated by necessity (browser redirect); the signed state is
    the integrity check. Imports every page-linked IG professional account
    the grant covers and subscribes each page to the webhook."""
    try:
        organization_id, client_id = decode_state_token(state, "ig_oauth")
    except pyjwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired OAuth state")
    client = db.get(Client, client_id)
    if client is None or client.organization_id != organization_id:
        raise HTTPException(400, "OAuth state does not match a known tenant")

    integration_creds.bind(db, organization_id)
    token_data = meta_api.exchange_code_for_token(code)
    long_lived = meta_api.exchange_for_long_lived_token(token_data["access_token"])
    imported = 0
    for page in instagram_api.fetch_page_ig_accounts(long_lived["access_token"]):
        ig = page.get("instagram_business_account")
        page_token = page.get("access_token")
        if not ig or not page_token:
            continue
        account = db.execute(
            select(InstagramAccount).where(
                InstagramAccount.ig_user_id == str(ig["id"])
            )
        ).scalar_one_or_none()
        if account is not None and account.organization_id != organization_id:
            # An IG account can only live in one tenant; never silently move it.
            continue
        if account is None:
            account = InstagramAccount(
                organization_id=organization_id,
                client_id=client_id,
                ig_user_id=str(ig["id"]),
                daily_send_cap=get_settings().outreach_default_daily_cap,
            )
            db.add(account)
        account.client_id = client_id
        account.page_id = str(page.get("id") or "")
        account.username = ig.get("username")
        account.name = ig.get("name")
        account.access_token_encrypted = encrypt_secret(page_token)
        account.status = IG_ACTIVE
        account.error_detail = None
        account.connected_at = utcnow()
        try:
            instagram_api.subscribe_page_webhooks(page_token, account.page_id)
        except Exception:
            pass  # webhook subscription is retryable from settings
        imported += 1
        # Reconnect re-arms enrollments parked on the auth loss.
        db.flush()
        for e in db.execute(
            select(OutreachEnrollment)
            .join(
                OutreachConversation,
                OutreachEnrollment.conversation_id == OutreachConversation.id,
            )
            .where(
                OutreachConversation.account_id == account.id,
                OutreachEnrollment.status == ENROLL_ACTIVE,
                OutreachEnrollment.next_run_at.is_(None),
                OutreachEnrollment.waiting_window.is_(False),
            )
        ).scalars():
            e.next_run_at = utcnow()
    db.commit()
    if imported == 0:
        raise HTTPException(
            400,
            "No Instagram professional account is linked to the pages this "
            "login can manage — link one in Meta Business settings first",
        )
    return post_connect_response(client_id, "instagram")


class AccountPatch(BaseModel):
    daily_send_cap: Optional[int] = Field(None, ge=1, le=1000)
    automation_paused: Optional[bool] = None


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: str,
    body: AccountPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    account = scope.get_or_404(db, InstagramAccount, account_id)
    if body.daily_send_cap is not None:
        account.daily_send_cap = body.daily_send_cap
    if body.automation_paused is not None:
        account.automation_paused = body.automation_paused
    db.commit()
    return _account_out(account)


@router.delete("/accounts/{account_id}")
def disconnect_account(
    account_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    account = scope.get_or_404(db, InstagramAccount, account_id)
    account.status = IG_DISCONNECTED
    account.access_token_encrypted = None
    account.error_detail = "Disconnected by " + user.email
    db.commit()
    return {"status": "disconnected"}


# --- Trigger rules ---


class RuleBody(BaseModel):
    account_id: str
    name: str
    trigger_type: str
    enabled: bool = True
    keywords: List[str] = []
    media_ids: List[str] = []
    filters: dict = {}
    reply_text: Optional[str] = None
    create_contact: bool = True
    tag_names: List[str] = []
    enroll_sequence_id: Optional[str] = None
    capture_prospect: bool = False
    once_per_user: bool = True


def _rule_out(r: OutreachTriggerRule) -> dict:
    return {
        "id": r.id,
        "client_id": r.client_id,
        "account_id": r.account_id,
        "name": r.name,
        "enabled": r.enabled,
        "trigger_type": r.trigger_type,
        "keywords": r.keywords or [],
        "media_ids": r.media_ids or [],
        "filters": r.filters or {},
        "reply_text": r.reply_text,
        "create_contact": r.create_contact,
        "tag_names": r.tag_names or [],
        "enroll_sequence_id": r.enroll_sequence_id,
        "capture_prospect": r.capture_prospect,
        "once_per_user": r.once_per_user,
    }


@router.get("/rules")
def list_rules(
    client_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(OutreachTriggerRule), OutreachTriggerRule)
    if client_id:
        stmt = stmt.where(OutreachTriggerRule.client_id == client_id)
    return [_rule_out(r) for r in db.execute(stmt).scalars().all()]


def _validate_rule_refs(db: Session, scope: TenantScope, body: RuleBody) -> InstagramAccount:
    if body.trigger_type not in TRIGGER_TYPES:
        raise HTTPException(400, f"Unknown trigger_type {body.trigger_type}")
    account = scope.get_or_404(db, InstagramAccount, body.account_id)
    if body.enroll_sequence_id:
        seq = scope.get_or_404(db, OutreachSequence, body.enroll_sequence_id)
        if seq.client_id != account.client_id:
            raise HTTPException(400, "Sequence belongs to a different client")
    return account


@router.post("/rules", status_code=201)
def create_rule(
    body: RuleBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    account = _validate_rule_refs(db, scope, body)
    rule = OutreachTriggerRule(
        organization_id=scope.organization_id,
        client_id=account.client_id,
        **body.model_dump(),
    )
    db.add(rule)
    db.commit()
    return _rule_out(rule)


@router.put("/rules/{rule_id}")
def update_rule(
    rule_id: str,
    body: RuleBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    rule = scope.get_or_404(db, OutreachTriggerRule, rule_id)
    account = _validate_rule_refs(db, scope, body)
    for key, value in body.model_dump().items():
        setattr(rule, key, value)
    rule.client_id = account.client_id
    db.commit()
    return _rule_out(rule)


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    rule = scope.get_or_404(db, OutreachTriggerRule, rule_id)
    if _rule_has_sends(db, rule_id):
        # Messages keep their rule_id linkage for the audit trail — a rule
        # that has fired is disabled, never hard-deleted.
        rule.enabled = False
    else:
        db.delete(rule)
    db.commit()
    return {"status": "deleted"}


def _rule_has_sends(db: Session, rule_id: str) -> bool:
    return (
        db.execute(
            select(OutreachMessage.id).where(OutreachMessage.rule_id == rule_id).limit(1)
        ).scalar_one_or_none()
        is not None
    )


# --- Sequences + steps ---


class StepBody(BaseModel):
    kind: str
    text_a: Optional[str] = None
    text_b: Optional[str] = None
    wait_hours: Optional[int] = Field(None, ge=0, le=24 * 90)
    condition: Optional[str] = None
    on_true: Optional[str] = None
    on_false: Optional[str] = None


class SequenceBody(BaseModel):
    account_id: str
    name: str
    description: Optional[str] = None
    review_first_day: bool = False
    exit_on_reply: bool = True
    settings: dict = {}


def _step_out(s: OutreachStep) -> dict:
    return {
        "id": s.id,
        "position": s.position,
        "kind": s.kind,
        "text_a": s.text_a,
        "text_b": s.text_b,
        "promoted_variant": s.promoted_variant,
        "wait_hours": s.wait_hours,
        "condition": s.condition,
        "on_true": s.on_true,
        "on_false": s.on_false,
    }


def _sequence_out(db: Session, s: OutreachSequence, with_steps: bool = False) -> dict:
    out = {
        "id": s.id,
        "client_id": s.client_id,
        "account_id": s.account_id,
        "name": s.name,
        "description": s.description,
        "status": s.status,
        "review_first_day": s.review_first_day,
        "exit_on_reply": s.exit_on_reply,
        "settings": s.settings or {},
        "activated_at": s.activated_at.isoformat() if s.activated_at else None,
    }
    if with_steps:
        out["steps"] = [
            _step_out(st)
            for st in db.execute(
                select(OutreachStep)
                .where(OutreachStep.sequence_id == s.id)
                .order_by(OutreachStep.position)
            ).scalars()
        ]
    return out


@router.get("/sequences")
def list_sequences(
    client_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(OutreachSequence), OutreachSequence)
    if client_id:
        stmt = stmt.where(OutreachSequence.client_id == client_id)
    return [_sequence_out(db, s) for s in db.execute(stmt).scalars().all()]


@router.post("/sequences", status_code=201)
def create_sequence(
    body: SequenceBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    account = scope.get_or_404(db, InstagramAccount, body.account_id)
    seq = OutreachSequence(
        organization_id=scope.organization_id,
        client_id=account.client_id,
        **body.model_dump(),
    )
    db.add(seq)
    db.commit()
    return _sequence_out(db, seq)


@router.get("/sequences/{sequence_id}")
def get_sequence(
    sequence_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    seq = scope.get_or_404(db, OutreachSequence, sequence_id)
    return _sequence_out(db, seq, with_steps=True)


@router.put("/sequences/{sequence_id}")
def update_sequence(
    sequence_id: str,
    body: SequenceBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    seq = scope.get_or_404(db, OutreachSequence, sequence_id)
    account = scope.get_or_404(db, InstagramAccount, body.account_id)
    for key, value in body.model_dump().items():
        setattr(seq, key, value)
    seq.client_id = account.client_id
    db.commit()
    return _sequence_out(db, seq, with_steps=True)


@router.put("/sequences/{sequence_id}/steps")
def replace_steps(
    sequence_id: str,
    steps: List[StepBody],
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    """The builder saves the whole step list at once — simplest correct model
    for reordering. Existing enrollments keep flowing by position."""
    seq = scope.get_or_404(db, OutreachSequence, sequence_id)
    for step in steps:
        if step.kind not in (STEP_MESSAGE, STEP_WAIT, STEP_CONDITION):
            raise HTTPException(400, f"Unknown step kind {step.kind}")
        if step.kind == STEP_MESSAGE and not (step.text_a or "").strip():
            raise HTTPException(400, "Message steps need text")
        if step.kind == STEP_WAIT and not step.wait_hours:
            raise HTTPException(400, "Wait steps need wait_hours")
    for old in db.execute(
        select(OutreachStep).where(OutreachStep.sequence_id == seq.id)
    ).scalars():
        db.delete(old)
    db.flush()
    for i, step in enumerate(steps):
        db.add(
            OutreachStep(
                organization_id=scope.organization_id,
                sequence_id=seq.id,
                position=i,
                **step.model_dump(),
            )
        )
    db.commit()
    return _sequence_out(db, seq, with_steps=True)


@router.post("/sequences/{sequence_id}/activate")
def activate_sequence(
    sequence_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    seq = scope.get_or_404(db, OutreachSequence, sequence_id)
    has_steps = (
        db.execute(
            select(OutreachStep.id).where(OutreachStep.sequence_id == seq.id).limit(1)
        ).scalar_one_or_none()
        is not None
    )
    if not has_steps:
        raise HTTPException(400, "Add at least one step before activating")
    seq.status = SEQ_ACTIVE
    if seq.activated_at is None:
        seq.activated_at = utcnow()
    db.commit()
    return _sequence_out(db, seq)


@router.post("/sequences/{sequence_id}/pause")
def pause_sequence(
    sequence_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    seq = scope.get_or_404(db, OutreachSequence, sequence_id)
    seq.status = SEQ_PAUSED
    db.commit()
    return _sequence_out(db, seq)


# --- Enrollments ---


class EnrollBody(BaseModel):
    sequence_id: str
    conversation_id: str


@router.post("/enrollments", status_code=201)
def create_enrollment(
    body: EnrollBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    seq = scope.get_or_404(db, OutreachSequence, body.sequence_id)
    convo = scope.get_or_404(db, OutreachConversation, body.conversation_id)
    if seq.client_id != convo.client_id:
        raise HTTPException(400, "Sequence belongs to a different client")
    enrollment = outreach_sequences.enroll(db, seq, convo, enrolled_by="manual")
    db.commit()
    return {"id": enrollment.id, "status": enrollment.status}


@router.delete("/enrollments/{enrollment_id}")
def unenroll(
    enrollment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    enrollment = scope.get_or_404(db, OutreachEnrollment, enrollment_id)
    outreach_sequences.exit_manual(db, enrollment)
    db.commit()
    return {"status": "exited"}


# --- Unified inbox (Rep-accessible) ---


def _convo_out(db: Session, c: OutreachConversation) -> dict:
    contact = db.get(Contact, c.contact_id) if c.contact_id else None
    enrollments = db.execute(
        select(OutreachEnrollment, OutreachSequence.name)
        .join(OutreachSequence, OutreachEnrollment.sequence_id == OutreachSequence.id)
        .where(OutreachEnrollment.conversation_id == c.id)
    ).all()
    deal_value = None
    if contact is not None:
        deal_value = db.execute(
            select(func.sum(Deal.value_cents)).where(
                Deal.contact_id == contact.id, Deal.status == "open"
            )
        ).scalar_one()
    return {
        "id": c.id,
        "client_id": c.client_id,
        "account_id": c.account_id,
        "ig_user_id": c.ig_user_id,
        "peer": c.peer or {},
        "contact_id": c.contact_id,
        "contact_name": (
            " ".join(p for p in [contact.first_name, contact.last_name] if p)
            if contact
            else None
        ),
        "window_open": outreach_send.window_open(c),
        "human_agent_available": outreach_send.human_agent_allowed(c),
        "last_user_message_at": (
            c.last_user_message_at.isoformat() if c.last_user_message_at else None
        ),
        "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
        "last_message_preview": c.last_message_preview,
        "unread_count": c.unread_count,
        "enrollments": [
            {"id": e.id, "sequence_name": name, "status": e.status,
             "exit_reason": e.exit_reason}
            for e, name in enrollments
        ],
        "deal_value_cents": int(deal_value) if deal_value else None,
        "qualified": bool(contact.qualified_at) if contact else False,
    }


@router.get("/inbox")
def inbox(
    client_id: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(OutreachConversation), OutreachConversation)
    if client_id:
        stmt = stmt.where(OutreachConversation.client_id == client_id)
    stmt = stmt.order_by(OutreachConversation.last_message_at.desc()).limit(
        min(limit, 500)
    )
    rows = db.execute(stmt).scalars().all()
    out = [_convo_out(db, c) for c in rows]
    if q:
        needle = q.lower()
        out = [
            c
            for c in out
            if needle in (c["peer"].get("username") or "").lower()
            or needle in (c["contact_name"] or "").lower()
            or needle in (c["last_message_preview"] or "").lower()
        ]
    return out


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    convo = scope.get_or_404(db, OutreachConversation, conversation_id)
    msgs = db.execute(
        select(OutreachMessage)
        .where(OutreachMessage.conversation_id == convo.id)
        .order_by(OutreachMessage.created_at)
    ).scalars()
    return [
        {
            "id": m.id,
            "direction": m.direction,
            "text": m.text,
            "status": m.status,
            "kind": m.kind,
            "variant": m.variant,
            "event_type": m.event_type,
            "message_tag": m.message_tag,
            "error_detail": m.error_detail,
            "sent_at": m.sent_at.isoformat() if m.sent_at else None,
            "created_at": m.created_at.isoformat(),
        }
        for m in msgs
    ]


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    use_human_agent: bool = False


@router.post("/conversations/{conversation_id}/reply")
def manual_reply(
    conversation_id: str,
    body: ReplyBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),  # Rep-accessible: manual replies
    scope: TenantScope = Depends(get_scope),
):
    convo = scope.get_or_404(db, OutreachConversation, conversation_id)
    account = db.get(InstagramAccount, convo.account_id)
    code, msg = outreach_send.send(
        db,
        account,
        convo,
        body.text,
        kind=KIND_MANUAL,
        sent_by_user_id=user.id,
        use_human_agent=body.use_human_agent,
    )
    db.commit()
    if code == outreach_send.AUTH_ERROR:
        raise HTTPException(409, "Instagram account needs to be reconnected")
    if code == outreach_send.CAP_REACHED:
        raise HTTPException(429, "Daily send cap reached for this account")
    if code == outreach_send.FAILED:
        raise HTTPException(400, msg.error_detail or "Send failed")
    return {"status": code, "message_id": msg.id if msg else None}


@router.post("/conversations/{conversation_id}/read")
def mark_read(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
):
    convo = scope.get_or_404(db, OutreachConversation, conversation_id)
    convo.unread_count = 0
    db.commit()
    return {"status": "ok"}


# --- Pending-review approvals (first-day safety toggle) ---


@router.get("/messages/pending")
def pending_messages(
    client_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(
        select(OutreachMessage).where(OutreachMessage.status == MSG_PENDING_REVIEW),
        OutreachMessage,
    )
    if client_id:
        stmt = stmt.where(OutreachMessage.client_id == client_id)
    return [
        {"id": m.id, "conversation_id": m.conversation_id, "text": m.text,
         "created_at": m.created_at.isoformat()}
        for m in db.execute(stmt).scalars().all()
    ]


@router.post("/messages/{message_id}/approve")
def approve_message(
    message_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    msg = scope.get_or_404(db, OutreachMessage, message_id)
    if msg.status != MSG_PENDING_REVIEW:
        raise HTTPException(400, "Message is not pending review")
    code, msg = outreach_send.release_pending(db, msg)
    db.commit()
    return {"status": code}


@router.post("/messages/{message_id}/discard")
def discard_message(
    message_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    msg = scope.get_or_404(db, OutreachMessage, message_id)
    if msg.status != MSG_PENDING_REVIEW:
        raise HTTPException(400, "Message is not pending review")
    msg.status = MSG_DISCARDED
    db.commit()
    return {"status": "discarded"}


# --- Prospects (watch list) ---


class ProspectImportBody(BaseModel):
    client_id: str
    handles: List[str] = Field(min_length=1, max_length=1000)
    vertical: Optional[str] = None
    sequence_id: Optional[str] = None
    account_id: Optional[str] = None


def _prospect_out(p: OutreachProspect) -> dict:
    return {
        "id": p.id,
        "client_id": p.client_id,
        "username": p.username,
        "ig_user_id": p.ig_user_id,
        "source": p.source,
        "status": p.status,
        "vertical": p.vertical,
        "enrichment": p.enrichment or {},
        "contact_id": p.contact_id,
        "conversation_id": p.conversation_id,
        "sequence_id": p.sequence_id,
        "engaged_at": p.engaged_at.isoformat() if p.engaged_at else None,
        "created_at": p.created_at.isoformat(),
    }


@router.get("/prospects")
def list_prospects(
    client_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    stmt = scope.filter(select(OutreachProspect), OutreachProspect)
    if client_id:
        stmt = stmt.where(OutreachProspect.client_id == client_id)
    return [
        _prospect_out(p)
        for p in db.execute(
            stmt.order_by(OutreachProspect.created_at.desc()).limit(1000)
        ).scalars()
    ]


@router.post("/prospects/import", status_code=201)
def import_prospects(
    body: ProspectImportBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    """Watch-list import (CRM lists, Google-Maps-sourced handles, …). These
    are NOT cold-send targets: the engine enrolls them into `sequence_id`
    only when they engage. Validation/enrichment runs via Business Discovery
    per prospect afterwards."""
    client = _client_or_404(db, scope, body.client_id)
    if body.sequence_id:
        scope.get_or_404(db, OutreachSequence, body.sequence_id)
    if body.account_id:
        scope.get_or_404(db, InstagramAccount, body.account_id)
    created = 0
    seen: set[str] = set()  # in-batch dedupe (session autoflush is off)
    for raw in body.handles:
        handle = raw.strip().lstrip("@").lower()
        if not handle or handle in seen:
            continue
        seen.add(handle)
        exists = db.execute(
            select(OutreachProspect.id).where(
                OutreachProspect.client_id == client.id,
                OutreachProspect.username == handle,
            )
        ).scalar_one_or_none()
        if exists:
            continue
        db.add(
            OutreachProspect(
                organization_id=scope.organization_id,
                client_id=client.id,
                account_id=body.account_id,
                username=handle,
                source="import",
                vertical=body.vertical,
                sequence_id=body.sequence_id,
            )
        )
        created += 1
    db.commit()
    return {"created": created, "skipped": len(body.handles) - created}


@router.post("/prospects/{prospect_id}/enrich")
def enrich_prospect(
    prospect_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    """Validate the handle + pull public business fields via Business
    Discovery (API-provided data only — the compliant enrichment path)."""
    prospect = scope.get_or_404(db, OutreachProspect, prospect_id)
    account = None
    if prospect.account_id:
        account = db.get(InstagramAccount, prospect.account_id)
    if account is None:
        account = db.execute(
            scope.filter(
                select(InstagramAccount).where(InstagramAccount.status == IG_ACTIVE),
                InstagramAccount,
            ).limit(1)
        ).scalar_one_or_none()
    if account is None or account.status != IG_ACTIVE:
        raise HTTPException(409, "Connect an Instagram account first")
    from ..security import decrypt_secret

    try:
        data = instagram_api.business_discovery(
            decrypt_secret(account.access_token_encrypted),
            account.ig_user_id,
            prospect.username,
        )
    except instagram_api.MetaApiError as e:
        prospect.enrichment = {**(prospect.enrichment or {}), "error": str(e)}
        db.commit()
        return {"status": "invalid", "detail": str(e)}
    if data.get("id"):
        prospect.ig_user_id = str(data["id"])
    prospect.enrichment = {
        k: data.get(k)
        for k in ("name", "biography", "website", "followers_count", "media_count")
        if data.get(k) is not None
    }
    db.commit()
    return {"status": "ok", "prospect": _prospect_out(prospect)}


@router.delete("/prospects/{prospect_id}")
def delete_prospect(
    prospect_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    prospect = scope.get_or_404(db, OutreachProspect, prospect_id)
    db.delete(prospect)
    db.commit()
    return {"status": "deleted"}


# --- Analytics ---


@router.get("/analytics")
def analytics(
    client_id: Optional[str] = None,
    days: int = 30,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    since = utcnow() - dt.timedelta(days=max(1, min(days, 365)))

    def scoped(model):
        stmt = scope.filter(select(model), model)
        if client_id:
            stmt = stmt.where(model.client_id == client_id)
        return stmt

    msgs = db.execute(
        scoped(OutreachMessage).where(OutreachMessage.created_at >= since)
    ).scalars().all()
    sent = [m for m in msgs if m.direction == "out" and m.status == MSG_SENT]
    received = [m for m in msgs if m.direction == "in"]
    enrollments = db.execute(
        scoped(OutreachEnrollment).where(OutreachEnrollment.created_at >= since)
    ).scalars().all()

    # Funnel per sequence: enrolled → sent → replied → booked (deal exists) →
    # closed (deal won). Delivered isn't surfaced by IG webhooks; sent is the
    # honest column.
    sequences = db.execute(scoped(OutreachSequence)).scalars().all()
    seq_rows = []
    for seq in sequences:
        seq_enr = [e for e in enrollments if e.sequence_id == seq.id]
        seq_sent = [m for m in sent if m.enrollment_id in {e.id for e in seq_enr}]
        replied = [e for e in seq_enr if e.replied_at is not None]
        contact_ids = {e.contact_id for e in seq_enr if e.contact_id}
        booked = closed = 0
        if contact_ids:
            deals = db.execute(
                select(Deal).where(Deal.contact_id.in_(contact_ids))
            ).scalars().all()
            booked = len({d.contact_id for d in deals})
            closed = len({d.contact_id for d in deals if d.status == "won"})
        # variant leaderboard per step
        steps = db.execute(
            select(OutreachStep)
            .where(OutreachStep.sequence_id == seq.id, OutreachStep.kind == "message")
            .order_by(OutreachStep.position)
        ).scalars().all()
        variants = []
        for st in steps:
            if not st.text_b:
                continue
            row = {"step_position": st.position, "promoted": st.promoted_variant}
            for v in ("a", "b"):
                v_msgs = [m for m in sent if m.step_id == st.id and m.variant == v]
                row[v] = {
                    "sent": len(v_msgs),
                    "replies": sum(1 for m in v_msgs if m.replied_to),
                }
            variants.append(row)
        seq_rows.append(
            {
                "sequence_id": seq.id,
                "name": seq.name,
                "status": seq.status,
                "enrolled": len(seq_enr),
                "sent": len(seq_sent),
                "replied": len(replied),
                "booked": booked,
                "closed": closed,
                "reply_rate": round(len(replied) / len(seq_enr), 3) if seq_enr else 0,
                "variants": variants,
            }
        )

    # Per trigger rule.
    rules = db.execute(scoped(OutreachTriggerRule)).scalars().all()
    rule_rows = []
    for rule in rules:
        fired = [m for m in msgs if m.rule_id == rule.id and m.direction == "out"]
        rule_rows.append(
            {
                "rule_id": rule.id,
                "name": rule.name,
                "trigger_type": rule.trigger_type,
                "fired": len(fired),
                "sent": sum(1 for m in fired if m.status == MSG_SENT),
                "replies": sum(1 for m in fired if m.replied_to),
            }
        )

    # Business-vertical breakdown from prospect verticals.
    prospects = db.execute(scoped(OutreachProspect)).scalars().all()
    verticals: dict = {}
    for p in prospects:
        key = p.vertical or "(unset)"
        row = verticals.setdefault(
            key, {"vertical": key, "prospects": 0, "engaged": 0}
        )
        row["prospects"] += 1
        if p.status == "engaged":
            row["engaged"] += 1

    # Time-to-reply: inbound following an outbound, same conversation.
    deltas = []
    by_convo: dict = {}
    for m in sorted(msgs, key=lambda m: m.created_at):
        if m.direction == "out" and m.status == MSG_SENT:
            by_convo[m.conversation_id] = m.sent_at or m.created_at
        elif m.direction == "in" and m.conversation_id in by_convo:
            start = by_convo.pop(m.conversation_id)
            deltas.append((m.created_at - start).total_seconds())
    avg_reply_seconds = int(sum(deltas) / len(deltas)) if deltas else None

    return {
        "headline": {
            "sent": len(sent),
            "received": len(received),
            "reply_rate": (
                round(
                    sum(1 for e in enrollments if e.replied_at) / len(enrollments), 3
                )
                if enrollments
                else 0
            ),
            "active_enrollments": sum(
                1 for e in enrollments if e.status == ENROLL_ACTIVE
            ),
            "avg_reply_seconds": avg_reply_seconds,
        },
        "sequences": seq_rows,
        "rules": rule_rows,
        "verticals": sorted(verticals.values(), key=lambda r: -r["prospects"]),
    }


@router.get("/audit/export")
def export_audit(
    client_id: Optional[str] = None,
    days: int = 90,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
):
    """CSV of every outbound message (the per-tenant audit trail): trigger,
    text, timestamps, status, API response id."""
    since = utcnow() - dt.timedelta(days=max(1, min(days, 365)))
    stmt = scope.filter(
        select(OutreachMessage).where(
            OutreachMessage.direction == "out", OutreachMessage.created_at >= since
        ),
        OutreachMessage,
    )
    if client_id:
        stmt = stmt.where(OutreachMessage.client_id == client_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["created_at", "sent_at", "conversation_id", "kind", "rule_id",
         "enrollment_id", "step_id", "variant", "status", "message_tag",
         "text", "external_mid", "error_detail"]
    )
    for m in db.execute(stmt.order_by(OutreachMessage.created_at)).scalars():
        writer.writerow(
            [m.created_at.isoformat(), m.sent_at.isoformat() if m.sent_at else "",
             m.conversation_id, m.kind or "", m.rule_id or "", m.enrollment_id or "",
             m.step_id or "", m.variant or "", m.status, m.message_tag or "",
             m.text or "", m.external_mid or "", m.error_detail or ""]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=outreach-audit.csv"},
    )


# --- Scheduler tick (also callable on demand: desktop mode / tests) ---


@router.post("/run-tick")
def run_tick(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    processed = outreach_sequences.run_due(db)
    return {"processed": processed}
