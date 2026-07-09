import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Quiet down noisy startup chatter from tooling.
logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)

from .api import (
    admin,
    ai,
    attribution,
    auth,
    billing,
    branding,
    browser,
    clients,
    connect_google,
    connect_meta,
    conversions,
    crm,
    dashboard,
    integrations,
    lead_webhooks,
    leads,
    manage,
    metrics,
    mfa,
    orgs,
    platforms,
    social_auth,
)
from .config import get_settings
from .deps import mfa_gate
from .migrations import upgrade_to_head

_settings = get_settings()

# Error tracking — a no-op until a Sentry DSN is configured. Imported lazily so
# sentry-sdk is only needed where it's actually turned on (the hosted deploy).
if _settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(
        dsn=_settings.sentry_dsn,
        environment=_settings.sentry_environment,
        traces_sample_rate=_settings.sentry_traces_sample_rate,
    )

# Interactive docs (/docs, /redoc, /openapi.json) disclose the full API surface,
# so serve them only in local/dev (sqlite) or the desktop app — never on a
# hosted Postgres deployment.
_docs_enabled = _settings.desktop_mode or _settings.database_url.startswith("sqlite")
app = FastAPI(
    title="Salescale",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# A production deployment (real Postgres DB, not the desktop app) must not run
# on the built-in dev JWT secret — sessions would be forgeable. Fail closed
# there; only warn for local sqlite / desktop dev.
if _settings.jwt_secret == "dev-only-secret-change-me":
    _prod_like = not _settings.desktop_mode and not _settings.database_url.startswith(
        "sqlite"
    )
    _secret_msg = (
        "JWT_SECRET is the built-in dev default — sessions are forgeable. "
        'Generate one: python3 -c "import secrets; print(secrets.token_urlsafe(48))"'
    )
    if _prod_like:
        raise RuntimeError(_secret_msg)
    logging.getLogger("salescale").warning(_secret_msg)

# Per-request access logging is provided by uvicorn's own access logger
# (`GET /path -> 200`), so we don't add a duplicate middleware. Sentry (above)
# captures exceptions with request context.

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _settings.desktop_mode else _settings.frontend_origins(),
    # Bearer-token auth carries no cookies, so wildcard origins are safe; the
    # two flags are also mutually exclusive under the CORS spec.
    allow_credentials=not _settings.desktop_mode,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers left OPEN so a 2FA-gated user can still authenticate, enroll, and
# manage their session/policy: auth, social_auth, mfa, orgs, admin (super-admin
# only), platforms (discovery), and the public ingest/branding routers.
app.include_router(auth.router)
app.include_router(social_auth.router)
app.include_router(mfa.router)
app.include_router(orgs.router)
app.include_router(admin.router)
app.include_router(platforms.router)
app.include_router(attribution.router)
app.include_router(leads.router)
app.include_router(lead_webhooks.router)
app.include_router(branding.router)

# App-data routers — hard-gated by the org 2FA policy (mfa_gate is a no-op for
# unauthenticated requests, so their public webhooks still work).
_MFA = [Depends(mfa_gate)]
app.include_router(billing.router, dependencies=_MFA)
app.include_router(integrations.router, dependencies=_MFA)
app.include_router(clients.router, dependencies=_MFA)
app.include_router(connect_meta.router, dependencies=_MFA)
app.include_router(connect_google.router, dependencies=_MFA)
app.include_router(browser.router, dependencies=_MFA)
app.include_router(manage.router, dependencies=_MFA)
app.include_router(conversions.router, dependencies=_MFA)
app.include_router(metrics.router, dependencies=_MFA)
app.include_router(dashboard.router, dependencies=_MFA)
app.include_router(crm.router, dependencies=_MFA)
app.include_router(ai.router, dependencies=_MFA)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.on_event("startup")
def _migrate():
    # Bring any database (fresh or existing) up to the current schema via
    # Alembic — the single source of truth for schema, in dev and prod alike.
    upgrade_to_head()
