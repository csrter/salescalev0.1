import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import require_admin
from ..models.core import AdAccount, Client, PLATFORM_GOOGLE, User
from ..security import create_state_token, decode_state_token
from ..services import connections, google_ads_api, integration_creds

router = APIRouter(prefix="/api/connect/google", tags=["connect"])


@router.get("/start")
def start_google_oauth(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
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
    code: str, state: str, db: Session = Depends(get_db)
):
    try:
        organization_id, client_id = decode_state_token(state, "google_oauth")
    except pyjwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired OAuth state")
    client = db.get(Client, client_id)
    if client is None or client.organization_id != organization_id:
        raise HTTPException(400, "OAuth state does not match a known tenant")

    integration_creds.bind(db, organization_id)  # use this org's app for exchange + calls
    tokens = google_ads_api.exchange_code_for_tokens(code)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            400,
            "Google did not return a refresh token — remove the app's access "
            "at myaccount.google.com/permissions and reconnect",
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

    # list_accessible_customers is the real auth check — if the token itself is
    # bad, fail the whole connection here.
    try:
        customer_ids = google_ads_api.list_accessible_customers(refresh_token)
    except google_ads_api.GoogleAuthError as e:
        connections.mark_disconnected(db, conn, f"Auth failed after OAuth: {e}")
        raise HTTPException(502, f"Google Ads auth failed: {e}")

    # An agency login routinely sees accounts it can't actually query: a manager
    # (MCC) account rather than an ad account, or one that's deactivated / not
    # yet enabled. Skip those individually — one bad account must not abort the
    # whole connection, which otherwise leaves every good account unconnected.
    # Collect the ad accounts to attach: accounts shared directly with this
    # login, plus — for any manager (MCC) — the enabled ad accounts under it
    # (the agency model: a whole client roster onboards from one connect). One
    # inaccessible account never aborts the rest.
    discovered: list[dict] = []
    for cid in customer_ids:
        try:
            details = google_ads_api.fetch_customer_details(refresh_token, cid)
        except (google_ads_api.GoogleAuthError, google_ads_api.GoogleApiError):
            continue  # not-enabled / deactivated / inaccessible — skip
        if details.get("is_manager"):
            try:
                discovered.extend(
                    google_ads_api.list_manager_child_accounts(refresh_token, cid)
                )
            except (google_ads_api.GoogleAuthError, google_ads_api.GoogleApiError):
                continue  # can't read under this manager — skip it
        else:
            discovered.append(details)  # a directly-shared single ad account

    seen: set[str] = set()
    for details in discovered:
        ext = details["external_id"]
        if ext in seen:
            continue  # reachable both directly and under its manager
        seen.add(ext)
        existing = db.execute(
            select(AdAccount).where(
                AdAccount.platform == PLATFORM_GOOGLE,
                AdAccount.external_id == ext,
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                AdAccount(
                    organization_id=organization_id,
                    client_id=client_id,
                    connection_id=conn.id,
                    platform=PLATFORM_GOOGLE,
                    external_id=ext,
                    name=details["name"],
                    currency=details.get("currency"),
                    timezone=details.get("timezone"),
                    status=details.get("status"),
                )
            )
        elif (
            existing.client_id != client_id
            or existing.organization_id != organization_id
        ):
            raise HTTPException(
                409,
                f"Google Ads account {ext} is already connected elsewhere",
            )
    db.commit()

    settings = get_settings()
    return RedirectResponse(
        f"{settings.frontend_origin}/clients/{client_id}?connected=google"
    )
