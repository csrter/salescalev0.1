"""The one gateway every outbound SMS goes through (mirrors
services/email_outreach_send.py — same single-choke-point rule that makes the
compliance guardrails enforceable in one place):

Ordered guards, checked server-side on EVERY send:
1. account active
2. suppression — a STOP'd number can never be texted through any path
3. the shared consent gate (services/sms_consent.assert_can_sms — TCPA
   prior-express-written-consent record required)
4. quiet hours — campaign sends only go out inside the campaign's send
   window/days in the campaign's timezone (defaults keep continental-US
   recipients inside TCPA's 8am–9pm local); manual sends (human replies in a
   live conversation) skip the window but never the consent/suppression gates
5. per-account + per-campaign daily caps

Every attempt — sent or failed — is an append-only SmsMessage row (audit
trail + the monthly entitlement meter). CTIA sender identification + opt-out
language: step-1 campaign bodies are suffixed with the org name and
"Reply STOP to opt out" when the template doesn't already carry them.

Transport is Twilio's REST API via httpx (thin-client style like
services/places.py — no SDK dependency). Twilio error 21610 ("attempt to
message an opted-out recipient") writes a carrier suppression row so our
ledger converges with Twilio Advanced Opt-Out's.
"""

import datetime as dt
import logging
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.base import utcnow
from ..models.crm import Contact
from ..models.sms_outreach import (
    SMS_ACCOUNT_ACTIVE,
    SMS_DIR_OUT,
    SMS_KIND_CAMPAIGN,
    SMS_MSG_FAILED,
    SMS_MSG_SENT,
    SMS_SUPPRESS_CARRIER,
    SmsAccount,
    SmsCampaign,
    SmsMessage,
    SmsStep,
)
from ..security import decrypt_secret
from . import sms_consent

log = logging.getLogger("salescale.sms_outreach")

# send() result codes callers (and the engine) branch on — mirrors email.
SENT = "sent"
FAILED = "failed"
SUPPRESSED = "suppressed"
BLOCKED = "blocked"
CAP_REACHED = "cap"
OUTSIDE_WINDOW = "window"

_TWILIO_OPTED_OUT_CODE = "21610"
_STOP_FOOTER = "Reply STOP to opt out"


class SmsProviderError(Exception):
    """Network/API failure talking to the SMS provider — never a bare 500."""


# --- daily caps ---


def sends_today(db: Session, account: SmsAccount) -> int:
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.execute(
            select(func.count(SmsMessage.id)).where(
                SmsMessage.account_id == account.id,
                SmsMessage.direction == SMS_DIR_OUT,
                SmsMessage.status == SMS_MSG_SENT,
                SmsMessage.created_at >= day_start,
            )
        ).scalar_one()
        or 0
    )


def campaign_sends_today(db: Session, campaign: SmsCampaign) -> int:
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.execute(
            select(func.count(SmsMessage.id)).where(
                SmsMessage.campaign_id == campaign.id,
                SmsMessage.direction == SMS_DIR_OUT,
                SmsMessage.status == SMS_MSG_SENT,
                SmsMessage.created_at >= day_start,
            )
        ).scalar_one()
        or 0
    )


# --- quiet hours (TCPA) ---


def in_send_window(campaign: SmsCampaign, now: Optional[dt.datetime] = None) -> bool:
    """True when `now` falls on an allowed day inside the campaign's hour
    window, evaluated in the CAMPAIGN's timezone. This is the quiet-hours
    guard — the engine also checks it, but the gateway re-checks so no code
    path can text at 6am."""
    now = now or utcnow()
    try:
        local = now.astimezone(ZoneInfo(campaign.timezone or "America/New_York"))
    except Exception:
        local = now
    days = campaign.send_days if campaign.send_days is not None else [0, 1, 2, 3, 4]
    if local.weekday() not in days:
        return False
    return campaign.send_window_start <= local.hour < campaign.send_window_end


# --- compliance body suffix ---


def apply_compliance_suffix(body: str, org_name: str, first_step: bool) -> str:
    """CTIA: the first message of a program identifies the sender and carries
    opt-out language. Idempotent — templates that already include them are
    left alone."""
    out = body.rstrip()
    if first_step:
        lowered = out.lower()
        if org_name and org_name.lower() not in lowered:
            out = f"{org_name}: {out}"
        if "stop" not in lowered:
            out = f"{out}\n{_STOP_FOOTER}"
    return out


# --- Twilio transport (thin httpx client, BYO credentials only) ---


def _twilio_send(
    account: SmsAccount, to_number: str, body: str
) -> Tuple[str, Optional[str], Optional[str]]:
    """One Messages.json POST. Returns (provider_sid, error_code,
    error_detail) — error fields None on success. Raises SmsProviderError on
    network-level failure."""
    auth_token = decrypt_secret(account.auth_token_encrypted or "")
    data = {"To": to_number, "Body": body}
    if account.messaging_service_sid:
        data["MessagingServiceSid"] = account.messaging_service_sid
    else:
        data["From"] = account.from_number or ""
    settings = get_settings()
    base = (settings.api_base_url or "").rstrip("/")
    if base and not base.startswith("http://localhost"):
        data["StatusCallback"] = f"{base}/api/sms/webhooks/status/{account.id}"
    try:
        resp = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account.account_sid}/Messages.json",
            data=data,
            auth=(account.account_sid, auth_token),
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise SmsProviderError(f"Twilio is unreachable: {e}")
    payload = {}
    try:
        payload = resp.json()
    except Exception:
        pass
    if resp.status_code // 100 == 2:
        return payload.get("sid", ""), None, None
    code = str(payload.get("code") or resp.status_code)
    detail = payload.get("message") or f"Twilio HTTP {resp.status_code}"
    return "", code, detail


def verify_credentials(account: SmsAccount) -> Tuple[bool, str]:
    """Cheap credential probe: GET the Account resource. Returns (ok, detail).
    Never raises — used by the connect/test endpoint."""
    auth_token = decrypt_secret(account.auth_token_encrypted or "")
    try:
        resp = httpx.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{account.account_sid}.json",
            auth=(account.account_sid, auth_token),
            timeout=15,
        )
    except httpx.HTTPError as e:
        return False, f"Twilio is unreachable: {e}"
    if resp.status_code // 100 == 2:
        return True, "ok"
    if resp.status_code == 401:
        return False, "Twilio rejected the Account SID / Auth Token."
    return False, f"Twilio HTTP {resp.status_code}"


# --- THE gateway ---


def send(
    db: Session,
    account: SmsAccount,
    contact: Contact,
    body: str,
    *,
    kind: str = SMS_KIND_CAMPAIGN,
    campaign: Optional[SmsCampaign] = None,
    step: Optional[SmsStep] = None,
    enrollment_id: Optional[str] = None,
    org_name: str = "",
    now: Optional[dt.datetime] = None,
) -> Tuple[str, Optional[SmsMessage]]:
    """Send one SMS through every guard. Returns (result_code, ledger_row) —
    ledger_row is None only when the send was refused before any attempt
    (blocked/suppressed/cap/window), so refusals never consume quota or leave
    a false audit trail of provider attempts."""
    if account.status != SMS_ACCOUNT_ACTIVE:
        return BLOCKED, None
    try:
        to_number = sms_consent.assert_can_sms(db, contact)
    except sms_consent.SmsBlockedError as e:
        ok, reason = sms_consent.sendable(db, contact)
        del ok
        log.info("sms send refused (%s): contact=%s — %s", reason, contact.id, e)
        return (SUPPRESSED if reason == "suppressed" else BLOCKED), None
    if campaign is not None and not in_send_window(campaign, now):
        return OUTSIDE_WINDOW, None
    if sends_today(db, account) >= account.daily_send_cap:
        return CAP_REACHED, None
    if campaign is not None and campaign_sends_today(db, campaign) >= campaign.daily_cap:
        return CAP_REACHED, None

    final_body = apply_compliance_suffix(
        body,
        org_name,
        first_step=(step is None or step.position == 1),
    )

    row = SmsMessage(
        organization_id=account.organization_id,
        account_id=account.id,
        campaign_id=campaign.id if campaign else None,
        step_id=step.id if step else None,
        enrollment_id=enrollment_id,
        contact_id=contact.id,
        direction=SMS_DIR_OUT,
        kind=kind,
        to_number=to_number,
        from_number=account.from_number,
        body=final_body,
    )
    try:
        sid, error_code, error_detail = _twilio_send(account, to_number, final_body)
    except SmsProviderError as e:
        row.status = SMS_MSG_FAILED
        row.error_detail = str(e)
        db.add(row)
        db.flush()
        return FAILED, row
    if error_code is None:
        row.status = SMS_MSG_SENT
        row.provider_sid = sid
        db.add(row)
        db.flush()
        return SENT, row
    row.status = SMS_MSG_FAILED
    row.error_code = error_code
    row.error_detail = error_detail
    db.add(row)
    if error_code == _TWILIO_OPTED_OUT_CODE:
        # Twilio knows this number opted out (Advanced Opt-Out) — converge
        # our ledger with theirs so we never retry it.
        sms_consent.record_opt_out(
            db,
            account.organization_id,
            to_number,
            SMS_SUPPRESS_CARRIER,
            detail="Twilio 21610 — recipient opted out at the carrier level",
        )
    db.flush()
    return FAILED, row
