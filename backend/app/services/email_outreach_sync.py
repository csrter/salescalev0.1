"""IMAP reply/bounce sync for the cold-email Outreach module.

sync_account pulls new INBOX messages for one mailbox and classifies each:
  (a) bounce / DSN  → mark the original outbound message bounced, suppress the
      address (reason="bounced"), flip the contact's verification_status to
      "invalid", fire hooks.on_bounce. Kept out of the human inbox.
  (b) warmup echo (X-Salescale-Warmup header) → hooks.on_warmup_received, kept
      out of the inbox.
  (c) a genuine reply → an inbound EmailMessage (idempotent on the sender's
      Message-ID), the thread marked unread, hooks.on_reply. A body opening
      with "unsubscribe / stop / remove me" also suppresses the address.

`hooks` is a module-level registry of no-op callbacks the Phase 2 campaign
engine overrides (exit-on-reply, bounce back-off, unsubscribe cascade, warmup
accounting) — this foundation only defines the extension points and calls them.

Transport failures never propagate out of sync_account: they flip the account
to status="error" with last_sync_error and the loop moves on, so one bad
mailbox can't wedge the scheduler.
"""

import datetime as dt
import logging
import re
from email.message import Message
from email.utils import parseaddr
from typing import Callable, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.base import utcnow
from ..models.crm import Contact
from ..models.email_outreach import (
    ACCOUNT_ACTIVE,
    ACCOUNT_ERROR,
    DIR_IN,
    DIR_OUT,
    MSG_BOUNCED,
    MSG_RECEIVED,
    SUPPRESS_BOUNCED,
    SUPPRESS_UNSUBSCRIBED,
    EmailAccount,
    EmailMessage,
    EmailThread,
)
from ..services import email_outreach_send as gateway
from ..services import email_transport

log = logging.getLogger("salescale.email_outreach")


def _noop(*args, **kwargs) -> None:
    return None


# Phase 2 fills these in place (registry pattern, like the IG module):
#   on_reply(db, thread, message)          — a prospect replied
#   on_bounce(db, original_message, contact) — a send hard-bounced
#   on_unsubscribe(db, suppression)        — an opt-out was recorded
#   on_warmup_received(db, account, parsed_message)
#   on_warmup_junk(db, account, sender_addr) — a warmup mail from sender_addr
#     was found in `account`'s spam folder (and rescued to INBOX)
hooks: Dict[str, Callable] = {
    "on_reply": _noop,
    "on_bounce": _noop,
    "on_unsubscribe": _noop,
    "on_warmup_received": _noop,
    "on_warmup_junk": _noop,
}

_MSGID_RE = re.compile(r"<[^<>@\s]+@[^<>@\s]+>")
_UNSUB_RE = re.compile(r"\b(?:unsubscribe|stop|remove me)\b", re.IGNORECASE)


def _header_message_ids(parsed: Message, *headers: str) -> List[str]:
    ids: List[str] = []
    for h in headers:
        v = parsed.get(h)
        if v:
            ids.extend(_MSGID_RE.findall(v))
    return ids


def _all_message_ids(parsed: Message) -> List[str]:
    """Every Message-ID-shaped token in the message — headers plus any embedded
    original (a DSN quotes the failed message's headers). Matched only against
    our own outbound Message-IDs, so foreign ids are harmless noise."""
    ids = set(_header_message_ids(parsed, "In-Reply-To", "References", "Message-ID"))
    try:
        ids.update(_MSGID_RE.findall(parsed.as_string()))
    except Exception:
        pass
    return list(ids)


def _is_bounce(parsed: Message) -> bool:
    if parsed.get_content_type() == "multipart/report":
        if (parsed.get_param("report-type") or "").lower() == "delivery-status":
            return True
    frm = (parsed.get("From") or "").lower()
    return "mailer-daemon" in frm or "postmaster" in frm


def _plain_body(parsed: Message) -> str:
    try:
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload is not None:
                        return payload.decode(part.get_content_charset() or "utf-8", "replace")
            return ""
        payload = parsed.get_payload(decode=True)
        if payload is not None:
            return payload.decode(parsed.get_content_charset() or "utf-8", "replace")
        return parsed.get_payload() or ""
    except Exception:
        return ""


def _find_outbound(db: Session, account: EmailAccount, ids: List[str]) -> Optional[EmailMessage]:
    if not ids:
        return None
    return db.execute(
        select(EmailMessage).where(
            EmailMessage.account_id == account.id,
            EmailMessage.direction == DIR_OUT,
            EmailMessage.message_id_header.in_(ids),
        )
    ).scalars().first()


def _find_contact_by_email(db: Session, org_id: str, addr: str) -> Optional[Contact]:
    if not addr:
        return None
    return db.execute(
        select(Contact).where(
            Contact.organization_id == org_id,
            func.lower(Contact.email) == addr.casefold(),
        )
    ).scalars().first()


def _handle_bounce(db: Session, account: EmailAccount, parsed: Message) -> str:
    original = _find_outbound(db, account, _all_message_ids(parsed))
    if original is None:
        return "bounce_unmatched"
    original.status = MSG_BOUNCED
    original.bounced_at = utcnow()
    contact = db.get(Contact, original.contact_id) if original.contact_id else None
    addr = contact.email if contact and contact.email else None
    if addr:
        gateway.suppress(
            db,
            account.organization_id,
            addr,
            SUPPRESS_BOUNCED,
            contact_id=contact.id if contact else None,
        )
    if contact is not None:
        contact.verification_status = "invalid"
    hooks["on_bounce"](db, original, contact)
    return "bounced"


def _handle_inbound(db: Session, account: EmailAccount, parsed: Message) -> str:
    msg_id = parsed.get("Message-ID")
    # Idempotency: a re-synced message is a no-op (uq account+message_id_header).
    if msg_id:
        existing = db.execute(
            select(EmailMessage.id).where(
                EmailMessage.account_id == account.id,
                EmailMessage.message_id_header == msg_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return "duplicate"

    from_addr = parseaddr(parsed.get("From") or "")[1]
    body = _plain_body(parsed)

    # Thread match: prefer the In-Reply-To/References chain back to one of our
    # outbound messages; otherwise (account, from-address → org contact).
    ref_ids = _header_message_ids(parsed, "In-Reply-To", "References")
    ref_msg = _find_outbound(db, account, ref_ids)
    thread: Optional[EmailThread] = None
    contact: Optional[Contact] = None
    if ref_msg is not None:
        thread = db.get(EmailThread, ref_msg.thread_id)
        contact = db.get(Contact, thread.contact_id) if thread else None
    if thread is None:
        contact = _find_contact_by_email(db, account.organization_id, from_addr)
        if contact is None:
            # No contact to attach the conversation to (threads require one in
            # this phase). Leave it in the mailbox for a human; nothing to sync.
            return "unmatched"
        thread = db.execute(
            select(EmailThread).where(
                EmailThread.account_id == account.id,
                EmailThread.contact_id == contact.id,
            )
        ).scalar_one_or_none()
        if thread is None:
            thread = EmailThread(
                organization_id=account.organization_id,
                account_id=account.id,
                contact_id=contact.id,
                subject=parsed.get("Subject"),
                message_count=0,
            )
            db.add(thread)
            db.flush()

    now = utcnow()
    inbound = EmailMessage(
        organization_id=account.organization_id,
        thread_id=thread.id,
        account_id=account.id,
        contact_id=contact.id if contact else None,
        direction=DIR_IN,
        status=MSG_RECEIVED,
        subject=parsed.get("Subject"),
        body_text=body,
        message_id_header=msg_id,
        in_reply_to=parsed.get("In-Reply-To"),
        received_at=now,
    )
    db.add(inbound)
    thread.unread = True
    thread.last_inbound_at = now
    thread.last_message_at = now
    thread.snippet = (body or "")[:400]
    thread.message_count = (thread.message_count or 0) + 1
    db.flush()

    # Opt-out inside the reply body (first 200 chars, whole-word).
    if contact and contact.email and _UNSUB_RE.search((body or "")[:200]):
        sup = gateway.suppress(
            db,
            account.organization_id,
            contact.email,
            SUPPRESS_UNSUBSCRIBED,
            contact_id=contact.id,
        )
        if sup is not None:
            hooks["on_unsubscribe"](db, sup)

    hooks["on_reply"](db, thread, inbound)
    return "reply"


def sync_account(db: Session, account: EmailAccount) -> dict:
    """Pull and classify new INBOX mail for one mailbox. Never raises: a
    transport failure flips the account to error and is reported in the result.
    Updates last_imap_uid / last_synced_at. Caller owns the commit."""
    last_uid = account.last_imap_uid or 0
    try:
        messages = email_transport.fetch_new(account, last_uid)
    except email_transport.EmailTransportError as e:
        account.status = ACCOUNT_ERROR
        account.last_sync_error = str(e)
        return {"account_id": account.id, "error": str(e)}

    counts: Dict[str, int] = {}
    max_uid = last_uid
    for uid, raw in messages:
        try:
            parsed = email_transport.parse_message(raw)
            if _is_bounce(parsed):
                outcome = _handle_bounce(db, account, parsed)
            elif parsed.get("X-Salescale-Warmup"):
                hooks["on_warmup_received"](db, account, parsed)
                outcome = "warmup"
            else:
                outcome = _handle_inbound(db, account, parsed)
        except Exception as e:  # one malformed message must not abort the batch
            log.warning("email sync: message uid=%s failed: %s", uid, e)
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
        if uid > max_uid:
            max_uid = uid

    # Warmup engagement signals on the receiving side: rescue warmup mail out
    # of the spam folder and mark inbox warmup read. Fail-soft — hygiene must
    # never cost us the UID/timestamp bookkeeping above.
    if account.warmup_enabled:
        try:
            hygiene = email_transport.warmup_inbox_hygiene(account)
            for sender_addr in hygiene["rescued_from"]:
                hooks["on_warmup_junk"](db, account, sender_addr)
            if hygiene["rescued_from"] or hygiene["seen"]:
                counts["warmup_hygiene"] = (
                    len(hygiene["rescued_from"]) + hygiene["seen"]
                )
        except email_transport.EmailTransportError as e:
            log.info("warmup hygiene skipped for %s: %s", account.id, e)

    account.last_imap_uid = max_uid
    account.last_synced_at = utcnow()
    account.last_sync_error = None
    return {"account_id": account.id, "processed": len(messages), "outcomes": counts}


def sync_due(db: Session, limit: int = 10) -> List[dict]:
    """Sync the active mailboxes whose last poll is older than the configured
    floor (or that have never synced). Commits per account so progress persists
    even if a later account errors."""
    cutoff = utcnow() - dt.timedelta(
        seconds=get_settings().email_sync_min_interval_seconds
    )
    accounts = db.execute(
        select(EmailAccount)
        .where(
            EmailAccount.status == ACCOUNT_ACTIVE,
            or_(
                EmailAccount.last_synced_at.is_(None),
                EmailAccount.last_synced_at < cutoff,
            ),
        )
        .order_by(EmailAccount.last_synced_at.asc().nulls_first())
        .limit(limit)
    ).scalars().all()
    results: List[dict] = []
    for account in accounts:
        results.append(sync_account(db, account))
        db.commit()
    return results
