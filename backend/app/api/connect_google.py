from typing import Optional

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin, require_verified_email
from ..models.core import Client, PLATFORM_GOOGLE, User
from ..security import create_state_token, decode_state_token
from ..services import ad_accounts, connections, google_ads_api, integration_creds
from .connect_common import post_connect_response

router = APIRouter(prefix="/api/connect/google", tags=["connect"])


@router.get("/start")
def start_google_oauth(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _verified: User = Depends(require_verified_email),
):
    # Client ownership first (404 for cross-tenant, before leaking config state).
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Unknown client")
    creds = integration_creds.resolve_google(db, user.organization_id)
    if not creds.configured:
        raise HTTPException(
            503, "Google isn't connected — add your Google Ads credentials in Integrations"
        )
    integration_creds.bind(db, user.organization_id)
    state = create_state_token("google_oauth", user.organization_id, client_id)
    return {"url": google_ads_api.build_oauth_url(state)}


@router.get("/callback")
def google_oauth_callback(
    state: str,
    code: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    # Unauthenticated by necessity (browser redirect from Google); the signed
    # state token is the integrity check.
    try:
        organization_id, client_id = decode_state_token(state, "google_oauth")
    except pyjwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired OAuth state")
    client = db.get(Client, client_id)
    if client is None or client.organization_id != organization_id:
        raise HTTPException(400, "OAuth state does not match a known tenant")

    # The user backed out of Google's consent screen (or Google reported a
    # failure) — land on a clear page instead of a 422/500.
    if error or not code:
        msg = (
            "You canceled the Google sign-in."
            if error == "access_denied"
            else f"Google reported an error: {error or 'no authorization code returned'}."
        )
        return post_connect_response(client_id, "google", error=msg)

    integration_creds.bind(db, organization_id)  # use this org's app for exchange + calls
    try:
        tokens = google_ads_api.exchange_code_for_tokens(code)
    except google_ads_api.GoogleApiError as e:
        return post_connect_response(
            client_id, "google", error=f"Google rejected the sign-in: {e}"
        )
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return post_connect_response(
            client_id,
            "google",
            error=(
                "Google did not return a refresh token — remove the app's access "
                "at myaccount.google.com/permissions and reconnect"
            ),
        )

    conn = connections.upsert_connection(
        db,
        organization_id=organization_id,
        client_id=client_id,
        platform=PLATFORM_GOOGLE,
        access_token=tokens.get("access_token"),
        refresh_token=refresh_token,
        expires_in_seconds=tokens.get("expires_in"),
        scopes=google_ads_api.GOOGLE_ADS_SCOPE,
    )

    # Discovery is the real auth check — if the token itself is bad, fail the
    # whole connection here. It lists accounts shared directly with this login
    # plus, for any manager (MCC), the enabled ad accounts under it.
    try:
        discovered = ad_accounts.discover(db, conn)
    except google_ads_api.GoogleAuthError as e:
        connections.mark_disconnected(db, conn, f"Auth failed after OAuth: {e}")
        return post_connect_response(
            client_id, "google", error=f"Google Ads auth failed: {e}"
        )
    except google_ads_api.GoogleApiError as e:
        connections.mark_disconnected(db, conn, f"Account listing failed: {e}")
        return post_connect_response(
            client_id, "google", error=f"Google Ads account listing failed: {e}"
        )

    # Attach only when unambiguous (exactly one new account). An MCC/agency
    # login that can see a whole client roster attaches nothing here — the
    # Admin distributes accounts to the right clients in the account picker,
    # instead of every account landing on this one client profile.
    outcome = ad_accounts.auto_attach(db, organization_id, client_id, conn, discovered)
    db.commit()

    return post_connect_response(
        client_id, "google", select_accounts=outcome.needs_selection
    )
