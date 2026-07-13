"""iMessage channel webhooks (BlueBubbles + Sendblue aliases).

This lives alongside — not instead of — sms_webhooks.py: iMessage is a third
provider (`"bluebubbles"`, plus the existing `"sendblue"`) in the same SMS
Outreach module, so inbound/status handling reuses sms_webhooks' provider-
agnostic `_process_inbound` / `_apply_status` helpers rather than
reimplementing them. No new tables, no new engine.

Routes:
- POST /api/webhooks/imessage/bluebubbles/{account_id} — BlueBubbles (self-
  hosted, reached via a VPS relay) new-message + updated-message webhook.
  Auth is a shared secret (the account's own `webhook_token`), since
  BlueBubbles has no request-signing scheme: header
  `X-Salescale-Webhook-Secret` (what the VPS relay/Caddy injects) or a
  `?secret=` query param fallback (BlueBubbles' own webhook config can only
  point at a static URL, so it can't send a custom header itself — the relay
  is what adds the header in front of it; the query param covers a direct,
  relay-less setup).
- POST /api/webhooks/imessage/sendblue/inbound/{account_id}/{token} and
  .../sendblue/status/{account_id}/{token} — additive aliases for the
  EXISTING Sendblue webhooks already live under
  /api/sms/webhooks/sendblue/inbound|status/{account_id}/{token}. Either URL
  works; these exist so "the iMessage channel" has one documented webhook
  namespace. The status alias also captures `service`/`was_downgraded`
  (iMessage falling back to green/SMS — a degraded-channel signal) onto the
  matched SmsMessage row, since the canonical Sendblue status route doesn't
  need that for plain SMS/Twilio accounts.
"""

import hmac
import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models.sms_outreach import SmsAccount, SmsMessage
from .sms_webhooks import _apply_status, _process_inbound
from .sms_webhooks import sendblue_inbound as _sendblue_inbound
from .sms_webhooks import sendblue_status as _sendblue_status

log = logging.getLogger("salescale.sms_outreach")

router = APIRouter(prefix="/api/webhooks/imessage", tags=["imessage-webhooks"])


def _require_bb_secret(account: SmsAccount, request: Request) -> None:
    """BlueBubbles has no request-signing scheme, so authenticity is a
    shared secret carried either as a header (what the VPS relay/Caddy
    injects in front of the self-hosted BlueBubbles server) or a `?secret=`
    query param (BlueBubbles' own webhook config can only set a static URL,
    so a relay-less setup needs the secret embedded in the URL itself)."""
    supplied = (
        request.headers.get("X-Salescale-Webhook-Secret")
        or request.query_params.get("secret")
        or ""
    )
    expected = account.webhook_token or ""
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(403, "Invalid webhook secret")


# --- BlueBubbles (shared-secret authenticated) ---


@router.post("/bluebubbles/{account_id}")
async def bluebubbles_webhook(account_id: str, request: Request):
    """BlueBubbles posts every event (new-message, updated-message, and
    others we don't act on) to this one URL; `type` in the body picks the
    branch. Own-echo messages (isFromMe=true) are ignored."""
    db: Session = SessionLocal()
    try:
        account = db.get(SmsAccount, account_id)
        if account is None:
            raise HTTPException(404, "Not found")
        _require_bb_secret(account, request)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        typ = payload.get("type")
        data = payload.get("data") or {}

        if typ == "new-message" and not data.get("isFromMe"):
            handle = data.get("handle") or {}
            _process_inbound(
                db,
                account,
                from_raw=handle.get("address"),
                to_raw=account.from_number,
                body=(data.get("text") or "").strip(),
                provider_sid=data.get("guid"),
                create_missing=True,
                service="iMessage",
            )
        elif typ == "updated-message":
            if data.get("error"):
                status = "failed"
            elif data.get("dateRead"):
                status = "read"
            elif data.get("dateDelivered"):
                status = "delivered"
            else:
                status = None
            if status:
                _apply_status(db, account, data.get("guid"), status, data.get("error"))
        db.commit()
    finally:
        db.close()
    return {"ok": True}


# --- Sendblue (token-authenticated) — additive aliases for the canonical
# /api/sms/webhooks/sendblue/... routes in sms_webhooks.py. Either URL works;
# these delegate to the SAME handler functions (imported above) rather than
# re-implementing STOP/opt-out/enrollment-exit handling by hand, so there's
# no risk of the alias drifting from the canonical behavior. ---


@router.post("/sendblue/inbound/{account_id}/{token}")
async def sendblue_inbound_alias(account_id: str, token: str, request: Request):
    """Additive alias for POST /api/sms/webhooks/sendblue/inbound/{account_id}/
    {token} — identical behavior, just under the iMessage webhook namespace."""
    return await _sendblue_inbound(account_id, token, request)


@router.post("/sendblue/status/{account_id}/{token}")
async def sendblue_status_alias(account_id: str, token: str, request: Request):
    """Additive alias for POST /api/sms/webhooks/sendblue/status/{account_id}/
    {token}, plus capturing the iMessage/SMS/RCS `service` (and the
    was_downgraded fallback — iMessage falling back to green/SMS is a
    degraded-channel signal) onto the matched SmsMessage row for channel-
    health reporting. The canonical status route doesn't need this for plain
    Twilio/SMS accounts, so it stays here rather than growing
    `_apply_status`'s shared signature. Request.json() is cached by
    Starlette, so re-reading it after delegating doesn't re-parse the body."""
    result = await _sendblue_status(account_id, token, request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    handle = payload.get("message_handle")
    service = payload.get("service") or (
        "SMS" if payload.get("was_downgraded") else None
    )
    if handle and service:
        db: Session = SessionLocal()
        try:
            account = db.get(SmsAccount, account_id)
            if account is not None:
                row = db.execute(
                    select(SmsMessage).where(
                        SmsMessage.provider_sid == handle,
                        SmsMessage.organization_id == account.organization_id,
                    )
                ).scalar_one_or_none()
                if row is not None:
                    row.service = service
                    db.commit()
        finally:
            db.close()
    return result
