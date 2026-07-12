from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite fallback keeps local dev working on machines without Postgres;
    # production must set DATABASE_URL to Postgres.
    database_url: str = "sqlite:///./dev.db"
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_expire_minutes: int = 60 * 12
    token_encryption_key: Optional[str] = None

    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "http://localhost:8000/api/connect/meta/callback"
    meta_api_version: str = "v25.0"
    # Shared token for Meta's one-time webhook verification GET (Phase 6
    # leadgen webhooks) — any string, must match the App Dashboard config.
    meta_webhook_verify_token: str = ""

    # Outreach module (Instagram DM automation). The scheduler is the asyncio
    # loop in main.py driving sequence steps + queued-send flushes; disabled in
    # tests (they call the tick synchronously).
    outreach_scheduler_enabled: bool = True
    outreach_tick_seconds: int = 60
    # Default per-account daily send cap for newly connected IG accounts —
    # deliberately conservative; tenants can raise it per account.
    outreach_default_daily_cap: int = 100
    # Queued (window-closed) automated messages older than this are dropped
    # rather than sent into a long-dead thread.
    outreach_queue_max_age_hours: int = 168

    # Cold-email outreach module (SMTP/IMAP mailboxes). The scheduler is the
    # asyncio loop in main.py driving IMAP reply/bounce sync (Phase 2 adds the
    # campaign step engine to the same tick); disabled in tests. tick_seconds
    # is the loop cadence; email_sync_min_interval_seconds is the per-account
    # floor between IMAP polls (sync_due skips accounts synced more recently).
    email_outreach_scheduler_enabled: bool = True
    email_outreach_tick_seconds: int = 60
    email_sync_min_interval_seconds: int = 180

    google_client_id: str = ""
    google_client_secret: str = ""
    google_developer_token: str = ""
    google_login_customer_id: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/connect/google/callback"

    # Web frontend origin(s) allowed by CORS. Comma-separated for multiple
    # (e.g. a production domain plus a preview/staging domain). Ignored when
    # DESKTOP_MODE=1 (the Electron UI is file:// and uses a wildcard instead).
    frontend_origin: str = "http://localhost:5173"

    def frontend_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

    # Rate limiting on public endpoints (signup/login). Disabled in the test
    # suite; keep on in every real environment.
    rate_limit_enabled: bool = True
    # Only honor the X-Forwarded-For header (for the real client IP behind a
    # load balancer / reverse proxy) when this is set — otherwise a client
    # could spoof it to evade rate limits. Turn on in a hosted deploy that sits
    # behind a trusted proxy; leave off for direct/desktop.
    trust_forwarded_for: bool = False

    # When true, users must verify their email before they can log in. Off by
    # default so a fresh deploy works before email delivery is wired up.
    require_email_verification: bool = False

    # Observability. Error tracking is off until a Sentry DSN is provided.
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.0

    # Desktop (Electron) mode: the UI is served from file:// so its Origin is
    # "null" rather than a fixed http origin. When true, CORS allows any origin
    # (safe here because auth is Bearer-token only — no cookies to protect).
    desktop_mode: bool = False

    # Platform super-admins (Salescale operators). Comma-separated emails.
    # Super-admin is derived from this allowlist, never granted via the API or
    # signup — the only way to become one is to be listed here (env-controlled).
    # These users can read across ALL organizations via /api/admin/*.
    superadmin_emails: str = ""

    def superadmin_email_set(self) -> set[str]:
        return {
            e.strip().lower()
            for e in self.superadmin_emails.split(",")
            if e.strip()
        }

    # Phase 9 — AI insights (Claude API, server-side only; never expose the
    # key to the frontend).
    anthropic_api_key: str = ""
    ai_model: str = "claude-opus-4-8"
    # Global default monthly cap on AI queries per Organization until Phase 8
    # wires real tier limits into services/entitlements.py.
    ai_monthly_query_limit: int = 200

    # Phase 9 — white-labeling. The neutral sender identity used when an
    # Organization hasn't configured branded email. SMTP unset = dev mode:
    # emails are composed and logged (email_log table) but not delivered.
    email_default_from_name: str = "Salescale"
    email_default_from_address: str = "no-reply@salescale.app"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""

    # Transactional email via Resend (preferred). When set, it's used instead
    # of SMTP. The sender address (email_default_from_address, or a branded
    # per-org address) must be on a domain you've verified in Resend.
    resend_api_key: str = ""

    # Phase 12 — Lead Finder. The operator's global Google Places API key,
    # used when an Organization hasn't connected its own (BYO key via
    # /api/lead-finder/providers). Unset + no org key = Lead Finder search
    # returns 503. Server-side only, like every platform secret.
    google_places_api_key: str = ""
    # Phase 12 — email verification (ZeroBounce reference adapter). Same
    # org-key-first, operator-fallback resolution as Places. Unset + no org
    # key = verification requests are recorded as "unknown" in dev, so the
    # pipeline stays exercisable without a paid key.
    zerobounce_api_key: str = ""
    # Website email-discovery crawler (Part B). Kill switch + politeness knobs;
    # the crawler only ever fetches the imported business's own site.
    lead_finder_crawl_enabled: bool = True
    lead_finder_crawl_timeout_seconds: float = 5.0
    lead_finder_crawl_max_pages: int = 5

    # SMS via Twilio, used for phone-based 2FA. All three must be set or SMS
    # 2FA is unavailable (enrollment returns 503). TOTP + email 2FA need none
    # of this.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Phase 8 — Stripe subscription billing. Unset = billing disabled (the
    # /api/billing endpoints return 503). Each plan maps to a Stripe Price.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""
    stripe_price_agency: str = ""
    # Public URL of the web app — Checkout success/cancel + portal return + the
    # OAuth social-login return all land here.
    app_base_url: str = "http://localhost:5173"

    # Public URL of THIS backend — used to build OAuth redirect_uri values that
    # must match what's registered with Google/Meta.
    api_base_url: str = "http://localhost:8000"

    # Social login. Reuses the ad OAuth apps by default; override per-provider
    # if you register separate apps for sign-in.
    google_login_client_id: str = ""
    google_login_client_secret: str = ""
    meta_login_app_id: str = ""
    meta_login_app_secret: str = ""

    def google_login_creds(self) -> tuple[str, str]:
        return (
            self.google_login_client_id or self.google_client_id,
            self.google_login_client_secret or self.google_client_secret,
        )

    def meta_login_creds(self) -> tuple[str, str]:
        return (
            self.meta_login_app_id or self.meta_app_id,
            self.meta_login_app_secret or self.meta_app_secret,
        )

    def stripe_price_for_plan(self, plan: str) -> str:
        return {"pro": self.stripe_price_pro, "agency": self.stripe_price_agency}.get(
            plan, ""
        )

    def plan_for_stripe_price(self, price_id: str) -> str | None:
        for plan in ("pro", "agency"):
            if price_id and self.stripe_price_for_plan(plan) == price_id:
                return plan
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
