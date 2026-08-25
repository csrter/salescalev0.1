"""SMS outreach module — framework (accounts, campaigns, sequences, the
message ledger, suppression) for texting numbers that OPTED IN on the
Organization's own website and arrive via CSV import.

Design constraints (mirror the cold-email module, models/email_outreach.py):
- Every send routes through ONE gateway (services/sms_send.py). SmsMessage is
  the append-only audit log: kind + campaign/step/enrollment linkage, the
  rendered body, the provider SID/status, timestamps. Rows are never deleted.
- SMS is MORE compliance-critical than email (TCPA: statutory damages per
  text). No send path may bypass (a) the consent gate — contacts must carry
  a recorded opt-in (sms_opt_in/at/source on Contact) — or (b) SmsSuppression,
  the org-scoped STOP ledger. Quiet hours (TCPA 8am–9pm recipient-local,
  enforced via the campaign send window) are checked in the gateway too.
- Provider is BYO ONLY: each Organization connects its OWN Twilio account
  (account SID + Fernet-encrypted auth token + from number / messaging
  service). Salescale never operates a shared sending number, and A2P 10DLC
  brand/campaign registration is the tenant's responsibility inside their
  Twilio console — surfaced in the UI, not automated.
- STOP/HELP handling: Twilio Advanced Opt-Out already blocks sends to
  opted-out numbers at the carrier level (error 21610); we ALSO record every
  STOP in SmsSuppression and exit all of the contact's active SMS campaigns
  org-wide, so our own ledger never depends on the provider's.

The campaign/enrollment engine (services/sms_campaigns.py) builds on these
tables — this file defines the schema it drives.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column

# --- Account lifecycle (mirrors email ACCOUNT_*) ---
SMS_ACCOUNT_ACTIVE = "active"
SMS_ACCOUNT_ERROR = "error"  # auth/transport failure — reconnect banner

# --- Campaign lifecycle (identical state machine to EmailCampaign) ---
SMS_CAMPAIGN_DRAFT = "draft"
SMS_CAMPAIGN_ACTIVE = "active"
SMS_CAMPAIGN_PAUSED = "paused"
SMS_CAMPAIGN_ARCHIVED = "archived"

SMS_ENROLL_ACTIVE = "active"
SMS_ENROLL_COMPLETED = "completed"
SMS_ENROLL_EXITED = "exited"  # exit_reason: replied | opted_out | manual | failed |
# render_empty | render_error | too_long (the send-time personalization
# failsafes in services/sms_campaigns.py — deterministic, so they exit
# rather than retry)
SMS_ENROLL_ERROR = "error"

# --- Step triggers ---
# "schedule" (default) — fires wait_days/wait_minutes after the previous step,
# the classic drip. "reply" — fires wait_days/wait_minutes AFTER THE LEAD
# REPLIES: the engine parks the enrollment awaiting a reply
# (awaiting_reply_since) and the inbound webhook schedules the step when one
# arrives (services/sms_campaigns.handle_reply). A reply step may carry
# `branches` so the response depends on WHAT they said.
SMS_TRIGGER_SCHEDULE = "schedule"
SMS_TRIGGER_REPLY = "reply"

SMS_DIR_IN = "in"
SMS_DIR_OUT = "out"
SMS_MSG_QUEUED = "queued"
SMS_MSG_SENT = "sent"
SMS_MSG_DELIVERED = "delivered"  # via Twilio status callback
SMS_MSG_READ = "read"  # Sendblue/iMessage read receipt only — Twilio never sends this
SMS_MSG_FAILED = "failed"
SMS_MSG_RECEIVED = "received"  # inbound

# Monotonic status ladder. Delivery reports arrive OUT OF ORDER as a matter of
# course (Sendblue emits DELIVERED and READ as separate callbacks; providers
# retry callbacks; the BlueBubbles verification pass reads state back minutes
# later) — and `status` is one destructive column. Assigning it directly means
# a late-arriving 'delivered' overwrites a 'read', which both drops the read
# from analytics and, on the failure path, can rewind an enrollment that
# already succeeded and re-text the lead. Every writer goes through
# advance_status() instead of assigning.
_STATUS_RANK = {
    SMS_MSG_QUEUED: 0,
    SMS_MSG_SENT: 1,
    SMS_MSG_DELIVERED: 2,
    SMS_MSG_READ: 3,
}


def advance_status(current: Optional[str], incoming: str) -> str:
    """The status a row should hold after an `incoming` report, never moving
    backwards down the ladder.

    Rules, in order:
    - A progress report (sent/delivered/read) only applies if it moves UP.
      A stale 'delivered' after a 'read' is ignored.
    - A failure only applies to a row that hasn't been confirmed received.
      Once a message is delivered or read it demonstrably arrived, so a late
      failure callback is the stale one, not the delivery.
    - Conversely a delivered/read report DOES override a prior 'failed': proof
      of arrival is stronger evidence than a provider's failure guess.
    """
    current = current or SMS_MSG_QUEUED
    if incoming == SMS_MSG_FAILED:
        # Don't let a late failure erase a confirmed delivery/read.
        if _STATUS_RANK.get(current, 0) >= _STATUS_RANK[SMS_MSG_DELIVERED]:
            return current
        return SMS_MSG_FAILED
    if incoming not in _STATUS_RANK:
        return current
    if current == SMS_MSG_FAILED:
        return incoming
    return incoming if _STATUS_RANK[incoming] > _STATUS_RANK.get(current, 0) else current


SMS_KIND_CAMPAIGN = "campaign"
SMS_KIND_MANUAL = "manual"
# An alert to the agency's OWN team (services/lead_notify.py), not lead
# outreach — the recipient is an ops phone number, never a Contact, so this
# kind skips the TCPA consent/suppression gate entirely (see sms_send.
# send_notification).
SMS_KIND_NOTIFICATION = "notification"

SMS_SUPPRESS_STOP = "stop"  # inbound STOP/UNSUBSCRIBE/etc.
SMS_SUPPRESS_CARRIER = "carrier"  # Twilio 21610 — opted out at the provider
SMS_SUPPRESS_MANUAL = "manual"

# FCC (2025): any reasonable revocation must be honored. These are the
# standard keywords checked case-insensitively as the full trimmed body.
STOP_KEYWORDS = ("stop", "stopall", "stop all", "unsubscribe", "cancel", "end", "quit", "revoke", "optout", "opt out")
HELP_KEYWORDS = ("help", "info")


class SmsAccount(Base):
    """One connected Twilio sender (per Organization): the org's own account
    SID + auth token (Fernet — never serialized back) and either a from
    number (E.164) or a Messaging Service SID."""

    __tablename__ = "sms_accounts"
    __table_args__ = (
        UniqueConstraint("organization_id", "from_number", name="uq_sms_account_from"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), default="twilio", nullable=False)
    account_sid: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    # E.164 sending number ("+14805550100"). Optional when messaging_service_sid
    # is set (Twilio picks the number from the service's pool).
    from_number: Mapped[Optional[str]] = mapped_column(String(20))
    messaging_service_sid: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(20), default=SMS_ACCOUNT_ACTIVE, nullable=False
    )
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    # Tenant guardrail, enforced server-side in the gateway. Long codes are
    # carrier-limited under A2P 10DLC anyway; 200 is a sane default.
    daily_send_cap: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    # Webhook URL secret for providers WITHOUT request signing (Sendblue).
    # Twilio's webhooks are authenticated by X-Twilio-Signature instead; this
    # token is generated for every account regardless (secrets.token_urlsafe)
    # and compared via hmac.compare_digest in the webhook routes.
    webhook_token: Mapped[Optional[str]] = mapped_column(String(64))
    # BlueBubbles (self-hosted iMessage, dev/prototype provider) VPS relay
    # base URL — e.g. https://relay.example.com. auth_token_encrypted carries
    # the BlueBubbles server password for this provider.
    relay_url: Mapped[Optional[str]] = mapped_column(String(500))
    # Minimum seconds between outbound sends on this account, enforced in the
    # gateway (services/sms_send.send) for ANY provider — null/0 = off. Mainly
    # useful for BlueBubbles' single-device send rate.
    min_send_spacing_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    # Upper bound of the pacing range. When set alongside min (max > min), a
    # deferred send is rescheduled to a UNIFORM RANDOM point in
    # [min, max] seconds (services/sms_send.next_spacing_time) — a real
    # randomized range rather than a floor scaled by a fixed jitter factor.
    # Null falls back to the older floor*1.0-1.8x jitter behavior.
    max_send_spacing_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    # DEPRECATED (2026-08-25) — no longer read anywhere. BlueBubbles now sends
    # EVERY message on the green-bubble SMS service unconditionally; see
    # services/sms_send.BLUEBUBBLES_SERVICE for why the per-account toggle and
    # the iMessage routing it guarded were removed. Kept as a column so no
    # migration is needed; drop it in a later sweep.
    bluebubbles_force_sms: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class SmsCampaign(Base):
    """An SMS sequence campaign. The send window doubles as the TCPA
    quiet-hours guard: defaults 11–20 in America/New_York keep every
    continental-US recipient inside 8am–9pm local even when the list spans
    time zones — orgs narrowing to one region may widen it."""

    __tablename__ = "sms_campaigns"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=SMS_CAMPAIGN_DRAFT, nullable=False
    )
    # Nullable: deleting the account this campaign sends from clears this to
    # NULL instead of forcing the campaign to be archived first (see
    # api.sms_outreach.delete_account). process_enrollment already parks an
    # active campaign's enrollments when the account is missing — an admin
    # restores sending with a PATCH that sets a new account_id.
    account_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("sms_accounts.id"), nullable=True, index=True
    )
    timezone: Mapped[str] = mapped_column(
        String(64), default="America/New_York", nullable=False
    )
    send_window_start: Mapped[int] = mapped_column(Integer, default=11, nullable=False)
    send_window_end: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    # Weekdays sending is allowed on, 0=Mon … 6=Sun (Python weekday()).
    send_days: Mapped[Optional[list]] = mapped_column(
        JSON, default=lambda: [0, 1, 2, 3, 4]
    )
    daily_cap: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    exit_on_reply: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # CTIA sender-id + "Reply STOP to opt out" on the first message of the
    # program. Default on; an org may turn it off per-campaign for known,
    # already-consenting contacts (e.g. past clients being followed up with).
    # STOP handling itself is unaffected either way — this only controls
    # whether the reminder text is shown.
    # Once a reply step sends a matched BRANCH response — the pitch, the
    # parting message, the substantive answer — the automated conversation is
    # over and a human takes it from there. Without this an enrollment
    # re-opens on every later keyword match, so a lead who keeps texting can
    # be pitched twice, or pitched and then sent the parting message on top.
    # The step's generic default body does NOT count as a branch: it is a
    # placeholder that deliberately leaves the door open for the real branch
    # to fire on the lead's next message. Off = a deliberately multi-turn
    # campaign keeps answering.
    stop_after_branch: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )
    # A person on the team texting this lead directly (the Messages tab, or the
    # lead relay from their own phone) ends the automated sequence for them —
    # a scheduled drip landing on top of a live human conversation is the most
    # embarrassing thing this module can do. See
    # sms_campaigns.stop_for_human_takeover; the durable marker is
    # Contact.sms_handover_at, and this flag is the per-campaign opt-out for a
    # sequence that is MEANT to run alongside a person.
    stop_on_human_reply: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )
    include_compliance_footer: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    # When true (and status is active and client_id is set), a NEW lead created
    # for this campaign's client is auto-enrolled the moment it arrives
    # (services/lead_autoenroll.py, fired at the same lead-creation call sites
    # as the team alert). The consent gate still applies at enroll time — a lead
    # with no recorded SMS opt-in is skipped, not force-enrolled. Requires
    # client_id so the trigger knows whose leads flow in; enforced in the API.
    auto_enroll_new_leads: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # A template holds config + steps for reuse but is otherwise inert: the
    # API refuses to activate it or enroll anyone into it (see api/sms_outreach
    # .activate_campaign / enroll_contacts). "Create from template" clones a
    # template into a normal is_template=False campaign (create_campaign's
    # template_id path), mirroring the duplicate-campaign endpoint.
    is_template: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    settings: Mapped[Optional[dict]] = mapped_column(JSON)
    activated_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class SmsStep(Base):
    """One step in an SMS sequence. body_template supports the same
    {{token|fallback}} personalization as email (minus ai_snippet /
    unsubscribe_url). wait_days is the delay after the previous step."""

    __tablename__ = "sms_steps"
    __table_args__ = (
        UniqueConstraint("campaign_id", "position", name="uq_sms_step_position"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("sms_campaigns.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    wait_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Finer-grained delay ADDED to wait_days (total = days + minutes). For a
    # reply-triggered step this is the delay after the lead's reply — "text
    # them back 5 minutes after they respond".
    wait_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # SMS_TRIGGER_SCHEDULE | SMS_TRIGGER_REPLY (see constants above).
    trigger: Mapped[str] = mapped_column(
        String(20), default=SMS_TRIGGER_SCHEDULE, nullable=False
    )
    body_template: Mapped[Optional[str]] = mapped_column(Text)
    # Reply-step response branching: [{"label": str, "keywords": [str], "body":
    # str}]. At send time the lead's last reply is matched against each
    # branch's keywords in order (deterministic, word-boundary); the first hit's
    # body is sent instead of body_template. body_template is the DEFAULT
    # response when nothing matches. Branch bodies use the same {{token}}
    # grammar and are validated at save time like body_template.
    branches: Mapped[Optional[list]] = mapped_column(JSON)
    # When keywords miss and this is on, ONE cheap grounded AI call classifies
    # the reply into a branch label (services/sms_campaigns.classify_reply) —
    # fail-open to the default body on any AI failure, never blocking a send.
    ai_branching: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Grounded {{ai_snippet}} instructions (mirrors EmailStep.ai_instructions).
    ai_instructions: Mapped[Optional[str]] = mapped_column(Text)


class SmsEnrollment(Base):
    """One contact enrolled in one SMS campaign. current_position (1-indexed,
    matching EmailEnrollment's convention) + next_run_at drive the engine."""

    __tablename__ = "sms_enrollments"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "contact_id", name="uq_sms_enroll_campaign_contact"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("sms_campaigns.id"), nullable=False, index=True
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=SMS_ENROLL_ACTIVE, nullable=False
    )
    exit_reason: Mapped[Optional[str]] = mapped_column(String(30))
    current_position: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    next_run_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    replied_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    # Awaiting-a-reply park state: set (with next_run_at NULL) when the
    # enrollment's current step is reply-triggered and no reply has arrived
    # yet. Distinct from the paused/disconnected park (next_run_at NULL,
    # awaiting NULL) so rearm_parked never force-fires a step that's supposed
    # to wait for the lead.
    awaiting_reply_since: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    # Most recent inbound reply (branch-matching input + UI context). replied_at
    # above stays the FIRST reply (the stats definition).
    last_reply_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    last_reply_body: Mapped[Optional[str]] = mapped_column(Text)
    # When a matched BRANCH response was sent (not the step's default body).
    # With the campaign's stop_after_branch on, this is what makes the
    # enrollment stop answering: no re-open from completed, no further
    # reply-step scheduling. NULL means the real answer has not gone out yet.
    branch_sent_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    enrolled_by: Mapped[Optional[str]] = mapped_column(String(36))
    # How the contact entered the campaign — "manual" | "list" | "client" |
    # "auto_new_lead" — with a human-readable detail (list name at enroll time,
    # or the lead's own capture source for auto-enrolls). Plain strings by
    # design: attribution must survive list rename/delete.
    source: Mapped[Optional[str]] = mapped_column(String(30))
    source_detail: Mapped[Optional[str]] = mapped_column(String(120))
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    # Cache: step_id -> generated snippet text (mirrors EmailEnrollment.ai_snippets)
    # so re-processing an enrollment never re-bills the AI provider.
    ai_snippets: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = created_at_column()


class SmsMessage(Base):
    """Append-only send/receive ledger — the audit trail (guardrail #8) and
    the monthly entitlement meter (direction=out rows). provider_sid is
    Twilio's Message SID (or Sendblue's message_handle); status moves
    queued→sent→delivered/failed via the status callback, or →read for a
    Sendblue/iMessage read receipt (Twilio never sends this). read_at is
    dual-purpose by direction: for an outbound row it's when the RECIPIENT
    read it (from that same Sendblue receipt); for an inbound row it's when
    OUR team marked the conversation read (POST /messages/mark-read) — null
    means unread on whichever side is doing the reading."""

    __tablename__ = "sms_messages"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("sms_accounts.id"), nullable=False, index=True
    )
    campaign_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("sms_campaigns.id"), index=True
    )
    step_id: Mapped[Optional[str]] = mapped_column(ForeignKey("sms_steps.id"))
    enrollment_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("sms_enrollments.id")
    )
    contact_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("contacts.id"), index=True
    )
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20), default=SMS_KIND_CAMPAIGN, nullable=False
    )
    to_number: Mapped[str] = mapped_column(String(20), nullable=False)
    from_number: Mapped[Optional[str]] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=SMS_MSG_QUEUED, nullable=False
    )
    provider_sid: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(20))
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    read_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    # When the transition into delivered/failed actually happened. created_at
    # is the send instant, so (delivered_at - created_at) is real time-to-
    # delivery — previously unmeasurable, since only the STATUS was stored and
    # never the moment it changed. Stamped by the status webhooks and by the
    # verification pass; never overwritten once set (first confirmation wins).
    delivered_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    failed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    # BlueBubbles post-send verification (services/sms_verify): when this
    # row's REAL outcome was read back from the Mac's Messages DB. The
    # AppleScript path reports success at hand-off and only records failures
    # asynchronously, so "sent" is provisional until this is stamped.
    #
    # verified_at means TERMINAL — the outcome is final and the row is never
    # polled again. last_checked_at/check_attempts drive the RE-POLL loop that
    # runs before that point: the relay can report error=0 on a send that later
    # flips to failed, so an early success read is provisional (recorded here)
    # and only becomes terminal once the row is old enough to trust. A failure
    # (error != 0) is trustworthy immediately and terminalizes on first sight.
    last_checked_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    check_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default=text("0")
    )
    verified_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    # Transport actually used for this message — "iMessage"/"SMS"/"RCS".
    # Populated by status webhooks (Sendblue's `service` field / BlueBubbles'
    # updated-message), not at send time. An iMessage-capable provider
    # (sendblue/bluebubbles) reporting "SMS" here is the green-bubble
    # downgrade signal channel_health watches for.
    service: Mapped[Optional[str]] = mapped_column(String(20))
    # Inbound only: this reply is an automated out-of-office / auto-responder,
    # not a human answer. Set at ingest so campaign stats can exclude it from
    # real reply engagement (see services/sms_campaigns.is_auto_reply).
    is_auto_reply: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    # Outbound only: this row is a reply-step branch response whose branch is
    # flagged `interested` (see SmsStep.branches). Stamped once at send time
    # from the matched branch — persisted rather than recomputed from the
    # step's current branches, so it survives later edits to the step. The
    # Messages tab's "interested only" filter reads this.
    is_interested: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class SmsSuppression(Base):
    """Org-scoped opt-out ledger, keyed on normalized E.164. The gateway
    consults it before EVERY campaign/manual send; inbound STOP and Twilio
    21610 errors both write here. Never bypassed, never auto-expired."""

    __tablename__ = "sms_suppressions"
    __table_args__ = (
        UniqueConstraint("organization_id", "phone_e164", name="uq_sms_suppress"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(
        String(20), default=SMS_SUPPRESS_STOP, nullable=False
    )
    detail: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = created_at_column()
