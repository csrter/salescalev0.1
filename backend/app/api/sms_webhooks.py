"""Public Twilio webhooks for the SMS module (no session auth — Twilio calls
these). Authenticity comes from X-Twilio-Signature validation against the
account's own auth token, so a forged POST can't fake a STOP or a delivery
status. Both endpoints are per-account URLs; an unknown account id 404s.

Inbound handling is the compliance half of the module:
- STOP (any of models/sms_outreach.STOP_KEYWORDS, or Twilio's OptOutType=STOP
  when Advanced Opt-Out is active) → suppression row + sms_opt_in cleared on
  every matching contact + exit ALL of the contact's active SMS enrollments
  org-wide (mirrors the email unsubscribe rule).
- HELP → recorded; Twilio's own auto-responder answers it.
- Any other body → inbound SmsMessage row (stamped with the campaign/
  enrollment/step it replies to, for per-campaign reply tracking) and routed
  through services/sms_campaigns.handle_reply: campaigns with a
  reply-triggered step ahead schedule it (the timed, branch-matched
  response); otherwise exit_on_reply campaigns exit with reason "replied".
"""

import base64
import hashlib
import hmac
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Contact
from ..models.sms_outreach import (
    HELP_KEYWORDS,
    SMS_DIR_IN,
    SMS_DIR_OUT,
    SMS_ENROLL_ACTIVE,
    SMS_ENROLL_EXITED,
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_READ,
    SMS_MSG_RECEIVED,
    SMS_MSG_SENT,
    SMS_SUPPRESS_STOP,
    STOP_KEYWORDS,
    SmsAccount,
    SmsEnrollment,
    SmsMessage,
    advance_status,
)
from ..security import decrypt_secret
from ..services import crm, lead_relay, sms_campaigns, sms_consent

log = logging.getLogger("salescale.sms_outreach")

router = APIRouter(prefix="/api/sms/webhooks", tags=["sms-webhooks"])


def validate_twilio_signature(
    auth_token: str, url: str, params: dict, signature: str
) -> bool:
    """Twilio's documented scheme: HMAC-SHA1 over the full URL + the POST
    params concatenated key-sorted, base64-encoded."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(
        auth_token.encode(), payload.encode("utf-8"), hashlib.sha1
    ).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature or "")


async def _validated(request: Request, account: SmsAccount) -> dict:
    form = dict((await request.form()).items())
    signature = request.headers.get("X-Twilio-Signature", "")
    auth_token = decrypt_secret(account.auth_token_encrypted or "")
    if not validate_twilio_signature(auth_token, str(request.url), form, signature):
        raise HTTPException(403, "Invalid Twilio signature")
    return form


def _exit_contact_enrollments(db: Session, contact: Contact, reason: str) -> int:
    """Exit every ACTIVE SMS enrollment this contact has, org-wide."""
    rows = list(
        db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.organization_id == contact.organization_id,
                SmsEnrollment.contact_id == contact.id,
                SmsEnrollment.status == SMS_ENROLL_ACTIVE,
            )
        ).scalars()
    )
    for e in rows:
        e.status = SMS_ENROLL_EXITED
        e.exit_reason = reason
        e.next_run_at = None
        e.ended_at = utcnow()
    return len(rows)


def _contacts_for_number(db: Session, org_id: str, number: str) -> list:
    """Every contact in the org reachable at this number, OLDEST FIRST.

    The order matters: callers attribute the inbound message to contacts[0],
    so an unordered scan meant duplicate contacts sharing a phone attributed
    replies to an arbitrary one — and a different one on a re-run. Oldest-first
    is the stable, defensible choice (the original lead record wins)."""
    return [
        c
        for c in db.execute(
            select(Contact)
            .where(Contact.organization_id == org_id)
            .order_by(Contact.created_at.asc(), Contact.id.asc())
        ).scalars()
        if sms_consent.contact_sms_number(c) == number
    ]


def _process_inbound(
    db: Session,
    account: SmsAccount,
    *,
    from_raw: Optional[str],
    to_raw: Optional[str],
    body: str,
    provider_sid: Optional[str],
    forced_stop: bool = False,
    create_missing: bool = False,
    service: Optional[str] = None,
) -> None:
    """Provider-agnostic inbound handling: record the message, and on STOP
    suppress + exit all enrollments org-wide; on a real (non-HELP) reply exit
    exit_on_reply campaigns. `forced_stop` lets a provider that flags opt-out
    structurally (Sendblue's opted_out=true) short-circuit keyword matching.

    `create_missing` (used by the iMessage/BlueBubbles webhook — an
    unsolicited iMessage is often the FIRST contact we have with a number, so
    there's no existing lead to attach it to) creates a minimal Contact under
    the org's house CRM when no contact matches the sender. This is never
    TCPA consent — sms_opt_in stays at its default False — and nothing gets
    auto-enrolled; it just makes the inbound thread visible in the CRM.
    `service` (iMessage/SMS/RCS) is recorded on the row for channel-health
    reporting (an iMessage-capable provider falling back to green/SMS)."""
    from_number = sms_consent.normalize_phone(from_raw) or ""
    lowered = body.lower().strip(" .!")

    # Idempotency. Providers retry a webhook on any non-2xx (and BlueBubbles
    # can re-fire new-message on its own), so without this a retry creates a
    # duplicate ledger row AND re-runs handle_reply — which re-schedules the
    # reply step and texts the lead a second time.
    if provider_sid:
        # Sessions are autoflush=False, so a row added earlier in THIS session
        # would be invisible to the check below. Retries normally arrive as
        # separate requests, but a provider can also double-fire inside one.
        db.flush()
        already = db.execute(
            select(SmsMessage.id).where(
                SmsMessage.provider_sid == provider_sid,
                SmsMessage.organization_id == account.organization_id,
                SmsMessage.direction == SMS_DIR_IN,
            )
        ).first()
        if already:
            return

    # Relay: a text FROM the operator's relay phone is a command to reply to a
    # lead (tagged with the lead's code), never a lead message — route it and
    # stop before any lead/STOP/enrollment handling.
    org = db.get(Organization, account.organization_id)
    if lead_relay.is_operator(org, account, from_number):
        lead_relay.handle_operator_reply(db, account, from_number, body)
        return

    contacts = _contacts_for_number(db, account.organization_id, from_number)

    if not contacts and create_missing and from_number:
        house_client = crm.get_or_create_house_client(db, account.organization_id)
        new_contact = Contact(
            organization_id=account.organization_id,
            client_id=house_client.id,
            phone=from_number,
            source=f"imessage:{account.provider}",
        )
        db.add(new_contact)
        db.flush()
        contacts = [new_contact]

    row = SmsMessage(
        organization_id=account.organization_id,
        account_id=account.id,
        contact_id=contacts[0].id if contacts else None,
        direction=SMS_DIR_IN,
        kind="inbound",
        to_number=sms_consent.normalize_phone(to_raw) or "",
        from_number=from_number,
        body=body,
        status=SMS_MSG_RECEIVED,
        provider_sid=provider_sid,
        service=service,
        # Flag automated out-of-office replies at ingest so campaign stats can
        # separate real human engagement from auto-responder noise. STOP is a
        # keyword opt-out, never an auto-reply.
        is_auto_reply=(not forced_stop and lowered not in STOP_KEYWORDS)
        and sms_campaigns.is_auto_reply(body),
    )
    db.add(row)

    if (forced_stop or lowered in STOP_KEYWORDS) and from_number:
        sms_consent.record_opt_out(
            db,
            account.organization_id,
            from_number,
            SMS_SUPPRESS_STOP,
            detail=f"Inbound: {body[:100]}",
        )
        for c in contacts:
            _exit_contact_enrollments(db, c, "opted_out")
    elif lowered not in HELP_KEYWORDS:
        # Route the reply through the campaign engine: reply-triggered steps
        # get scheduled (the timed, branch-matched response); exit_on_reply
        # campaigns with no reply step exit "replied", as before. The
        # returned linkage stamps this inbound row with the campaign/
        # enrollment/step it replied to — per-campaign reply tracking.
        linkage = None
        for c in contacts:
            routed = sms_campaigns.handle_reply(db, c, body)
            linkage = linkage or routed
        if linkage is not None:
            row.campaign_id = linkage["campaign_id"]
            row.enrollment_id = linkage["enrollment_id"]
            row.step_id = linkage["step_id"]
        # Forward a genuine lead reply to the operator's phone (best-effort).
        # STOP is handled above and not forwarded (the lead opted out — there's
        # nothing to reply to); an automated out-of-office auto-responder isn't
        # a real reply either, so it's not forwarded (no operator ping for bots).
        if contacts and not sms_campaigns.is_auto_reply(body):
            lead_relay.forward_to_operator(db, account, contacts[0], body)


# Provider status vocabularies → our ladder. Anything absent here is a
# no-op, deliberately: an unknown word must never move a row.
_STATUS_PROGRESS = {
    # Twilio, Sendblue, Telnyx all use these three spellings.
    "sent": SMS_MSG_SENT,
    "delivered": SMS_MSG_DELIVERED,
    "read": SMS_MSG_READ,
}
_STATUS_FAILURE = {
    "failed",
    "error",
    "declined",
    # Twilio: the carrier rejected it after Twilio accepted (spam filter,
    # unreachable handset) — distinct cause from a Twilio-side failure, so it
    # gets its own detail line below even though both end at 'failed'.
    "undelivered",
    "canceled",
    "cancelled",
    # Telnyx terminal failures (message.finalized).
    "delivery_failed",
    "sending_failed",
    "expired",
}
# Reported when a carrier returns no DLR at all. NOT a failure — the message
# very likely arrived — but it must not be counted as a confirmed delivery
# either, so it records the fact and leaves the status where it is.
_STATUS_UNCONFIRMED = {"delivery_unconfirmed"}

_FAILURE_DETAIL = {
    "undelivered": "Carrier rejected the message after the provider accepted it",
    "canceled": "Send was canceled before delivery",
    "cancelled": "Send was canceled before delivery",
    "expired": "Provider gave up before the carrier accepted it",
    "declined": "Recipient's device or carrier declined the message",
}


def _apply_status(
    db: Session,
    account: SmsAccount,
    sid: Optional[str],
    status: str,
    error_code,
    error_detail: Optional[str] = None,
    service: Optional[str] = None,
) -> None:
    """Provider-agnostic delivery-receipt handling, shared by all four
    providers. `status` is normalized to lowercase and mapped through
    _STATUS_* above; every write goes through advance_status so an
    out-of-order callback can't walk a row backwards (a late 'delivered'
    after a 'read' used to drop the read out of analytics entirely)."""
    if not sid:
        return
    # Scoped to this account and to OUTBOUND rows. Inbound rows carry a
    # provider_sid too, and matching one would rewrite read_at — which means
    # something different by direction (recipient-read vs our-team-read).
    # .first() not .scalar_one_or_none(): duplicate SIDs are reachable (the
    # BlueBubbles duplicate-send rescue probe can return an existing message's
    # guid), and raising here 500s the webhook, which makes the provider retry
    # forever and loses every later receipt for that message.
    row = db.execute(
        select(SmsMessage)
        .where(
            SmsMessage.provider_sid == sid,
            SmsMessage.organization_id == account.organization_id,
            SmsMessage.account_id == account.id,
            SmsMessage.direction == SMS_DIR_OUT,
        )
        .order_by(SmsMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return

    if service and not row.service:
        row.service = str(service)[:20]

    status = (status or "").lower()
    now = utcnow()

    if status in _STATUS_UNCONFIRMED:
        row.error_detail = row.error_detail or (
            "Carrier returned no delivery confirmation"
        )
        return

    if status in _STATUS_FAILURE:
        before = row.status
        row.status = advance_status(row.status, SMS_MSG_FAILED)
        if row.status == SMS_MSG_FAILED and before != SMS_MSG_FAILED:
            row.failed_at = row.failed_at or now
        if row.status == SMS_MSG_FAILED:
            if error_code is not None:
                row.error_code = str(error_code)[:20]
            detail = error_detail or _FAILURE_DETAIL.get(status)
            if detail:
                row.error_detail = str(detail)[:500]
        return

    mapped = _STATUS_PROGRESS.get(status)
    if mapped is None:
        return
    row.status = advance_status(row.status, mapped)
    if mapped == SMS_MSG_READ and row.status == SMS_MSG_READ:
        row.read_at = row.read_at or now
    if mapped in (SMS_MSG_READ, SMS_MSG_DELIVERED):
        # A read implies delivery, so both receipts stamp delivered_at.
        row.delivered_at = row.delivered_at or now
    # A success report supersedes an earlier failure guess (advance_status
    # allows that transition) — clear the stale reason so the failure
    # breakdown doesn't keep counting a message that demonstrably arrived.
    if row.status in (SMS_MSG_DELIVERED, SMS_MSG_READ):
        row.error_code = None
        row.error_detail = None
        row.failed_at = None


def _require_token(account: SmsAccount, token: str) -> None:
    """Constant-time check of the per-account URL secret used by providers
    without request signing (Sendblue)."""
    expected = account.webhook_token or ""
    if not expected or not hmac.compare_digest(expected, token or ""):
        raise HTTPException(403, "Invalid webhook token")


# --- Twilio (signature-authenticated) ---


@router.post("/inbound/{account_id}")
async def inbound(account_id: str, request: Request):
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, account_id)
        if account is None:
            raise HTTPException(404, "Not found")
        form = await _validated(request, account)
        _process_inbound(
            db,
            account,
            from_raw=form.get("From"),
            to_raw=form.get("To"),
            body=(form.get("Body") or "").strip(),
            provider_sid=form.get("MessageSid"),
            forced_stop=form.get("OptOutType") == "STOP",
        )
        db.commit()
    finally:
        db.close()
    # Empty TwiML: acknowledge without auto-replying (Twilio's opt-out
    # confirmation / HELP responder handles those messages itself).
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


@router.post("/status/{account_id}")
async def status_callback(account_id: str, request: Request):
    """Delivery receipts: move the ledger row queued/sent → delivered/failed."""
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, account_id)
        if account is None:
            raise HTTPException(404, "Not found")
        form = await _validated(request, account)
        _apply_status(
            db,
            account,
            form.get("MessageSid"),
            form.get("MessageStatus") or "",
            form.get("ErrorCode"),
        )
        db.commit()
    finally:
        db.close()
    return {"ok": True}


# --- Sendblue (token-authenticated; no documented signature header) ---


@router.post("/sendblue/inbound/{account_id}/{token}")
async def sendblue_inbound(account_id: str, token: str, request: Request):
    """Sendblue inbound-message webhook. JSON body; from_number/content/
    message_handle/opted_out fields. Authed by the per-account URL token."""
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, account_id)
        if account is None:
            raise HTTPException(404, "Not found")
        _require_token(account, token)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        _process_inbound(
            db,
            account,
            from_raw=payload.get("from_number"),
            to_raw=payload.get("to_number") or payload.get("sendblue_number"),
            body=(payload.get("content") or "").strip(),
            provider_sid=payload.get("message_handle"),
            forced_stop=bool(payload.get("opted_out")),
        )
        db.commit()
    finally:
        db.close()
    return {"ok": True}


@router.post("/sendblue/status/{account_id}/{token}")
async def sendblue_status(account_id: str, token: str, request: Request):
    """Sendblue status callback. JSON body; message_handle/status/error_code."""
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, account_id)
        if account is None:
            raise HTTPException(404, "Not found")
        _require_token(account, token)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        # A status callback can also be where Sendblue reports a post-send
        # opt-out (opted_out=true) — converge our ledger if so.
        if payload.get("opted_out") and payload.get("from_number") is None:
            number = sms_consent.normalize_phone(payload.get("number"))
            if number:
                sms_consent.record_opt_out(
                    db,
                    account.organization_id,
                    number,
                    SMS_SUPPRESS_STOP,
                    detail="Sendblue reported opted_out on a status callback",
                )
                for c in _contacts_for_number(db, account.organization_id, number):
                    _exit_contact_enrollments(db, c, "opted_out")
        _apply_status(
            db,
            account,
            payload.get("message_handle"),
            payload.get("status") or "",
            payload.get("error_code"),
        )
        db.commit()
    finally:
        db.close()
    return {"ok": True}


# --- Telnyx (v2 webhooks; token-authenticated URL, same posture as Sendblue) ---
#
# Telnyx DOES sign webhooks (Ed25519 over "timestamp|body" with a portal
# public key), but that key is account-level portal config with no home in
# SmsAccount, so authenticity here rests on the unguessable per-account token
# in the URL path — the mechanism already proven for Sendblue. Adding
# signature verification later means storing the public key and checking it
# in _require_token's place; nothing else in this file changes.


def _telnyx_event(payload: dict) -> tuple[str, dict]:
    """Telnyx wraps everything as {data: {event_type, payload}}."""
    data = payload.get("data") or {}
    return (data.get("event_type") or ""), (data.get("payload") or {})


def _telnyx_number(value) -> Optional[str]:
    """`from` is an object; `to` is a list of objects. Both carry
    phone_number."""
    if isinstance(value, dict):
        return value.get("phone_number")
    if isinstance(value, list) and value:
        first = value[0]
        return first.get("phone_number") if isinstance(first, dict) else None
    return value if isinstance(value, str) else None


@router.post("/telnyx/inbound/{account_id}/{token}")
async def telnyx_inbound(account_id: str, token: str, request: Request):
    """Telnyx inbound-message webhook (event_type message.received)."""
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, account_id)
        if account is None:
            raise HTTPException(404, "Not found")
        _require_token(account, token)
        try:
            body_json = await request.json()
        except Exception:
            body_json = {}
        event_type, data = _telnyx_event(body_json)
        # The same URL may receive delivery events if the operator pointed the
        # profile's single webhook here — route them rather than drop them.
        if event_type.startswith("message.") and event_type != "message.received":
            recipients = data.get("to") or []
            status = (recipients[0].get("status") if recipients else "") or ""
            errors = data.get("errors") or []
            _apply_status(
                db,
                account,
                data.get("id"),
                status,
                (errors[0] or {}).get("code") if errors else None,
            )
        elif event_type == "message.received":
            _process_inbound(
                db,
                account,
                from_raw=_telnyx_number(data.get("from")),
                to_raw=_telnyx_number(data.get("to")),
                body=(data.get("text") or "").strip(),
                provider_sid=data.get("id"),
            )
        db.commit()
    finally:
        db.close()
    return {"ok": True}


@router.post("/telnyx/status/{account_id}/{token}")
async def telnyx_status(account_id: str, token: str, request: Request):
    """Telnyx delivery receipts (message.sent / message.finalized). The
    per-recipient status under `to` is the outcome; `errors` carries the
    failure code."""
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, account_id)
        if account is None:
            raise HTTPException(404, "Not found")
        _require_token(account, token)
        try:
            body_json = await request.json()
        except Exception:
            body_json = {}
        event_type, data = _telnyx_event(body_json)
        if event_type == "message.received":
            # Profile pointed both event types at this URL — handle it.
            _process_inbound(
                db,
                account,
                from_raw=_telnyx_number(data.get("from")),
                to_raw=_telnyx_number(data.get("to")),
                body=(data.get("text") or "").strip(),
                provider_sid=data.get("id"),
            )
        else:
            recipients = data.get("to") or []
            status = (recipients[0].get("status") if recipients else "") or ""
            errors = data.get("errors") or []
            _apply_status(
                db,
                account,
                data.get("id"),
                status,
                (errors[0] or {}).get("code") if errors else None,
            )
        db.commit()
    finally:
        db.close()
    return {"ok": True}
