"""Two-factor auth primitives: TOTP (authenticator apps), email/SMS one-time
codes, and single-use backup codes.

Secrets (TOTP seed, phone) are Fernet-encrypted at rest; codes are bcrypt-
hashed and never stored in the clear. The one active method lives in
User.mfa_method; enrollment and the login challenge are wired in api/mfa.py and
api/auth.py.
"""
import datetime as dt
import secrets
from typing import List, Optional, Tuple

import pyotp

from ..models.base import utcnow
from ..models.core import User
from ..security import decrypt_secret, encrypt_secret, hash_password, verify_password

ISSUER = "Salescale"
OTP_TTL_SECONDS = 300  # email / SMS code lifetime
BACKUP_CODE_COUNT = 10

METHOD_TOTP = "totp"
METHOD_EMAIL = "email"
METHOD_SMS = "sms"
METHODS = {METHOD_TOTP, METHOD_EMAIL, METHOD_SMS}


# --- TOTP (Google Authenticator etc.) ---

def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, email: str) -> str:
    """otpauth:// URI the authenticator app scans (rendered as a QR client-side)."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    # valid_window=1 tolerates one 30s step of clock skew either way.
    return pyotp.TOTP(secret).verify((code or "").strip(), valid_window=1)


def store_totp_secret(user: User, secret: str) -> None:
    user.totp_secret_encrypted = encrypt_secret(secret)


def load_totp_secret(user: User) -> Optional[str]:
    return decrypt_secret(user.totp_secret_encrypted) if user.totp_secret_encrypted else None


# --- Phone (SMS) storage ---

def store_phone(user: User, phone: str) -> None:
    user.mfa_phone_encrypted = encrypt_secret(phone)


def load_phone(user: User) -> Optional[str]:
    return decrypt_secret(user.mfa_phone_encrypted) if user.mfa_phone_encrypted else None


# --- Email / SMS one-time codes ---

def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def set_pending_code(user: User) -> str:
    """Generate a 6-digit code, store its hash + expiry on the user, and return
    the plaintext (to email/SMS). Overwrites any previous pending code."""
    code = new_code()
    user.mfa_otp_hash = hash_password(code)
    user.mfa_otp_expires_at = utcnow() + dt.timedelta(seconds=OTP_TTL_SECONDS)
    return code


def verify_pending_code(user: User, code: str) -> bool:
    """Check a submitted email/SMS code and consume it (single-use). Expired or
    absent codes fail."""
    if not user.mfa_otp_hash or not user.mfa_otp_expires_at:
        return False
    # SQLite returns naive datetimes even for timezone=True columns; treat a
    # naive value as UTC so the comparison never mixes aware/naive.
    expires = user.mfa_otp_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    if utcnow() > expires:
        return False
    if not verify_password((code or "").strip(), user.mfa_otp_hash):
        return False
    user.mfa_otp_hash = None
    user.mfa_otp_expires_at = None
    return True


# --- Backup / recovery codes ---

def new_backup_codes() -> Tuple[List[str], List[str]]:
    """Return (plaintext codes shown to the user once, bcrypt hashes to store)."""
    codes = ["-".join((secrets.token_hex(2), secrets.token_hex(2))) for _ in range(BACKUP_CODE_COUNT)]
    return codes, [hash_password(c) for c in codes]


def consume_backup_code(user: User, code: str) -> bool:
    """Verify and remove a backup code (single-use). Returns False if none match."""
    stored = list(user.mfa_backup_codes or [])
    candidate = (code or "").strip().lower()
    for i, h in enumerate(stored):
        if verify_password(candidate, h):
            user.mfa_backup_codes = stored[:i] + stored[i + 1 :]
            return True
    return False


def clear(user: User) -> None:
    """Disable 2FA and wipe every associated secret/code."""
    user.mfa_method = None
    user.totp_secret_encrypted = None
    user.mfa_phone_encrypted = None
    user.mfa_backup_codes = None
    user.mfa_otp_hash = None
    user.mfa_otp_expires_at = None
