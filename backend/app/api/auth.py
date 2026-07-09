from typing import List

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user, is_superadmin
from ..models.core import ORG_SUSPENDED, TEAM_ROLES, Organization, User
from ..ratelimit import rate_limit
from ..schemas import (
    ForgotPasswordRequest,
    LoginChallenge,
    LoginRequest,
    MfaLoginIn,
    OkResponse,
    ResetPasswordRequest,
    SessionOut,
    TokenResponse,
    VerifyEmailRequest,
)
from ..security import (
    create_access_token,
    create_action_token,
    decode_action_payload,
    decode_action_token,
    hash_password,
    password_fingerprint,
    verify_password,
)
from ..services import auth_email
from ..services import email as email_service
from ..services import mfa, sessions, sms

_MFA_CHALLENGE_PURPOSE = "mfa_login"

router = APIRouter(prefix="/api/auth", tags=["auth"])

# A valid bcrypt hash (cost 12) to verify against when the email isn't
# registered, so login timing doesn't reveal whether an account exists.
_DUMMY_PASSWORD_HASH = "$2b$12$CrQk5LRFFPgo3j2mGp9yU.QLPIsNhSbEaBqnCCRFVGon50EBLwGm2"

# Brute-force brake: 20 login attempts per 5 minutes per IP.
_login_limit = rate_limit("login", limit=20, window_seconds=300)
# Account-recovery mail is cheap to abuse; keep it tight.
_recover_limit = rate_limit("recover", limit=5, window_seconds=900)


def _mfa_setup_required(org: Organization, user: User) -> bool:
    """True when the org requires 2FA of its team members and this one hasn't
    set it up yet — the frontend gates them to enrollment until they do."""
    return bool(org.require_mfa and user.role in TEAM_ROLES and not user.mfa_method)


def _token_response(user: User, org: Organization, session_id: str | None) -> TokenResponse:
    token = create_access_token(
        user.id,
        user.role,
        user.organization_id,
        user.client_id,
        user.token_version,
        session_id,
    )
    return TokenResponse(
        access_token=token,
        role=user.role,
        organization_id=user.organization_id,
        organization_name=org.name,
        client_id=user.client_id,
        full_name=user.full_name,
        is_superadmin=is_superadmin(user),
        email_verified=user.email_verified,
        mfa_setup_required=_mfa_setup_required(org, user),
    )


def _dispatch_login_code(db: Session, user: User) -> None:
    """For email/SMS 2FA, send the one-time login code to the user."""
    code = mfa.set_pending_code(user)
    if user.mfa_method == mfa.METHOD_SMS:
        phone = mfa.load_phone(user)
        try:
            sms.send_sms(phone, f"Your Salescale verification code is {code}")
        except RuntimeError:
            pass  # delivery failure surfaces as an inability to complete login
    else:
        org = db.get(Organization, user.organization_id)
        email_service.send_email(
            db,
            org,
            user.email,
            "Your Salescale verification code",
            f"Your Salescale verification code is {code}. It expires in 5 minutes.",
        )


@router.post("/login", response_model=None)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = _login_limit,
):
    user = db.execute(
        select(User).where(User.email == body.email.lower())
    ).scalar_one_or_none()
    # Always run one bcrypt comparison — against a fixed dummy hash when the
    # email isn't registered — so a missing account can't be told apart from a
    # wrong password by response timing (user enumeration).
    hashed = user.hashed_password if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(body.password, hashed)
    if user is None or not user.is_active or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    org = db.get(Organization, user.organization_id)
    # A suspended Organization can't be logged into by any of its users. The
    # super-admin (allowlist-derived) is exempt so operators aren't locked out.
    if org.status == ORG_SUSPENDED and not is_superadmin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization suspended")
    if (
        get_settings().require_email_verification
        and not user.email_verified
        and not is_superadmin(user)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Please verify your email before logging in"
        )
    # Second factor: password alone isn't a session — issue a short-lived
    # challenge and (for email/SMS) send the code. The client completes at
    # /login/mfa.
    if user.mfa_method:
        challenge = create_action_token(
            _MFA_CHALLENGE_PURPOSE,
            user.id,
            minutes=10,
            extra={"tv": user.token_version},
        )
        if user.mfa_method in (mfa.METHOD_EMAIL, mfa.METHOD_SMS):
            _dispatch_login_code(db, user)
            db.commit()
        return LoginChallenge(method=user.mfa_method, challenge_token=challenge)
    sid = sessions.create(db, user, request)
    resp = _token_response(user, org, sid)
    db.commit()
    return resp


@router.post("/login/mfa", response_model=TokenResponse)
def login_mfa(
    body: MfaLoginIn,
    request: Request,
    db: Session = Depends(get_db),
    _: None = _login_limit,
):
    """Second step of a 2FA login: exchange the challenge + a valid code (TOTP,
    the emailed/texted code, or a backup code) for a session."""
    try:
        payload = decode_action_payload(body.challenge_token, _MFA_CHALLENGE_PURPOSE)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired challenge")
    user = db.get(User, payload["sub"])
    if (
        user is None
        or not user.is_active
        or not user.mfa_method
        or payload.get("tv", 0) != user.token_version
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired challenge")
    org = db.get(Organization, user.organization_id)
    if org.status == ORG_SUSPENDED and not is_superadmin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization suspended")
    if not _verify_mfa_code(user, body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code")
    sid = sessions.create(db, user, request)
    db.commit()  # persist a consumed one-time / backup code + the new session
    return _token_response(user, org, sid)


def _verify_mfa_code(user: User, code: str) -> bool:
    if user.mfa_method == mfa.METHOD_TOTP:
        secret = mfa.load_totp_secret(user)
        if secret and mfa.verify_totp(secret, code):
            return True
    elif user.mfa_method in (mfa.METHOD_EMAIL, mfa.METHOD_SMS):
        if mfa.verify_pending_code(user, code):
            return True
    # A backup/recovery code satisfies any method.
    return mfa.consume_backup_code(user, code)


@router.get("/me", response_model=TokenResponse)
def me(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The session for the current token — used by the frontend after a social
    login redirect to fill in role/org details. Reuses the caller's session id
    so re-issuing the token doesn't spawn a new device session."""
    org = db.get(Organization, user.organization_id)
    return _token_response(user, org, getattr(request.state, "session_id", None))


@router.get("/sessions", response_model=List[SessionOut])
def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current = getattr(request.state, "session_id", None)
    return [
        SessionOut(
            id=s.id,
            user_agent=s.user_agent,
            ip=s.ip,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            current=s.id == current,
        )
        for s in sessions.list_for_user(db, user.id)
    ]


@router.delete("/sessions/{session_id}", response_model=OkResponse)
def revoke_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sign a specific device out. Its next request 401s."""
    if not sessions.revoke_one(db, session_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    db.commit()
    return OkResponse()


@router.post("/verify-email", response_model=OkResponse)
def verify_email(
    body: VerifyEmailRequest,
    db: Session = Depends(get_db),
    _: None = _recover_limit,
):
    try:
        user_id = decode_action_token(body.token, auth_email.VERIFY_PURPOSE)
    except pyjwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired verification link")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(400, "Invalid or expired verification link")
    user.email_verified = True
    db.commit()
    return OkResponse()


@router.post("/resend-verification", response_model=OkResponse)
def resend_verification(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = _recover_limit,
):
    if not user.email_verified:
        org = db.get(Organization, user.organization_id)
        auth_email.send_verification_email(db, org, user)
        db.commit()
    return OkResponse()


@router.post("/forgot-password", response_model=OkResponse)
def forgot_password(
    body: ForgotPasswordRequest,
    db: Session = Depends(get_db),
    _: None = _recover_limit,
):
    # Always report success — never reveal whether an email is registered.
    user = db.execute(
        select(User).where(User.email == body.email.lower())
    ).scalar_one_or_none()
    if user is not None and user.is_active:
        org = db.get(Organization, user.organization_id)
        auth_email.send_reset_email(db, org, user)
        db.commit()
    return OkResponse()


@router.post("/reset-password", response_model=OkResponse)
def reset_password(
    body: ResetPasswordRequest,
    db: Session = Depends(get_db),
    _: None = _recover_limit,
):
    try:
        payload = decode_action_payload(body.token, auth_email.RESET_PURPOSE)
    except pyjwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired reset link")
    user = db.get(User, payload["sub"])
    # The fingerprint pins the token to the password hash it was issued for, so
    # a used (or superseded) link no longer resets anything.
    if user is None or payload.get("pw") != password_fingerprint(user.hashed_password):
        raise HTTPException(400, "Invalid or expired reset link")
    user.hashed_password = hash_password(body.new_password)
    # Revoke every existing session — a reset should log out other devices,
    # and if an attacker triggered it, kick them the moment the owner resets.
    user.token_version += 1
    db.commit()
    return OkResponse()


@router.post("/logout-all", response_model=OkResponse)
def logout_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sign out everywhere (all devices), including the caller. Bumps
    token_version (so every existing JWT fails) and marks all sessions revoked
    (so the device list reflects it)."""
    user.token_version += 1
    sessions.revoke_all(db, user.id)
    db.commit()
    return OkResponse()
