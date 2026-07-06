import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import require_team
from ..models.core import AdAccount, Client, PLATFORM_META, User
from ..security import create_state_token, decode_state_token
from ..services import connections, meta_api

router = APIRouter(prefix="/api/connect/meta", tags=["connect"])


@router.get("/start")
def start_meta_oauth(
    client_id: str,
    user: User = Depends(require_team),
    db: Session = Depends(get_db),
):
    """Team-only: begin OAuth for one client. The signed state token binds
    the eventual callback to this client so tokens can't land on the wrong
    tenant."""
    if db.get(Client, client_id) is None:
        raise HTTPException(404, "Unknown client")
    state = create_state_token("meta_oauth", client_id)
    return {"url": meta_api.build_oauth_url(state)}


@router.get("/callback")
def meta_oauth_callback(
    code: str, state: str, db: Session = Depends(get_db)
):
    # Unauthenticated by necessity (browser redirect from Meta); the signed
    # state token is the integrity check.
    try:
        client_id = decode_state_token(state, "meta_oauth")
    except pyjwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired OAuth state")

    token_data = meta_api.exchange_code_for_token(code)
    long_lived = meta_api.exchange_for_long_lived_token(token_data["access_token"])
    access_token = long_lived["access_token"]
    expires_in = long_lived.get("expires_in")

    me = meta_api.fetch_me(access_token)
    conn = connections.upsert_connection(
        db,
        client_id=client_id,
        platform=PLATFORM_META,
        access_token=access_token,
        expires_in_seconds=expires_in,
        scopes=meta_api.META_SCOPES,
        external_user_id=me.get("id"),
    )

    # Pull the ad accounts this token can see and attach them to the client.
    for acct in meta_api.fetch_ad_accounts(access_token):
        existing = db.execute(
            select(AdAccount).where(
                AdAccount.platform == PLATFORM_META,
                AdAccount.external_id == acct["id"],
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                AdAccount(
                    client_id=client_id,
                    connection_id=conn.id,
                    platform=PLATFORM_META,
                    external_id=acct["id"],
                    name=acct.get("name") or acct["id"],
                    currency=acct.get("currency"),
                    timezone=acct.get("timezone_name"),
                    status=str(acct.get("account_status")),
                )
            )
        elif existing.client_id != client_id:
            # Same ad account surfacing under a second client is a tenant
            # mixup — refuse rather than silently reassign.
            raise HTTPException(
                409,
                f"Meta ad account {acct['id']} is already connected to a "
                "different client",
            )
    db.commit()

    settings = get_settings()
    return RedirectResponse(
        f"{settings.frontend_origin}/clients/{client_id}?connected=meta"
    )
