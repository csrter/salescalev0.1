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

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.core import Client, Organization
from ..models.crm import Contact
from ..models.sms_outreach import SMS_ACCOUNT_ACTIVE, SmsAccount
from . import sms_send

_PROVIDER_BLUEBUBBLES = "bluebubbles"

log = logging.getLogger("salescale.lead_notify")

_MAX_BODY_LEN = 300  # generous single-segment-ish cap; this is a short alert, never personalized/long


def _lead_label(contact: Contact) -> str:
    name = " ".join(p for p in (contact.first_name, contact.last_name) if p)
    return name or "New lead"


def _notification_body(client: Client, contact: Contact) -> str:
    parts = [f"New lead for {client.name}: {_lead_label(contact)}"]
    if contact.phone:
        parts.append(contact.phone)
    elif contact.email:
        parts.append(contact.email)
    if contact.source:
        parts.append(f"via {contact.source.replace('_', ' ')}")
    return " · ".join(parts)[:_MAX_BODY_LEN]


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
        body = _notification_body(client, contact)
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
