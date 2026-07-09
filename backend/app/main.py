import logging

from fastapi import FastAPI
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

app = FastAPI(title="Salescale")

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

app.include_router(auth.router)
app.include_router(social_auth.router)
app.include_router(mfa.router)
app.include_router(orgs.router)
app.include_router(admin.router)
app.include_router(billing.router)
app.include_router(integrations.router)
app.include_router(platforms.router)
app.include_router(clients.router)
app.include_router(connect_meta.router)
app.include_router(connect_google.router)
app.include_router(browser.router)
app.include_router(manage.router)
app.include_router(attribution.router)
app.include_router(leads.router)
app.include_router(conversions.router)
app.include_router(metrics.router)
app.include_router(dashboard.router)
app.include_router(crm.router)
app.include_router(lead_webhooks.router)
app.include_router(branding.router)
app.include_router(ai.router)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.on_event("startup")
def _migrate():
    # Bring any database (fresh or existing) up to the current schema via
    # Alembic — the single source of truth for schema, in dev and prod alike.
    upgrade_to_head()
