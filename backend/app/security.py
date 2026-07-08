import datetime as dt
from typing import Any, Dict, Optional

import bcrypt
import jwt
from cryptography.fernet import Fernet

from .config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(
    user_id: str, role: str, organization_id: str, client_id: Optional[str]
) -> str:
    settings = get_settings()
    payload = {
        "sub": user_id,
        "role": role,
        "organization_id": organization_id,
        "client_id": client_id,
        "exp": dt.datetime.now(dt.timezone.utc)
        + dt.timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> Dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def create_state_token(purpose: str, organization_id: str, client_id: str) -> str:
    """Short-lived signed state for OAuth flows — binds the callback to the
    organization AND client the flow was started for, so a callback can't
    attach tokens to a different tenant at either level."""
    settings = get_settings()
    payload = {
        "purpose": purpose,
        "organization_id": organization_id,
        "client_id": client_id,
        "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_state_token(token: str, purpose: str) -> tuple[str, str]:
    """Returns (organization_id, client_id) from a valid state token."""
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("state purpose mismatch")
    return payload["organization_id"], payload["client_id"]


def create_action_token(purpose: str, user_id: str, minutes: int) -> str:
    """Short-lived signed token for a one-off account action (email
    verification, password reset). Bound to a purpose so a verify link can't
    be replayed as a reset, and vice versa."""
    settings = get_settings()
    payload = {
        "purpose": purpose,
        "sub": user_id,
        "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_action_token(token: str, purpose: str) -> str:
    """Return the user_id from a valid action token, or raise jwt errors."""
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("action token purpose mismatch")
    return payload["sub"]


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY is not set — refusing to store platform "
            "tokens unencrypted. Generate one with "
            "`python3 -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`"
        )
    return Fernet(key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
