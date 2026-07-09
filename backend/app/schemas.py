import datetime as dt
from typing import Annotated, Any, Dict, List, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

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
    created_at: dt.datetime


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
    name: str
    internal_notes: Optional[str] = None


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
    created_at: dt.datetime


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
    client_id: str
    session_key: str
    landing_url: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    referrer: Optional[str] = None
    fbclid: Optional[str] = None
    fbp: Optional[str] = None
    gclid: Optional[str] = None
    # Additional platforms' click IDs keyed by URL param (msclkid, ttclid,
    # li_fat_id, sccid, rdt_cid, epik, …). fbclid/gclid keep dedicated fields.
    click_ids: Optional[Dict[str, str]] = None
    user_agent: Optional[str] = None


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

    client_id: str
    session_key: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None
    # Dedup key shared with the browser pixel's eventID; generated
    # server-side when the page doesn't send one.
    event_id: Optional[str] = None
    event_name: str = "Lead"
    event_source_url: Optional[str] = None
    fbc: Optional[str] = None
    fbp: Optional[str] = None
    fbclid: Optional[str] = None
    gclid: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    user_agent: Optional[str] = None
    value_cents: Optional[int] = None
    currency: Optional[str] = None


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
    source: Optional[str] = None
    qualified_at: Optional[dt.datetime] = None
    created_at: dt.datetime


class ContactOutTeam(ContactOutPublic):
    source_external_id: Optional[str] = None
    source_detail: Optional[Dict[str, Any]] = None
    qualification: Optional[Dict[str, bool]] = None
    external_crm_id: Optional[str] = None


class ContactCreateIn(BaseModel):
    client_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None


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
    body: Optional[str] = None
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
    apply_to_team: bool = False


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
