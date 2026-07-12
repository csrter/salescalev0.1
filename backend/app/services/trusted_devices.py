"""Remember-this-device for 2FA logins.

An opaque token, minted only right after a successful MFA verification, that
lets POST /api/auth/login skip issuing a new challenge for the SAME device
until it expires (settings.mfa_remember_device_days, default 30). Only its
sha256 hash is ever stored — the same lookup-by-hash pattern models/team.py
uses for invite tokens (raw value in the client, hash server-side, O(1)
lookup by hash instead of a bcrypt scan since this is checked on every login
attempt). This is NOT a session and never substitutes for one: it only ever
short-circuits the MFA step in api/auth.py login(); every real request still
needs a genuine access token tied to a live UserSession.
"""
import datetime as dt
import hashlib
import secrets
from typing import List, Optional, Tuple

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.base import utcnow
from ..models.core import TrustedDevice, User


def _client_ip(request: Request) -> Optional[str]:
    if get_settings().trust_forwarded_for:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_token() -> Tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def remember(db: Session, user: User, request: Request) -> str:
    """Mint a fresh trusted-device grant for this user and return the raw
    token (shown/stored once — only the hash is ever persisted)."""
    raw, hashed = _new_token()
    db.add(
        TrustedDevice(
            user_id=user.id,
            token_hash=hashed,
            user_agent=(request.headers.get("user-agent") or "")[:400] or None,
            ip=_client_ip(request),
            expires_at=utcnow()
            + dt.timedelta(days=get_settings().mfa_remember_device_days),
            last_used_at=utcnow(),
        )
    )
    db.flush()
    return raw


def verify(db: Session, user_id: str, raw_token: Optional[str]) -> bool:
    """True (and touches last_used_at) iff `raw_token` is a live, unexpired,
    unrevoked trusted-device grant belonging to this user."""
    if not raw_token:
        return False
    row = db.execute(
        select(TrustedDevice).where(TrustedDevice.token_hash == hash_token(raw_token))
    ).scalar_one_or_none()
    if row is None or row.revoked or row.user_id != user_id:
        return False
    expires = row.expires_at
    if expires.tzinfo is None:  # SQLite hands back naive datetimes
        expires = expires.replace(tzinfo=dt.timezone.utc)
    if utcnow() > expires:
        return False
    row.last_used_at = utcnow()
    return True


def list_for_user(db: Session, user_id: str) -> List[TrustedDevice]:
    return (
        db.execute(
            select(TrustedDevice)
            .where(TrustedDevice.user_id == user_id, TrustedDevice.revoked.is_(False))
            .order_by(TrustedDevice.last_used_at.desc())
        )
        .scalars()
        .all()
    )


def revoke_one(db: Session, device_id: str, user_id: str) -> bool:
    row = db.get(TrustedDevice, device_id)
    if row is None or row.user_id != user_id:
        return False
    row.revoked = True
    return True


def revoke_all(db: Session, user_id: str) -> None:
    """Wipe every standing device-trust grant for this user — called
    alongside every other account-wide credential reset (logout-all,
    password reset, MFA disable), since a device trust is itself a standing
    credential that should die with them."""
    for row in (
        db.execute(
            select(TrustedDevice).where(
                TrustedDevice.user_id == user_id, TrustedDevice.revoked.is_(False)
            )
        )
        .scalars()
        .all()
    ):
        row.revoked = True
