"""Two-factor auth enrollment & management (authenticated).

Setup is two-step per method — a setup call stores the pending secret/phone (and
for email/SMS sends a code), then an enable call verifies a code and activates
the method, returning one-time backup codes. The login-time challenge lives in
api/auth.py. Only one method is active at a time (User.mfa_method).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models.core import Organization, User
from ..ratelimit import rate_limit
from ..schemas import (
    MfaCodeIn,
    MfaDisableIn,
    MfaEnabledOut,
    MfaSmsSetupIn,
    MfaStatusOut,
    OkResponse,
    TotpSetupOut,
)
from ..security import verify_password
from ..services import email as email_service
from ..services import mfa, sms

router = APIRouter(prefix="/api/mfa", tags=["mfa"])

# Sending codes (email/SMS) is abuse-prone — keep it tight per IP.
_send_limit = rate_limit("mfa_send", limit=5, window_seconds=300)


def _phone_hint(phone: str | None) -> str | None:
    return "•••• " + phone[-4:] if phone and len(phone) >= 4 else None


def _org(db: Session, user: User) -> Organization:
    return db.get(Organization, user.organization_id)


def _email_code(db: Session, user: User) -> None:
    code = mfa.set_pending_code(user)
    email_service.send_email(
        db,
        _org(db, user),
        user.email,
        "Your Salescale verification code",
        f"Your Salescale verification code is {code}. It expires in 5 minutes.\n\n"
        "If you didn't request this, ignore this email.",
    )


def _finalize_enable(db: Session, user: User, method: str) -> MfaEnabledOut:
    user.mfa_method = method
    codes, hashes = mfa.new_backup_codes()
    user.mfa_backup_codes = hashes
    db.commit()
    return MfaEnabledOut(method=method, backup_codes=codes)


@router.get("", response_model=MfaStatusOut)
def status(user: User = Depends(get_current_user)):
    return MfaStatusOut(
        method=user.mfa_method,
        phone_hint=_phone_hint(mfa.load_phone(user)) if user.mfa_method == mfa.METHOD_SMS else None,
        backup_codes_remaining=len(user.mfa_backup_codes or []),
    )


# --- TOTP (authenticator app) ---


@router.post("/totp/setup", response_model=TotpSetupOut)
def totp_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    secret = mfa.new_totp_secret()
    mfa.store_totp_secret(user, secret)  # stored but inactive until enable
    db.commit()
    return TotpSetupOut(
        secret=secret, otpauth_uri=mfa.totp_provisioning_uri(secret, user.email)
    )


@router.post("/totp/enable", response_model=MfaEnabledOut)
def totp_enable(
    body: MfaCodeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    secret = mfa.load_totp_secret(user)
    if not secret:
        raise HTTPException(400, "Start TOTP setup first")
    if not mfa.verify_totp(secret, body.code):
        raise HTTPException(400, "Invalid code")
    return _finalize_enable(db, user, mfa.METHOD_TOTP)


# --- Email code ---


@router.post("/email/setup", response_model=OkResponse)
def email_setup(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = _send_limit,
):
    _email_code(db, user)
    db.commit()
    return OkResponse()


@router.post("/email/enable", response_model=MfaEnabledOut)
def email_enable(
    body: MfaCodeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not mfa.verify_pending_code(user, body.code):
        raise HTTPException(400, "Invalid or expired code")
    return _finalize_enable(db, user, mfa.METHOD_EMAIL)


# --- SMS code ---


@router.post("/sms/setup", response_model=OkResponse)
def sms_setup(
    body: MfaSmsSetupIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = _send_limit,
):
    if not sms.sms_configured():
        raise HTTPException(503, "SMS 2FA is not available on this deployment")
    mfa.store_phone(user, body.phone)
    code = mfa.set_pending_code(user)
    try:
        sms.send_sms(body.phone, f"Your Salescale verification code is {code}")
    except RuntimeError:
        raise HTTPException(502, "Could not send the SMS code — check the number")
    db.commit()
    return OkResponse()


@router.post("/sms/enable", response_model=MfaEnabledOut)
def sms_enable(
    body: MfaCodeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not mfa.verify_pending_code(user, body.code):
        raise HTTPException(400, "Invalid or expired code")
    return _finalize_enable(db, user, mfa.METHOD_SMS)


@router.post("/disable", response_model=OkResponse)
def disable(
    body: MfaDisableIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Re-authenticate with the password before turning off the second factor.
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(400, "Password is incorrect")
    mfa.clear(user)
    db.commit()
    return OkResponse()
