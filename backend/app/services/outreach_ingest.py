"""Instagram webhook ingestion: raw Meta delivery → stored event →
conversation/message upsert → engine hooks (reply handling, queue flush,
enrollment re-arm, trigger rules, prospect linking).

Payload shapes handled (object: "instagram"):
- entry[].messaging[] — DMs, story replies (message.reply_to.story), story
  mentions (attachment type story_mention), echoes (message.is_echo), reads.
- entry[].changes[] — field "comments" | "live_comments" | "mentions".
Routing key is entry.id (the IG professional account id) matched against
instagram_accounts.ig_user_id — payload contents are never trusted for
tenant routing (LeadFormConfig pattern).
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.crm import Contact
from ..models.outreach import (
    DIR_IN,
    DIR_OUT,
    EVENT_COMMENT,
    EVENT_DM,
    EVENT_LIVE_COMMENT,
    EVENT_MENTION,
    EVENT_STORY_MENTION,
    EVENT_STORY_REPLY,
    KIND_EXTERNAL,
    MSG_RECEIVED,
    MSG_SENT,
    PROSPECT_ENGAGED,
    InstagramAccount,
    InstagramWebhookEvent,
    OutreachConversation,
    OutreachMessage,
    OutreachProspect,
    OutreachSequence,
)
from . import outreach_rules, outreach_send, outreach_sequences

log = logging.getLogger("salescale.outreach")


def _get_account(db: Session, entry_id: str) -> Optional[InstagramAccount]:
    return db.execute(
        select(InstagramAccount).where(InstagramAccount.ig_user_id == str(entry_id))
    ).scalar_one_or_none()


def _store_event(
    db: Session,
    account: InstagramAccount,
    event_type: str,
    external_id: str,
    payload: dict,
) -> Optional[InstagramWebhookEvent]:
    """Idempotency gate: Meta redelivers on any non-2xx anywhere in a batch —
    a known (account, external_id) is a no-op."""
    exists = db.execute(
        select(InstagramWebhookEvent.id).where(
            InstagramWebhookEvent.account_id == account.id,
            InstagramWebhookEvent.external_id == external_id,
        )
    ).scalar_one_or_none()
    if exists is not None:
        return None
    event = InstagramWebhookEvent(
        organization_id=account.organization_id,
        client_id=account.client_id,
        account_id=account.id,
        event_type=event_type,
        external_id=external_id,
        payload=payload,
    )
    db.add(event)
    db.flush()
    return event


def _upsert_convo(
    db: Session, account: InstagramAccount, igsid: str, username: Optional[str] = None
) -> OutreachConversation:
    convo = db.execute(
        select(OutreachConversation).where(
            OutreachConversation.account_id == account.id,
            OutreachConversation.ig_user_id == str(igsid),
        )
    ).scalar_one_or_none()
    if convo is None:
        convo = OutreachConversation(
            organization_id=account.organization_id,
            client_id=account.client_id,
            account_id=account.id,
            ig_user_id=str(igsid),
            peer={"username": username} if username else None,
        )
        db.add(convo)
        db.flush()
        _link_existing(db, convo, username)
    elif username and not (convo.peer or {}).get("username"):
        convo.peer = {**(convo.peer or {}), "username": username}
    return convo


def _link_existing(
    db: Session, convo: OutreachConversation, username: Optional[str]
):
    """Auto-link a new conversation to an existing CRM contact (matched by
    IG-scoped id) and to a watch-list prospect (by igsid or handle). A linked
    prospect flips to engaged and auto-enrolls in its sequence, which is the
    entire compliant 'outbound' path: the moment they engage, the sequence
    engine takes over."""
    contact = db.execute(
        select(Contact).where(
            Contact.client_id == convo.client_id,
            Contact.source_external_id == f"ig:{convo.ig_user_id}",
        )
    ).scalar_one_or_none()
    if contact is not None:
        convo.contact_id = contact.id

    stmt = select(OutreachProspect).where(
        OutreachProspect.client_id == convo.client_id,
        OutreachProspect.ig_user_id == convo.ig_user_id,
    )
    prospect = db.execute(stmt).scalar_one_or_none()
    if prospect is None and username:
        prospect = db.execute(
            select(OutreachProspect).where(
                OutreachProspect.client_id == convo.client_id,
                OutreachProspect.username == username,
            )
        ).scalar_one_or_none()
    if prospect is not None:
        prospect.ig_user_id = convo.ig_user_id
        prospect.conversation_id = convo.id
        prospect.contact_id = prospect.contact_id or convo.contact_id
        if prospect.status != PROSPECT_ENGAGED:
            prospect.status = PROSPECT_ENGAGED
            prospect.engaged_at = utcnow()
        if prospect.sequence_id:
            seq = db.get(OutreachSequence, prospect.sequence_id)
            if seq is not None:
                outreach_sequences.enroll(
                    db, seq, convo,
                    contact_id=convo.contact_id,
                    prospect_id=prospect.id,
                    enrolled_by="prospect",
                )


def _classify_message(message: Dict[str, Any]) -> str:
    if message.get("reply_to", {}).get("story"):
        return EVENT_STORY_REPLY
    for att in message.get("attachments") or []:
        if att.get("type") == "story_mention":
            return EVENT_STORY_MENTION
    return EVENT_DM


def _handle_messaging(db: Session, account: InstagramAccount, item: Dict[str, Any]) -> dict:
    message = item.get("message")
    if not message:
        # reads/postbacks: update state only for reads; postbacks arrive as
        # messages with quick_reply payloads in current IG messaging.
        return {"status": "ignored", "reason": "no message"}
    mid = str(message.get("mid") or "")
    if not mid:
        return {"status": "ignored", "reason": "no mid"}

    if message.get("is_echo"):
        # A send made outside Salescale (IG app / another tool): record it so
        # the inbox thread is complete, attributed as external.
        recipient = str((item.get("recipient") or {}).get("id") or "")
        if not recipient:
            return {"status": "ignored", "reason": "echo without recipient"}
        if _store_event(db, account, "echo", mid, item) is None:
            return {"status": "duplicate"}
        convo = _upsert_convo(db, account, recipient)
        db.add(
            OutreachMessage(
                organization_id=account.organization_id,
                client_id=account.client_id,
                conversation_id=convo.id,
                direction=DIR_OUT,
                external_mid=mid,
                text=message.get("text"),
                status=MSG_SENT,
                kind=KIND_EXTERNAL,
                sent_at=utcnow(),
            )
        )
        convo.last_message_at = utcnow()
        convo.last_message_preview = (message.get("text") or "")[:400]
        return {"status": "echo recorded"}

    sender = str((item.get("sender") or {}).get("id") or "")
    if not sender:
        return {"status": "ignored", "reason": "no sender"}
    event_type = _classify_message(message)
    if _store_event(db, account, event_type, mid, item) is None:
        return {"status": "duplicate"}

    convo = _upsert_convo(db, account, sender)
    now = utcnow()
    convo.last_user_message_at = now
    convo.last_message_at = now
    convo.last_message_preview = (message.get("text") or "")[:400]
    convo.unread_count = (convo.unread_count or 0) + 1
    db.add(
        OutreachMessage(
            organization_id=account.organization_id,
            client_id=account.client_id,
            conversation_id=convo.id,
            direction=DIR_IN,
            external_mid=mid,
            text=message.get("text"),
            attachments=message.get("attachments"),
            event_type=event_type,
            status=MSG_RECEIVED,
        )
    )
    db.flush()

    # Engine hooks, in order: reply bookkeeping (may exit enrollments), then
    # queued sends + parked enrollments wake up (window just reopened), then
    # trigger rules for new automation.
    outreach_sequences.handle_reply(db, convo)
    outreach_send.flush_queue(db, convo)
    outreach_sequences.rearm_waiting(db, convo)
    outreach_rules.run_rules(db, account, convo, event_type, message.get("text") or "")
    return {"status": "processed", "conversation_id": convo.id}


_CHANGE_FIELDS = {
    "comments": EVENT_COMMENT,
    "live_comments": EVENT_LIVE_COMMENT,
    "mentions": EVENT_MENTION,
}


def _handle_change(db: Session, account: InstagramAccount, change: Dict[str, Any]) -> dict:
    event_type = _CHANGE_FIELDS.get(change.get("field") or "")
    if event_type is None:
        return {"status": "ignored", "reason": f"field {change.get('field')}"}
    value = change.get("value") or {}
    external_id = str(
        value.get("id") or value.get("comment_id") or value.get("media_id") or ""
    )
    if not external_id:
        return {"status": "ignored", "reason": "no id"}
    frm = value.get("from") or {}
    igsid = str(frm.get("id") or "")
    if not igsid:
        return {"status": "ignored", "reason": "no author"}
    if igsid == account.ig_user_id:
        return {"status": "ignored", "reason": "own activity"}
    if _store_event(db, account, event_type, external_id, change) is None:
        return {"status": "duplicate"}

    convo = _upsert_convo(db, account, igsid, username=frm.get("username"))
    # A comment/mention does NOT open the 24h DM window — rules may still
    # respond via the private-reply allowance (comment_id), and any sequence
    # message will queue until the user actually DMs back.
    media = value.get("media") or {}
    text = value.get("text") or ""
    comment_id = (
        external_id if event_type in (EVENT_COMMENT, EVENT_LIVE_COMMENT) else None
    )
    fired = outreach_rules.run_rules(
        db,
        account,
        convo,
        event_type,
        text,
        media_id=str(media.get("id") or "") or None,
        comment_id=comment_id,
    )
    return {"status": "processed", "rules_fired": fired}


def process_webhook_body(db: Session, body: Dict[str, Any]) -> list[dict]:
    """Process one verified webhook delivery. Always returns per-item results;
    unroutable entries are acknowledged and dropped (never guessed into a
    tenant)."""
    results = []
    for entry in body.get("entry") or []:
        account = _get_account(db, str(entry.get("id") or ""))
        if account is None:
            results.append({"status": "ignored", "reason": "no account for entry"})
            continue
        for item in entry.get("messaging") or []:
            try:
                results.append(_handle_messaging(db, account, item))
            except Exception as e:  # one bad item never fails the batch
                log.exception("messaging item failed")
                results.append({"status": "failed", "reason": str(e)})
        for change in entry.get("changes") or []:
            try:
                results.append(_handle_change(db, account, change))
            except Exception as e:
                log.exception("change item failed")
                results.append({"status": "failed", "reason": str(e)})
    return results
