"""Outreach module — compliant Instagram DM automation (official Graph API
only; the webhook + send paths mirror the Meta leadgen webhook's trust model).

Design constraints these tables encode:
- The Instagram Messaging API cannot start a conversation with a user who has
  never messaged / commented / mentioned the account. "Outbound" sequences
  therefore fire only into conversations with an open (or reopenable)
  messaging window; prospect lists are WATCH lists that auto-enroll on first
  engagement, never cold-send targets.
- Automated sends never use message tags. HUMAN_AGENT is reserved for manual
  replies typed by a human in the inbox (Meta's condition for that tag).
- OutreachMessage doubles as the append-only audit log of every send:
  trigger (kind + rule/enrollment linkage), rendered text, timestamp, and the
  raw API response are all on the row, and rows are never deleted.
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
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column

# Instagram account lifecycle (mirrors CONN_* on PlatformConnection).
IG_ACTIVE = "active"
IG_DISCONNECTED = "disconnected"  # token invalid/revoked — reconnect banner

# Inbound event types (webhook) — also the trigger_type vocabulary for rules.
EVENT_DM = "dm"
EVENT_STORY_REPLY = "story_reply"
EVENT_COMMENT = "comment"
EVENT_LIVE_COMMENT = "live_comment"
EVENT_MENTION = "mention"
EVENT_STORY_MENTION = "story_mention"
TRIGGER_TYPES = {
    EVENT_DM,
    EVENT_STORY_REPLY,
    EVENT_COMMENT,
    EVENT_LIVE_COMMENT,
    EVENT_MENTION,
    EVENT_STORY_MENTION,
}

# Message rows.
DIR_IN = "in"
DIR_OUT = "out"
MSG_RECEIVED = "received"  # inbound
MSG_QUEUED = "queued"  # window closed — sends when it reopens
MSG_PENDING_REVIEW = "pending_review"  # sequence first-day safety toggle
MSG_SENT = "sent"
MSG_FAILED = "failed"
MSG_DISCARDED = "discarded"  # pending-review message rejected by a human
# Who/what initiated an outbound message.
KIND_MANUAL = "manual"  # human typed it in the inbox
KIND_RULE = "rule"  # inbound trigger rule action
KIND_SEQUENCE = "sequence"  # sequence step
KIND_EXTERNAL = "external"  # echo of a send made outside Salescale (IG app)

# Sequences.
SEQ_DRAFT = "draft"
SEQ_ACTIVE = "active"
SEQ_PAUSED = "paused"
STEP_MESSAGE = "message"
STEP_WAIT = "wait"
STEP_CONDITION = "condition"
COND_REPLIED = "replied"
# Branch actions for condition steps: "continue" | "exit" | "goto:<position>".

ENROLL_ACTIVE = "active"
ENROLL_COMPLETED = "completed"
ENROLL_EXITED = "exited"
EXIT_REPLIED = "replied"
EXIT_STAGE_CHANGE = "stage_change"
EXIT_MANUAL = "manual"
EXIT_ERROR = "error"

PROSPECT_WATCHING = "watching"  # imported/captured, has not engaged yet
PROSPECT_ENGAGED = "engaged"  # they commented/DM'd — a conversation exists
PROSPECT_REMOVED = "removed"

# Messaging-window rules (Instagram): a user message opens a 24h standard
# window; HUMAN_AGENT extends MANUAL replies to 7 days. Automated sends only
# ever use the standard window.
STANDARD_WINDOW_HOURS = 24
HUMAN_AGENT_WINDOW_DAYS = 7
# One private reply per comment, within 7 days of the comment.
PRIVATE_REPLY_WINDOW_DAYS = 7


class InstagramAccount(Base):
    """One connected IG professional account (per client). Also the webhook
    routing row: entry.id on an `object: "instagram"` delivery is the IG
    account's id, matched against ig_user_id — same never-trust-the-payload
    routing as LeadFormConfig.page_id."""

    __tablename__ = "instagram_accounts"
    __table_args__ = (UniqueConstraint("ig_user_id", name="uq_ig_account_user"),)

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    ig_user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    page_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    username: Mapped[Optional[str]] = mapped_column(String(150))
    name: Mapped[Optional[str]] = mapped_column(String(300))
    # Page access token (Fernet) — the credential IG messaging calls use.
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    token_expires_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[str] = mapped_column(String(20), default=IG_ACTIVE, nullable=False)
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    # Tenant-configurable guardrails (STANDING GUARDRAILS: caps are enforced
    # server-side in the send gateway, not just hidden in the UI).
    daily_send_cap: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    automation_paused: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    connected_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class OutreachConversation(Base):
    """One DM thread between a connected IG account and one IG-scoped user.
    last_user_message_at drives the 24h-window check for every send."""

    __tablename__ = "outreach_conversations"
    __table_args__ = (
        # Auto-dedupe by IG-scoped user id per connected account.
        UniqueConstraint("account_id", "ig_user_id", name="uq_convo_account_user"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("instagram_accounts.id"), nullable=False, index=True
    )
    ig_user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    contact_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("contacts.id"), index=True
    )
    # API-provided peer profile (username, name, follower_count, …) — cached
    # from the user-profile endpoint; never scraped.
    peer: Mapped[Optional[dict]] = mapped_column(JSON)
    last_user_message_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    last_message_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_message_preview: Mapped[Optional[str]] = mapped_column(String(400))
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class OutreachMessage(Base):
    """Every message in/out — and, for outbound rows, the audit record of the
    send (trigger linkage, timestamp, raw API response). Append-only."""

    __tablename__ = "outreach_messages"
    __table_args__ = (
        # Webhook idempotency: Meta redelivers; a known mid is a no-op.
        UniqueConstraint("conversation_id", "external_mid", name="uq_msg_convo_mid"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("outreach_conversations.id"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(5), nullable=False)  # in | out
    external_mid: Mapped[Optional[str]] = mapped_column(String(200))
    text: Mapped[Optional[str]] = mapped_column(Text)
    attachments: Mapped[Optional[list]] = mapped_column(JSON)
    # Inbound: which event type carried it (dm | story_reply | …).
    event_type: Mapped[Optional[str]] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default=MSG_RECEIVED, nullable=False)
    # Outbound provenance — the "trigger" half of the audit requirement.
    kind: Mapped[Optional[str]] = mapped_column(String(20))
    rule_id: Mapped[Optional[str]] = mapped_column(ForeignKey("outreach_trigger_rules.id"))
    enrollment_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("outreach_enrollments.id")
    )
    step_id: Mapped[Optional[str]] = mapped_column(ForeignKey("outreach_steps.id"))
    variant: Mapped[Optional[str]] = mapped_column(String(1))  # a | b
    # Reply attribution for variant stats: flipped when the peer's next
    # inbound message lands after this outbound one.
    replied_to: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # HUMAN_AGENT when a manual reply used the tag; automated sends: always None.
    message_tag: Mapped[Optional[str]] = mapped_column(String(30))
    sent_by_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    # Private reply target when this send answered a comment.
    reply_to_comment_id: Mapped[Optional[str]] = mapped_column(String(100))
    api_response: Mapped[Optional[dict]] = mapped_column(JSON)
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    queued_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class InstagramWebhookEvent(Base):
    """Raw inbound webhook events, stored idempotently per account. Only
    routable events (entry.id matched a connected account) are stored —
    unroutable ones are acknowledged and dropped, mirroring leadgen."""

    __tablename__ = "instagram_webhook_events"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_ig_event_account_ext"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("instagram_accounts.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # mid for messaging events, comment/media id for change events.
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="processed", nullable=False)
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = created_at_column()


class OutreachTriggerRule(Base):
    """IF [event matches] THEN [reply + CRM actions]. The visual rule builder
    edits exactly these fields."""

    __tablename__ = "outreach_trigger_rules"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("instagram_accounts.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Empty list = match every event of trigger_type. Matching is
    # case-insensitive substring ("contains any").
    keywords: Mapped[Optional[list]] = mapped_column(JSON)
    # Restrict comment/mention triggers to specific media/ad post ids.
    media_ids: Mapped[Optional[list]] = mapped_column(JSON)
    # Business-signal filters over API-provided profile fields only:
    # {"min_followers": int, "max_followers": int, "verified_only": bool}.
    filters: Mapped[Optional[dict]] = mapped_column(JSON)
    # Actions.
    reply_text: Mapped[Optional[str]] = mapped_column(Text)  # DM / private reply
    create_contact: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tag_names: Mapped[Optional[list]] = mapped_column(JSON)
    enroll_sequence_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("outreach_sequences.id")
    )
    capture_prospect: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Fire at most once per IG user (dedupe across redeliveries/re-engagement).
    once_per_user: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class OutreachSequence(Base):
    __tablename__ = "outreach_sequences"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("instagram_accounts.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=SEQ_DRAFT, nullable=False)
    # Safety toggle: for 24h after activation, message steps require a human
    # approve in the inbox instead of auto-sending. Off by default (spec).
    review_first_day: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    exit_on_reply: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # {"promotion_min_sends": int} etc. — engine knobs with code defaults.
    settings: Mapped[Optional[dict]] = mapped_column(JSON)
    activated_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class OutreachStep(Base):
    __tablename__ = "outreach_steps"
    __table_args__ = (
        UniqueConstraint("sequence_id", "position", name="uq_step_position"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    sequence_id: Mapped[str] = mapped_column(
        ForeignKey("outreach_sequences.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # message|wait|condition
    # message step — text_b present = A/B test until promoted_variant is set
    # by the reply-rate promotion job.
    text_a: Mapped[Optional[str]] = mapped_column(Text)
    text_b: Mapped[Optional[str]] = mapped_column(Text)
    promoted_variant: Mapped[Optional[str]] = mapped_column(String(1))
    # wait step
    wait_hours: Mapped[Optional[int]] = mapped_column(Integer)
    # condition step: COND_REPLIED with two branch actions
    # ("continue" | "exit" | "goto:<position>").
    condition: Mapped[Optional[str]] = mapped_column(String(20))
    on_true: Mapped[Optional[str]] = mapped_column(String(20))
    on_false: Mapped[Optional[str]] = mapped_column(String(20))


class OutreachEnrollment(Base):
    __tablename__ = "outreach_enrollments"
    __table_args__ = (
        # A conversation runs a given sequence at most once at a time; the
        # partial-uniqueness (re-enroll after exit) is enforced in the service.
        UniqueConstraint(
            "sequence_id", "conversation_id", name="uq_enroll_seq_convo"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    sequence_id: Mapped[str] = mapped_column(
        ForeignKey("outreach_sequences.id"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("outreach_conversations.id"), nullable=False, index=True
    )
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contacts.id"))
    prospect_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("outreach_prospects.id")
    )
    status: Mapped[str] = mapped_column(
        String(20), default=ENROLL_ACTIVE, nullable=False
    )
    exit_reason: Mapped[Optional[str]] = mapped_column(String(30))
    current_position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # None = not scheduled (waiting on window reopen / reconnect / terminal).
    next_run_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    waiting_window: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    variant_assignments: Mapped[Optional[dict]] = mapped_column(JSON)
    replied_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    enrolled_by: Mapped[Optional[str]] = mapped_column(String(20))  # rule|manual|prospect
    created_at: Mapped[dt.datetime] = created_at_column()
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))


class OutreachProspect(Base):
    """A target-list entry: a business IG handle we want to reach. Compliant
    semantics: a WATCH-list row — the engine cannot cold-DM it; when the
    prospect engages (comment/DM/mention), it links to the conversation and
    auto-enrolls in `sequence_id` if set."""

    __tablename__ = "outreach_prospects"
    __table_args__ = (
        UniqueConstraint("client_id", "username", name="uq_prospect_client_username"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    account_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("instagram_accounts.id"), index=True
    )
    username: Mapped[str] = mapped_column(String(150), nullable=False)
    ig_user_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # import|engagement|ad_library
    status: Mapped[str] = mapped_column(
        String(20), default=PROSPECT_WATCHING, nullable=False
    )
    # Business-vertical label for the analytics breakdown (hvac, plumbing, …).
    vertical: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    # Business Discovery enrichment (category/website/bio/followers) — public
    # fields the API provides for professional accounts, nothing scraped.
    enrichment: Mapped[Optional[dict]] = mapped_column(JSON)
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contacts.id"))
    conversation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("outreach_conversations.id")
    )
    # Sequence to auto-enroll in the moment the prospect engages.
    sequence_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("outreach_sequences.id")
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)
    engaged_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()
