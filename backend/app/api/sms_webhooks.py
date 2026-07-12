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
- Any other body → inbound SmsMessage row; enrollments with exit_on_reply
  exit with reason "replied".
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
from ..models.crm import Contact
from ..models.sms_outreach import (
    HELP_KEYWORDS,
    SMS_DIR_IN,
    SMS_ENROLL_ACTIVE,
    SMS_ENROLL_EXITED,
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_RECEIVED,
    SMS_SUPPRESS_STOP,
    STOP_KEYWORDS,
    SmsAccount,
    SmsCampaign,
    SmsEnrollment,
    SmsMessage,
)
from ..security import decrypt_secret
from ..services import sms_consent

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
    return [
        c
        for c in db.execute(
            select(Contact).where(Contact.organization_id == org_id)
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
) -> None:
    """Provider-agnostic inbound handling: record the message, and on STOP
    suppress + exit all enrollments org-wide; on a real (non-HELP) reply exit
    exit_on_reply campaigns. `forced_stop` lets a provider that flags opt-out
    structurally (Sendblue's opted_out=true) short-circuit keyword matching."""
    from_number = sms_consent.normalize_phone(from_raw) or ""
    lowered = body.lower().strip(" .!")
    contacts = _contacts_for_number(db, account.organization_id, from_number)

    db.add(
        SmsMessage(
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
        )
    )

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
        for c in contacts:
            for e in db.execute(
                select(SmsEnrollment).where(
                    SmsEnrollment.contact_id == c.id,
                    SmsEnrollment.status == SMS_ENROLL_ACTIVE,
                )
            ).scalars():
                campaign = db.get(SmsCampaign, e.campaign_id)
                if campaign is not None and campaign.exit_on_reply:
                    e.status = SMS_ENROLL_EXITED
                    e.exit_reason = "replied"
                    e.next_run_at = None
                    e.replied_at = utcnow()
                    e.ended_at = utcnow()


def _apply_status(
    db: Session, account: SmsAccount, sid: Optional[str], status: str, error_code
) -> None:
    """Provider-agnostic delivery-receipt handling. `status` is normalized to
    lowercase; 'delivered'/'sent' → delivered, failure words → failed."""
    if not sid:
        return
    row = db.execute(
        select(SmsMessage).where(
            SmsMessage.provider_sid == sid,
            SmsMessage.organization_id == account.organization_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return
    status = (status or "").lower()
    if status in ("delivered",):
        row.status = SMS_MSG_DELIVERED
    elif status in ("failed", "undelivered", "error", "declined"):
        row.status = SMS_MSG_FAILED
        if error_code is not None:
            row.error_code = str(error_code)


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
