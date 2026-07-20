"""Two-way lead-reply relay over BlueBubbles.

When an org enables the relay (Organization.lead_relay_enabled +
lead_relay_phone), an inbound lead reply on the org's BlueBubbles number is
forwarded to the operator's phone, and a message FROM that phone is routed
back to the right lead through BlueBubbles — so the operator runs the whole
conversation from their own texts.

Routing is TAG-based (the operator chose this over sticky "last lead"): every
forward carries the lead's reply CODE — the last 4 digits of the lead's number
— and the operator starts their reply with it ("1234 on my way"). The code
resolves the lead directly, so there's no ambiguity or stored "active
conversation" state, even with several leads texting at once.

Compliance: the forward to the operator goes through sms_send.send_notification
(the operator's own number, no consent gate). The relay back to the LEAD goes
through sms_send.send_reply, which skips the opt-in gate (the lead texted first)
but still honors STOP/suppression. BlueBubbles-only — that's the transport the
operator loops through; Twilio/Sendblue inbound is untouched.

Everything here is best-effort: a relay failure must never break the inbound
webhook that received the lead's message.
"""

import logging
import re
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.core import Client, Organization
from ..models.crm import Contact
from ..models.sms_outreach import (
    SMS_DIR_IN,
    SmsAccount,
    SmsMessage,
)
from . import sms_consent
from . import sms_send

log = logging.getLogger("salescale.lead_relay")

_PROVIDER_BLUEBUBBLES = "bluebubbles"

# A leading reply code: 3–5 digits, optionally "#"-prefixed, then a separator
# (space / colon / dash) and the actual message. Lenient so "1234 hi",
# "#1234: hi", and "1234 - hi" all parse.
_TAG_RE = re.compile(r"^\s*#?\s*(\d{3,5})\s*[:\-]?\s+(.*)$", re.DOTALL)


def relay_code(contact: Contact) -> Optional[str]:
    """The lead's reply code: the last 4 digits of the number we'd text."""
    number = sms_consent.contact_sms_number(contact)
    if not number:
        return None
    digits = "".join(ch for ch in number if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


def _is_bluebubbles_relay(org: Optional[Organization], account: SmsAccount) -> bool:
    return bool(
        org
        and org.lead_relay_enabled
        and org.lead_relay_phone
        and account.provider == _PROVIDER_BLUEBUBBLES
    )


def is_operator(
    org: Optional[Organization], account: SmsAccount, from_number: str
) -> bool:
    """True when this inbound is the operator's relay phone (a command), not a
    lead. Compared on normalized E.164 so formatting can't cause a miss."""
    if not _is_bluebubbles_relay(org, account):
        return False
    return sms_consent.normalize_phone(org.lead_relay_phone) == from_number


def _client_label(db: Session, contact: Contact) -> str:
    if not contact.client_id:
        return ""
    client = db.get(Client, contact.client_id)
    if client is None or client.is_house:
        return ""
    return client.name


def _lead_name(contact: Contact) -> str:
    name = " ".join(p for p in (contact.first_name, contact.last_name) if p).strip()
    return name or (sms_consent.contact_sms_number(contact) or "Lead")


def forward_to_operator(
    db: Session, account: SmsAccount, contact: Contact, message: str
) -> None:
    """Forward one lead reply to the operator's phone via BlueBubbles. Best-
    effort — swallows every error so inbound processing is never affected."""
    try:
        org = db.get(Organization, account.organization_id)
        if not _is_bluebubbles_relay(org, account):
            return
        code = relay_code(contact)
        if not code:
            return
        name = _lead_name(contact)
        first = (contact.first_name or "").strip() or "them"
        label = _client_label(db, contact)
        header = f"📩 New reply — {label}" if label else "📩 New lead reply"
        number = sms_consent.contact_sms_number(contact) or ""
        body = (
            f"{header}\n"
            f"{name} · {number}\n\n"
            f"{message}\n\n"
            f"↩︎ Reply starting with {code} to text {first} back."
        )
        result, _row = sms_send.send_notification(
            db, account, sms_consent.normalize_phone(org.lead_relay_phone), body
        )
        if result != sms_send.SENT:
            log.info(
                "lead relay forward to operator did not send (%s), org=%s",
                result,
                account.organization_id,
            )
    except Exception:
        log.exception("lead relay forward failed for org=%s", account.organization_id)


def parse_operator_reply(body: str) -> Tuple[Optional[str], str]:
    """(code, message) from an operator's text, or (None, "") when it doesn't
    start with a reply code."""
    m = _TAG_RE.match(body or "")
    if not m:
        return None, ""
    return m.group(1)[-4:], m.group(2).strip()


def resolve_lead_by_code(
    db: Session, account: SmsAccount, code: str
) -> Optional[Contact]:
    """The lead a reply code points at: among numbers that have texted this
    account whose last 4 digits match, the MOST RECENTLY active one (handles
    the rare last-4 collision by recency)."""
    rows = db.execute(
        select(SmsMessage.contact_id)
        .where(
            SmsMessage.account_id == account.id,
            SmsMessage.direction == SMS_DIR_IN,
            SmsMessage.contact_id.is_not(None),
            SmsMessage.from_number.like(f"%{code}"),
        )
        .order_by(SmsMessage.created_at.desc())
        .limit(50)
    ).scalars()
    for contact_id in rows:
        contact = db.get(Contact, contact_id)
        if contact is not None and relay_code(contact) == code:
            return contact
    return None


def _tell_operator(db: Session, account: SmsAccount, org: Organization, text: str) -> None:
    try:
        sms_send.send_notification(
            db, account, sms_consent.normalize_phone(org.lead_relay_phone), text
        )
    except Exception:
        log.exception("lead relay operator-notice failed for org=%s", account.organization_id)


def handle_operator_reply(
    db: Session, account: SmsAccount, from_number: str, body: str
) -> None:
    """Route an operator's tagged text to the lead through BlueBubbles. Assumes
    the caller already confirmed `from_number` is the relay phone. On success
    stays silent (the operator sees their own sent text); only speaks up to
    guide or report a failure. Best-effort throughout."""
    try:
        org = db.get(Organization, account.organization_id)
        if org is None:
            return
        code, message = parse_operator_reply(body)
        if not code or not message:
            _tell_operator(
                db,
                account,
                org,
                "To reply to a lead, start your text with their code, e.g. "
                "\"1234 On my way\". The code is shown in each forwarded message.",
            )
            return
        lead = resolve_lead_by_code(db, account, code)
        if lead is None:
            _tell_operator(
                db,
                account,
                org,
                f"No recent lead found with code {code}. Check the code in the "
                "forwarded message and try again.",
            )
            return
        result, _row = sms_send.send_reply(db, account, lead, message)
        if result == sms_send.SUPPRESSED:
            _tell_operator(
                db, account, org, f"Lead {code} opted out — can't text them."
            )
        elif result != sms_send.SENT:
            _tell_operator(
                db,
                account,
                org,
                f"Couldn't send to lead {code} ({result}). Try again shortly.",
            )
    except Exception:
        log.exception("lead relay operator reply failed for org=%s", account.organization_id)
