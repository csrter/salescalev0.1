import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user, is_superadmin
from ..models.core import ORG_SUSPENDED, Organization, User
from ..ratelimit import rate_limit
from ..schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    OkResponse,
    ResetPasswordRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from ..security import (
    create_access_token,
    decode_action_payload,
    decode_action_token,
    hash_password,
    password_fingerprint,
    verify_password,
)
from ..services import auth_email

router = APIRouter(prefix="/api/auth", tags=["auth"])

# A valid bcrypt hash (cost 12) to verify against when the email isn't
# registered, so login timing doesn't reveal whether an account exists.
_DUMMY_PASSWORD_HASH = "$2b$12$CrQk5LRFFPgo3j2mGp9yU.QLPIsNhSbEaBqnCCRFVGon50EBLwGm2"

# Brute-force brake: 20 login attempts per 5 minutes per IP.
_login_limit = rate_limit("login", limit=20, window_seconds=300)
# Account-recovery mail is cheap to abuse; keep it tight.
_recover_limit = rate_limit("recover", limit=5, window_seconds=900)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db), _: None = _login_limit):
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
    token = create_access_token(
        user.id, user.role, user.organization_id, user.client_id, user.token_version
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
    )


@router.get("/me", response_model=TokenResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The session for the current token — used by the frontend after a social
    login redirect to fill in role/org details."""
    org = db.get(Organization, user.organization_id)
    token = create_access_token(
        user.id, user.role, user.organization_id, user.client_id, user.token_version
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
    )


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
    """Revoke every outstanding session for the caller (all devices). The
    current token stops working on its next request too."""
    user.token_version += 1
    db.commit()
    return OkResponse()
