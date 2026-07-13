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
    connect_accounts,
    connect_google,
    connect_meta,
    conversions,
    crm,
    custom_fields,
    dashboard,
    email_outreach,
    email_outreach_public,
    imessage_webhooks,
    integrations,
    lead_finder,
    lead_webhooks,
    leads,
    manage,
    metrics,
    mfa,
    orgs,
    outreach,
    outreach_webhooks,
    platforms,
    sms_outreach,
    sms_webhooks,
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

# Reject oversized bodies up-front (defense-in-depth vs. huge-payload DoS on the
# public capture endpoints + any JSON-dict field). Generous for this API.
_MAX_BODY_BYTES = 512 * 1024


@app.middleware("http")
async def _limit_body_size(request, call_next):
    from fastapi.responses import JSONResponse

    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > _MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)

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
app.include_router(outreach_webhooks.router)
app.include_router(email_outreach_public.router)
app.include_router(sms_webhooks.router)
app.include_router(imessage_webhooks.router)
app.include_router(branding.router)

# App-data routers — hard-gated by the org 2FA policy (mfa_gate is a no-op for
# unauthenticated requests, so their public webhooks still work).
_MFA = [Depends(mfa_gate)]
app.include_router(billing.router, dependencies=_MFA)
app.include_router(integrations.router, dependencies=_MFA)
app.include_router(clients.router, dependencies=_MFA)
app.include_router(connect_meta.router, dependencies=_MFA)
app.include_router(connect_google.router, dependencies=_MFA)
app.include_router(connect_accounts.router, dependencies=_MFA)
app.include_router(browser.router, dependencies=_MFA)
app.include_router(manage.router, dependencies=_MFA)
app.include_router(conversions.router, dependencies=_MFA)
app.include_router(metrics.router, dependencies=_MFA)
app.include_router(dashboard.router, dependencies=_MFA)
app.include_router(crm.router, dependencies=_MFA)
app.include_router(lead_finder.router, dependencies=_MFA)
app.include_router(custom_fields.router, dependencies=_MFA)
app.include_router(ai.router, dependencies=_MFA)
app.include_router(outreach.router, dependencies=_MFA)
app.include_router(email_outreach.router, dependencies=_MFA)
app.include_router(sms_outreach.router, dependencies=_MFA)


# Platform-API failures that escape a router (live refresh paths catch only
# auth errors) must become structured 502s: an unhandled exception is a bare
# 500 emitted outside CORSMiddleware, which browsers report as an opaque
# "NetworkError" instead of the actual reason.
from .services.google_ads_api import GoogleApiError as _GoogleApiError
from .services.meta_api import MetaApiError as _MetaApiError
from .services.places import PlacesError as _PlacesError

_PLATFORM_ERROR_LABELS = {
    _GoogleApiError: "Google Ads API error",
    _MetaApiError: "Meta API error",
    _PlacesError: "Google Places error",
}


def _platform_error_handler(request, exc):
    from fastapi.responses import JSONResponse

    label = next(
        (v for k, v in _PLATFORM_ERROR_LABELS.items() if isinstance(exc, k)),
        "Platform API error",
    )
    return JSONResponse({"detail": f"{label}: {exc}"}, status_code=502)


for _exc_type in _PLATFORM_ERROR_LABELS:
    app.add_exception_handler(_exc_type, _platform_error_handler)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.on_event("startup")
def _migrate():
    # Bring any database (fresh or existing) up to the current schema via
    # Alembic — the single source of truth for schema, in dev and prod alike.
    upgrade_to_head()


@app.on_event("startup")
async def _outreach_scheduler():
    """Outreach sequence scheduler — the first background loop in the app.
    Drives due sequence steps + queued-send flushes on an interval. Each tick
    runs in a worker thread (sync SQLAlchemy) with its own session; failures
    are logged and never kill the loop. Disabled in tests (they tick
    synchronously via the service)."""
    if not _settings.outreach_scheduler_enabled or not _settings.run_schedulers():
        return
    import asyncio

    from .db import SessionLocal
    from .services import outreach_sequences

    log = logging.getLogger("salescale.outreach")

    def _tick():
        db = SessionLocal()
        try:
            outreach_sequences.run_due(db)
        finally:
            db.close()

    async def _loop():
        while True:
            await asyncio.sleep(max(5, _settings.outreach_tick_seconds))
            try:
                await asyncio.get_event_loop().run_in_executor(None, _tick)
            except Exception:
                log.exception("outreach scheduler tick failed")

    asyncio.create_task(_loop())


@app.on_event("startup")
async def _email_outreach_scheduler():
    """Cold-email background loop. Each tick, in a worker thread with its own
    session (failures logged, never fatal):
      (a) fires due campaign enrollment steps (email_campaigns.run_due),
      (b) drips warmup peer exchanges + ramps caps (email_warmup.run_due),
      (c) polls each connected mailbox's INBOX for replies/bounces
          (email_outreach_sync.sync_due, per-account floor via
          email_sync_min_interval_seconds),
      (d) fires due SMS campaign enrollment steps (sms_campaigns.run_due) —
          the SMS module shares this loop rather than running its own; its
          reply/opt-out exits happen synchronously in the inbound Twilio
          webhook instead of a sync tick, so there's nothing else for it to
          do here.
    Same pattern as the IG scheduler. Disabled in tests (they drive run_due /
    sync_account / run_warmup_tick synchronously)."""
    if not _settings.email_outreach_scheduler_enabled or not _settings.run_schedulers():
        return
    import asyncio

    from .db import SessionLocal
    from .services import email_campaigns, email_outreach_sync, email_warmup
    from .services import sms_campaigns

    log = logging.getLogger("salescale.email_outreach")

    def _tick():
        db = SessionLocal()
        try:
            email_campaigns.run_due(db)
            email_warmup.run_due(db)
            email_outreach_sync.sync_due(db)
        finally:
            db.close()
        # Isolated session + its own try/except: an SMS failure must never
        # stall (or roll back) the email tick that already ran above.
        try:
            sms_db = SessionLocal()
            try:
                sms_campaigns.run_due(sms_db)
            finally:
                sms_db.close()
        except Exception:
            log.exception("sms campaign scheduler tick failed")

    async def _loop():
        while True:
            await asyncio.sleep(max(5, _settings.email_outreach_tick_seconds))
            try:
                await asyncio.get_event_loop().run_in_executor(None, _tick)
            except Exception:
                log.exception("email outreach scheduler tick failed")

    asyncio.create_task(_loop())
