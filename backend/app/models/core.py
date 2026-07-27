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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .base import created_at_column, id_column

# Roles. Owner/Admin/Member are Organization team roles; Client is the
# read-only portal role for an Organization's client contacts.
#   owner  — everything, including team membership (and billing, Phase 8)
#   admin  — manage clients, platform connections, and team members
#   member — day-to-day campaign work; no client or team management
#   client — read-only visibility into their own client account
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLE_CLIENT = "client"
TEAM_ROLES = {ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER}
ADMIN_ROLES = {ROLE_OWNER, ROLE_ADMIN}

PLATFORM_META = "meta"
PLATFORM_GOOGLE = "google"

CONN_ACTIVE = "active"
CONN_DISCONNECTED = "disconnected"  # client revoked or token invalid
CONN_ERROR = "error"

# Organization lifecycle (managed by the platform super-admin).
ORG_ACTIVE = "active"
ORG_SUSPENDED = "suspended"  # blocks login for all of the org's users
# Subscription plans. Informational for now — no payment processor is wired up
# yet (Phase 8), so the super-admin sets these manually.
ORG_PLANS = ("starter", "pro", "agency")


class Organization(Base):
    """The root tenant entity. Every other tenant-owned table carries an
    organization_id and must be filtered by it in every query — an unscoped
    query is a cross-tenant data leak, not a style issue."""

    __tablename__ = "organizations"

    id: Mapped[str] = id_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Platform-managed lifecycle + subscription (set by the super-admin).
    status: Mapped[str] = mapped_column(
        String(20), default=ORG_ACTIVE, nullable=False
    )
    plan: Mapped[str] = mapped_column(String(20), default="starter", nullable=False)
    suspended_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    # Org policy: when true, team members must have 2FA enabled — they're gated
    # to enrollment until they do (see api/auth mfa_setup_required).
    require_mfa: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # Org policy: when true (default), a member who checks "remember this
    # device" at a 2FA challenge gets a trusted-device grant that skips future
    # challenges on that device (see models.core.TrustedDevice, services/
    # trusted_devices.py). Owners handling compliance-sensitive clients may
    # turn this off to force 2FA on every login regardless of device.
    allow_remember_device: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    # Org policy: some agencies collect SMS consent upstream (their own site
    # funnel) before a lead ever reaches Salescale. When true, every NEWLY
    # created contact is stamped opted-in at creation (services/sms_consent.
    # apply_org_default) — STOP/suppression at send time is unaffected.
    sms_opt_in_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # Org policy: text-the-team alerts on new leads, reusing the SMS Outreach
    # module's connected account (services/lead_notify.py) rather than new
    # send infrastructure. lead_notification_phones is the org's own ops
    # numbers (E.164), never a CRM contact — one org-wide list, not per-client.
    notify_new_leads: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    lead_notification_phones: Mapped[Optional[list]] = mapped_column(JSON)
    # Editable message template for the lead-notification SMS (both org-wide
    # and per-client recipients share it) — {{name}}/{{first_name}}/
    # {{last_name}}/{{phone}}/{{email}}/{{brand}}/{{zip}}/{{source}} tokens,
    # substituted in services/lead_notify.py. None means the built-in default
    # template. Capped 1000 chars; unknown tokens are rejected at save time.
    lead_notification_template: Mapped[Optional[str]] = mapped_column(Text)
    # Phase 8 — Stripe subscription linkage. Populated by the billing webhook.
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, index=True
    )
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(64))
    subscription_status: Mapped[Optional[str]] = mapped_column(String(32))
    # `created` time of the last applied Stripe subscription event — an older
    # event is ignored so an out-of-order/replayed webhook can't regress plan.
    subscription_event_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    # Phase 6: the Organization's own qualified-lead definition — a structured
    # checklist (list of {"key", "label"} dicts), not free text. None/empty
    # means the Organization uses a simple qualified yes/no with no checklist.
    # Atlas Reach's 14-Day Trial Sprint criteria are one Organization's data
    # here, never a product assumption.
    qualified_lead_criteria: Mapped[Optional[list]] = mapped_column(JSON)
    # Phase 9 white-labeling. `branding` holds the Organization's own look
    # (see services/branding.py for the shape and defaults) — None means the
    # neutral Salescale identity. The custom domain is how a client-facing
    # portal resolves to this tenant: an Organization claims a hostname,
    # proves control via a DNS TXT record carrying custom_domain_token, and
    # only a *verified* domain ever resolves to its branding (an unverified
    # claim must never let one tenant impersonate another's portal).
    branding: Mapped[Optional[dict]] = mapped_column(JSON)
    custom_domain: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, index=True
    )
    custom_domain_token: Mapped[Optional[str]] = mapped_column(String(100))
    custom_domain_verified_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    # Standing AI-writing context for cold outreach personalization/research
    # (Feature C): {"company_description", "icp", "offer", "tone_guide"},
    # each an optional string capped 2000 chars server-side. None means no
    # org-level context is injected into grounding.
    outreach_context: Mapped[Optional[dict]] = mapped_column(JSON)
    # Owner-selectable AI provider + model overrides (services/ai_provider).
    # NULL = fall back to the operator-global default (settings.ai_provider and
    # that provider's default model). ai_model, when set, applies to BOTH
    # insights and outreach calls for this org.
    ai_provider: Mapped[Optional[str]] = mapped_column(String(20))
    ai_model: Mapped[Optional[str]] = mapped_column(String(80))
    # The agency's default IANA timezone (e.g. "America/Phoenix"). NULL = the
    # outreach fallback. New SMS/email campaigns inherit it for their
    # send-window / TCPA quiet-hours evaluation; a client's own timezone (below)
    # takes precedence for that client's campaigns. See services/timezones.
    timezone: Mapped[Optional[str]] = mapped_column(String(64))
    # Lead-reply relay (services/lead_relay.py): when enabled, an inbound lead
    # reply on the org's BlueBubbles number is forwarded to lead_relay_phone
    # (E.164), and a message FROM that phone — tagged with a lead's reply code —
    # is relayed back to the lead through BlueBubbles. The relay phone is also
    # the one number whose inbound is treated as an operator command, never a
    # lead. BlueBubbles-only (that's the transport the operator loops through).
    lead_relay_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    lead_relay_phone: Mapped[Optional[str]] = mapped_column(String(20))
    created_at: Mapped[dt.datetime] = created_at_column()


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # Free-form vertical label (e.g. "hvac", "dental") — the grouping key for
    # cross-client benchmarking within the same Organization (Phase 3). Set by
    # the Organization; never compared across Organizations.
    vertical: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    # Where lead-quality truth lives during a transition: "salescale" (native,
    # default) or "external" (client's nurture automation still runs in an
    # external CRM — see services/lead_quality.py for the provider interface).
    lead_quality_source: Mapped[str] = mapped_column(
        String(20), default="salescale", nullable=False
    )
    # Per-client metric configuration (JSON): funnel-tier name patterns, UTM
    # convention overrides, external-CRM provider settings. Everything in it
    # has a documented code default — the column exists so per-client
    # variation is data, not code.
    metric_settings: Mapped[Optional[dict]] = mapped_column(JSON)
    # This client's IANA timezone (e.g. the market their leads are in). NULL =
    # inherit the Organization's default. Takes precedence over the org default
    # when a campaign scoped to this client picks its send-window timezone.
    timezone: Mapped[Optional[str]] = mapped_column(String(64))
    # Organization-internal — must never be serialized to client-role users.
    internal_notes: Mapped[Optional[str]] = mapped_column(Text)
    # The Organization's own prospect pipeline (the agency "house" CRM) lives on
    # a single synthetic Client row flagged here — one per org, hidden from the
    # client roster/counts and never billed as a client, never given a portal
    # user. The existing CRM then runs against it unchanged (see
    # api/orgs get_house_client).
    is_house: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[dt.datetime] = created_at_column()

    connections: Mapped[list] = relationship(
        "PlatformConnection", back_populates="client"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # owner | admin | member | client
    # Required when role == client; identifies the one client they can see.
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Bumped to revoke all outstanding sessions (password reset, logout-all).
    # The access token carries this value; get_current_user rejects a token
    # whose version is behind the user's.
    token_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # How the account was created: None = email/password (local), else the
    # social provider ("google"/"meta"). Used to decide whether a social login
    # may attach to an existing account (see api/social_auth.py).
    auth_provider: Mapped[Optional[str]] = mapped_column(String(20))

    # --- Two-factor auth (see services/mfa.py, api/mfa.py) ---
    # Active second factor: None = off, else "totp" | "email" | "sms".
    mfa_method: Mapped[Optional[str]] = mapped_column(String(10))
    # TOTP shared secret (Fernet-encrypted at rest). Present once TOTP is set up
    # (may exist unconfirmed during enrollment before mfa_method flips to totp).
    totp_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    # SMS destination for phone 2FA (encrypted — it's PII).
    mfa_phone_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    # One-time recovery codes, each stored as a bcrypt hash; consumed on use.
    mfa_backup_codes: Mapped[Optional[list]] = mapped_column(JSON)
    # A pending email/SMS one-time code (bcrypt-hashed) + its expiry, set when a
    # login challenge or enrollment sends a code.
    mfa_otp_hash: Mapped[Optional[str]] = mapped_column(String(200))
    mfa_otp_expires_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class ProcessedStripeEvent(Base):
    """Idempotency ledger for Stripe webhooks: an event id present here was
    already handled, so retries/replays become no-ops."""

    __tablename__ = "processed_stripe_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # Stripe event id
    created_at: Mapped[dt.datetime] = created_at_column()


class UserSession(Base):
    """One active login session (device). The access token carries this row's
    id as `sid`; get_current_user rejects a token whose session is missing or
    revoked, which powers session viewing and per-device / everywhere logout."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    user_agent: Mapped[Optional[str]] = mapped_column(String(400))
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class TrustedDevice(Base):
    """A "remember this device" grant: proof that this browser recently passed
    a 2FA challenge, so POST /api/auth/login can skip issuing a new one for it
    until expires_at. Proven by a random token the client stores locally and
    sends back on /login (header, never a cookie — this app has no cookie
    auth surface) — only its sha256 hash is ever persisted, the same
    lookup-by-hash pattern models/team.py uses for invite tokens. This is
    NOT a session and never substitutes for one; it only ever short-circuits
    the MFA step, and every real request still needs a genuine access token
    tied to a UserSession. Explicitly listable and revocable per user
    (api/auth.py trusted-devices endpoints), and wiped alongside every other
    account-wide credential reset (logout-all, password reset, MFA disable)."""

    __tablename__ = "trusted_devices"

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    user_agent: Mapped[Optional[str]] = mapped_column(String(400))
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_used_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class PlatformConnection(Base):
    __tablename__ = "platform_connections"
    __table_args__ = (
        UniqueConstraint("client_id", "platform", name="uq_connection_client_platform"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # meta | google
    status: Mapped[str] = mapped_column(String(20), default=CONN_ACTIVE, nullable=False)
    # Fernet-encrypted; never store plaintext (see security.encrypt_secret).
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    token_expires_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    scopes: Mapped[Optional[str]] = mapped_column(Text)
    external_user_id: Mapped[Optional[str]] = mapped_column(String(100))
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    connected_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    # Cursor for the background insights poll (insights_sync.run_due) —
    # stamped at attempt start so a failing connection retries on the
    # interval instead of hot-looping. Also the dashboard's staleness cue.
    last_insights_sync_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    client: Mapped["Client"] = relationship("Client", back_populates="connections")


class AdAccount(Base):
    __tablename__ = "ad_accounts"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_ad_account_platform_ext"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("platform_connections.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    timezone: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = created_at_column()
