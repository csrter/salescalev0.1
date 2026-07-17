"""Timezone normalization for campaign send windows.

zoneinfo can only load real IANA keys (e.g. "America/Phoenix"). It CANNOT
load the common US abbreviations users type — "MST", "EST", "PST" — and in a
slim container those aren't even in tzdata, so `ZoneInfo("MST")` raises
ZoneInfoNotFoundError. Every campaign _tz() used to swallow that and fall
back to UTC, which silently shifted the send window by the real offset: an
"MST" campaign with an 8am-5pm window sent 1am-10am its own morning, so it
looked like it "wasn't sending" (parked to the next UTC window) — the exact
Q2 CPA symptom. For SMS this is worse: the TCPA quiet-hours guard would run
in the wrong zone and could text outside 8am-9pm local.

normalize() canonicalizes at save time (schema validators reject the
truly-unresolvable); resolve() is the runtime lookup that also rescues any
abbreviation already stored on an existing campaign, so no data migration is
needed for those.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("salescale.timezones")

# Common (non-IANA) abbreviations → a representative, DST-aware IANA city
# zone. We deliberately do NOT invent a fixed offset: a user typing "EST" in
# July means US Eastern, which is on EDT then — America/New_York gets that
# right. An org that genuinely wants no-DST (Arizona) picks America/Phoenix.
_ABBREV = {
    "UTC": "UTC", "GMT": "UTC", "Z": "UTC",
    "ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
    "CT": "America/Chicago", "CST": "America/Chicago", "CDT": "America/Chicago",
    "MT": "America/Denver", "MST": "America/Denver", "MDT": "America/Denver",
    "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "AKST": "America/Anchorage", "AKDT": "America/Anchorage",
    "HST": "Pacific/Honolulu", "HT": "Pacific/Honolulu",
}


def normalize(name: Optional[str]) -> Optional[str]:
    """Canonical IANA name for `name`, or None when unresolvable. Maps the
    common abbreviations; otherwise the value must load as a real IANA key."""
    if not name or not name.strip():
        return None
    s = name.strip()
    mapped = _ABBREV.get(s.upper())
    if mapped:
        return mapped
    try:
        ZoneInfo(s)
        return s
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def resolve(name: Optional[str]) -> dt.tzinfo:
    """Runtime tzinfo, tolerating legacy abbreviations already stored on a
    campaign. Falls back to UTC (logged, not silent) only when truly
    unresolvable — new saves can't reach that state (validators reject it)."""
    canonical = normalize(name)
    if canonical is None:
        if name:
            log.warning("unresolvable campaign timezone %r; falling back to UTC", name)
        return dt.timezone.utc
    return ZoneInfo(canonical)
