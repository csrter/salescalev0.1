import datetime as dt
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    organization_id: str
    organization_name: str
    client_id: Optional[str] = None
    full_name: str


class OrgSignupRequest(BaseModel):
    """Self-serve Organization signup — the same generic flow every tenant
    (including Atlas Reach) is created through."""

    organization_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=200)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: dt.datetime


class TeamMemberCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=200)
    role: str  # admin | member (owner is only created via signup)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    role: str
    client_id: Optional[str] = None
    is_active: bool
    created_at: dt.datetime


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
    gclid: Optional[str] = None
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
    gclid: Optional[str] = None
    occurred_at: dt.datetime
    contact_id: Optional[str] = None
