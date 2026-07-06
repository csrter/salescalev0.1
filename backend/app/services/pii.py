"""PII normalization + SHA-256 hashing for server-side conversion uploads.

Verified against live platform documentation on 2026-07-06 — these rules
shift over time, so re-check the sources before extending:

- Meta: developers.facebook.com/docs/marketing-api/conversions-api/
  parameters/customer-information-parameters
- Google: developers.google.com/google-ads/api/docs/conversions/
  upload-identifiers

The two platforms normalize DIFFERENTLY (Google strips dots/plus-suffixes
from gmail addresses, Meta does not; Google wants E.164 phones with the
leading "+", Meta wants bare digits) — hence per-platform functions instead
of one shared normalizer. Hashes are lowercase hex SHA-256 of the UTF-8
normalized string on both platforms. Never hash IPs, user agents, fbc, or
fbp (Meta rejects matching on hashed values of those).
"""

import hashlib
import re
from typing import Optional


def sha256_hex(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


# --- Meta normalization (then SHA-256) ---


def meta_email(value: Optional[str]) -> Optional[str]:
    value = _clean(value)
    return sha256_hex(value.lower()) if value else None


def meta_phone(value: Optional[str], default_country_code: str = "1") -> Optional[str]:
    """Meta: digits only — no symbols/letters — no leading zeros, and the
    country code must be included. Numbers arriving as bare 10-digit US
    locals get the default country code prefixed; anything already carrying
    a country code (11+ digits or a "+" prefix) is passed through as digits.
    """
    value = _clean(value)
    if not value:
        return None
    had_plus = value.lstrip().startswith("+")
    digits = re.sub(r"\D", "", value).lstrip("0")
    if not digits:
        return None
    if not had_plus and len(digits) == 10 and default_country_code == "1":
        digits = "1" + digits
    return sha256_hex(digits)


def meta_name(value: Optional[str]) -> Optional[str]:
    """First/last name and city share the rule: lowercase, no punctuation."""
    value = _clean(value)
    if not value:
        return None
    return sha256_hex(re.sub(r"[^\w\s]", "", value.lower(), flags=re.UNICODE))


def meta_city(value: Optional[str]) -> Optional[str]:
    value = _clean(value)
    if not value:
        return None
    # City additionally drops spaces ("new york" → "newyork").
    return sha256_hex(re.sub(r"[^\w]", "", value.lower(), flags=re.UNICODE))


def meta_state(value: Optional[str]) -> Optional[str]:
    """2-character ANSI code, lowercase, for the US."""
    value = _clean(value)
    if not value:
        return None
    return sha256_hex(re.sub(r"[^a-z]", "", value.lower()))


def meta_zip(value: Optional[str]) -> Optional[str]:
    """Lowercase, no spaces/dashes; US zips use only the first 5 digits."""
    value = _clean(value)
    if not value:
        return None
    normalized = value.lower().replace(" ", "").replace("-", "")
    if re.fullmatch(r"\d{5,}", normalized):
        normalized = normalized[:5]
    return sha256_hex(normalized)


def meta_country(value: Optional[str]) -> Optional[str]:
    """Lowercase ISO 3166-1 alpha-2 ("us"), hashed like the rest."""
    value = _clean(value)
    if not value or len(value) != 2:
        return None
    return sha256_hex(value.lower())


def meta_external_id(value: Optional[str]) -> Optional[str]:
    # Hashing is recommended (not required) for external_id; we hash so raw
    # internal ids never leave the platform boundary.
    value = _clean(value)
    return sha256_hex(value) if value else None


# --- Google normalization (then SHA-256) ---


def google_email(value: Optional[str]) -> Optional[str]:
    """Trim + lowercase; for gmail.com/googlemail.com only, also strip dots
    and any +suffix from the local part — Google's spec, not Meta's."""
    value = _clean(value)
    if not value:
        return None
    value = value.lower()
    if "@" in value:
        local, _, domain = value.rpartition("@")
        if domain in ("gmail.com", "googlemail.com"):
            local = local.split("+", 1)[0].replace(".", "")
            value = f"{local}@{domain}"
    return sha256_hex(value)


def google_phone(value: Optional[str], default_country_code: str = "1") -> Optional[str]:
    """Google wants E.164 ("+16505551234") before hashing."""
    value = _clean(value)
    if not value:
        return None
    had_plus = value.lstrip().startswith("+")
    digits = re.sub(r"\D", "", value).lstrip("0")
    if not digits:
        return None
    if not had_plus and len(digits) == 10 and default_country_code == "1":
        digits = "1" + digits
    return sha256_hex(f"+{digits}")


def google_name(value: Optional[str]) -> Optional[str]:
    """First/last name: trim + lowercase, then hash."""
    value = _clean(value)
    return sha256_hex(value.lower()) if value else None
