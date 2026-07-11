"""Social login — Sign in with Google / Meta.

Flow: the frontend hits /start to get a provider consent URL, the user
authorizes, the provider redirects to /callback, we exchange the code for the
user's verified email, find-or-create their account, and bounce back to the
web app with a session token in the URL fragment.

OAuth apps are shared with the ad integrations (extra redirect URIs). httpx is
used for the token/userinfo calls; providers are unconfigured-safe (503).
"""
import logging
import secrets
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models.core import ROLE_OWNER, Organization, User
from ..ratelimit import rate_limit
from ..security import create_access_token, create_action_token, decode_action_token, hash_password
from ..services import sessions, team

router = APIRouter(prefix="/api/auth/oauth", tags=["auth"])
log = logging.getLogger("salescale.social")

# start/callback are public; the callback fires an outbound token-exchange, so
# cap it to prevent unauthenticated outbound-request amplification.
_oauth_limit = rate_limit("social_oauth", limit=30, window_seconds=60)

PROVIDERS = ("google", "meta")


def _redirect_uri(provider: str) -> str:
    return f"{get_settings().api_base_url}/api/auth/oauth/{provider}/callback"


def _creds(provider: str) -> tuple[str, str]:
    s = get_settings()
    return s.google_login_creds() if provider == "google" else s.meta_login_creds()


def _require_configured(provider: str) -> None:
    cid, csecret = _creds(provider)
    if not cid or not csecret:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"{provider.title()} login is not configured",
        )


def _auth_url(provider: str, state: str) -> str:
    cid, _ = _creds(provider)
    redirect = _redirect_uri(provider)
    if provider == "google":
        q = urlencode(
            {
                "response_type": "code",
                "client_id": cid,
                "redirect_uri": redirect,
                "scope": "openid email profile",
                "state": state,
                "access_type": "online",
                "prompt": "select_account",
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{q}"
    q = urlencode(
        {"client_id": cid, "redirect_uri": redirect, "state": state, "scope": "email"}
    )
    return f"https://www.facebook.com/{get_settings().meta_api_version}/dialog/oauth?{q}"


def _exchange_and_fetch(provider: str, code: str) -> tuple[str, str, bool]:
    """Return (email, full_name, email_verified) for the authorizing user.
    email_verified reflects the provider's assertion. Raises on failure."""
    cid, csecret = _creds(provider)
    redirect = _redirect_uri(provider)
    if provider == "google":
        tok = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": cid,
                "client_secret": csecret,
                "redirect_uri": redirect,
                "grant_type": "authorization_code",
            },
            timeout=30,
        ).json()
        access = tok.get("access_token")
        if not access:
            raise HTTPException(400, "Google token exchange failed")
        info = httpx.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access}"},
            timeout=30,
        ).json()
        email = info.get("email")
        if not email:
            raise HTTPException(400, "Google account has no email")
        verified = str(info.get("email_verified")).lower() == "true"
        return email, info.get("name") or "", verified
    # Meta
    v = get_settings().meta_api_version
    tok = httpx.get(
        f"https://graph.facebook.com/{v}/oauth/access_token",
        params={
            "client_id": cid,
            "client_secret": csecret,
            "redirect_uri": redirect,
            "code": code,
        },
        timeout=30,
    ).json()
    access = tok.get("access_token")
    if not access:
        raise HTTPException(400, "Meta token exchange failed")
    info = httpx.get(
        f"https://graph.facebook.com/{v}/me",
        params={"fields": "id,name,email", "access_token": access},
        timeout=30,
    ).json()
    email = info.get("email")
    if not email:
        raise HTTPException(400, "Meta account did not share an email")
    # Meta's /me email is not a guaranteed-verified signal, so treat it as
    # unverified — it must not silently attach to a pre-existing account.
    return email, info.get("name") or "", False


def find_or_create_social_user(
    db: Session, email: str, full_name: str, provider: str, email_verified: bool
) -> User:
    """Log in an existing user by email, or provision a new Organization + Owner.

    Attaching to an EXISTING account is only allowed when the provider verified
    the email, or the account was itself created via this same provider —
    otherwise an unverified provider email (e.g. Meta's) could take over a
    password or other-provider account."""
    email = email.lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is not None:
        if not email_verified and user.auth_provider != provider:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "An account with this email already exists. Sign in with your "
                "password.",
            )
        return user
    org = Organization(name=f"{full_name}'s Organization" if full_name else "My Organization")
    db.add(org)
    db.flush()
    user = User(
        organization_id=org.id,
        email=email,
        # A random password they never use — they sign in via the provider.
        hashed_password=hash_password(secrets.token_urlsafe(24)),
        full_name=full_name or email,
        role=ROLE_OWNER,
        email_verified=email_verified,
        auth_provider=provider,
    )
    db.add(user)
    db.flush()
    team.add_membership(db, org.id, user, ROLE_OWNER)
    db.commit()
    return user


@router.get("/{provider}/start")
def start(provider: str, _: None = _oauth_limit):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    _require_configured(provider)
    state = create_action_token(f"social:{provider}", secrets.token_urlsafe(8), minutes=15)
    return {"url": _auth_url(provider, state)}


@router.get("/{provider}/callback")
def callback(
    provider: str,
    request: Request,
    code: str = "",
    state: str = "",
    db: Session = Depends(get_db),
    _: None = _oauth_limit,
):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    _require_configured(provider)
    try:
        decode_action_token(state, f"social:{provider}")
    except pyjwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired login state")

    email, full_name, email_verified = _exchange_and_fetch(provider, code)
    user = find_or_create_social_user(db, email, full_name, provider, email_verified)
    sid = sessions.create(db, user, request)
    db.commit()
    token = create_access_token(
        user.id, user.role, user.organization_id, user.client_id, user.token_version, sid
    )
    # Hand the session token back to the web app via the URL fragment (not the
    # query string, so it isn't logged by servers/proxies). The app reads it,
    # then calls /api/auth/me for the rest of the session.
    return RedirectResponse(f"{get_settings().app_base_url}/#access_token={token}")
