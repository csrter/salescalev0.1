"""Account picker + reassignment for the ad-platform connect flows.

Why this exists: an agency's platform login sees many ad accounts (a Google
MCC's whole roster, a Meta Business Manager's client list). The OAuth
callback attaches nothing in that case — these endpoints let an Admin see
what the connection's token can reach (live, never cached) and assign each
account to the right client, or move an account that landed on the wrong
client profile.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import bind_integration_creds, require_admin
from ..models.core import (
    AdAccount,
    Client,
    PLATFORM_GOOGLE,
    PLATFORM_META,
    PlatformConnection,
    User,
)
from ..services import ad_accounts, connections as conn_svc
from ..services import google_ads_api, meta_api

router = APIRouter(
    prefix="/api", tags=["connect"], dependencies=[Depends(bind_integration_creds)]
)

_CONNECT_PLATFORMS = (PLATFORM_META, PLATFORM_GOOGLE)

_AUTH_ERRORS = (meta_api.MetaAuthError, google_ads_api.GoogleAuthError)
_API_ERRORS = (meta_api.MetaApiError, google_ads_api.GoogleApiError)


class AttachedTo(BaseModel):
    account_id: str
    client_id: str
    client_name: str


class ConnectableAccountOut(BaseModel):
    external_id: str
    name: str
    currency: Optional[str] = None
    timezone: Optional[str] = None
    status: Optional[str] = None
    # False when another Organization already holds this account (which one is
    # never disclosed).
    available: bool
    attached: Optional[AttachedTo] = None


class AttachAccountsIn(BaseModel):
    client_id: str
    external_ids: List[str] = Field(min_length=1, max_length=200)


class ReassignAccountIn(BaseModel):
    client_id: str


def _client_or_404(db: Session, user: User, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Unknown client")
    return client


def _active_connection(
    db: Session, user: User, client_id: str, platform: str
) -> PlatformConnection:
    if platform not in _CONNECT_PLATFORMS:
        raise HTTPException(404, "Unknown platform")
    conn = db.execute(
        select(PlatformConnection).where(
            PlatformConnection.organization_id == user.organization_id,
            PlatformConnection.client_id == client_id,
            PlatformConnection.platform == platform,
        )
    ).scalar_one_or_none()
    if conn is None or conn.status != "active":
        raise HTTPException(
            409,
            f"No active {platform} connection for this client — connect first"
            + (f": {conn.error_detail}" if conn and conn.error_detail else ""),
        )
    return conn


def _discover(db: Session, conn: PlatformConnection) -> List[dict]:
    try:
        return ad_accounts.discover(db, conn)
    except _AUTH_ERRORS as e:
        conn_svc.mark_disconnected(db, conn, str(e))
        raise HTTPException(
            502,
            f"{conn.platform} rejected the stored credentials — the connection "
            "has been marked disconnected and needs to be re-authorized",
        )
    except _API_ERRORS as e:
        raise HTTPException(502, f"{conn.platform} account listing failed: {e}")


@router.get(
    "/connect/{platform}/accounts", response_model=List[ConnectableAccountOut]
)
def list_connectable_accounts(
    platform: str,
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Everything the client's connection token can reach right now, annotated
    with where each account is already attached (same org only)."""
    _client_or_404(db, user, client_id)
    conn = _active_connection(db, user, client_id, platform)
    discovered = _discover(db, conn)
    return ad_accounts.annotate_attachment(
        db, user.organization_id, platform, discovered
    )


@router.post("/connect/{platform}/accounts")
def attach_accounts(
    platform: str,
    body: AttachAccountsIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Attach selected discoverable accounts to a client. Idempotent for
    accounts already attached in this org (they stay where they are — moving
    is the explicit PATCH /ad-accounts/{id}); 409 when another Organization
    holds one of them."""
    _client_or_404(db, user, body.client_id)
    conn = _active_connection(db, user, body.client_id, platform)
    # Re-discover live: proves the token can actually reach the requested
    # accounts and supplies their current details.
    by_ext = {d["external_id"]: d for d in _discover(db, conn)}
    unknown = [e for e in body.external_ids if e not in by_ext]
    if unknown:
        raise HTTPException(
            400, f"Not visible to this connection: {', '.join(sorted(unknown)[:5])}"
        )
    attached = 0
    skipped: List[str] = []
    for ext in dict.fromkeys(body.external_ids):  # de-dupe, keep order
        try:
            acct = ad_accounts.attach(
                db, user.organization_id, body.client_id, conn, by_ext[ext]
            )
        except PermissionError as e:
            db.rollback()
            raise HTTPException(409, str(e))
        if acct is None:
            skipped.append(ext)
            continue
        db.flush()
        ad_accounts.write_account_audit(
            db, user, acct, "attach", None, body.client_id
        )
        attached += 1
    db.commit()
    return {"attached": attached, "skipped": skipped}


@router.patch("/ad-accounts/{account_id}")
def reassign_ad_account(
    account_id: str,
    body: ReassignAccountIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Move an ad account to another client in the same Organization —
    the repair path when a multi-account connect landed accounts on the wrong
    client profile. Cascades the denormalized client_id across the cached
    campaign hierarchy, pending changes, insight history, and quality
    snapshots (see services/ad_accounts.reassign)."""
    account = db.get(AdAccount, account_id)
    if account is None or account.organization_id != user.organization_id:
        raise HTTPException(404, "Unknown ad account")
    _client_or_404(db, user, body.client_id)
    if account.client_id == body.client_id:
        return {"moved": False, "cascade": {}}
    before = account.client_id
    cascade = ad_accounts.reassign(db, account, body.client_id)
    ad_accounts.write_account_audit(
        db, user, account, "reassign", before, body.client_id
    )
    db.commit()
    return {"moved": True, "cascade": cascade}
