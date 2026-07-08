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
    decode_action_token,
    hash_password,
    verify_password,
)
from ..services import auth_email

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Brute-force brake: 20 login attempts per 5 minutes per IP.
_login_limit = rate_limit("login", limit=20, window_seconds=300)
# Account-recovery mail is cheap to abuse; keep it tight.
_recover_limit = rate_limit("recover", limit=5, window_seconds=900)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db), _: None = _login_limit):
    user = db.execute(
        select(User).where(User.email == body.email.lower())
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(
        body.password, user.hashed_password
    ):
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
    token = create_access_token(user.id, user.role, user.organization_id, user.client_id)
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
    token = create_access_token(user.id, user.role, user.organization_id, user.client_id)
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
def verify_email(body: VerifyEmailRequest, db: Session = Depends(get_db)):
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
        user_id = decode_action_token(body.token, auth_email.RESET_PURPOSE)
    except pyjwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired reset link")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(400, "Invalid or expired reset link")
    user.hashed_password = hash_password(body.new_password)
    db.commit()
    return OkResponse()
