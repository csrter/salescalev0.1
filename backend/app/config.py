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

    google_client_id: str = ""
    google_client_secret: str = ""
    google_developer_token: str = ""
    google_login_customer_id: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/connect/google/callback"

    frontend_origin: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
