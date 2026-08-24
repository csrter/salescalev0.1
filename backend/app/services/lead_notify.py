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

import datetime as dt
import logging
import re
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Client, Organization
from ..models.crm import Contact
from ..models.sms_outreach import (
    SMS_ACCOUNT_ACTIVE,
    SMS_DIR_OUT,
    SMS_KIND_NOTIFICATION,
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_READ,
    SMS_MSG_SENT,
    SmsAccount,
    SmsMessage,
)
from . import custom_fields as custom_fields_svc
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
# {{custom.<key>}} references one of the org's own custom fields (Phase 14) —
# e.g. a "Brand" select field for an org whose {{brand}} (client name) isn't
# what they mean by "Brand". Auto-populated from a landing-page webhook field
# of the same name (api/lead_webhooks.py _match_custom_fields) when defined.
_CUSTOM_PREFIX = "custom."
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")


def unknown_tokens(template: str) -> list:
    """Tokens in `template` not in KNOWN_TOKENS and not a custom.* reference,
    for the save-time 422 — mirrors the SMS/email step-editor's own
    unknown-token validation. custom.* is never flagged unknown regardless of
    whether that key currently exists — custom fields are added/renamed
    independently of the template."""
    found = {m.group(1) for m in _TOKEN_RE.finditer(template)}
    bad = {t for t in found if t not in KNOWN_TOKENS and not t.startswith(_CUSTOM_PREFIX)}
    return sorted(bad)


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


def _custom_option_labels(db, organization_id: str) -> dict:
    """def.key -> {option_key: option_label} for every select/multi_select
    custom field in the org, so {{custom.<key>}} shows the human label
    ("Glacier") rather than the stored option key ("glacier")."""
    defs = custom_fields_svc.list_definitions(
        db, organization_id, "contact", include_archived=True
    )
    return {
        d.key: {o["key"]: o["label"] for o in (d.options or [])}
        for d in defs
        if d.field_type in ("select", "multi_select")
    }


def _display_custom_value(value, option_labels: Optional[dict]) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        labels = [
            (option_labels or {}).get(v, str(v)) if option_labels else str(v)
            for v in value
        ]
        return ", ".join(labels)
    if option_labels:
        return option_labels.get(value, str(value))
    return str(value)


def render_notification_body(
    db, template: Optional[str], client: Client, contact: Contact
) -> str:
    tokens = _template_tokens(client, contact)
    custom_values = contact.custom_fields or {}
    option_labels: Optional[dict] = None
    loaded = False

    def _sub(m):
        nonlocal option_labels, loaded
        name = m.group(1)
        if name.startswith(_CUSTOM_PREFIX):
            key = name[len(_CUSTOM_PREFIX):]
            if not loaded:
                option_labels = _custom_option_labels(db, client.organization_id)
                loaded = True
            return _display_custom_value(custom_values.get(key), option_labels.get(key))
        return str(tokens.get(name, ""))

    body = _TOKEN_RE.sub(_sub, template or DEFAULT_TEMPLATE)
    return body[:_MAX_BODY_LEN]


def resolve_template(org: Organization, client: Client) -> Optional[str]:
    """The effective template for a notification about one of `client`'s leads:
    the client's own template overrides the org-wide one, and both fall back to
    DEFAULT_TEMPLATE inside render_notification_body. One body is sent to every
    recipient (org-wide ops numbers + the client's own numbers) — a client that
    customizes its template customizes the alert for all of its own leads. The
    per-client template lives next to its phones in
    client.metric_settings["lead_notifications"]["template"]."""
    client_cfg = (client.metric_settings or {}).get("lead_notifications") or {}
    return client_cfg.get("template") or org.lead_notification_template


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


# An account whose most recent notification attempt failed this recently is
# tried LAST rather than dropped: a dead relay costs one wasted attempt per
# cooldown instead of one per lead, and the account promotes itself back the
# moment a probe succeeds. Deliberately not a status flip — a 502 relay means
# "unreachable right now", not "these credentials are wrong", and flipping
# SmsAccount.status parks every campaign enrollment on that account too.
UNHEALTHY_COOLDOWN_MINUTES = 30


def candidate_accounts(db: Session, organization_id: str) -> list:
    """Active SMS accounts in send-preference order for an ops alert.

    BlueBubbles leads (a real iMessage from a personal number reads as a human
    ping, not a shortcode blast), then everything else oldest-first — but any
    account that just failed a notification is demoted behind the healthy ones.
    Returning a LIST rather than one account is what makes the caller able to
    fail over: an org with a dead iMessage relay and a live Telnyx number
    should still get its lead alerts.
    """
    accounts = (
        db.execute(
            select(SmsAccount)
            .where(
                SmsAccount.organization_id == organization_id,
                SmsAccount.status == SMS_ACCOUNT_ACTIVE,
            )
            .order_by(SmsAccount.created_at)
        )
        .scalars()
        .all()
    )
    if len(accounts) < 2:
        return list(accounts)

    cutoff = utcnow() - dt.timedelta(minutes=UNHEALTHY_COOLDOWN_MINUTES)
    unhealthy = set()
    for account in accounts:
        last = db.execute(
            select(SmsMessage.status)
            .where(
                SmsMessage.kind == SMS_KIND_NOTIFICATION,
                SmsMessage.direction == SMS_DIR_OUT,
                SmsMessage.account_id == account.id,
                SmsMessage.created_at >= cutoff,
            )
            .order_by(SmsMessage.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last == SMS_MSG_FAILED:
            unhealthy.add(account.id)

    def rank(account) -> tuple:
        return (
            1 if account.id in unhealthy else 0,
            0 if account.provider == _PROVIDER_BLUEBUBBLES else 1,
        )

    return sorted(accounts, key=rank)


def _send_with_failover(db: Session, accounts: list, phone: str, body: str):
    """Try each candidate until one actually sends. Returns the account that
    sent (so the caller can pin the rest of a multi-recipient alert to it and
    not re-probe a dead provider), or None if every candidate failed."""
    for account in accounts:
        result, _row = sms_send.send_notification(db, account, phone, body)
        if result == sms_send.SENT:
            return account
        log.info(
            "lead notification to %s did not send via %s (%s)",
            phone,
            account.provider,
            result,
        )
    return None


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
        accounts = candidate_accounts(db, client.organization_id)
        if not accounts:
            log.info(
                "lead notification skipped for org=%s: no active SMS account",
                client.organization_id,
            )
            return
        body = render_notification_body(db, resolve_template(org, client), client, contact)
        for phone in phones:
            sender = _send_with_failover(db, accounts, phone, body)
            if sender is None:
                log.warning(
                    "lead notification to %s failed on every account, org=%s",
                    phone,
                    client.organization_id,
                )
                continue
            # Pin the remaining recipients to the account that just worked.
            accounts = [sender] + [a for a in accounts if a.id != sender.id]
    except Exception:
        log.exception(
            "lead notification failed for org=%s, contact=%s",
            client.organization_id,
            contact.id,
        )


_BRANCH_REPLY_BODY_LEN = 200  # keep the quoted reply readable, not a full transcript


def notify_branch_reply(
    db: Session,
    org: Organization,
    campaign_name: str,
    contact: Contact,
    branch_label: str,
    reply_text: str,
) -> None:
    """Ops alert when a lead's reply matches an SMS campaign branch flagged
    notify=True (services/sms_campaigns._branch_options) — e.g. a "yes"
    branch on the pitch step, so a positive reply pings a real phone instead
    of waiting to be noticed in the CRM. Reuses the exact same recipient
    phones (org-wide + this lead's client, per _recipient_phones) and account
    failover as notify_new_lead; this is a different TRIGGER (a reply
    matched a branch, not a lead being created), not a different delivery
    mechanism. Deliberately no configurable template (unlike the new-lead
    alert) — this fires from inside the send engine's hot path, so it stays
    a fixed, simple message rather than adding another render pass there.
    Called from process_enrollment right after the branch response actually
    sends; best-effort — never raises, never blocks the send that triggered
    it."""
    try:
        client = db.get(Client, contact.client_id)
        if client is None:
            return
        phones = _recipient_phones(org, client)
        if not phones:
            return
        accounts = candidate_accounts(db, org.id)
        if not accounts:
            return
        name = " ".join(p for p in (contact.first_name, contact.last_name) if p) or "A lead"
        number = contact.mobile_phone or contact.phone or ""
        who = f"{name} ({number})" if number else name
        snippet = (reply_text or "").strip()[:_BRANCH_REPLY_BODY_LEN]
        body = f"\U0001f525 Positive reply — {campaign_name}\n{who}"
        body += f'\nSaid: "{snippet}"' if snippet else f"\nMatched: {branch_label}"
        for phone in phones:
            sender = _send_with_failover(db, accounts, phone, body)
            if sender is None:
                log.warning(
                    "branch-reply notification to %s failed on every account, org=%s",
                    phone,
                    org.id,
                )
                continue
            accounts = [sender] + [a for a in accounts if a.id != sender.id]
    except Exception:
        log.exception(
            "branch-reply notification failed for org=%s, contact=%s",
            org.id,
            contact.id,
        )


# --- automatic retry of failed notification texts ----------------------------
# notify_new_lead / the relay forwards are single-shot best-effort at lead-
# creation time (a webhook request can't sit in a retry loop), but the
# BlueBubbles path runs through a real Mac whose Messages.app intermittently
# errors — observed live: the same alert failing several times then sending
# minutes later. The scheduler calls retry_failed() every tick so a transient
# device/relay error can't silently cost the team an alert.

NOTIFY_RETRY_WINDOW_HOURS = 6
# Total attempt rows per (number, body) pair — the original + 3 retries.
# Self-limiting via the ledger alone: every attempt IS a row, so no schema.
NOTIFY_MAX_ATTEMPTS = 4

_SUCCESS_STATUSES = (SMS_MSG_SENT, SMS_MSG_DELIVERED, SMS_MSG_READ)


def retry_failed(db: Session, limit: int = 20) -> int:
    """One scheduler tick's retry pass over recently failed notification
    texts (lead alerts AND relay forwards — everything kind="notification").
    A (to_number, body) pair is retried while: its newest attempt is younger
    than NOTIFY_RETRY_WINDOW_HOURS, no attempt ever succeeded, and total
    attempts < NOTIFY_MAX_ATTEMPTS. One retry per pair per tick gives a
    natural ~60s backoff — deliberately gentle, so a genuinely broken device
    isn't machine-gunned (which is exactly what gets an Apple ID flagged)."""
    since = utcnow() - dt.timedelta(hours=NOTIFY_RETRY_WINDOW_HOURS)
    rows = (
        db.execute(
            select(SmsMessage)
            .where(
                SmsMessage.kind == SMS_KIND_NOTIFICATION,
                SmsMessage.direction == SMS_DIR_OUT,
                SmsMessage.status == SMS_MSG_FAILED,
                SmsMessage.created_at >= since,
            )
            .order_by(SmsMessage.created_at.desc())
        )
        .scalars()
        .all()
    )
    retried = 0
    seen: set = set()
    for m in rows:
        key = (m.organization_id, m.to_number, m.body)
        if key in seen:
            continue
        seen.add(key)
        pair_filter = (
            SmsMessage.kind == SMS_KIND_NOTIFICATION,
            SmsMessage.organization_id == m.organization_id,
            SmsMessage.to_number == m.to_number,
            SmsMessage.body == m.body,
        )
        succeeded = db.execute(
            select(SmsMessage.id)
            .where(*pair_filter, SmsMessage.status.in_(_SUCCESS_STATUSES))
            .limit(1)
        ).scalar_one_or_none()
        if succeeded is not None:
            continue
        attempts = (
            db.execute(select(func.count(SmsMessage.id)).where(*pair_filter)).scalar_one()
            or 0
        )
        if attempts >= NOTIFY_MAX_ATTEMPTS:
            continue
        # Deliberately NOT pinned to m.account_id: the original attempt failed,
        # and retrying the same dead provider three more times is how 100+
        # alerts died on an unreachable iMessage relay while a healthy Telnyx
        # number sat unused on the same org.
        accounts = candidate_accounts(db, m.organization_id)
        if not accounts:
            continue
        # One account per tick (not the full failover fan-out): that keeps the
        # deliberate ~60s backoff and the NOTIFY_MAX_ATTEMPTS budget intact,
        # while candidate_accounts' demotion means this tick's pick is a
        # DIFFERENT, healthier provider than the one that just failed.
        try:
            sms_send.send_notification(db, accounts[0], m.to_number, m.body)
        except Exception:
            log.exception("notification retry errored for message %s", m.id)
            continue
        retried += 1
        if retried >= limit:
            break
    if retried:
        db.commit()
    return retried
