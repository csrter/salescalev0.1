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

    google_client_id: str = ""
    google_client_secret: str = ""
    google_developer_token: str = ""
    google_login_customer_id: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/connect/google/callback"

    frontend_origin: str = "http://localhost:5173"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
