"""Meta Lead Ads retrieval (Instant Forms), verified against live docs
2026-07-06 (Graph API v25.0, developers.facebook.com — "Retrieving Leads"
+ "Webhooks Getting Started"):

- Webhook verification: Meta GETs the endpoint with hub.mode=subscribe,
  hub.verify_token (must equal our configured token) and hub.challenge
  (echo it back as the response body).
- Delivery: POST with an X-Hub-Signature-256 header, "sha256=" + the hex
  SHA-256 HMAC of the RAW request body keyed by the app secret. Envelope:
  {"object": "page", "entry": [{"id": page_id, "changes": [{"field":
  "leadgen", "value": {leadgen_id, page_id, form_id, ad_id, adgroup_id,
  created_time}}]}]}.
- The webhook value carries ids only — the actual answers come from
  GET /{version}/{leadgen_id}?fields=... (requires leads_retrieval), whose
  field_data is [{"name": ..., "values": [...]}].
"""

import hashlib
import hmac
from typing import Any, Dict, Optional

from .meta_api import _base, _get

# Standard Instant Form field names → our contact fields. Custom questions
# come through under advertiser-chosen names and are kept in source_detail.
_FIELD_MAP = {
    "email": "email",
    "phone_number": "phone",
    "first_name": "first_name",
    "last_name": "last_name",
    "full_name": "full_name",
}

LEAD_FIELDS = "created_time,ad_id,adset_id,campaign_id,form_id,field_data"


def verify_signature(app_secret: str, raw_body: bytes, header: Optional[str]) -> bool:
    """X-Hub-Signature-256 check — constant-time compare, computed over the
    raw bytes (re-serializing parsed JSON would break the HMAC)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256=") :], expected)


def fetch_lead(access_token: str, leadgen_id: str) -> Dict[str, Any]:
    """Pull the submitted answers for one lead. Monkeypatched in tests; in
    production this needs the connection's token to carry leads_retrieval."""
    return _get(
        f"{_base()}/{leadgen_id}",
        {"access_token": access_token, "fields": LEAD_FIELDS},
    )


def parse_field_data(lead: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """field_data → {email, phone, first_name, last_name}. A full_name
    answer splits on the first space when first/last weren't asked."""
    out: Dict[str, Optional[str]] = {
        "email": None,
        "phone": None,
        "first_name": None,
        "last_name": None,
    }
    full_name = None
    for item in lead.get("field_data") or []:
        values = item.get("values") or []
        value = values[0] if values else None
        if not value:
            continue
        key = _FIELD_MAP.get((item.get("name") or "").lower())
        if key == "full_name":
            full_name = value
        elif key:
            out[key] = value
    if full_name and not out["first_name"]:
        parts = full_name.split(" ", 1)
        out["first_name"] = parts[0]
        if len(parts) > 1 and not out["last_name"]:
            out["last_name"] = parts[1]
    return out
