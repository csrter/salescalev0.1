"""Instagram messaging/comments webhook (Outreach module).

Same trust model as the Meta leadgen webhook: one app-level public endpoint,
authenticity = X-Hub-Signature-256 HMAC (operator app secret first, then the
BYO app secrets of orgs owning the entry ids named in the still-untrusted
payload), tenant routing = entry.id matched against instagram_accounts —
unknown accounts are acknowledged and dropped, never guessed."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models.outreach import InstagramAccount
from ..ratelimit import rate_limit
from ..services import integration_creds, meta_leadgen, outreach_ingest

router = APIRouter(prefix="/api/webhooks", tags=["outreach-webhooks"])

_webhook_limit = rate_limit("ig_webhook", limit=240, window_seconds=60)
_MAX_FALLBACK_IDS = 20


@router.get("/meta/instagram")
def instagram_verify(request: Request):
    """Meta's one-time subscription handshake (same verify token as leadgen —
    it's an app-level setting, not a per-product one)."""
    params = request.query_params
    settings = get_settings()
    if (
        params.get("hub.mode") == "subscribe"
        and settings.meta_webhook_verify_token
        and params.get("hub.verify_token") == settings.meta_webhook_verify_token
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "Verification failed")


def _verify_signature(db: Session, raw: bytes, signature) -> bool:
    settings = get_settings()
    if settings.meta_app_secret and meta_leadgen.verify_signature(
        settings.meta_app_secret, raw, signature
    ):
        return True
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return False
    entry_ids = {str(e.get("id") or "") for e in (body.get("entry") or [])}
    entry_ids.discard("")
    if not entry_ids:
        return False
    accounts = (
        db.execute(
            select(InstagramAccount).where(
                InstagramAccount.ig_user_id.in_(list(entry_ids)[:_MAX_FALLBACK_IDS])
            )
        )
        .scalars()
        .all()
    )
    tried: set[str] = set()
    for account in accounts:
        secret = integration_creds.resolve_meta(db, account.organization_id).app_secret
        if secret and secret not in tried:
            tried.add(secret)
            if meta_leadgen.verify_signature(secret, raw, signature):
                return True
    return False


@router.post("/meta/instagram")
async def instagram_webhook(
    request: Request, db: Session = Depends(get_db), _: None = _webhook_limit
):
    raw = await request.body()
    if not _verify_signature(db, raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "Invalid signature")
    body = json.loads(raw)
    results = outreach_ingest.process_webhook_body(db, body)
    db.commit()
    # Always 200 once the signature checks out — Meta redelivers on non-2xx.
    return {"received": len(results), "results": results}
