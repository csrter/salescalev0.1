"""Resolve platform API credentials per Organization (bring-your-own), with a
fallback to the operator's global env credentials.

To avoid threading a credentials object through every platform-API function,
the resolved credentials for the current request/job are stashed in
contextvars. A dependency (or a background job) calls `bind()` once; the
service layer reads `current_meta()` / `current_google()`. Contextvars
propagate into FastAPI's sync threadpool, so this works for sync endpoints.
"""
import contextvars
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.integrations import IntegrationCredential
from ..security import decrypt_secret


@dataclass
class MetaCreds:
    app_id: str
    app_secret: str

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)


@dataclass
class GoogleCreds:
    client_id: str
    client_secret: str
    developer_token: str
    login_customer_id: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.developer_token)


def _row(db: Session, org_id: str, provider: str):
    return db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.organization_id == org_id,
            IntegrationCredential.provider == provider,
        )
    ).scalar_one_or_none()


def _meta_from_settings() -> MetaCreds:
    s = get_settings()
    return MetaCreds(s.meta_app_id, s.meta_app_secret)


def _google_from_settings() -> GoogleCreds:
    s = get_settings()
    return GoogleCreds(
        s.google_client_id,
        s.google_client_secret,
        s.google_developer_token,
        s.google_login_customer_id,
    )


def resolve_meta(db: Session, org_id: str) -> MetaCreds:
    row = _row(db, org_id, "meta")
    if row and row.public_id and row.secret_encrypted:
        return MetaCreds(row.public_id, decrypt_secret(row.secret_encrypted))
    return _meta_from_settings()  # operator's shared app, if any


def resolve_google(db: Session, org_id: str) -> GoogleCreds:
    row = _row(db, org_id, "google")
    if row and row.public_id and row.secret_encrypted:
        return GoogleCreds(
            row.public_id,
            decrypt_secret(row.secret_encrypted),
            decrypt_secret(row.secret2_encrypted) if row.secret2_encrypted else "",
            row.login_customer_id or "",
        )
    return _google_from_settings()


# --- Phase 12: simple single-key providers (BYO-first, operator fallback) ---

# provider id -> Settings attr holding the operator's global fallback key
# ("" = no fallback: the org must connect its own key, e.g. Hunter, whose API
# terms prohibit multi-tenant use of one shared key).
KEY_PROVIDERS: dict[str, str] = {
    "google_places": "google_places_api_key",
    "zerobounce": "zerobounce_api_key",
    "hunter": "",
    # People-data / firmographics profile provider (owner contact + revenue).
    # BYO only, like Hunter — people-data ToS prohibit a shared operator key.
    "apollo": "",
    # AI providers (grounded insights + cold-email personalization). The active
    # one is chosen operator-side (settings.ai_provider); these let an org bring
    # its own key for whichever provider is active, resolved BYO-first with the
    # operator's env key as fallback. See services/ai_provider.
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "gemini": "gemini_api_key",
}


def resolve_key(db: Session, org_id: str, provider: str) -> str:
    """The org's own key for a single-key provider, else the operator's
    global fallback (when that provider allows one), else ""."""
    row = _row(db, org_id, provider)
    if row and row.secret_encrypted:
        return decrypt_secret(row.secret_encrypted)
    settings_attr = KEY_PROVIDERS.get(provider) or ""
    return getattr(get_settings(), settings_attr, "") if settings_attr else ""


def key_source(db: Session, org_id: str, provider: str) -> str:
    """organization | global | none — for the settings UI, never the key."""
    row = _row(db, org_id, provider)
    if row and row.secret_encrypted:
        return "organization"
    settings_attr = KEY_PROVIDERS.get(provider) or ""
    if settings_attr and getattr(get_settings(), settings_attr, ""):
        return "global"
    return "none"


# --- request/job-scoped current credentials ---

_meta_var: contextvars.ContextVar = contextvars.ContextVar("meta_creds", default=None)
_google_var: contextvars.ContextVar = contextvars.ContextVar("google_creds", default=None)


def bind(db: Session, org_id: str) -> None:
    """Resolve and stash this Organization's credentials for the current
    request/job context."""
    _meta_var.set(resolve_meta(db, org_id))
    _google_var.set(resolve_google(db, org_id))


def current_meta() -> MetaCreds:
    return _meta_var.get() or _meta_from_settings()


def current_google() -> GoogleCreds:
    return _google_var.get() or _google_from_settings()
