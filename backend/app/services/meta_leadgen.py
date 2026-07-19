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
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import meta_api
from ..models.crm import LeadFormConfig
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
    # leadgen_id comes from the (signature-verified) webhook body; require it to
    # be numeric so it can't manipulate the Graph URL path/query.
    if not str(leadgen_id).isdigit():
        raise ValueError("Invalid leadgen_id")
    return _get(
        f"{_base()}/{leadgen_id}",
        {"access_token": access_token, "fields": LEAD_FIELDS},
    )


def subscribe_client_pages(
    db: Session,
    *,
    organization_id: str,
    client_id: str,
    user_access_token: str,
) -> Dict[str, List[str]]:
    """On Meta connect, subscribe every Page the user manages to the app's
    `leadgen` webhook AND register a LeadFormConfig so incoming Instant Form
    leads route to this client.

    Best-effort: never raises — a missing page permission (pre-reconnect /
    pre-App-Review) or a single bad Page must not fail the connect flow.
    Tenant-safe: a page already routed to a DIFFERENT org, or to a sibling
    client in the same org, is left untouched (we never hijack an assignment
    an admin already made). Does not commit — the caller owns the transaction.
    """
    subscribed: List[str] = []
    routed: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    try:
        pages = meta_api.fetch_pages_with_tokens(user_access_token)
    except Exception as e:  # no pages_show_list yet, or API down — non-fatal
        return {"subscribed": [], "routed": [], "skipped": [], "errors": [str(e)]}

    for page in pages:
        page_id = str(page.get("id") or "")
        if not page_id:
            continue
        page_token = page.get("access_token")
        if page_token:
            try:
                meta_api.subscribe_page_leadgen(page_token, page_id)
                subscribed.append(page_id)
            except Exception as e:  # e.g. pages_manage_metadata not granted yet
                errors.append(f"{page_id}: {e}")

        # Only AUTO-ROUTE when the login manages exactly ONE Page. An agency
        # Meta account manages many clients' (and its own) Pages; routing them
        # all to the one client being connected would land other businesses'
        # leads in this client's CRM. With >1 Page, subscribe them (delivery)
        # but leave routing to the admin, who maps each Page to its client via
        # the CRM lead-form routing card.
        if len(pages) != 1:
            skipped.append(page_id)
            continue

        existing = db.execute(
            select(LeadFormConfig).where(
                LeadFormConfig.platform == "meta",
                LeadFormConfig.external_key == page_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                LeadFormConfig(
                    organization_id=organization_id,
                    client_id=client_id,
                    platform="meta",
                    external_key=page_id,
                    enabled=True,
                )
            )
            routed.append(page_id)
        elif (
            existing.organization_id == organization_id
            and existing.client_id == client_id
        ):
            existing.enabled = True  # refresh a prior mapping for this client
            routed.append(page_id)
        else:
            # Another org, or a sibling client already owns this page's routing.
            skipped.append(page_id)
    return {
        "subscribed": subscribed,
        "routed": routed,
        "skipped": skipped,
        "errors": errors,
    }


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
