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


@router.post("/inbound/{account_id}")
async def inbound(account_id: str, request: Request):
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, account_id)
        if account is None:
            raise HTTPException(404, "Not found")
        form = await _validated(request, account)
        from_number = sms_consent.normalize_phone(form.get("From")) or ""
        body = (form.get("Body") or "").strip()
        lowered = body.lower().strip(" .!")
        contacts = _contacts_for_number(db, account.organization_id, from_number)

        db.add(
            SmsMessage(
                organization_id=account.organization_id,
                account_id=account.id,
                contact_id=contacts[0].id if contacts else None,
                direction=SMS_DIR_IN,
                kind="inbound",
                to_number=sms_consent.normalize_phone(form.get("To")) or "",
                from_number=from_number,
                body=body,
                status=SMS_MSG_RECEIVED,
                provider_sid=form.get("MessageSid"),
            )
        )

        is_stop = lowered in STOP_KEYWORDS or form.get("OptOutType") == "STOP"
        if is_stop and from_number:
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
            # A real reply: stop the sequence for exit_on_reply campaigns.
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
        sid = form.get("MessageSid")
        status = (form.get("MessageStatus") or "").lower()
        if sid:
            row = db.execute(
                select(SmsMessage).where(
                    SmsMessage.provider_sid == sid,
                    SmsMessage.organization_id == account.organization_id,
                )
            ).scalar_one_or_none()
            if row is not None:
                if status == "delivered":
                    row.status = SMS_MSG_DELIVERED
                elif status in ("failed", "undelivered"):
                    row.status = SMS_MSG_FAILED
                    row.error_code = form.get("ErrorCode")
                db.commit()
    finally:
        db.close()
    return {"ok": True}
