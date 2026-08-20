"""Carrier line-type lookup: can this number receive a text at all?

iMessage capability (services/imessage_check.py) answers "blue bubble or
green bubble". It does NOT answer this question — a landline is neither, and
Apple's IDS lookup simply reports it as not-registered, indistinguishable
from a perfectly good mobile that isn't on iMessage. Every SMS sent to a
landline is a silent, billed failure.

Mechanism: the org's OWN Telnyx or Twilio account, via that provider's number
lookup API. Two consequences worth stating plainly:

- This costs money PER LOOKUP (roughly a third of a cent), unlike the IDS
  lookup which is free. The caller is expected to skip numbers it already
  knows are real devices — see should_skip().
- It is BYO, resolved from the SmsAccount rows the org already connected, so
  Salescale never fronts a shared lookup key.

A failed lookup writes NOTHING (leaves sms_capable None) — same reasoning as
the iMessage checker: a stored capability flag that is wrong silently
misroutes a whole segment, so "unknown" must stay distinguishable from "no".

This is deliberately ADVISORY. It does not gate sending: sms_consent remains
the only thing that decides whether a send is allowed. A landline verdict is
there to let a human exclude those contacts from an audience, not to have the
engine quietly drop them on a provider's say-so.
"""

import logging
from typing import Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.sms_outreach import SMS_ACCOUNT_ACTIVE, SmsAccount
from ..security import decrypt_secret

log = logging.getLogger("salescale.line_lookup")

_TELNYX_BASE = "https://api.telnyx.com"
_TWILIO_LOOKUP_BASE = "https://lookups.twilio.com"

# Providers that expose a number-lookup API, in preference order. Telnyx first
# purely because its lookup is cheaper; both return the same normalized shape.
LOOKUP_PROVIDERS = ("telnyx", "twilio")

# Provider verdict -> our vocabulary. Anything unrecognized becomes "unknown"
# rather than being guessed at.
_TYPE_MAP = {
    "mobile": "mobile",
    "wireless": "mobile",
    "landline": "landline",
    "fixed line": "landline",
    "fixedvoip": "voip",
    "nonfixedvoip": "voip",
    "voip": "voip",
    "toll-free": "tollfree",
    "tollfree": "tollfree",
    "personal": "mobile",
}

# Line types that can actually receive an SMS. VoIP is deliberately True:
# plenty of small businesses run Google Voice / RingCentral numbers that do
# receive texts, so calling it undeliverable would cut real prospects.
TEXTABLE = {"mobile", "voip", "tollfree"}

# Verified against the live Telnyx API (2026-08-20) on six numbers whose real
# behaviour was already known from the send ledger: five leads that had replied
# to a text all returned "mobile", as did a known iMessage cell. One caveat
# found the same way — a CPaaS DID (Telnyx's own sending number) comes back
# "fixed line", so a business running RingCentral/Google Voice CAN be
# mislabelled undeliverable. That is why this stays advisory and never gates a
# send: carrier_name is stored alongside so a human can sanity-check a verdict
# that looks wrong.


def resolve_account(db: Session, organization_id: str) -> Optional[SmsAccount]:
    """The org's connected account that can perform lookups, or None. Prefers
    Telnyx, falls back to Twilio; ignores BlueBubbles/Sendblue, which have no
    lookup API at all."""
    rows = (
        db.execute(
            select(SmsAccount).where(
                SmsAccount.organization_id == organization_id,
                SmsAccount.status == SMS_ACCOUNT_ACTIVE,
            )
        )
        .scalars()
        .all()
    )
    for provider in LOOKUP_PROVIDERS:
        for a in rows:
            if a.provider == provider:
                return a
    return None


def should_skip(imessage_capable: Optional[bool]) -> bool:
    """A number registered with Apple is, by construction, a real device that
    receives messages — so paying for a carrier lookup on it is waste. Only
    numbers that are NOT on iMessage (or not yet checked) need the paid call."""
    return imessage_capable is True


def _normalize(raw_type: Optional[str]) -> str:
    return _TYPE_MAP.get((raw_type or "").strip().lower().replace("_", ""), "unknown")


def _telnyx_lookup(account: SmsAccount, e164: str) -> Optional[Tuple[str, Optional[str]]]:
    base = (get_settings().telnyx_base_url or _TELNYX_BASE).rstrip("/")
    token = decrypt_secret(account.auth_token_encrypted)
    resp = httpx.get(
        f"{base}/v2/number_lookup/{e164}",
        params={"type": "carrier"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code >= 400:
        log.warning("telnyx lookup %s -> HTTP %s", e164, resp.status_code)
        return None
    data = (resp.json() or {}).get("data") or {}
    carrier = data.get("carrier") or {}
    # valid_number is Telnyx's own "this number does not exist" verdict and is
    # unambiguous where the carrier type is not — surface it as its own type so
    # a disconnected line is never mistaken for a landline.
    if data.get("valid_number") is False:
        return "invalid", carrier.get("name")
    return _normalize(carrier.get("type")), carrier.get("name")


def _twilio_lookup(account: SmsAccount, e164: str) -> Optional[Tuple[str, Optional[str]]]:
    token = decrypt_secret(account.auth_token_encrypted)
    resp = httpx.get(
        f"{_TWILIO_LOOKUP_BASE}/v2/PhoneNumbers/{e164}",
        params={"Fields": "line_type_intelligence"},
        auth=(account.account_sid, token),
        timeout=15,
    )
    if resp.status_code >= 400:
        log.warning("twilio lookup %s -> HTTP %s", e164, resp.status_code)
        return None
    lti = (resp.json() or {}).get("line_type_intelligence") or {}
    return _normalize(lti.get("type")), lti.get("carrier_name")


def lookup(account: SmsAccount, e164: str) -> Optional[Tuple[str, Optional[str]]]:
    """(line_type, carrier_name) for one number, or None if the lookup failed.

    None means "we don't know" and the caller must write nothing — never
    conflate it with an undeliverable verdict."""
    try:
        if account.provider == "telnyx":
            return _telnyx_lookup(account, e164)
        if account.provider == "twilio":
            return _twilio_lookup(account, e164)
    except Exception:  # noqa: BLE001 — a lookup failure is never fatal
        log.exception("line lookup failed for %s", e164)
    return None
