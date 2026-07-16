"""The one gateway every outbound cold email goes through.

Nothing else in the module builds a MIME message or calls the SMTP transport
directly — that is what makes the compliance guardrails (CLAUDE.md #9)
enforceable in a single place:

- Suppression / opt-out is checked BEFORE the send for every campaign/manual
  message; a suppressed or verified-invalid address can never be emailed
  through any path.
- The shared email gate (services/email_verification.assert_can_email) is the
  same check Lead Finder and every other send feature routes through.
- The per-account daily cap is enforced server-side (today's real sends, UTC).
- Every attempt — sent or failed — is an append-only EmailMessage row (the
  audit trail): kind + campaign/step/enrollment provenance, the rendered
  subject/body, the Message-ID, the SMTP response, and the CAN-SPAM /
  unsubscribe tokens all live on the row.
- Warmup traffic (kind="warmup") deliberately skips suppression + the gate
  (it's mailbox-to-mailbox reputation traffic, not prospect outreach) and
  carries an X-Salescale-Warmup header so the IMAP sync keeps it out of the
  human inbox.

Compliance footer + List-Unsubscribe headers are attached to every
campaign/manual send. A body containing the {{unsubscribe_url}} token renders
the link in place; otherwise the footer (org name, mailing address, unsubscribe
link) is appended.
"""

import datetime as dt
import logging
from email.message import EmailMessage as MimeEmailMessage
from email.utils import make_msgid
from typing import Optional, Tuple

import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Contact
from ..models.email_outreach import (
    ACCOUNT_ACTIVE,
    DIR_OUT,
    KIND_WARMUP,
    MSG_FAILED,
    MSG_SENT,
    EmailAccount,
    EmailCampaign,
    EmailEnrollment,
    EmailMessage,
    EmailStep,
    EmailSuppression,
    EmailThread,
)
from ..services import branding, email_transport
from ..services.email_verification import EmailBlockedError, assert_can_email

log = logging.getLogger("salescale.email_outreach")

# send() result codes the callers (and the Phase 2 engine) branch on.
SENT = "sent"
FAILED = "failed"
SUPPRESSED = "suppressed"
BLOCKED = "blocked"
CAP_REACHED = "cap"

_UNSUB_TOKEN = "{{unsubscribe_url}}"


# --- suppression ledger (the opt-out truth every send consults) ---


def is_suppressed(db: Session, org_id: str, email_addr: str) -> bool:
    if not email_addr:
        return False
    return (
        db.execute(
            select(EmailSuppression.id).where(
                EmailSuppression.organization_id == org_id,
                EmailSuppression.email == email_addr.casefold(),
            )
        ).scalar_one_or_none()
        is not None
    )


def suppress(
    db: Session,
    org_id: str,
    email_addr: str,
    reason: str,
    *,
    contact_id: Optional[str] = None,
) -> Optional[EmailSuppression]:
    """Add (org, casefolded email) to the do-not-contact ledger, idempotently.
    Returns the row (existing or new); None for a blank address."""
    if not email_addr:
        return None
    folded = email_addr.casefold()
    existing = db.execute(
        select(EmailSuppression).where(
            EmailSuppression.organization_id == org_id,
            EmailSuppression.email == folded,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = EmailSuppression(
        organization_id=org_id, email=folded, reason=reason, contact_id=contact_id
    )
    db.add(row)
    db.flush()
    return row


# --- daily cap ---


def sends_today(db: Session, account: EmailAccount) -> int:
    """Real outbound sends since UTC midnight for this mailbox — the unit a
    provider's reputation systems see."""
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.execute(
            select(func.count(EmailMessage.id)).where(
                EmailMessage.account_id == account.id,
                EmailMessage.direction == DIR_OUT,
                EmailMessage.status == MSG_SENT,
                EmailMessage.sent_at >= day_start,
            )
        ).scalar_one()
        or 0
    )


# --- threading + footer helpers ---


def _reply_subject(base: Optional[str]) -> str:
    base = (base or "").strip()
    if not base:
        return "Re:"
    if base.lower().startswith("re:"):
        return base
    return f"Re: {base}"


def _upsert_thread(
    db: Session, account: EmailAccount, contact: Contact, subject: Optional[str]
) -> EmailThread:
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
            subject=subject,
            message_count=0,
        )
        db.add(thread)
        db.flush()
    return thread


def _from_domain(from_email: str) -> str:
    return from_email.rsplit("@", 1)[-1] if "@" in from_email else "localhost"


def _identity_block(org: Organization) -> str:
    """CAN-SPAM identity block: the sending Organization's real name + physical
    mailing address. Required on EVERY commercial email — including one whose
    body renders {{unsubscribe_url}} inline (that token supplies the opt-out
    link but not the postal address, which is a separate CAN-SPAM requirement)."""
    b = branding.merged(org)
    lines = [org.name]
    address = (b.get("mailing_address") or "").strip()
    if address:
        lines.append(address)
    return "\n\n--\n" + "\n".join(lines)


def _footer(org: Organization, unsub_url: str) -> str:
    """CAN-SPAM footer for a body that did NOT place {{unsubscribe_url}} itself:
    the identity block plus the one-click unsubscribe link."""
    return f"{_identity_block(org)}\nUnsubscribe: {unsub_url}"


def _compose_body(
    account: EmailAccount, org: Organization, body_text: str, unsub_url: Optional[str]
) -> str:
    body = body_text or ""
    if account.signature:
        body = f"{body}\n\n{account.signature}"
    if unsub_url is None:
        return body
    if _UNSUB_TOKEN in body:
        # The body supplies the unsubscribe link in place; still append the
        # identity block (org name + mailing address) so the postal-address
        # requirement is met — just without a second unsubscribe link.
        return body.replace(_UNSUB_TOKEN, unsub_url) + _identity_block(org)
    return body + _footer(org, unsub_url)


# --- the gateway ---


def send(
    db: Session,
    account: EmailAccount,
    *,
    to_contact: Optional[Contact] = None,
    to_email: Optional[str] = None,
    subject: Optional[str],
    body_text: str,
    kind: str,
    campaign: Optional[EmailCampaign] = None,
    step: Optional[EmailStep] = None,
    enrollment: Optional[EmailEnrollment] = None,
    in_reply_to_message: Optional[EmailMessage] = None,
    # Warmup-only threading: the inbound warmup mail has no EmailMessage row,
    # so its Message-ID comes in raw. Depth rides a header so two mailboxes
    # auto-replying at each other can't loop (reply only when depth < cap).
    reply_to_header: Optional[str] = None,
    warmup_depth: int = 0,
) -> Tuple[str, Optional[EmailMessage]]:
    """Send one email. Returns (code, message_row). Never raises for a policy
    outcome — the caller branches on the code; only programming errors
    propagate. `subject` may be None/blank when threading a reply, in which case
    it is derived from the thread ("Re: …").

    Recipient: exactly one of `to_contact` (CRM contact — the normal path) or
    `to_email` (a raw address, used only by warmup where the peer is another of
    the org's OWN mailboxes, not a prospect). A raw-address send has no Contact,
    is threadless (no human-inbox thread), and skips suppression + the verified-
    email gate; it is only valid for kind="warmup"."""
    settings = get_settings()
    org = db.get(Organization, account.organization_id)
    is_warmup = kind == KIND_WARMUP

    if (to_contact is None) == (to_email is None):
        raise ValueError("send() requires exactly one of to_contact / to_email")
    to_addr = to_contact.email if to_contact is not None else to_email

    # 1. account must be connected.
    if account.status != ACCOUNT_ACTIVE:
        return FAILED, None

    # 2/3. suppression + the shared verified-email gate — skipped only for
    # warmup traffic (mailbox-to-mailbox, not prospect outreach).
    if not is_warmup and to_contact is not None:
        if is_suppressed(db, account.organization_id, to_contact.email or ""):
            return SUPPRESSED, None
        try:
            assert_can_email(to_contact)
        except EmailBlockedError:
            return BLOCKED, None

    # 4. per-account daily cap (real sends today, UTC). Warmup ramps the
    # effective cap up over time (email_warmup) — lazy import breaks the cycle
    # (email_warmup sends through this gateway).
    from . import email_warmup

    if sends_today(db, account) >= email_warmup.effective_daily_cap(account, db):
        return CAP_REACHED, None

    # Threading + subject resolution (campaign/manual only — warmup is
    # threadless: it has no Contact and never enters the unified inbox).
    thread = None
    if to_contact is not None:
        thread = _upsert_thread(db, account, to_contact, subject)
        if not (subject or "").strip():
            subject = _reply_subject(thread.subject)

    # Compliance plumbing (campaign/manual only, never warmup).
    open_token = unsubscribe_token = None
    unsub_url = None
    if not is_warmup:
        open_token = secrets.token_urlsafe(24)
        unsubscribe_token = secrets.token_urlsafe(24)
        base = settings.api_base_url.rstrip("/")
        unsub_url = f"{base}/api/email-outreach/unsubscribe/{unsubscribe_token}"

    rendered_body = _compose_body(account, org, body_text, unsub_url)

    # Build the MIME message.
    message_id = make_msgid(domain=_from_domain(account.from_email))
    mime = MimeEmailMessage()
    mime["From"] = f"{account.from_name} <{account.from_email}>"
    mime["To"] = to_addr or ""
    mime["Subject"] = subject
    mime["Message-ID"] = message_id
    in_reply_to_header = None
    if in_reply_to_message is not None and in_reply_to_message.message_id_header:
        in_reply_to_header = in_reply_to_message.message_id_header
    elif reply_to_header:
        in_reply_to_header = reply_to_header
    if in_reply_to_header:
        mime["In-Reply-To"] = in_reply_to_header
        mime["References"] = in_reply_to_header
    if is_warmup:
        mime["X-Salescale-Warmup"] = "1"
        mime["X-Salescale-Warmup-Depth"] = str(warmup_depth)
    elif unsub_url is not None:
        mime["List-Unsubscribe"] = f"<{unsub_url}>"
        mime["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    mime.set_content(rendered_body)

    # Persist the audit row up-front (status flips after the transport call).
    msg = EmailMessage(
        organization_id=account.organization_id,
        thread_id=thread.id if thread else None,
        account_id=account.id,
        contact_id=to_contact.id if to_contact is not None else None,
        direction=DIR_OUT,
        status=MSG_FAILED,  # provisional; set to SENT on success
        kind=kind,
        campaign_id=campaign.id if campaign else None,
        step_id=step.id if step else None,
        enrollment_id=enrollment.id if enrollment else None,
        subject=subject,
        body_text=rendered_body,
        message_id_header=message_id,
        in_reply_to=in_reply_to_header,
        open_token=open_token,
        unsubscribe_token=unsubscribe_token,
    )
    db.add(msg)
    db.flush()

    try:
        response = email_transport.smtp_send(account, mime)
    except email_transport.EmailTransportError as e:
        # A send failure is almost always an auth/connection problem with the
        # mailbox — surface it as a reconnect banner and stop.
        msg.status = MSG_FAILED
        msg.error_detail = str(e)
        account.status = "error"
        account.error_detail = str(e)
        log.warning("email account %s send failed: %s", account.id, e)
        return FAILED, msg

    now = utcnow()
    msg.status = MSG_SENT
    msg.sent_at = now
    msg.smtp_response = response
    if thread is not None:
        thread.snippet = (rendered_body or "")[:400]
        thread.last_message_at = now
        thread.message_count = (thread.message_count or 0) + 1
        if thread.subject is None:
            thread.subject = subject
    return SENT, msg
