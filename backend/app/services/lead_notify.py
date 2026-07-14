"""Text-the-team alert on a new lead — reuses the SMS Outreach module's
connected account and provider transport (services/sms_send.py) rather than
building new send infrastructure. This is NOT lead outreach: recipients are
ops phone numbers configured directly (Organization.lead_notification_phones
for the agency's own team; client.metric_settings["lead_notifications"] for
the client's own contact, e.g. the business owner — mirrors the external_sync
per-client-config convention), never a CRM Contact, so it goes through
sms_send.send_notification — which deliberately skips the TCPA consent gate
built for texting prospects — and logs to the same ledger with
kind="notification", contact_id=None. Both sources are independent opt-ins
and simply combine (deduped) when both are configured.

Account choice (no per-purpose "default account" concept exists in the SMS
module yet): prefers the org's BlueBubbles account (a real iMessage from a
personal number reads as a human ping, not a shortcode blast) over any other
active provider, falling back to the first other active account for orgs
with no BlueBubbles connected. Silently does nothing when notifications are
off, no numbers are configured, or the org has no active SMS account at all —
this is a nice-to-have side effect of lead creation, never something that
should fail or block the request that created the lead.
"""

import logging
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.core import Client, Organization
from ..models.crm import Contact
from ..models.sms_outreach import SMS_ACCOUNT_ACTIVE, SmsAccount
from . import sms_send

_PROVIDER_BLUEBUBBLES = "bluebubbles"

log = logging.getLogger("salescale.lead_notify")

_MAX_BODY_LEN = 500  # a multi-line labeled template runs longer than the old one-liner

# Admin-editable via PUT /api/orgs/me/lead-notifications (message_template).
# {{name}} is the full name; {{brand}} is the client's name (the business the
# lead is for) — kept distinct from "client" since a client-role person could
# be a template recipient too and "brand" reads more naturally in a text.
DEFAULT_TEMPLATE = (
    "*NEW LEAD*\n"
    "Name: {{name}}\n"
    "Phone: {{phone}}\n"
    "Brand: {{brand}}\n"
    "Email: {{email}}\n"
    "Zip Code: {{zip}}"
)

KNOWN_TOKENS = frozenset(
    {"name", "first_name", "last_name", "phone", "email", "brand", "zip", "source"}
)
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*\}\}")


def unknown_tokens(template: str) -> list:
    """Tokens in `template` not in KNOWN_TOKENS, for the save-time 422 —
    mirrors the SMS/email step-editor's unknown-token validation."""
    found = {m.group(1) for m in _TOKEN_RE.finditer(template)}
    return sorted(found - KNOWN_TOKENS)


def _template_tokens(client: Client, contact: Contact) -> dict:
    full_name = " ".join(p for p in (contact.first_name, contact.last_name) if p)
    return {
        "name": full_name or "New lead",
        "first_name": contact.first_name or "",
        "last_name": contact.last_name or "",
        "phone": contact.phone or "",
        "email": contact.email or "",
        "brand": client.name,
        "zip": contact.zip or "",
        "source": (contact.source or "").replace("_", " "),
    }


def render_notification_body(
    template: Optional[str], client: Client, contact: Contact
) -> str:
    tokens = _template_tokens(client, contact)
    body = _TOKEN_RE.sub(
        lambda m: str(tokens.get(m.group(1), "")), template or DEFAULT_TEMPLATE
    )
    return body[:_MAX_BODY_LEN]


def _recipient_phones(org: Organization, client: Client) -> list:
    phones: list = []
    if org.notify_new_leads:
        for p in org.lead_notification_phones or []:
            if p not in phones:
                phones.append(p)
    client_config = (client.metric_settings or {}).get("lead_notifications") or {}
    if client_config.get("enabled"):
        for p in client_config.get("phones") or []:
            if p not in phones:
                phones.append(p)
    return phones


def notify_new_lead(db: Session, client: Client, contact: Contact) -> None:
    """Best-effort side effect of lead creation — never commits or rolls back
    the session itself (the caller's own commit, right after this returns,
    persists whatever SmsMessage rows this adds alongside the rest of the
    request's work), and never lets a notification failure propagate: a
    Twilio outage must not cost the lead that was just successfully created."""
    try:
        org = db.get(Organization, client.organization_id)
        if org is None:
            return
        phones = _recipient_phones(org, client)
        if not phones:
            return
        account = db.execute(
            select(SmsAccount)
            .where(
                SmsAccount.organization_id == client.organization_id,
                SmsAccount.status == SMS_ACCOUNT_ACTIVE,
                SmsAccount.provider == _PROVIDER_BLUEBUBBLES,
            )
            .order_by(SmsAccount.created_at)
            .limit(1)
        ).scalar_one_or_none()
        if account is None:
            account = db.execute(
                select(SmsAccount)
                .where(
                    SmsAccount.organization_id == client.organization_id,
                    SmsAccount.status == SMS_ACCOUNT_ACTIVE,
                )
                .order_by(SmsAccount.created_at)
                .limit(1)
            ).scalar_one_or_none()
        if account is None:
            log.info(
                "lead notification skipped for org=%s: no active SMS account",
                client.organization_id,
            )
            return
        body = render_notification_body(org.lead_notification_template, client, contact)
        for phone in phones:
            result, _row = sms_send.send_notification(db, account, phone, body)
            if result != sms_send.SENT:
                log.info(
                    "lead notification to %s did not send (%s), org=%s",
                    phone,
                    result,
                    client.organization_id,
                )
    except Exception:
        log.exception(
            "lead notification failed for org=%s, contact=%s",
            client.organization_id,
            contact.id,
        )
