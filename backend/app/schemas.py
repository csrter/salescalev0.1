import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    client_id: Optional[str] = None
    full_name: str


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
