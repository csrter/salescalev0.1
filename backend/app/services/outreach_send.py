"""The one gateway every outbound Instagram message goes through.

Nothing else in the module calls instagram_api.send_* directly. That is what
makes the guardrails enforceable in one place:
- 24h messaging window checked BEFORE the API call; automated sends outside
  it queue until the window reopens (never a tag). Manual replies may use
  HUMAN_AGENT within 7 days — a human typed those, which is Meta's condition
  for the tag.
- Per-account daily cap enforced server-side.
- Every attempt (sent/failed/queued) is an append-only OutreachMessage row
  with trigger provenance + raw API response = the audit log.
- MetaAuthError flips the account to disconnected so the UI shows a
  reconnect banner and the engine stops trying, instead of silently failing.
"""

import datetime as dt
import logging
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.base import utcnow
from ..models.core import Client
from ..models.crm import Company, Contact
from ..models.outreach import (
    DIR_OUT,
    HUMAN_AGENT_WINDOW_DAYS,
    IG_DISCONNECTED,
    KIND_MANUAL,
    MSG_FAILED,
    MSG_PENDING_REVIEW,
    MSG_QUEUED,
    MSG_SENT,
    STANDARD_WINDOW_HOURS,
    InstagramAccount,
    OutreachConversation,
    OutreachMessage,
)
from ..security import decrypt_secret
from . import instagram_api

log = logging.getLogger("salescale.outreach")

# send() result codes the engine branches on.
SENT = "sent"
QUEUED = "queued"
PENDING_REVIEW = "pending_review"
CAP_REACHED = "cap"
FAILED = "failed"
AUTH_ERROR = "auth"


def window_open(convo: OutreachConversation, now: Optional[dt.datetime] = None) -> bool:
    if convo.last_user_message_at is None:
        return False
    now = now or utcnow()
    last = convo.last_user_message_at
    if last.tzinfo is None:  # sqlite round-trip safety, same as meta_capi._utc
        last = last.replace(tzinfo=dt.timezone.utc)
    return now - last < dt.timedelta(hours=STANDARD_WINDOW_HOURS)


def human_agent_allowed(
    convo: OutreachConversation, now: Optional[dt.datetime] = None
) -> bool:
    if convo.last_user_message_at is None:
        return False
    now = now or utcnow()
    last = convo.last_user_message_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    return now - last < dt.timedelta(days=HUMAN_AGENT_WINDOW_DAYS)


def sends_today(db: Session, account: InstagramAccount) -> int:
    """Outbound messages actually sent since UTC midnight, across every
    conversation on this account — the unit Meta's account standing sees."""
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.execute(
            select(func.count(OutreachMessage.id))
            .join(
                OutreachConversation,
                OutreachMessage.conversation_id == OutreachConversation.id,
            )
            .where(
                OutreachConversation.account_id == account.id,
                OutreachMessage.direction == DIR_OUT,
                OutreachMessage.sent_at >= day_start,
            )
        ).scalar_one()
        or 0
    )


def render_tokens(
    db: Session, text: str, convo: OutreachConversation
) -> str:
    """Personalization tokens from CRM fields. Unknown/empty tokens render as
    empty string rather than leaking {{braces}} into a real DM."""
    contact = db.get(Contact, convo.contact_id) if convo.contact_id else None
    client = db.get(Client, convo.client_id)
    company = (
        db.get(Company, contact.company_id) if contact and contact.company_id else None
    )
    peer = convo.peer or {}
    values = {
        "first_name": (contact.first_name if contact else None) or peer.get("name", "").split(" ")[0],
        "contact_name": " ".join(
            p for p in [(contact.first_name if contact else None), (contact.last_name if contact else None)] if p
        )
        or peer.get("name", ""),
        "business_name": (company.name if company else None) or peer.get("name", ""),
        "username": peer.get("username", ""),
        "client_name": client.name if client else "",
        "vertical": (client.vertical if client else "") or "",
        "source_campaign": (
            (contact.source_detail or {}).get("campaign_id", "") if contact else ""
        ),
    }
    out = text
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", (val or "").strip())
    return out


def _record(
    db: Session,
    account: InstagramAccount,
    convo: OutreachConversation,
    text: str,
    status: str,
    *,
    kind: str,
    rule_id: Optional[str] = None,
    enrollment_id: Optional[str] = None,
    step_id: Optional[str] = None,
    variant: Optional[str] = None,
    tag: Optional[str] = None,
    sent_by_user_id: Optional[str] = None,
    reply_to_comment_id: Optional[str] = None,
    api_response: Optional[dict] = None,
    error_detail: Optional[str] = None,
) -> OutreachMessage:
    now = utcnow()
    msg = OutreachMessage(
        organization_id=convo.organization_id,
        client_id=convo.client_id,
        conversation_id=convo.id,
        direction=DIR_OUT,
        text=text,
        status=status,
        kind=kind,
        rule_id=rule_id,
        enrollment_id=enrollment_id,
        step_id=step_id,
        variant=variant,
        message_tag=tag,
        sent_by_user_id=sent_by_user_id,
        reply_to_comment_id=reply_to_comment_id,
        api_response=api_response,
        error_detail=error_detail,
        queued_at=now if status == MSG_QUEUED else None,
        sent_at=now if status == MSG_SENT else None,
        external_mid=(api_response or {}).get("message_id"),
    )
    db.add(msg)
    db.flush()
    if status == MSG_SENT:
        convo.last_message_at = now
        convo.last_message_preview = text[:400]
    return msg


def send(
    db: Session,
    account: InstagramAccount,
    convo: OutreachConversation,
    text: str,
    *,
    kind: str,
    rule_id: Optional[str] = None,
    enrollment_id: Optional[str] = None,
    step_id: Optional[str] = None,
    variant: Optional[str] = None,
    sent_by_user_id: Optional[str] = None,
    use_human_agent: bool = False,
    reply_to_comment_id: Optional[str] = None,
    hold_for_review: bool = False,
) -> Tuple[str, Optional[OutreachMessage]]:
    """Returns (result_code, message_row). Never raises for policy outcomes —
    the caller branches on the code; only programming errors propagate."""
    rendered = render_tokens(db, text, convo)
    common = dict(
        kind=kind,
        rule_id=rule_id,
        enrollment_id=enrollment_id,
        step_id=step_id,
        variant=variant,
        sent_by_user_id=sent_by_user_id,
        reply_to_comment_id=reply_to_comment_id,
    )
    automated = kind != KIND_MANUAL

    if account.status != "active":
        return AUTH_ERROR, None
    if automated and account.automation_paused:
        return FAILED, _record(
            db, account, convo, rendered, MSG_FAILED,
            error_detail="Automation is paused for this account", **common,
        )
    if hold_for_review:
        return PENDING_REVIEW, _record(
            db, account, convo, rendered, MSG_PENDING_REVIEW, **common
        )

    # Daily cap — counts real sends; queued/pending rows don't consume it.
    if sends_today(db, account) >= account.daily_send_cap:
        return CAP_REACHED, None

    # Window routing. Private replies to a comment carry their own (7-day,
    # once-per-comment) allowance and skip the standard-window check.
    tag: Optional[str] = None
    if reply_to_comment_id is None and not window_open(convo):
        if automated:
            # Never a tag for automation: queue until the window reopens.
            return QUEUED, _record(db, account, convo, rendered, MSG_QUEUED, **common)
        if not (use_human_agent and human_agent_allowed(convo)):
            return FAILED, _record(
                db, account, convo, rendered, MSG_FAILED,
                error_detail="Messaging window closed — HUMAN_AGENT tag required "
                "(available up to 7 days after the user's last message)",
                **common,
            )
        tag = "HUMAN_AGENT"

    try:
        token = decrypt_secret(account.access_token_encrypted)
        if reply_to_comment_id is not None:
            resp = instagram_api.send_private_reply(
                token, account.ig_user_id, reply_to_comment_id, rendered
            )
        else:
            resp = instagram_api.send_text(
                token, account.ig_user_id, convo.ig_user_id, rendered, tag=tag
            )
    except instagram_api.MetaAuthError as e:
        account.status = IG_DISCONNECTED
        account.error_detail = str(e)
        log.warning("IG account %s disconnected: %s", account.id, e)
        _record(
            db, account, convo, rendered, MSG_FAILED,
            error_detail=f"auth: {e}", tag=tag, **common,
        )
        return AUTH_ERROR, None
    except instagram_api.MetaApiError as e:
        return FAILED, _record(
            db, account, convo, rendered, MSG_FAILED,
            error_detail=str(e), tag=tag, **common,
        )

    return SENT, _record(
        db, account, convo, rendered, MSG_SENT, api_response=resp, tag=tag, **common
    )


def flush_queue(db: Session, convo: OutreachConversation) -> int:
    """Send this conversation's queued messages, oldest first — called the
    moment an inbound message reopens the window. Stale queued messages
    (older than the configured max age) are marked failed, not sent."""
    account = db.get(InstagramAccount, convo.account_id)
    if account is None or account.status != "active":
        return 0
    max_age = dt.timedelta(hours=get_settings().outreach_queue_max_age_hours)
    now = utcnow()
    queued = (
        db.execute(
            select(OutreachMessage)
            .where(
                OutreachMessage.conversation_id == convo.id,
                OutreachMessage.status == MSG_QUEUED,
            )
            .order_by(OutreachMessage.queued_at)
        )
        .scalars()
        .all()
    )
    sent = 0
    for msg in queued:
        queued_at = msg.queued_at or msg.created_at
        if queued_at.tzinfo is None:
            queued_at = queued_at.replace(tzinfo=dt.timezone.utc)
        if now - queued_at > max_age:
            msg.status = MSG_FAILED
            msg.error_detail = "Expired in queue before the window reopened"
            continue
        if not window_open(convo, now):
            break
        if sends_today(db, account) >= account.daily_send_cap:
            break
        try:
            token = decrypt_secret(account.access_token_encrypted)
            resp = instagram_api.send_text(
                token, account.ig_user_id, convo.ig_user_id, msg.text or ""
            )
        except instagram_api.MetaAuthError as e:
            account.status = IG_DISCONNECTED
            account.error_detail = str(e)
            msg.status = MSG_FAILED
            msg.error_detail = f"auth: {e}"
            break
        except instagram_api.MetaApiError as e:
            msg.status = MSG_FAILED
            msg.error_detail = str(e)
            continue
        msg.status = MSG_SENT
        msg.sent_at = utcnow()
        msg.api_response = resp
        msg.external_mid = resp.get("message_id")
        convo.last_message_at = msg.sent_at
        convo.last_message_preview = (msg.text or "")[:400]
        sent += 1
    return sent


def release_pending(db: Session, msg: OutreachMessage) -> Tuple[str, OutreachMessage]:
    """Human approved a pending-review message: send it through the same
    policy path (window/cap re-checked at approval time)."""
    convo = db.get(OutreachConversation, msg.conversation_id)
    account = db.get(InstagramAccount, convo.account_id)
    if account is None or account.status != "active":
        return AUTH_ERROR, msg
    if sends_today(db, account) >= account.daily_send_cap:
        return CAP_REACHED, msg
    if not window_open(convo):
        msg.status = MSG_QUEUED
        msg.queued_at = utcnow()
        return QUEUED, msg
    try:
        token = decrypt_secret(account.access_token_encrypted)
        resp = instagram_api.send_text(
            token, account.ig_user_id, convo.ig_user_id, msg.text or ""
        )
    except instagram_api.MetaAuthError as e:
        account.status = IG_DISCONNECTED
        account.error_detail = str(e)
        msg.status = MSG_FAILED
        msg.error_detail = f"auth: {e}"
        return AUTH_ERROR, msg
    except instagram_api.MetaApiError as e:
        msg.status = MSG_FAILED
        msg.error_detail = str(e)
        return FAILED, msg
    msg.status = MSG_SENT
    msg.sent_at = utcnow()
    msg.api_response = resp
    msg.external_mid = resp.get("message_id")
    convo.last_message_at = msg.sent_at
    convo.last_message_preview = (msg.text or "")[:400]
    return SENT, msg
