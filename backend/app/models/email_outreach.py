"""Cold-email outreach module — Phase 1 foundation (SMTP/IMAP mailboxes,
campaigns, sequences, threads, the message ledger, suppression).

Design constraints these tables encode (mirror the IG Outreach module's
idioms, models/outreach.py):
- Every send routes through ONE gateway (services/email_outreach_send.py).
  EmailMessage doubles as the append-only audit log of every send: kind +
  campaign/step/enrollment linkage, rendered subject/body, timestamps, the
  SMTP response, and the compliance tokens (open/unsubscribe) all live on the
  row, and rows are never deleted.
- Cold email is compliance-critical (CLAUDE.md #9): no send path may bypass
  suppression/opt-out. EmailSuppression is the org-scoped opt-out ledger the
  gateway consults before every campaign/manual send, and every inbound
  unsubscribe / bounce writes a row here.
- Mailbox credentials are per-account, per-leg: SMTP and IMAP each have their
  own username + Fernet-encrypted password (smtp_password_encrypted /
  imap_password_encrypted), since send and receive are frequently different
  providers (e.g. Amazon SES SMTP credentials + a real IMAP mailbox). Neither
  password is ever returned to the client.

The campaign/sequence engine (enrollment scheduling, personalization, warmup)
is Phase 2 and builds on these tables + the hook registry in
services/email_outreach_sync.py — this file only defines the schema its
scheduler will drive.
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

# --- Account lifecycle (mirrors IG_ACTIVE/IG_DISCONNECTED on outreach) ---
ACCOUNT_ACTIVE = "active"
ACCOUNT_ERROR = "error"  # SMTP/IMAP auth or transport failure — reconnect banner

# --- Transport security modes ---
SEC_SSL = "ssl"
SEC_STARTTLS = "starttls"
SECURITY_MODES = {SEC_SSL, SEC_STARTTLS}

# --- Campaign lifecycle ---
CAMPAIGN_DRAFT = "draft"
CAMPAIGN_ACTIVE = "active"
CAMPAIGN_PAUSED = "paused"
CAMPAIGN_ARCHIVED = "archived"

# --- Enrollment lifecycle ---
ENROLL_ACTIVE = "active"
ENROLL_COMPLETED = "completed"
ENROLL_EXITED = "exited"
ENROLL_ERROR = "error"
EXIT_REPLIED = "replied"
EXIT_UNSUBSCRIBED = "unsubscribed"
EXIT_BOUNCED = "bounced"
EXIT_MANUAL = "manual"
EXIT_ERROR = "error"
# QA table (Feature B): a reviewer explicitly excluded this enrollment from
# sending rather than approving it.
EXIT_QA_EXCLUDED = "qa_excluded"
# A rendered send was deterministically unsendable (blank body, or a leftover
# "{{" template artifact) — retrying changes nothing, so the engine exits
# rather than defers (services/email_campaigns.py's render guard).
EXIT_RENDER_ERROR = "render_error"

# --- Message rows ---
DIR_IN = "in"
DIR_OUT = "out"
MSG_QUEUED = "queued"
MSG_SENT = "sent"
MSG_FAILED = "failed"
MSG_RECEIVED = "received"  # inbound
MSG_BOUNCED = "bounced"
# Who/what initiated an outbound message.
KIND_CAMPAIGN = "campaign"
KIND_MANUAL = "manual"
KIND_WARMUP = "warmup"

# --- Suppression reasons ---
SUPPRESS_UNSUBSCRIBED = "unsubscribed"
SUPPRESS_BOUNCED = "bounced"
SUPPRESS_MANUAL = "manual"


class EmailAccount(Base):
    """One connected sending mailbox (per Organization). SMTP is the send path,
    IMAP the reply/bounce sync path — each has its OWN username + Fernet-
    encrypted password, since they're frequently different providers (e.g.
    Amazon SES SMTP credentials for sending, a self-hosted or Workspace
    mailbox login for IMAP receive), not just different hosts on the same
    account. status flips to `error` when a probe or a live send/sync hits an
    auth/transport failure, surfacing a reconnect banner."""

    __tablename__ = "email_accounts"
    __table_args__ = (
        UniqueConstraint("organization_id", "from_email", name="uq_email_account_from"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    from_name: Mapped[str] = mapped_column(String(200), nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False)
    smtp_security: Mapped[str] = mapped_column(
        String(20), default=SEC_SSL, nullable=False
    )
    imap_host: Mapped[str] = mapped_column(String(255), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False)
    imap_security: Mapped[str] = mapped_column(
        String(20), default=SEC_SSL, nullable=False
    )
    smtp_username: Mapped[str] = mapped_column(String(320), nullable=False)
    # SMTP password (Fernet). Never serialized back to the client.
    smtp_password_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    imap_username: Mapped[str] = mapped_column(String(320), nullable=False)
    # IMAP password (Fernet). Never serialized back to the client.
    imap_password_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default=ACCOUNT_ACTIVE, nullable=False
    )
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    # Tenant-configurable guardrail — enforced server-side in the send gateway.
    daily_send_cap: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    # Warmup ramp (Phase 2 layers the allowance curve; the fields ship now).
    warmup_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    warmup_started_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    warmup_target_daily: Mapped[int] = mapped_column(
        Integer, default=100, nullable=False
    )
    # IANA zone the warmup engine's send window (08:00–18:00), weekend
    # reduction, and daily-budget midnight are evaluated in. None = UTC.
    warmup_timezone: Mapped[Optional[str]] = mapped_column(String(64))
    signature: Mapped[Optional[str]] = mapped_column(Text)
    # IMAP sync bookkeeping (services/email_outreach_sync.py).
    last_synced_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    last_imap_uid: Mapped[Optional[int]] = mapped_column(Integer)
    last_sync_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = created_at_column()


class EmailCampaign(Base):
    """A cold-email campaign: an account, a send schedule (window/days/cap in
    the account's send timezone), and a sequence of steps. Enrollments flow
    contacts through the steps (Phase 2 engine)."""

    __tablename__ = "email_campaigns"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=CAMPAIGN_DRAFT, nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("email_accounts.id"), nullable=False, index=True
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    send_window_start: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    send_window_end: Mapped[int] = mapped_column(Integer, default=17, nullable=False)
    # Weekdays sending is allowed on, 0=Mon … 6=Sun (Python weekday()).
    send_days: Mapped[Optional[list]] = mapped_column(
        JSON, default=lambda: [0, 1, 2, 3, 4]
    )
    daily_cap: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    open_tracking: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    exit_on_reply: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # QA gate (Feature B): when true, the engine defers any enrollment whose
    # qa_status isn't "approved" instead of sending — a human reviews the
    # audience preview and approves/excludes before the campaign actually
    # sends.
    require_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # AI writing controls (Feature C), threaded into generate_ai_snippet's
    # user content as explicit labeled sections — never into the system
    # prompt (which stays byte-stable for prompt caching).
    ai_tone: Mapped[Optional[str]] = mapped_column(String(200))
    ai_example: Mapped[Optional[str]] = mapped_column(Text)
    settings: Mapped[Optional[dict]] = mapped_column(JSON)
    activated_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class EmailCampaignAccount(Base):
    """One mailbox in a campaign's sending pool. A campaign may send from
    several mailboxes to raise total throughput: each CONTACT is assigned one
    mailbox at first send (cap-aware, least-loaded first) and keeps it for the
    whole sequence — follow-up steps must come from the same sender or the
    thread breaks for the recipient. `position` is the display/rotation order.
    EmailCampaign.account_id stays as the pool's first entry (legacy single-
    mailbox campaigns simply have one row here)."""

    __tablename__ = "email_campaign_accounts"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "account_id", name="uq_email_campaign_account"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("email_campaigns.id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("email_accounts.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class EmailStep(Base):
    """One step in a campaign sequence. A step with a null/blank
    subject_template threads as a "Re:" reply to the previous step's thread;
    wait_days is the delay after the previous step (first step 0)."""

    __tablename__ = "email_steps"
    __table_args__ = (
        UniqueConstraint("campaign_id", "position", name="uq_email_step_position"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("email_campaigns.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    wait_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    subject_template: Mapped[Optional[str]] = mapped_column(Text)
    body_template: Mapped[Optional[str]] = mapped_column(Text)
    ai_instructions: Mapped[Optional[str]] = mapped_column(Text)


class EmailEnrollment(Base):
    """One contact enrolled in one campaign. current_position + next_run_at
    drive the Phase 2 scheduler; ai_snippets caches per-step generated text
    (step_id -> text). thread_id links to the running conversation once the
    first step sends."""

    __tablename__ = "email_enrollments"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "contact_id", name="uq_email_enroll_campaign_contact"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("email_campaigns.id"), nullable=False, index=True
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=ENROLL_ACTIVE, nullable=False
    )
    exit_reason: Mapped[Optional[str]] = mapped_column(String(30))
    current_position: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # None = not scheduled (terminal / waiting on window / reconnect).
    next_run_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    thread_id: Mapped[Optional[str]] = mapped_column(ForeignKey("email_threads.id"))
    # The mailbox this contact's sequence sends from. Assigned on the FIRST
    # successful send (cap-aware pick from the campaign's pool) and sticky
    # thereafter: replies-in-thread must keep the same sender. NULL = not yet
    # sent — the engine picks fresh each attempt until one lands.
    account_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("email_accounts.id"), index=True
    )
    ai_snippets: Mapped[Optional[dict]] = mapped_column(JSON)
    # QA/preview (Feature B): step_id -> {"subject": str|None, "body": str} —
    # a human-edited override, used VERBATIM by process_enrollment instead of
    # render_full for that step. qa_status is "approved" or None; when the
    # owning campaign has require_approval on, only "approved" enrollments
    # are sent.
    overrides: Mapped[Optional[dict]] = mapped_column(JSON)
    qa_status: Mapped[Optional[str]] = mapped_column(String(20))
    replied_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    enrolled_by: Mapped[Optional[str]] = mapped_column(String(36))
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class EmailThread(Base):
    """One conversation between a sending account and one contact. Deduped by
    (account, contact); the unified inbox lists these newest-first."""

    __tablename__ = "email_threads"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "contact_id", name="uq_email_thread_account_contact"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("email_accounts.id"), nullable=False, index=True
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id"), nullable=False, index=True
    )
    subject: Mapped[Optional[str]] = mapped_column(String(500))
    snippet: Mapped[Optional[str]] = mapped_column(Text)
    last_message_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_inbound_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    unread: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class EmailMessage(Base):
    """Every message in/out — and, for outbound rows, the audit record of the
    send (kind + campaign/step/enrollment linkage, subject/body, Message-ID,
    SMTP response, compliance tokens). Append-only."""

    __tablename__ = "email_messages"
    __table_args__ = (
        # Idempotency for both outbound (our generated Message-ID) and inbound
        # (the sender's Message-ID) — a re-synced message is a no-op.
        UniqueConstraint(
            "account_id", "message_id_header", name="uq_email_msg_account_msgid"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    # Nullable so warmup traffic (kind="warmup", mailbox-to-mailbox) can be
    # recorded as an audit row without a human-inbox thread — warmup has no
    # Contact and never surfaces in the unified inbox. Every campaign/manual
    # message still carries a thread.
    thread_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("email_threads.id"), index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("email_accounts.id"), nullable=False, index=True
    )
    contact_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("contacts.id"), index=True
    )
    direction: Mapped[str] = mapped_column(String(5), nullable=False)  # in | out
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[Optional[str]] = mapped_column(String(20))
    campaign_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("email_campaigns.id")
    )
    step_id: Mapped[Optional[str]] = mapped_column(ForeignKey("email_steps.id"))
    enrollment_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("email_enrollments.id")
    )
    subject: Mapped[Optional[str]] = mapped_column(String(500))
    body_text: Mapped[Optional[str]] = mapped_column(Text)
    # RFC 5322 Message-ID header (ours on outbound, the sender's on inbound).
    message_id_header: Mapped[Optional[str]] = mapped_column(String(998))
    in_reply_to: Mapped[Optional[str]] = mapped_column(String(998))
    # Opaque per-message tokens for the tracking pixel / one-click unsubscribe
    # (campaign/manual sends only). Constant-time compared on the public paths.
    open_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    unsubscribe_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    opened_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    open_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bounced_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    smtp_response: Mapped[Optional[str]] = mapped_column(Text)
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    received_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class EmailSuppression(Base):
    """The org-scoped opt-out / do-not-contact ledger. The send gateway
    consults it (casefolded) before every campaign/manual send; inbound
    unsubscribes and bounces write rows here. One row per (org, email)."""

    __tablename__ = "email_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "email", name="uq_email_suppression_org_email"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contacts.id"))
    created_at: Mapped[dt.datetime] = created_at_column()


class EmailWarmupPeer(Base):
    """Persisted pairing state for the warmup loop (Phase 2). One row per
    ordered (account → peer_account) pair within ONE Organization — warmup
    exchange is same-org only (cross-tenant warmup would violate isolation).
    last_sent_at throttles how often `account` warms `peer_account`;
    last_received_at is bumped when `account` receives a warmup echo from
    `peer_account` (bookkeeping for the closed-loop realism check)."""

    __tablename__ = "email_warmup_peers"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "peer_account_id", name="uq_email_warmup_pair"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("email_accounts.id"), nullable=False, index=True
    )
    peer_account_id: Mapped[str] = mapped_column(
        ForeignKey("email_accounts.id"), nullable=False, index=True
    )
    last_sent_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    last_received_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    # Warmup health inputs: lifetime counters for this ordered pair. sent/
    # received feed the delivery-ratio check; junk_count is how many of THIS
    # account's warmup sends the peer's sync found in its spam folder (each
    # one was rescued to the inbox — the count is the placement signal).
    sent_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    received_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    junk_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    created_at: Mapped[dt.datetime] = created_at_column()
