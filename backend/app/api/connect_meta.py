from typing import Optional

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin, require_verified_email
from ..models.core import Client, PLATFORM_META, User
from ..security import create_state_token, decode_state_token
from ..services import (
    ad_accounts,
    connections,
    integration_creds,
    meta_api,
    meta_leadgen,
)
from .connect_common import post_connect_response

router = APIRouter(prefix="/api/connect/meta", tags=["connect"])


@router.get("/start")
def start_meta_oauth(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _verified: User = Depends(require_verified_email),
):
    """Admin-only: begin OAuth for one client in the caller's Organization.
    The signed state token binds the eventual callback to this organization
    AND client so tokens can't land on the wrong tenant at either level."""
    # Client ownership first (404 for cross-tenant, before leaking config state).
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Unknown client")
    creds = integration_creds.resolve_meta(db, user.organization_id)
    if not creds.configured:
        raise HTTPException(
            503, "Meta isn't connected — add your Meta app credentials in Integrations"
        )
    integration_creds.bind(db, user.organization_id)  # so build_oauth_url uses them
    state = create_state_token("meta_oauth", user.organization_id, client_id)
    return {"url": meta_api.build_oauth_url(state)}


@router.get("/callback")
def meta_oauth_callback(
    state: str,
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db),
):
    # Unauthenticated by necessity (browser redirect from Meta); the signed
    # state token is the integrity check.
    try:
        organization_id, client_id = decode_state_token(state, "meta_oauth")
    except pyjwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired OAuth state")
    client = db.get(Client, client_id)
    if client is None or client.organization_id != organization_id:
        raise HTTPException(400, "OAuth state does not match a known tenant")

    # The user backed out of Meta's dialog (or Meta reported a failure) —
    # land on a clear page instead of a 422/500.
    if error or not code:
        msg = (
            "You canceled the Meta sign-in."
            if error == "access_denied"
            else "Meta reported an error: "
            + (error_description or error or "no authorization code returned")
            + "."
        )
        return post_connect_response(client_id, "meta", error=msg)

    integration_creds.bind(db, organization_id)  # use this org's app for exchange
    try:
        token_data = meta_api.exchange_code_for_token(code)
        long_lived = meta_api.exchange_for_long_lived_token(token_data["access_token"])
    except (meta_api.MetaAuthError, meta_api.MetaApiError) as e:
        return post_connect_response(
            client_id, "meta", error=f"Meta rejected the sign-in: {e}"
        )
    access_token = long_lived["access_token"]
    expires_in = long_lived.get("expires_in")

    try:
        me = meta_api.fetch_me(access_token)
    except (meta_api.MetaAuthError, meta_api.MetaApiError) as e:
        return post_connect_response(
            client_id, "meta", error=f"Meta rejected the new token: {e}"
        )
    conn = connections.upsert_connection(
        db,
        organization_id=organization_id,
        client_id=client_id,
        platform=PLATFORM_META,
        access_token=access_token,
        expires_in_seconds=expires_in,
        scopes=meta_api.META_SCOPES,
        external_user_id=me.get("id"),
    )

    # Pull the ad accounts this token can see. A Business Manager/agency
    # login sees every client's account — attach only when unambiguous
    # (exactly one new account); otherwise the Admin assigns accounts to the
    # right clients in the account picker instead of everything landing on
    # this one client profile.
    try:
        discovered = ad_accounts.discover(db, conn)
    except meta_api.MetaAuthError as e:
        connections.mark_disconnected(db, conn, f"Auth failed after OAuth: {e}")
        return post_connect_response(
            client_id, "meta", error=f"Meta auth failed: {e}"
        )
    except meta_api.MetaApiError as e:
        connections.mark_disconnected(db, conn, f"Account listing failed: {e}")
        return post_connect_response(
            client_id, "meta", error=f"Meta ad-account listing failed: {e}"
        )

    outcome = ad_accounts.auto_attach(db, organization_id, client_id, conn, discovered)

    # Auto-subscribe the connected user's Pages to the leadgen webhook and
    # register routing so Instant Form leads flow into this client's CRM.
    # Best-effort by design — a missing page permission (before App Review) or
    # a Pages API hiccup must never fail the ad-account connect above.
    try:
        meta_leadgen.subscribe_client_pages(
            db,
            organization_id=organization_id,
            client_id=client_id,
            user_access_token=access_token,
        )
    except Exception:
        pass

    db.commit()

    return post_connect_response(
        client_id, "meta", select_accounts=outcome.needs_selection
    )
