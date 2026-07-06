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
from ..services import connections, google_ads_api

router = APIRouter(prefix="/api/connect/google", tags=["connect"])


@router.get("/start")
def start_google_oauth(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Unknown client")
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

    try:
        customer_ids = google_ads_api.list_accessible_customers(refresh_token)
        for cid in customer_ids:
            details = google_ads_api.fetch_customer_details(refresh_token, cid)
            if details.get("is_manager"):
                continue  # MCCs aren't ad accounts; client links live under them
            existing = db.execute(
                select(AdAccount).where(
                    AdAccount.platform == PLATFORM_GOOGLE,
                    AdAccount.external_id == details["external_id"],
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(
                    AdAccount(
                        organization_id=organization_id,
                        client_id=client_id,
                        connection_id=conn.id,
                        platform=PLATFORM_GOOGLE,
                        external_id=details["external_id"],
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
                    f"Google Ads account {details['external_id']} is already "
                    "connected elsewhere",
                )
        db.commit()
    except google_ads_api.GoogleAuthError as e:
        connections.mark_disconnected(db, conn, f"Auth failed after OAuth: {e}")
        raise HTTPException(502, f"Google Ads auth failed: {e}")

    settings = get_settings()
    return RedirectResponse(
        f"{settings.frontend_origin}/clients/{client_id}?connected=google"
    )
