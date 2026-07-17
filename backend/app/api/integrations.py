"""Per-Organization platform API credentials (bring-your-own app).

Owner/Admin enter their own Meta app + Google Ads OAuth client/developer token
here; the connect flows then use them. Secrets are write-only (encrypted at
rest, never returned) — reads report only whether a provider is configured and
by whom (this org's own credentials vs the operator's global fallback).
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import platforms as platform_registry
from ..config import get_settings
from ..db import get_db
from ..deps import require_admin, require_owner
from ..models.base import utcnow
from ..models.core import Organization, User
from ..models.integrations import IntegrationCredential
from ..security import encrypt_secret
from ..services import ai_provider, integration_creds

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class MetaCredentialsIn(BaseModel):
    app_id: str
    app_secret: str


class GoogleCredentialsIn(BaseModel):
    client_id: str
    client_secret: str
    developer_token: str
    login_customer_id: Optional[str] = None


class IntegrationStatusOut(BaseModel):
    provider: str
    configured: bool
    source: str  # organization | global | none
    public_id: Optional[str] = None  # non-secret identifier when org-configured


def _status(db: Session, org_id: str, provider: str) -> IntegrationStatusOut:
    row = db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.organization_id == org_id,
            IntegrationCredential.provider == provider,
        )
    ).scalar_one_or_none()
    if row and row.public_id and row.secret_encrypted:
        return IntegrationStatusOut(
            provider=provider, configured=True, source="organization", public_id=row.public_id
        )
    resolved = (
        integration_creds.resolve_meta(db, org_id)
        if provider == "meta"
        else integration_creds.resolve_google(db, org_id)
    )
    if resolved.configured:
        return IntegrationStatusOut(provider=provider, configured=True, source="global")
    return IntegrationStatusOut(provider=provider, configured=False, source="none")


def _upsert(db: Session, org_id: str, provider: str, **fields) -> None:
    row = db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.organization_id == org_id,
            IntegrationCredential.provider == provider,
        )
    ).scalar_one_or_none()
    if row is None:
        row = IntegrationCredential(organization_id=org_id, provider=provider)
        db.add(row)
    for k, v in fields.items():
        setattr(row, k, v)
    row.updated_at = utcnow()
    db.commit()


class RedirectUriOut(BaseModel):
    provider: str  # google | meta
    purpose: str  # connect | signin
    uri: str


@router.get("/redirect-uris", response_model=List[RedirectUriOut])
def list_redirect_uris(user: User = Depends(require_admin)):
    """The exact OAuth redirect URIs this deployment sends, so an operator can
    register them on their Google/Meta app verbatim. A mismatch is the classic
    `redirect_uri_mismatch` / "URL blocked" failure: the ad-connect and
    sign-in flows use DIFFERENT callback paths on the same OAuth app, and
    both must be registered."""
    s = get_settings()
    return [
        RedirectUriOut(provider="google", purpose="connect", uri=s.google_redirect_uri),
        RedirectUriOut(
            provider="google",
            purpose="signin",
            uri=f"{s.api_base_url}/api/auth/oauth/google/callback",
        ),
        RedirectUriOut(provider="meta", purpose="connect", uri=s.meta_redirect_uri),
        RedirectUriOut(
            provider="meta",
            purpose="signin",
            uri=f"{s.api_base_url}/api/auth/oauth/meta/callback",
        ),
    ]


AI_KEY_PROVIDERS = ("anthropic", "openai", "gemini")


class AiProviderSelectionIn(BaseModel):
    provider: str
    # None → use the provider's recommended default model (SELECTABLE_MODELS[p][0]).
    model: Optional[str] = None


def _ai_provider_status(db: Session, org: Organization) -> dict:
    """Active provider/model (org override → operator default) + this org's
    BYO-key status per provider + the selectable-model menu for the UI."""
    providers = []
    for p in AI_KEY_PROVIDERS:
        src = integration_creds.key_source(db, org.id, p)
        providers.append({"provider": p, "configured": src != "none", "source": src})
    return {
        "active": ai_provider.active_provider(org),
        "model": ai_provider.active_model(org),
        # True when THIS org picked the provider, vs inheriting the operator default.
        "org_selected": org.ai_provider in ai_provider.PROVIDERS,
        "available": {p: list(m) for p, m in ai_provider.SELECTABLE_MODELS.items()},
        "providers": providers,
    }


@router.get("/ai-provider")
def ai_provider_status(
    user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    org = db.get(Organization, user.organization_id)
    return _ai_provider_status(db, org)


@router.put("/ai-provider")
def set_ai_provider(
    body: AiProviderSelectionIn,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Owner-only: choose this org's active AI provider and model (overrides
    the operator default). A model of None resets to the provider's default.
    An explicit model applies to both insights and outreach for this org."""
    provider = body.provider.strip().lower()
    if provider not in ai_provider.PROVIDERS:
        raise HTTPException(400, f"Unknown provider '{body.provider}'")
    model = (body.model or "").strip() or None
    if model is not None and not ai_provider.is_selectable_model(provider, model):
        raise HTTPException(400, f"Model '{model}' is not available for {provider}")
    org = db.get(Organization, user.organization_id)
    org.ai_provider = provider
    org.ai_model = model
    db.commit()
    db.refresh(org)
    return _ai_provider_status(db, org)


@router.get("", response_model=List[IntegrationStatusOut])
def list_integrations(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [
        _status(db, user.organization_id, p)
        for p in platform_registry.byo_creds_platform_ids()
    ]


@router.put("/meta", response_model=IntegrationStatusOut)
def set_meta(
    body: MetaCredentialsIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _upsert(
        db,
        user.organization_id,
        "meta",
        public_id=body.app_id.strip(),
        secret_encrypted=encrypt_secret(body.app_secret.strip()),
        secret2_encrypted=None,
        login_customer_id=None,
    )
    return _status(db, user.organization_id, "meta")


@router.put("/google", response_model=IntegrationStatusOut)
def set_google(
    body: GoogleCredentialsIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _upsert(
        db,
        user.organization_id,
        "google",
        public_id=body.client_id.strip(),
        secret_encrypted=encrypt_secret(body.client_secret.strip()),
        secret2_encrypted=encrypt_secret(body.developer_token.strip()),
        login_customer_id=(body.login_customer_id or "").strip() or None,
    )
    return _status(db, user.organization_id, "google")


@router.delete("/{provider}", response_model=IntegrationStatusOut)
def delete_integration(
    provider: str, user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    row = db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.organization_id == user.organization_id,
            IntegrationCredential.provider == provider,
        )
    ).scalar_one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()
    return _status(db, user.organization_id, provider)
