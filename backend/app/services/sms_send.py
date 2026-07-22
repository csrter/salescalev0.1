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
import random
import uuid
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
    SMS_KIND_MANUAL,
    SMS_KIND_NOTIFICATION,
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_READ,
    SMS_MSG_SENT,
    SMS_SUPPRESS_CARRIER,
    SMS_TRIGGER_REPLY,
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
SPACING = "spacing"  # deferred by the per-account min-spacing throttle

# BlueBubbles sends go through a real Mac + Apple ID, so an account gets a
# conservative default pacing RANGE unless the operator sets their own — a
# personal iMessage sender machine-gunning texts is the fastest way to get
# the Apple ID flagged. Applied at account creation (both ends of the range).
BLUEBUBBLES_DEFAULT_SPACING_MIN_SECONDS = 20
BLUEBUBBLES_DEFAULT_SPACING_MAX_SECONDS = 45

_TWILIO_OPTED_OUT_CODE = "21610"
_STOP_FOOTER = "Reply STOP to opt out"

# Sendblue REST base. Their v1 docs show api.sendblue.co and the v2 overview
# shows api.sendblue.com; .co is the one the send-message reference documents,
# so it's the default — overridable via env if an org's account is on the
# other host. (Both accept the same sb-api-key-id / sb-api-secret-key auth.)
_SENDBLUE_BASE = "https://api.sendblue.co"
# Sendblue message.status values that mean the send did not succeed. Their
# docs: "any error_code besides 0 or null is a failure."
_SENDBLUE_FAIL_STATUSES = {"ERROR", "DECLINED"}


class SmsProviderError(Exception):
    """Network/API failure talking to the SMS provider — never a bare 500."""


# --- daily caps ---

# A message that went out counts against the daily cap regardless of where its
# delivery lifecycle has since moved: a status callback flips a row
# sent -> delivered -> read within seconds, so counting only SMS_MSG_SENT would
# let delivered/read messages fall OUT of the counter and make the cap (a TCPA
# volume + cost guardrail) effectively unbounded once receipts start flowing.
_COUNTED_SENT_STATUSES = (SMS_MSG_SENT, SMS_MSG_DELIVERED, SMS_MSG_READ)


def sends_today(db: Session, account: SmsAccount) -> int:
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.execute(
            select(func.count(SmsMessage.id)).where(
                SmsMessage.account_id == account.id,
                SmsMessage.direction == SMS_DIR_OUT,
                SmsMessage.status.in_(_COUNTED_SENT_STATUSES),
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
                SmsMessage.status.in_(_COUNTED_SENT_STATUSES),
                SmsMessage.created_at >= day_start,
            )
        ).scalar_one()
        or 0
    )


# --- send spacing (anti-detection pacing, BlueBubbles especially) ---


def _last_out_created_at(
    db: Session, account: SmsAccount
) -> Optional[dt.datetime]:
    """Timestamp of this account's most recent outbound message, tz-normalized
    to UTC-aware (SQLite hands back naive datetimes even for
    DateTime(timezone=True); Postgres returns aware)."""
    last = db.execute(
        select(SmsMessage.created_at)
        .where(
            SmsMessage.account_id == account.id,
            SmsMessage.direction == SMS_DIR_OUT,
        )
        .order_by(SmsMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    return last


def next_spacing_time(
    db: Session, account: SmsAccount, *, now: Optional[dt.datetime] = None
) -> dt.datetime:
    """When a spacing-deferred campaign send should next be attempted, so
    consecutive iMessages land at a human-irregular cadence rather than a
    fixed, obviously-scripted interval. Always strictly in the future.

    When BOTH min and max are configured (max > min), the gap is a uniform
    random point in [min, max] seconds — a real randomized range (e.g. the
    BlueBubbles default: anywhere from 20 to 45 seconds, picked fresh each
    time). With only min set, falls back to the older floor*1.0-1.8x jitter
    for backward compatibility with accounts configured before the range."""
    ref = now or utcnow()
    spacing_min = account.min_send_spacing_seconds or 0
    spacing_max = account.max_send_spacing_seconds
    last = _last_out_created_at(db, account)
    if spacing_max and spacing_max > spacing_min:
        gap = random.uniform(spacing_min, spacing_max)
    else:
        gap = spacing_min * random.uniform(1.0, 1.8)
    target = (last or ref) + dt.timedelta(seconds=gap)
    if target <= ref:
        # last + gap already passed (slow tick) — still keep a jittered gap off
        # `now` so we never fire two sends in the same instant.
        floor = spacing_min or 1
        target = ref + dt.timedelta(seconds=random.uniform(floor * 0.5, floor) + 1)
    return target


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


def apply_compliance_suffix(
    body: str, org_name: str, first_step: bool, include_footer: bool = True
) -> str:
    """CTIA: the first message of a program identifies the sender and carries
    opt-out language. Idempotent — templates that already include them are
    left alone. `include_footer=False` (a per-campaign, org-chosen setting)
    skips this entirely for known, already-consenting contacts — STOP
    handling itself is unaffected either way; this only controls the
    reminder text."""
    out = body.rstrip()
    if first_step and include_footer:
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


# --- Sendblue transport (iMessage/SMS; thin httpx client, BYO credentials) ---


def _sendblue_headers(account: SmsAccount) -> dict:
    return {
        "sb-api-key-id": account.account_sid,
        "sb-api-secret-key": decrypt_secret(account.auth_token_encrypted or ""),
        "Content-Type": "application/json",
    }


def _sendblue_send(
    account: SmsAccount, to_number: str, body: str
) -> Tuple[str, Optional[str], Optional[str]]:
    """One POST /api/send-message. Returns (message_handle, error_code,
    error_detail). Sendblue returns 2xx with the message object even for some
    provider-side failures, so the message's own status/error_code is the
    source of truth, not just the HTTP code. Raises SmsProviderError on a
    network-level failure."""
    base = (get_settings().sendblue_base_url or _SENDBLUE_BASE).rstrip("/")
    data = {
        "number": to_number,
        "content": body,
        "from_number": account.from_number or "",
    }
    api_base = (get_settings().api_base_url or "").rstrip("/")
    if api_base and not api_base.startswith("http://localhost") and account.webhook_token:
        # Sendblue's webhooks carry no documented signature header, so the
        # per-account token in the URL path is the authenticity check.
        data["status_callback"] = (
            f"{api_base}/api/sms/webhooks/sendblue/status/"
            f"{account.id}/{account.webhook_token}"
        )
    try:
        resp = httpx.post(
            f"{base}/api/send-message",
            json=data,
            headers=_sendblue_headers(account),
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise SmsProviderError(f"Sendblue is unreachable: {e}")
    payload = {}
    try:
        payload = resp.json()
    except Exception:
        pass
    if resp.status_code // 100 != 2:
        code = str(payload.get("error_code") or resp.status_code)
        detail = payload.get("error_message") or f"Sendblue HTTP {resp.status_code}"
        return "", code, detail
    # 2xx: trust the message object's own status/error_code.
    err_code = payload.get("error_code")
    status = (payload.get("status") or "").upper()
    if (err_code not in (0, None, "0")) or status in _SENDBLUE_FAIL_STATUSES:
        return (
            payload.get("message_handle", ""),
            str(err_code or status or "error"),
            payload.get("error_message") or f"Sendblue status {status}",
        )
    return payload.get("message_handle", ""), None, None


def _verify_sendblue(account: SmsAccount) -> Tuple[bool, str]:
    """Cheap authenticated probe — the iMessage service lookup against the
    account's own sending number requires valid credentials and is rate-limit
    cheap."""
    base = (get_settings().sendblue_base_url or _SENDBLUE_BASE).rstrip("/")
    number = account.from_number or "+15555550100"
    try:
        resp = httpx.get(
            f"{base}/api/evaluate-service",
            params={"number": number},
            headers=_sendblue_headers(account),
            timeout=15,
        )
    except httpx.HTTPError as e:
        return False, f"Sendblue is unreachable: {e}"
    if resp.status_code // 100 == 2:
        return True, "ok"
    if resp.status_code in (401, 403):
        return False, "Sendblue rejected the API Key ID / Secret Key."
    return False, f"Sendblue HTTP {resp.status_code}"


# --- BlueBubbles transport (self-hosted iMessage, dev/prototype provider;
# thin httpx client against the org's own VPS relay) ---


def _bb_json(resp) -> dict:
    try:
        return resp.json() or {}
    except Exception:
        return {}


def _bb_error_message(payload: dict) -> str:
    """BlueBubbles nests the real reason under error.message, with a generic
    top-level message ('Message Send Error'); prefer the specific one."""
    err = payload.get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    return str(payload.get("message") or "")


def _bb_chat_missing(resp, payload: dict) -> bool:
    """A first-ever message to a recipient fails with 500 'Chat does not
    exist!' because message/text targets an EXISTING chat guid. Detect that
    exact case so we can create the chat instead."""
    if resp.status_code != 500:
        return False
    text = _bb_error_message(payload) or ""
    if not text:
        try:
            text = resp.text
        except Exception:
            text = ""
    return "chat does not exist" in text.lower()


def _bluebubbles_resolve_service(base: str, pw: str, to_number: str) -> str:
    """iMessage where the number is registered, SMS otherwise. Cold-outreach
    lists are mostly plain cell numbers (not on iMessage); with Text Message
    Forwarding enabled on the host Mac (a paired iPhone), BlueBubbles sends
    those as green-bubble SMS via service 'SMS'. On any lookup failure we
    default to iMessage (the premium attempt) rather than silently downgrading
    everyone during a transient blip."""
    try:
        resp = httpx.get(
            f"{base}/api/v1/handle/availability/imessage",
            params={"password": pw, "address": to_number},
            timeout=12,
        )
    except httpx.HTTPError:
        return "iMessage"
    if resp.status_code // 100 == 2:
        available = (_bb_json(resp).get("data") or {}).get("available")
        return "iMessage" if available else "SMS"
    return "iMessage"


def _bluebubbles_send(
    account: SmsAccount, to_number: str, body: str
) -> Tuple[str, Optional[str], Optional[str]]:
    """Send via the org's BlueBubbles relay. The recipient's service (iMessage
    vs green-bubble SMS) is resolved first, so the chat guid uses the right
    service. For an EXISTING conversation we POST message/text (follow-up steps
    stay threaded); for a first-time recipient that chat doesn't exist yet
    (BlueBubbles 500s 'Chat does not exist!'), we fall back to chat/new which
    creates the conversation AND sends the opener in one call. Returns (message
    guid, error_code, error_detail). Raises SmsProviderError on a network-level
    failure talking to the relay."""
    base = (account.relay_url or "").rstrip("/")
    if not base:
        return "", "config", "No relay URL configured"
    pw = decrypt_secret(account.auth_token_encrypted or "")
    service = _bluebubbles_resolve_service(base, pw, to_number)
    data = {
        "chatGuid": f"{service};-;{to_number}",
        "tempGuid": str(uuid.uuid4()),
        "message": body,
        "method": "private-api",
    }
    try:
        resp = httpx.post(
            f"{base}/api/v1/message/text",
            params={"password": pw},
            json=data,
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise SmsProviderError(f"BlueBubbles is unreachable: {e}")
    payload = _bb_json(resp)
    if resp.status_code // 100 == 2:
        d = payload.get("data") or {}
        return d.get("guid") or data["tempGuid"], None, None
    if _bb_chat_missing(resp, payload):
        result = _bluebubbles_create_chat_send(base, pw, to_number, body, service)
    else:
        result = (
            "",
            str(payload.get("status") or resp.status_code),
            _bb_error_message(payload) or f"BlueBubbles HTTP {resp.status_code}",
        )
    # The iMessage-availability lookup can flap (observed live right after an
    # account re-registration: numbers that deliver blue-bubble fine resolve
    # as unavailable). A mislabeled recipient then goes down the SMS-service
    # path, which needs Text Message Forwarding — and without it fails with
    # "Failed to find all handles". Before accepting that failure, retry ONCE
    # as iMessage: it rescues every iMessage-capable recipient, and a true
    # green-bubble number just fails the same way it already had.
    if (
        service == "SMS"
        and result[1] is not None
        and "find all handles" in (result[2] or "").lower()
    ):
        retry = _bluebubbles_create_chat_send(base, pw, to_number, body, "iMessage")
        if retry[1] is None:
            return retry
    return result


def _bluebubbles_create_chat_send(
    base: str, pw: str, to_number: str, body: str, service: str = "iMessage"
) -> Tuple[str, Optional[str], Optional[str]]:
    """POST {relay}/api/v1/chat/new — creates the conversation and sends the
    first message on the resolved service. An iMessage attempt to a non-iMessage
    number, or an SMS attempt on a Mac without Text Message Forwarding, still
    fails here — a recipient-reachability / host-setup fact, not a transport
    bug — and surfaces the relay's own reason."""
    data = {
        "addresses": [to_number],
        "message": body,
        "method": "private-api",
        "service": service,
    }
    try:
        resp = httpx.post(
            f"{base}/api/v1/chat/new",
            params={"password": pw},
            json=data,
            timeout=25,
        )
    except httpx.HTTPError as e:
        raise SmsProviderError(f"BlueBubbles is unreachable: {e}")
    payload = _bb_json(resp)
    if resp.status_code // 100 == 2:
        d = payload.get("data") or {}
        guid = ""
        msgs = d.get("messages")
        if isinstance(msgs, list) and msgs:
            guid = (msgs[-1] or {}).get("guid") or ""
        return guid or d.get("guid") or "", None, None
    return (
        "",
        str(payload.get("status") or resp.status_code),
        _bb_error_message(payload) or f"BlueBubbles HTTP {resp.status_code}",
    )


def _verify_bluebubbles(account: SmsAccount) -> Tuple[bool, str]:
    """Cheap probe: GET {relay}/api/v1/ping — a 2xx means the relay is
    reachable and the server password is accepted."""
    base = (account.relay_url or "").rstrip("/")
    if not base:
        return False, "No relay URL configured."
    pw = decrypt_secret(account.auth_token_encrypted or "")
    try:
        resp = httpx.get(f"{base}/api/v1/ping", params={"password": pw}, timeout=15)
    except httpx.HTTPError as e:
        return False, f"BlueBubbles is unreachable: {e}"
    if resp.status_code // 100 == 2:
        return True, "ok"
    if resp.status_code in (401, 403):
        return False, "BlueBubbles rejected the server password."
    return False, f"BlueBubbles HTTP {resp.status_code}"


def _provider_send(
    account: SmsAccount, to_number: str, body: str
) -> Tuple[str, Optional[str], Optional[str]]:
    if account.provider == "bluebubbles":
        return _bluebubbles_send(account, to_number, body)
    if account.provider == "sendblue":
        return _sendblue_send(account, to_number, body)
    return _twilio_send(account, to_number, body)


def _verify_twilio(account: SmsAccount) -> Tuple[bool, str]:
    """Cheap credential probe: GET the Account resource."""
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


def verify_credentials(account: SmsAccount) -> Tuple[bool, str]:
    """Provider-dispatched credential probe. Returns (ok, detail). Never
    raises — used by the connect/test endpoint."""
    if account.provider == "bluebubbles":
        return _verify_bluebubbles(account)
    if account.provider == "sendblue":
        return _verify_sendblue(account)
    return _verify_twilio(account)


# --- channel health (account-level, all providers) ---

_CHANNEL_HEALTH_SAMPLE = 25


def channel_health(db: Session, account: SmsAccount) -> dict:
    """Rolls up the account's last 25 outbound sends into a single status —
    "healthy" | "degraded" | "blocked" — plus the raw counts driving it.
    `downgraded` is the green-bubble signal: an iMessage-capable provider
    (sendblue/bluebubbles) whose status webhook reported the message actually
    went out as SMS."""
    if account.status != SMS_ACCOUNT_ACTIVE:
        return {
            "status": "blocked",
            "sent": 0,
            "delivered": 0,
            "failed": 0,
            "downgraded": 0,
            "sampled": 0,
            "detail": account.error_detail or "Account not active",
        }
    rows = db.execute(
        select(SmsMessage.status, SmsMessage.service)
        .where(
            SmsMessage.account_id == account.id,
            SmsMessage.direction == SMS_DIR_OUT,
        )
        .order_by(SmsMessage.created_at.desc())
        .limit(_CHANNEL_HEALTH_SAMPLE)
    ).all()
    sampled = len(rows)
    sent = sum(1 for status, _ in rows if status == SMS_MSG_SENT)
    delivered = sum(
        1 for status, _ in rows if status in (SMS_MSG_DELIVERED, SMS_MSG_READ)
    )
    failed = sum(1 for status, _ in rows if status == SMS_MSG_FAILED)
    downgraded = sum(
        1 for _, service in rows if service and service.upper() == "SMS"
    )
    imessage_capable = account.provider in ("sendblue", "bluebubbles")

    if sampled == 0:
        return {
            "status": "healthy",
            "sent": sent,
            "delivered": delivered,
            "failed": failed,
            "downgraded": downgraded,
            "sampled": sampled,
            "detail": "No recent sends",
        }
    if sampled and failed / sampled >= 0.5:
        return {
            "status": "blocked",
            "sent": sent,
            "delivered": delivered,
            "failed": failed,
            "downgraded": downgraded,
            "sampled": sampled,
            "detail": "High recent failure rate",
        }
    if failed > 0 or (imessage_capable and downgraded > 0):
        reasons = []
        if imessage_capable and downgraded > 0:
            reasons.append("messages falling back to SMS")
        if failed > 0:
            reasons.append("recent send failures")
        return {
            "status": "degraded",
            "sent": sent,
            "delivered": delivered,
            "failed": failed,
            "downgraded": downgraded,
            "sampled": sampled,
            "detail": " and ".join(reasons).capitalize(),
        }
    return {
        "status": "healthy",
        "sent": sent,
        "delivered": delivered,
        "failed": failed,
        "downgraded": downgraded,
        "sampled": sampled,
        "detail": "Sending normally",
    }


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
    # A reply-step send answers the lead's OWN inbound message — a live
    # conversation, not cold volume. It is therefore exempt from the two
    # cold-outbound throttles below (campaign daily cap + min-spacing), the
    # same reasoning that already exempts a human's manual 1:1 reply:
    # timeliness IS the feature ("respond 3 minutes after they text back"),
    # reply volume is bounded by real inbound replies, and answering an
    # inbound quickly is the most human-looking pattern there is. The
    # consent gate, send window, and the ACCOUNT daily cap (the hard tenant
    # guardrail) still apply unconditionally.
    is_reply_response = (
        step is not None
        and (getattr(step, "trigger", None) or "schedule") == SMS_TRIGGER_REPLY
    )
    if campaign is not None and not in_send_window(campaign, now):
        return OUTSIDE_WINDOW, None
    if sends_today(db, account) >= account.daily_send_cap:
        return CAP_REACHED, None
    if (
        campaign is not None
        and not is_reply_response
        and campaign_sends_today(db, campaign) >= campaign.daily_cap
    ):
        return CAP_REACHED, None

    # Minimum spacing between sends on this account. This is the anti-detection
    # throttle for the BlueBubbles path especially: those sends go through a
    # real Mac + Apple ID, and machine-gun sending (many iMessages in seconds)
    # is exactly what Apple flags. Enforced here in the gateway — the one
    # adapter-agnostic layer — so it survives a provider swap. Only AUTOMATED
    # campaign sends are paced; a human's 1:1 reply in the inbox (campaign is
    # None) is already human-timed and is never throttled. A violation returns
    # SPACING, which the engine reschedules to a jittered short delay
    # (next_spacing_time) rather than the coarse cap backoff.
    spacing = account.min_send_spacing_seconds or 0
    if campaign is not None and spacing > 0 and not is_reply_response:
        last = _last_out_created_at(db, account)
        ref = now or utcnow()
        if last is not None and (ref - last).total_seconds() < spacing:
            return SPACING, None

    final_body = apply_compliance_suffix(
        body,
        org_name,
        first_step=(step is None or step.position == 1),
        # Manual sends (a live 1:1 conversation) never carry the footer —
        # it's a CTIA "first message of a program" convention, not something
        # a human reply/one-off text should repeat every time.
        include_footer=(campaign is not None and campaign.include_compliance_footer),
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
        sid, error_code, error_detail = _provider_send(account, to_number, final_body)
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
    if _is_opted_out_error(account, error_code, error_detail):
        # The provider knows this number opted out — converge our suppression
        # ledger with theirs so we never retry it (Twilio 21610 via Advanced
        # Opt-Out; Sendblue surfaces it in the error text / opted_out flag).
        sms_consent.record_opt_out(
            db,
            account.organization_id,
            to_number,
            SMS_SUPPRESS_CARRIER,
            detail=f"{account.provider}: recipient opted out at the provider "
            f"({error_code})",
        )
    db.flush()
    return FAILED, row


def send_notification(
    db: Session, account: SmsAccount, to_number: str, body: str
) -> Tuple[str, Optional[SmsMessage]]:
    """An alert to the agency's OWN team (services/lead_notify.py) — not
    lead outreach, so this deliberately skips the TCPA consent/suppression
    gate `send()` enforces for texting prospects (the recipient is an ops
    phone number the org itself configured, never a Contact) and carries no
    campaign/window/compliance-footer machinery. Still respects the
    account's daily cap and goes through the same provider dispatch +
    append-only ledger as every other send, so it shows up in the same
    number-health accounting."""
    if account.status != SMS_ACCOUNT_ACTIVE:
        return BLOCKED, None
    if sends_today(db, account) >= account.daily_send_cap:
        return CAP_REACHED, None

    row = SmsMessage(
        organization_id=account.organization_id,
        account_id=account.id,
        contact_id=None,
        direction=SMS_DIR_OUT,
        kind=SMS_KIND_NOTIFICATION,
        to_number=to_number,
        from_number=account.from_number,
        body=body,
    )
    try:
        sid, error_code, error_detail = _provider_send(account, to_number, body)
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
    db.flush()
    return FAILED, row


def send_reply(
    db: Session, account: SmsAccount, contact: Contact, body: str
) -> Tuple[str, Optional[SmsMessage]]:
    """A human reply to a lead who just texted us (services/lead_relay.py — the
    operator relaying from their phone). Deliberately SKIPS the opt-in gate
    that `send()` enforces for outbound prospecting — replying to someone who
    contacted you first is consented by their own initiation — but STILL
    honors suppression (a STOP always wins) and the account cap. Records the
    lead-linked outbound with kind=manual so it lands in that lead's
    conversation. No compliance footer, no send window (a live reply)."""
    if account.status != SMS_ACCOUNT_ACTIVE:
        return BLOCKED, None
    to_number = sms_consent.contact_sms_number(contact)
    if not to_number:
        return BLOCKED, None
    if sms_consent.is_suppressed(db, account.organization_id, to_number):
        return SUPPRESSED, None
    if sends_today(db, account) >= account.daily_send_cap:
        return CAP_REACHED, None

    row = SmsMessage(
        organization_id=account.organization_id,
        account_id=account.id,
        contact_id=contact.id,
        direction=SMS_DIR_OUT,
        kind=SMS_KIND_MANUAL,
        to_number=to_number,
        from_number=account.from_number,
        body=body,
    )
    try:
        sid, error_code, error_detail = _provider_send(account, to_number, body)
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
    db.flush()
    return FAILED, row


def _is_opted_out_error(
    account: SmsAccount, error_code: Optional[str], error_detail: Optional[str]
) -> bool:
    """Whether a provider send-failure means 'this recipient opted out'.
    Twilio has the explicit 21610 code; Sendblue has no documented equivalent
    code, so fall back to matching the opt-out language in its error text."""
    if account.provider == "sendblue":
        return "opt" in (error_detail or "").lower() and "out" in (
            error_detail or ""
        ).lower()
    return error_code == _TWILIO_OPTED_OUT_CODE
