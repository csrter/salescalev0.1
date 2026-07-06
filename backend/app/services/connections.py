"""Connection lifecycle: encrypted token storage and revocation surfacing.

Both platform services raise *AuthError when a token is invalid or revoked;
callers route that here so the connection flips to `disconnected` with a
human-readable reason instead of the app silently failing.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import (
    CONN_ACTIVE,
    CONN_DISCONNECTED,
    PlatformConnection,
)
from ..security import decrypt_secret, encrypt_secret


def upsert_connection(
    db: Session,
    client_id: str,
    platform: str,
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    expires_in_seconds: Optional[int] = None,
    scopes: Optional[str] = None,
    external_user_id: Optional[str] = None,
) -> PlatformConnection:
    conn = db.execute(
        select(PlatformConnection).where(
            PlatformConnection.client_id == client_id,
            PlatformConnection.platform == platform,
        )
    ).scalar_one_or_none()
    if conn is None:
        conn = PlatformConnection(client_id=client_id, platform=platform)
        db.add(conn)
    if access_token is not None:
        conn.access_token_encrypted = encrypt_secret(access_token)
    if refresh_token is not None:
        conn.refresh_token_encrypted = encrypt_secret(refresh_token)
    if expires_in_seconds is not None:
        conn.token_expires_at = utcnow() + dt.timedelta(seconds=expires_in_seconds)
    if scopes is not None:
        conn.scopes = scopes
    if external_user_id is not None:
        conn.external_user_id = external_user_id
    conn.status = CONN_ACTIVE
    conn.error_detail = None
    conn.connected_at = utcnow()
    conn.disconnected_at = None
    db.commit()
    return conn


def mark_disconnected(db: Session, conn: PlatformConnection, detail: str) -> None:
    conn.status = CONN_DISCONNECTED
    conn.error_detail = detail
    conn.disconnected_at = utcnow()
    db.commit()


def get_access_token(conn: PlatformConnection) -> str:
    if conn.status != CONN_ACTIVE or not conn.access_token_encrypted:
        raise ValueError(f"Connection {conn.id} is not active")
    return decrypt_secret(conn.access_token_encrypted)


def get_refresh_token(conn: PlatformConnection) -> str:
    if conn.status != CONN_ACTIVE or not conn.refresh_token_encrypted:
        raise ValueError(f"Connection {conn.id} is not active")
    return decrypt_secret(conn.refresh_token_encrypted)
