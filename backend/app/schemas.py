import datetime as dt
from typing import Annotated, Any, Dict, List, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from . import platforms as platform_registry


def _within_bcrypt_limit(v: str) -> str:
    # bcrypt silently truncates at 72 bytes; reject anything longer so a
    # password is never quietly shortened (which also weakens it).
    if len(v.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes long")
    return v


# A password to be hashed: 8..72 bytes. Reused by every credential-setting model.
Password = Annotated[str, Field(min_length=8), AfterValidator(_within_bcrypt_limit)]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class OkResponse(BaseModel):
    ok: bool = True


# --- Two-factor auth ---


class MfaStatusOut(BaseModel):
    method: Optional[str] = None  # None | "totp" | "email" | "sms"
    phone_hint: Optional[str] = None  # masked phone, for the sms method
    backup_codes_remaining: int = 0


class TotpSetupOut(BaseModel):
    secret: str  # base32, for manual entry
    otpauth_uri: str  # rendered as a QR code by the frontend


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class MfaSmsSetupIn(BaseModel):
    phone: str = Field(min_length=7, max_length=20)


class MfaDisableIn(BaseModel):
    password: str


class MfaEnabledOut(BaseModel):
    method: str
    backup_codes: List[str]  # shown once, at enable time


class LoginChallenge(BaseModel):
    """Returned by /login when the account has 2FA — no session yet."""

    mfa_required: bool = True
    method: str
    challenge_token: str


class MfaLoginIn(BaseModel):
    challenge_token: str
    code: str = Field(min_length=4, max_length=20)  # a 2FA code or a backup code
    # "Remember this device" — mint a trusted-device grant on success so this
    # browser skips the 2FA challenge next time (services/trusted_devices.py).
    # Ignored when the org has turned the policy off.
    remember_device: bool = False


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: Password


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    organization_id: str
    organization_name: str
    client_id: Optional[str] = None
    full_name: str
    # Platform-operator flag (derived from the SUPERADMIN_EMAILS allowlist).
    # Tells the frontend whether to surface the cross-tenant admin console.
    is_superadmin: bool = False
    email_verified: bool = False
    # True when the org requires 2FA of team members and this user hasn't set it
    # up — the frontend gates them to enrollment.
    mfa_setup_required: bool = False
    # Present only when /login/mfa just minted a "remember this device" grant
    # (remember_device=true on that request) — the frontend stores it and
    # sends it back as X-Device-Token on future /login calls.
    device_token: Optional[str] = None


class TrustedDeviceOut(BaseModel):
    id: str
    user_agent: Optional[str] = None
    ip: Optional[str] = None
    created_at: dt.datetime
    last_used_at: dt.datetime
    expires_at: dt.datetime


class SessionOut(BaseModel):
    id: str
    user_agent: Optional[str] = None
    ip: Optional[str] = None
    created_at: dt.datetime
    last_seen_at: dt.datetime
    current: bool = False


class OrgSignupRequest(BaseModel):
    """Self-serve Organization signup — the same generic flow every tenant
    (including Atlas Reach) is created through."""

    organization_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: Password
    full_name: str = Field(min_length=1, max_length=200)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    require_mfa: bool = False
    allow_remember_device: bool = True
    sms_opt_in_default: bool = False
    timezone: Optional[str] = None
    created_at: dt.datetime


class OrgSecurityIn(BaseModel):
    require_mfa: bool


class OrgRememberDeviceIn(BaseModel):
    allow_remember_device: bool


class OrgSmsOptInDefaultIn(BaseModel):
    sms_opt_in_default: bool


class OrgTimezoneIn(BaseModel):
    # None clears the org default (falls back to the outreach default). A
    # non-null value must be a resolvable IANA name / known abbreviation.
    timezone: Optional[str] = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return _valid_campaign_timezone(v)


class OrgLeadRelayIn(BaseModel):
    """Lead-reply relay config. `phone` is the operator's number that lead
    replies forward to and that can text leads back through BlueBubbles;
    required when enabling."""

    enabled: bool
    phone: Optional[str] = None


class TeamMemberCreate(BaseModel):
    email: EmailStr
    password: Password
    full_name: str = Field(min_length=1, max_length=200)
    role: str  # admin | member (owner is only created via signup)


class TeamMemberUpdate(BaseModel):
    """Owner-only edits to an existing team member."""

    role: Optional[str] = None  # admin | member
    is_active: Optional[bool] = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    role: str
    client_id: Optional[str] = None
    is_active: bool
    created_at: dt.datetime


# --- Phase 13: invites, memberships, seats ---


class InviteCreate(BaseModel):
    email: EmailStr
    role: str  # admin | member (ownership moves only via explicit transfer)


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: str
    status: str
    invited_by_user_id: str
    expires_at: dt.datetime
    created_at: dt.datetime
    # Present ONLY in the send/resend response when email delivery isn't
    # configured (dev/desktop) or failed — the inviting Admin shares the link
    # out-of-band. Never stored (the DB keeps only the token hash), so it can
    # never appear in list responses.
    invite_link: Optional[str] = None


class InviteLookupOut(BaseModel):
    """Public preview of an invite for the accept page: enough to render the
    right flow (login vs signup), nothing Organization-internal beyond the
    name the invitee was already told in the email."""

    organization_name: str
    email: str
    role: str
    status: str  # pending | accepted | revoked | expired
    account_exists: bool


class InviteAcceptRequest(BaseModel):
    token: str


class InviteAcceptSignupRequest(BaseModel):
    token: str
    full_name: str = Field(min_length=1, max_length=200)
    password: Password


class MembershipOut(BaseModel):
    organization_id: str
    organization_name: str
    role: str
    is_active_org: bool


class SwitchOrgRequest(BaseModel):
    organization_id: str


class TransferOwnershipRequest(BaseModel):
    member_id: str
    # Default: the transferring Owner steps down to Admin. False keeps them a
    # co-Owner (the last-Owner guard then counts both).
    demote_self: bool = True


class RemoveMemberRequest(BaseModel):
    # Where the removed member's open work (assigned CRM tasks) goes. None =
    # the remover. Records are reassigned, never orphaned or deleted.
    reassign_to_user_id: Optional[str] = None


class SeatUsageOut(BaseModel):
    used: int
    pending_invites: int
    limit: Optional[int] = None  # None = unlimited
    plan: str


class MembershipAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_email: str
    actor_name: str
    action: str
    target_email: Optional[str] = None
    detail: Optional[dict] = None
    created_at: dt.datetime


# --- Platform super-admin (cross-tenant) response models ---


class AdminStats(BaseModel):
    organizations: int
    users: int
    clients: int
    active_connections: int
    signups_last_30d: int


class AdminOrgRow(BaseModel):
    id: str
    name: str
    created_at: dt.datetime
    status: str
    plan: str
    user_count: int
    client_count: int
    connection_count: int
    contact_count: int


class AdminOrgDetail(BaseModel):
    id: str
    name: str
    created_at: dt.datetime
    status: str
    plan: str
    users: List[UserOut]
    clients: List[Dict[str, Any]]


class AdminOrgUpdate(BaseModel):
    status: Optional[str] = None  # active | suspended
    plan: Optional[str] = None  # starter | pro | agency


class PasswordResetResult(BaseModel):
    user_id: str
    email: str
    temporary_password: str


class AdminSignupPoint(BaseModel):
    date: str  # YYYY-MM-DD
    count: int


# --- Billing (Stripe) ---


class CheckoutRequest(BaseModel):
    plan: str  # pro | agency


class CheckoutSessionOut(BaseModel):
    url: str


class SubscriptionOut(BaseModel):
    plan: str
    status: Optional[str] = None
    billing_enabled: bool


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    internal_notes: Optional[str] = Field(default=None, max_length=10000)


# Two serializations of Client, chosen by caller role. ClientOutPublic is the
# only shape a client-role user ever receives — internal_notes is absent from
# the schema itself, not nulled, so it can't leak through serialization.
class ClientOutPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: str


class ClientOutTeam(ClientOutPublic):
    internal_notes: Optional[str] = None
    timezone: Optional[str] = None
    created_at: dt.datetime


class ClientTimezoneIn(BaseModel):
    # None clears the client's timezone (falls back to the org default).
    timezone: Optional[str] = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return _valid_campaign_timezone(v)


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    platform: str
    status: str
    scopes: Optional[str] = None
    error_detail: Optional[str] = None
    connected_at: Optional[dt.datetime] = None
    disconnected_at: Optional[dt.datetime] = None


class AdAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    platform: str
    external_id: str
    name: str
    currency: Optional[str] = None
    timezone: Optional[str] = None
    status: Optional[str] = None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    ad_account_id: str
    platform: str
    external_id: str
    name: str
    status: Optional[str] = None
    objective: Optional[str] = None
    daily_budget_micros: Optional[int] = None
    lifetime_budget_micros: Optional[int] = None
    synced_at: Optional[dt.datetime] = None


class AdGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    campaign_id: str
    platform: str
    external_id: str
    name: str
    status: Optional[str] = None
    synced_at: Optional[dt.datetime] = None


class AdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    ad_group_id: str
    platform: str
    external_id: str
    name: str
    status: Optional[str] = None
    synced_at: Optional[dt.datetime] = None


# --- Phase 4: dashboard layouts + guarantee config ---


class DashboardWidgetIn(BaseModel):
    """One widget slot. The frontend registry owns what each type renders;
    the backend only bounds the geometry so a corrupt layout can't be saved."""

    type: str = Field(min_length=1, max_length=50)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1, le=6)


class DashboardLayoutIn(BaseModel):
    widgets: List[DashboardWidgetIn] = Field(max_length=30)

    def model_post_init(self, __context: Any) -> None:
        seen = set()
        for w in self.widgets:
            if w.type in seen:
                raise ValueError(f"duplicate widget type {w.type!r}")
            seen.add(w.type)


class GuaranteeConfigIn(BaseModel):
    """Organization-configured guarantee terms for one client. Whether a
    guarantee exists at all, and what it promises, is tenant data — see
    services/metrics.py:GUARANTEE_METRICS for what can be counted."""

    name: str = Field(min_length=1, max_length=200)
    metric: str  # tracked_leads | qualified_leads | won_deals
    target: int = Field(gt=0)
    window_days: int = Field(gt=0, le=366)
    start_date: Optional[dt.date] = None


class LandingEventIn(BaseModel):
    # Public/unauthenticated capture — cap every field so a single request can't
    # store an oversized row (the global body-size limit is the outer guard).
    client_id: str = Field(max_length=64)
    session_key: str = Field(max_length=128)
    landing_url: Optional[str] = Field(default=None, max_length=2048)
    utm_source: Optional[str] = Field(default=None, max_length=512)
    utm_medium: Optional[str] = Field(default=None, max_length=512)
    utm_campaign: Optional[str] = Field(default=None, max_length=512)
    utm_content: Optional[str] = Field(default=None, max_length=512)
    utm_term: Optional[str] = Field(default=None, max_length=512)
    referrer: Optional[str] = Field(default=None, max_length=2048)
    fbclid: Optional[str] = Field(default=None, max_length=512)
    fbp: Optional[str] = Field(default=None, max_length=512)
    gclid: Optional[str] = Field(default=None, max_length=512)
    # Additional platforms' click IDs keyed by URL param (msclkid, ttclid,
    # li_fat_id, sccid, rdt_cid, epik, …). fbclid/gclid keep dedicated fields.
    click_ids: Optional[Dict[str, str]] = None
    user_agent: Optional[str] = Field(default=None, max_length=1024)

    @field_validator("click_ids")
    @classmethod
    def _cap_click_ids(cls, v):
        if v is not None and len(v) > 30:
            raise ValueError("too many click_ids")
        return v


# --- Phase 2: managed writes, audit, creatives, Google surface ---

CHANGE_ENTITY_TYPES = {
    "campaign",
    "ad_group",
    "ad",
    "keyword",
    "campaign_negative",
    "asset_group",
}
CHANGE_ACTIONS = {"create", "update", "pause", "resume", "add", "remove"}


class ChangeCreateIn(BaseModel):
    ad_account_id: str
    entity_type: str
    action: str
    # Local row id of the entity being changed; None for creates/adds.
    entity_id: Optional[str] = None
    # For asset groups (not cached locally) the external id comes directly.
    entity_external_id: Optional[str] = None
    entity_name: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class DiffRowOut(BaseModel):
    field: str
    before: Optional[Any] = None
    after: Optional[Any] = None


class PendingChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    platform: str
    ad_account_id: str
    entity_type: str
    entity_id: Optional[str] = None
    entity_external_id: Optional[str] = None
    entity_name: Optional[str] = None
    action: str
    payload: Dict[str, Any]
    diff: List[DiffRowOut]
    status: str
    error_detail: Optional[str] = None
    expires_at: dt.datetime
    executed_at: Optional[dt.datetime] = None
    created_at: dt.datetime


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    user_email: str
    user_name: str
    platform: str
    ad_account_external_id: Optional[str] = None
    entity_type: str
    entity_external_id: Optional[str] = None
    entity_name: Optional[str] = None
    action: str
    diff: List[DiffRowOut]
    status: str
    error_detail: Optional[str] = None
    created_at: dt.datetime


class ImageUploadIn(BaseModel):
    name: str
    data_b64: str


class CreativeCreateIn(BaseModel):
    name: str
    page_id: str
    message: str
    title: Optional[str] = None
    description: Optional[str] = None
    link: str
    image_hash: Optional[str] = None
    call_to_action: Optional[str] = None  # e.g. LEARN_MORE, GET_QUOTE


class CreativeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    platform: str
    external_id: str
    name: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    thumbnail_url: Optional[str] = None


class KeywordOut(BaseModel):
    criterion_id: str
    text: str
    match_type: str
    status: Optional[str] = None
    negative: bool = False
    cpc_bid_micros: Optional[int] = None


class SearchTermOut(BaseModel):
    search_term: str
    status: str
    impressions: int
    clicks: int
    cost_micros: int
    conversions: float
    ad_group_external_id: str
    campaign_external_id: str


class AssetGroupOut(BaseModel):
    external_id: str
    name: str
    status: str
    ad_strength: Optional[str] = None
    final_urls: List[str] = Field(default_factory=list)


class LandingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    session_key: str
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    referrer: Optional[str] = None
    fbclid: Optional[str] = None
    fbp: Optional[str] = None
    gclid: Optional[str] = None
    click_ids: Optional[Dict[str, str]] = None
    occurred_at: dt.datetime
    contact_id: Optional[str] = None


# --- Phase 5: server-side conversion tracking ---

CONVERSION_PLATFORMS = platform_registry.conversion_platform_ids()
CONSENT_STATUSES = {"GRANTED", "DENIED", "UNSPECIFIED"}


class ConversionConfigIn(BaseModel):
    enabled: bool = True
    # Platform-specific; validated in the route (meta: dataset_id required;
    # google: customer_id + conversion_action_id required).
    settings: Dict[str, Any]


class ConversionConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    platform: str
    enabled: bool
    settings: Dict[str, Any]


class LeadSubmissionIn(BaseModel):
    """Public lead-capture payload — the same embed that pings
    /api/track/landing posts here on form submit. PII fields arrive raw and
    are hashed per-platform inside the senders; the browser also forwards
    its _fbc/_fbp cookies and the pixel's eventID so server and browser
    events deduplicate."""

    client_id: str = Field(max_length=64)
    session_key: str = Field(max_length=128)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=40)
    first_name: Optional[str] = Field(default=None, max_length=200)
    last_name: Optional[str] = Field(default=None, max_length=200)
    city: Optional[str] = Field(default=None, max_length=120)
    state: Optional[str] = Field(default=None, max_length=120)
    zip: Optional[str] = Field(default=None, max_length=20)
    country: Optional[str] = Field(default=None, max_length=80)
    # Dedup key shared with the browser pixel's eventID; generated
    # server-side when the page doesn't send one.
    event_id: Optional[str] = Field(default=None, max_length=200)
    event_name: str = Field(default="Lead", max_length=100)
    event_source_url: Optional[str] = Field(default=None, max_length=2048)
    fbc: Optional[str] = Field(default=None, max_length=512)
    fbp: Optional[str] = Field(default=None, max_length=512)
    fbclid: Optional[str] = Field(default=None, max_length=512)
    gclid: Optional[str] = Field(default=None, max_length=512)
    utm_source: Optional[str] = Field(default=None, max_length=512)
    utm_medium: Optional[str] = Field(default=None, max_length=512)
    utm_campaign: Optional[str] = Field(default=None, max_length=512)
    utm_content: Optional[str] = Field(default=None, max_length=512)
    utm_term: Optional[str] = Field(default=None, max_length=512)
    user_agent: Optional[str] = Field(default=None, max_length=1024)
    value_cents: Optional[int] = None
    currency: Optional[str] = Field(default=None, max_length=8)


# --- Phase 6: Salescale CRM ---

ACTIVITY_TYPES = {"note", "call", "email", "sms", "meeting"}
DEAL_STATUSES = {"open", "won", "lost"}


class StageIn(BaseModel):
    """One stage in a per-client pipeline edit. `id` present = keep/rename
    that stage (deals in it survive); absent = create a new stage."""

    id: Optional[str] = None
    name: str = Field(min_length=1, max_length=200)
    is_qualified_stage: bool = False


class StagesUpdateIn(BaseModel):
    stages: List[StageIn] = Field(min_length=1, max_length=20)


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    position: int
    is_qualified_stage: bool


# Two serializations of Contact, chosen by caller role — same pattern as
# ClientOutPublic/ClientOutTeam. The public shape is what a client-role user
# gets: their own leads' identity + qualified status, but none of the
# Organization-internal workflow state (checklist, external CRM mapping,
# raw platform linkage).
class ContactOutPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    company_name: Optional[str] = None
    source: Optional[str] = None
    qualified_at: Optional[dt.datetime] = None
    created_at: dt.datetime


class ContactOutTeam(ContactOutPublic):
    # Enrichment fields are agency workflow data (owner's direct line,
    # firmographics from the org's own data provider) — never in the
    # client-portal payload.
    mobile_phone: Optional[str] = None
    job_title: Optional[str] = None
    company_description: Optional[str] = None
    company_estimated_revenue: Optional[str] = None
    company_employee_count: Optional[int] = None
    source_external_id: Optional[str] = None
    source_detail: Optional[Dict[str, Any]] = None
    qualification: Optional[Dict[str, bool]] = None
    external_crm_id: Optional[str] = None
    # Phase 12 — team-only: verification is agency workflow, not a
    # client-portal surface.
    verification_status: str = "unverified"
    verified_at: Optional[dt.datetime] = None
    candidate_emails: Optional[List[Dict[str, Any]]] = None
    # SMS consent record (TCPA) — team-only; the gate reads the model, this
    # is display/filter surface.
    sms_opt_in: bool = False
    sms_opt_in_at: Optional[dt.datetime] = None
    sms_opt_in_source: Optional[str] = None
    # AI research field answers (key -> {"value","confidence","source_url",
    # "researched_at"}) — agency workflow data, never in the client portal.
    research: Optional[Dict[str, Any]] = None


class ContactCreateIn(BaseModel):
    client_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    job_title: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = Field(default=None, max_length=20)
    # Non-empty resolves to a get-or-created org-scoped Company; the contact's
    # company_id is set to it.
    company_name: Optional[str] = None
    # True records a manual SMS opt-in (source="manual", stamped now) — the
    # team member asserts the person consented on the org's own surface.
    sms_opt_in: Optional[bool] = None
    # Phase 14: custom field values keyed by definition key. Validated/coerced
    # at the data-access layer (services/custom_fields), not trusted as sent.
    custom_fields: Optional[Dict[str, Any]] = None


class ContactUpdateIn(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    job_title: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = Field(default=None, max_length=20)
    # Present + non-empty get-or-creates and links a Company; present + empty
    # clears company_id (detected via model_fields_set in the router).
    company_name: Optional[str] = None
    # True records a manual opt-in; False revokes it (opt_in cleared, the
    # at/source record kept for audit).
    sms_opt_in: Optional[bool] = None
    # Only the keys present are changed; a key set to null clears that value.
    custom_fields: Optional[Dict[str, Any]] = None


class ContactBulkDeleteIn(BaseModel):
    contact_ids: List[str] = Field(min_length=1, max_length=500)


class ContactBulkUpdateIn(BaseModel):
    contact_ids: List[str] = Field(min_length=1, max_length=500)
    fields: ContactUpdateIn


class ContactListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    client_id: str
    member_count: int = 0


class ContactListCreateIn(BaseModel):
    client_id: str
    name: str = Field(min_length=1, max_length=100)


class ContactListRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ContactListMembersIn(BaseModel):
    contact_ids: List[str] = Field(min_length=1, max_length=1000)


# --- Phase 14: custom field definitions ---

CUSTOM_FIELD_TYPES = (
    "text",
    "number",
    "select",
    "multi_select",
    "date",
    "boolean",
    "url",
)


class CustomFieldOptionIn(BaseModel):
    key: Optional[str] = None  # generated from label when omitted
    label: str = Field(min_length=1, max_length=100)


class CustomFieldOptionOut(BaseModel):
    key: str
    label: str


class CustomFieldDefinitionCreate(BaseModel):
    label: str = Field(min_length=1, max_length=150)
    field_type: str
    options: Optional[List[CustomFieldOptionIn]] = None
    required: bool = False
    visible_to_clients: bool = False
    entity_type: str = "contact"

    @field_validator("field_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in CUSTOM_FIELD_TYPES:
            raise ValueError(f"field_type must be one of {', '.join(CUSTOM_FIELD_TYPES)}")
        return v


class CustomFieldDefinitionUpdate(BaseModel):
    """Rename is label-only (key never changes, task 5). Options can be edited;
    when a removed option is in use, `option_remap` says how to migrate its
    stored values (old key -> new key); unmapped removed keys are kept and
    render as "(removed option)" (task 7)."""

    label: Optional[str] = Field(default=None, min_length=1, max_length=150)
    options: Optional[List[CustomFieldOptionIn]] = None
    option_remap: Optional[Dict[str, str]] = None
    required: Optional[bool] = None
    visible_to_clients: Optional[bool] = None


class CustomFieldReorderIn(BaseModel):
    # Ordered list of definition ids; index becomes sort_order.
    ids: List[str]


class CustomFieldDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    entity_type: str
    label: str
    key: str
    field_type: str
    options: Optional[List[CustomFieldOptionOut]] = None
    required: bool
    visible_to_clients: bool
    sort_order: int
    archived_at: Optional[dt.datetime] = None
    created_at: dt.datetime


# --- AI research fields ("Claygent-lite") ---


class ResearchFieldIn(BaseModel):
    key: Optional[str] = Field(default=None, max_length=60)
    label: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=2000)
    max_words: int = Field(default=40, ge=1, le=200)


class ResearchFieldPatch(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=120)
    prompt: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    max_words: Optional[int] = Field(default=None, ge=1, le=200)
    archived: Optional[bool] = None


class ResearchFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    label: str
    prompt: str
    max_words: int
    archived: bool
    created_at: dt.datetime


class ResearchRunIn(BaseModel):
    contact_ids: List[str] = Field(min_length=1, max_length=200)
    field_keys: Optional[List[str]] = None
    force: bool = False


# --- Phase 14: CSV import ---


class CsvNewField(BaseModel):
    """A field created inline during import mapping (type inferred client-side,
    confirmed by the user)."""

    column: str
    label: str = Field(min_length=1, max_length=150)
    field_type: str
    options: Optional[List[CustomFieldOptionIn]] = None

    @field_validator("field_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in CUSTOM_FIELD_TYPES:
            raise ValueError(f"field_type must be one of {', '.join(CUSTOM_FIELD_TYPES)}")
        return v


class CsvImportIn(BaseModel):
    client_id: str
    # csv column header -> target: "first_name" | "last_name" | "email" |
    # "phone" | "custom:<key>" | "new" (resolved via new_fields) | "skip".
    mapping: Dict[str, str]
    rows: List[Dict[str, Any]]
    new_fields: Optional[List[CsvNewField]] = None
    # Phase 12: queue email verification for the imported rows (background,
    # quota-checked in the pipeline).
    verify: bool = False
    # SMS module: the importer attests EVERY row in this file opted in to SMS
    # on the Organization's own website — each created contact gets the TCPA
    # consent record (source="csv_import:website_attested"). A per-column
    # "sms_opt_in" mapping (truthy values) handles mixed files instead.
    sms_opt_in_all: bool = False


class VerifyContactsIn(BaseModel):
    """Phase 12: bulk email verification of a manually selected contact set."""

    contact_ids: List[str] = Field(min_length=1, max_length=500)


class QualificationIn(BaseModel):
    """The one status change that fans out everywhere. With Organization
    criteria configured, `checklist` drives qualified (all criteria true);
    without criteria, `qualified` toggles directly."""

    checklist: Optional[Dict[str, bool]] = None
    qualified: Optional[bool] = None


class DealCreateIn(BaseModel):
    client_id: str
    contact_id: str
    name: Optional[str] = Field(default=None, max_length=300)
    value_cents: Optional[int] = Field(default=None, ge=0)
    stage_id: Optional[str] = None  # default: first stage of the pipeline


class DealUpdateIn(BaseModel):
    stage_id: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    value_cents: Optional[int] = Field(default=None, ge=0)
    status: Optional[str] = None  # open | won | lost


class DealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    contact_id: str
    pipeline_id: str
    stage_id: str
    name: str
    value_cents: Optional[int] = None
    status: str
    created_at: dt.datetime
    closed_at: Optional[dt.datetime] = None


class ActivityCreateIn(BaseModel):
    contact_id: str
    type: str  # note | call | email | sms | meeting
    body: Optional[str] = Field(default=None, max_length=20000)
    is_internal: bool = False
    occurred_at: Optional[dt.datetime] = None


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contact_id: Optional[str] = None
    deal_id: Optional[str] = None
    type: str
    body: Optional[str] = None
    is_internal: bool
    occurred_at: dt.datetime
    created_by_user_id: Optional[str] = None


class CrmTaskCreateIn(BaseModel):
    client_id: str
    contact_id: Optional[str] = None
    deal_id: Optional[str] = None
    title: str = Field(min_length=1, max_length=300)
    due_at: Optional[dt.datetime] = None
    assigned_to_user_id: Optional[str] = None


class CrmTaskUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    due_at: Optional[dt.datetime] = None
    assigned_to_user_id: Optional[str] = None
    completed: Optional[bool] = None


class CrmTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    contact_id: Optional[str] = None
    deal_id: Optional[str] = None
    title: str
    due_at: Optional[dt.datetime] = None
    completed_at: Optional[dt.datetime] = None
    assigned_to_user_id: Optional[str] = None
    created_at: dt.datetime


class QualifiedLeadCriterionIn(BaseModel):
    key: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=300)


class QualifiedLeadCriteriaIn(BaseModel):
    """The Organization's structured qualified-lead checklist. Empty list =
    no checklist (simple qualified yes/no)."""

    criteria: List[QualifiedLeadCriterionIn] = Field(max_length=20)

    def model_post_init(self, __context: Any) -> None:
        keys = [c.key for c in self.criteria]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate criterion keys")


class ExternalSyncConfigIn(BaseModel):
    enabled: bool = True
    url: str = Field(min_length=1, max_length=2000)
    secret: str = Field(min_length=8, max_length=200)


class LeadFormConfigIn(BaseModel):
    """Per-client native lead-form routing: meta → the Page ID whose leadgen
    webhooks belong to this client; google → the google_key set on the form
    in Google Ads."""

    external_key: str = Field(min_length=1, max_length=200)
    enabled: bool = True


class LeadFormEnabledIn(BaseModel):
    """Toggle-only body for the generic landing-page webhook, whose key is
    server-generated (never client-supplied) — see /lead-forms/landing-page."""

    enabled: bool


class LeadFormConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    platform: str
    external_key: str
    enabled: bool


class ConversionDispatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversion_event_id: str
    platform: str
    status: str
    match_keys: Optional[List[str]] = None
    detail: Optional[str] = None
    is_test: bool
    attempted_at: dt.datetime


class ConversionLogEntryOut(BaseModel):
    """Dispatch log joined with its event for the team-facing log view."""

    dispatch: ConversionDispatchOut
    event_name: str
    event_id: str
    contact_id: Optional[str] = None
    occurred_at: dt.datetime


class TestSendIn(BaseModel):
    client_id: str
    platform: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    fbc: Optional[str] = None
    fbp: Optional[str] = None
    gclid: Optional[str] = None
    event_name: str = "Lead"


# --- Phase 9: white-labeling ---


class BrandingIn(BaseModel):
    """Organization branding config. All optional — anything unset falls
    back to the neutral default (services/branding.py). Color keys are
    validated against BRAND_COLOR_KEYS in the route."""

    product_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    logo_url: Optional[str] = Field(default=None, max_length=2000)
    favicon_url: Optional[str] = Field(default=None, max_length=2000)
    colors: Dict[str, str] = Field(default_factory=dict)
    email_from_name: Optional[str] = Field(default=None, max_length=200)
    email_from_address: Optional[EmailStr] = None
    # Postal address for the cold-email CAN-SPAM footer (Outreach module).
    mailing_address: Optional[str] = Field(default=None, max_length=500)
    apply_to_team: bool = False

    @field_validator("logo_url", "favicon_url")
    @classmethod
    def _safe_url(cls, v):
        # These land in client-side <img src>/<link href>; only allow http(s)
        # so a javascript:/data: scheme can't be introduced when rendered.
        if v and not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class CustomDomainIn(BaseModel):
    domain: str = Field(min_length=4, max_length=253)


# --- Phase 9: AI insights ---


class AiExplainIn(BaseModel):
    client_id: str
    metric: str  # services/ai_insights.py EXPLAINABLE_METRICS
    question: Optional[str] = Field(default=None, max_length=1000)
    since: Optional[dt.date] = None
    until: Optional[dt.date] = None
    platforms: Optional[str] = None  # same ?platforms= grammar as /api/metrics


class AiSummaryIn(BaseModel):
    client_id: str
    since: Optional[dt.date] = None
    until: Optional[dt.date] = None
    platforms: Optional[str] = None


# --- Cold-email Outreach module (Phase 1 foundation) ---


class EmailAccountIn(BaseModel):
    """Connect a sending mailbox. SMTP and IMAP each have their OWN username +
    password — send and receive are frequently different providers (e.g.
    Amazon SES SMTP credentials to send, a real IMAP mailbox to receive
    replies). Both passwords are write-only, encrypted at rest, never
    serialized back."""

    name: str = Field(min_length=1, max_length=200)
    from_name: str = Field(min_length=1, max_length=200)
    from_email: EmailStr
    smtp_host: str = Field(min_length=1, max_length=255)
    smtp_port: int = Field(ge=1, le=65535)
    smtp_security: str = Field(default="ssl")
    smtp_username: str = Field(min_length=1, max_length=320)
    smtp_password: str = Field(min_length=1, max_length=1000)
    imap_host: str = Field(min_length=1, max_length=255)
    imap_port: int = Field(ge=1, le=65535)
    imap_security: str = Field(default="ssl")
    imap_username: str = Field(min_length=1, max_length=320)
    imap_password: str = Field(min_length=1, max_length=1000)
    daily_send_cap: int = Field(default=100, ge=1, le=10000)
    signature: Optional[str] = Field(default=None, max_length=5000)

    @field_validator("smtp_security", "imap_security")
    @classmethod
    def _valid_security(cls, v):
        if v not in ("ssl", "starttls"):
            raise ValueError("security must be 'ssl' or 'starttls'")
        return v


class EmailAccountPatch(BaseModel):
    """Partial update. Any of host/port/username/password changing re-probes
    that leg (SMTP and IMAP probe independently) before persisting."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    from_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    smtp_host: Optional[str] = Field(default=None, min_length=1, max_length=255)
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_security: Optional[str] = None
    smtp_username: Optional[str] = Field(default=None, min_length=1, max_length=320)
    smtp_password: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    imap_host: Optional[str] = Field(default=None, min_length=1, max_length=255)
    imap_port: Optional[int] = Field(default=None, ge=1, le=65535)
    imap_security: Optional[str] = None
    imap_username: Optional[str] = Field(default=None, min_length=1, max_length=320)
    imap_password: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    daily_send_cap: Optional[int] = Field(default=None, ge=1, le=10000)
    signature: Optional[str] = Field(default=None, max_length=5000)
    # Warmup controls. Turning warmup on stamps warmup_started_at server-side
    # (the ramp clock); toggling off and back on restarts the ramp.
    warmup_enabled: Optional[bool] = None
    warmup_target_daily: Optional[int] = Field(default=None, ge=10, le=500)
    # IANA zone the warmup window/weekends/daily budget follow (None = UTC).
    warmup_timezone: Optional[str] = Field(default=None, max_length=64)

    @field_validator("smtp_security", "imap_security")
    @classmethod
    def _valid_security(cls, v):
        if v is not None and v not in ("ssl", "starttls"):
            raise ValueError("security must be 'ssl' or 'starttls'")
        return v

    @field_validator("warmup_timezone")
    @classmethod
    def _valid_warmup_timezone(cls, v):
        if v is not None:
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(v)
            except Exception:
                raise ValueError(f"unknown IANA timezone: {v!r}")
        return v


class EmailComposeIn(BaseModel):
    account_id: str
    contact_id: str
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100000)


class EmailReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=100000)


class EmailSuppressionIn(BaseModel):
    emails: List[EmailStr] = Field(min_length=1, max_length=1000)


# --- cold-email campaigns (Phase 2) ---------------------------------------


def _valid_send_days(v):
    if v is None:
        return v
    if not isinstance(v, list) or any(
        not isinstance(d, int) or d < 0 or d > 6 for d in v
    ):
        raise ValueError("send_days must be a list of ints 0..6 (0=Monday)")
    return sorted(set(v))


def _valid_campaign_timezone(v):
    """Normalize a campaign send-window timezone to a canonical IANA name,
    mapping common abbreviations (MST/EST/…). Rejects anything zoneinfo can't
    resolve — a save that stored an unloadable zone used to silently fall back
    to UTC and send at the wrong hour. None passes through (PATCH: unchanged)."""
    if v is None:
        return v
    from .services import timezones

    canonical = timezones.normalize(v)
    if canonical is None:
        raise ValueError(
            f"unknown timezone {v!r} — use an IANA name like 'America/Phoenix'"
        )
    return canonical


class EmailCampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Sending pool: either the legacy single account_id or account_ids (1-10,
    # rotation order). At least one of the two must be given; account_ids
    # wins when both are.
    account_id: Optional[str] = None
    account_ids: Optional[List[str]] = Field(default=None, min_length=1, max_length=10)
    # None = inherit the org's default timezone (→ "UTC"), resolved in create.
    timezone: Optional[str] = Field(default=None, max_length=64)
    send_window_start: int = Field(default=8, ge=0, le=23)
    send_window_end: int = Field(default=17, ge=1, le=24)
    send_days: Optional[List[int]] = Field(default=None)
    daily_cap: int = Field(default=50, ge=1, le=100000)
    open_tracking: bool = True
    exit_on_reply: bool = True
    # QA gate (Feature B): when true, only enrollments explicitly approved
    # via the audience preview/QA table are actually sent.
    require_approval: bool = False
    # AI writing controls (Feature C), threaded into generate_ai_snippet.
    ai_tone: Optional[str] = Field(default=None, max_length=200)
    ai_example: Optional[str] = Field(default=None, max_length=10000)

    @field_validator("send_days")
    @classmethod
    def _days(cls, v):
        return _valid_send_days(v)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return _valid_campaign_timezone(v)

    @model_validator(mode="after")
    def _pool(self):
        if not self.account_id and not self.account_ids:
            raise ValueError("account_id or account_ids is required")
        if self.account_ids is not None:
            deduped = list(dict.fromkeys(self.account_ids))
            if not deduped:
                raise ValueError("account_ids must name at least one mailbox")
            self.account_ids = deduped
        return self


class EmailCampaignPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    account_id: Optional[str] = None
    account_ids: Optional[List[str]] = Field(default=None, min_length=1, max_length=10)
    timezone: Optional[str] = Field(default=None, max_length=64)
    send_window_start: Optional[int] = Field(default=None, ge=0, le=23)
    send_window_end: Optional[int] = Field(default=None, ge=1, le=24)
    send_days: Optional[List[int]] = Field(default=None)
    daily_cap: Optional[int] = Field(default=None, ge=1, le=100000)
    open_tracking: Optional[bool] = None
    exit_on_reply: Optional[bool] = None
    require_approval: Optional[bool] = None
    ai_tone: Optional[str] = Field(default=None, max_length=200)
    ai_example: Optional[str] = Field(default=None, max_length=10000)

    @field_validator("send_days")
    @classmethod
    def _days(cls, v):
        return _valid_send_days(v)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return _valid_campaign_timezone(v)

    @field_validator("account_ids")
    @classmethod
    def _dedupe_pool(cls, v):
        if v is None:
            return v
        deduped = list(dict.fromkeys(v))
        if not deduped:
            raise ValueError("account_ids must name at least one mailbox")
        return deduped


class EmailStepIn(BaseModel):
    # id present = update that existing step in place (keeps the step's
    # identity stable so enrollment ai_snippet caches survive edits); absent =
    # create. Steps whose ids are missing from the payload are deleted.
    id: Optional[str] = None
    position: int = Field(ge=1)
    wait_days: int = Field(default=0, ge=0, le=365)
    subject: Optional[str] = Field(default=None, max_length=500)
    body: Optional[str] = Field(default=None, max_length=100000)
    ai_instructions: Optional[str] = Field(default=None, max_length=5000)


class EmailStepsIn(BaseModel):
    steps: List[EmailStepIn] = Field(min_length=1, max_length=50)


class EmailEnrollIn(BaseModel):
    # Exactly one of contact_ids / list_id must be provided (validated below).
    contact_ids: Optional[List[str]] = Field(default=None, max_length=1000)
    list_id: Optional[str] = None

    @model_validator(mode="after")
    def _one_of(self):
        if bool(self.contact_ids) == bool(self.list_id):
            raise ValueError("Provide exactly one of contact_ids or list_id")
        return self


class EmailPreviewIn(BaseModel):
    contact_id: str
    position: int = Field(ge=1)


# --- audience preview + QA (Feature B) --------------------------------------


class EmailPreviewBatchIn(BaseModel):
    position: int = Field(default=1, ge=1)
    limit: int = Field(default=25, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class EnrollmentOverrideIn(BaseModel):
    position: int = Field(ge=1)
    subject: Optional[str] = Field(default=None, max_length=500)
    body: str = Field(min_length=1, max_length=100000)


class CampaignQaIn(BaseModel):
    enrollment_ids: List[str] = Field(min_length=1, max_length=1000)
    action: str

    @field_validator("action")
    @classmethod
    def _known_action(cls, v: str) -> str:
        if v not in ("approve", "unapprove", "exclude"):
            raise ValueError("action must be one of approve, unapprove, exclude")
        return v


# --- org outreach context (Feature C) ---------------------------------------


class OrgOutreachContextIn(BaseModel):
    company_description: Optional[str] = Field(default=None, max_length=2000)
    icp: Optional[str] = Field(default=None, max_length=2000)
    offer: Optional[str] = Field(default=None, max_length=2000)
    tone_guide: Optional[str] = Field(default=None, max_length=2000)


class OrgLeadNotificationsIn(BaseModel):
    """Text-the-team alerts on new leads. Phones are raw strings — the
    endpoint normalizes to E.164 and rejects anything unparseable.
    message_template uses {{token}} placeholders (services/lead_notify.
    KNOWN_TOKENS); blank/omitted resets to the built-in default."""

    enabled: bool
    phones: List[str] = Field(default_factory=list)
    message_template: Optional[str] = Field(default=None, max_length=1000)


class ClientLeadNotificationsIn(BaseModel):
    """Per-client counterpart — e.g. the client's own business owner, texted
    alongside (or instead of) the agency's own ops numbers. Same shape and
    validation as OrgLeadNotificationsIn, stored in
    client.metric_settings["lead_notifications"] (mirrors external_sync)."""

    enabled: bool
    phones: List[str] = Field(default_factory=list)


# --- SMS campaigns (campaign engine) ---------------------------------------


class SmsCampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account_id: str
    client_id: Optional[str] = None
    # None = inherit (client tz → org tz → "America/New_York"), resolved in the
    # create endpoint. An explicit value still wins.
    timezone: Optional[str] = Field(default=None, max_length=64)
    send_window_start: int = Field(default=11, ge=0, le=23)
    send_window_end: int = Field(default=20, ge=1, le=24)
    send_days: Optional[List[int]] = Field(default=None)
    daily_cap: int = Field(default=100, ge=1, le=100000)
    exit_on_reply: bool = True
    include_compliance_footer: bool = True
    auto_enroll_new_leads: bool = False

    @field_validator("send_days")
    @classmethod
    def _days(cls, v):
        return _valid_send_days(v)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return _valid_campaign_timezone(v)


class SmsCampaignPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    account_id: Optional[str] = None
    client_id: Optional[str] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    send_window_start: Optional[int] = Field(default=None, ge=0, le=23)
    send_window_end: Optional[int] = Field(default=None, ge=1, le=24)
    send_days: Optional[List[int]] = Field(default=None)
    daily_cap: Optional[int] = Field(default=None, ge=1, le=100000)
    exit_on_reply: Optional[bool] = None
    include_compliance_footer: Optional[bool] = None
    auto_enroll_new_leads: Optional[bool] = None

    @field_validator("send_days")
    @classmethod
    def _days(cls, v):
        return _valid_send_days(v)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return _valid_campaign_timezone(v)


class SmsStepIn(BaseModel):
    # id present = update that existing step in place; absent = create.
    # Steps whose ids are missing from the payload are deleted (upsert-in-
    # place, mirrors EmailStepIn — same two-pass negative-position parking
    # on the API side keeps the unique(campaign,position) constraint happy).
    id: Optional[str] = None
    position: int = Field(ge=1)
    wait_days: int = Field(default=0, ge=0, le=365)
    body: str = Field(min_length=1, max_length=1600)
    ai_instructions: Optional[str] = Field(default=None, max_length=5000)


class SmsStepsIn(BaseModel):
    steps: List[SmsStepIn] = Field(min_length=1, max_length=50)


class SmsEnrollIn(BaseModel):
    """Either an explicit contact list, or a client_id to enroll every
    opted-in contact under that client (the house CRM client qualifies —
    it's just another org-scoped client_id, no special-casing)."""

    contact_ids: Optional[List[str]] = Field(default=None, max_length=1000)
    client_id: Optional[str] = None
    list_id: Optional[str] = None

    @field_validator("contact_ids")
    @classmethod
    def _nonempty(cls, v):
        if v is not None and len(v) == 0:
            raise ValueError("contact_ids must be non-empty when provided")
        return v


class SmsPreviewIn(BaseModel):
    contact_id: str
    position: int = Field(ge=1)


class SmsComposeIn(BaseModel):
    account_id: str
    contact_id: str
    body: str = Field(min_length=1, max_length=1600)


class SmsMarkReadIn(BaseModel):
    contact_id: str
