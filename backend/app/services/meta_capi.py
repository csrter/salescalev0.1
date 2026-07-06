"""Meta Conversions API: server-side events + Event Match Quality.

Verified against live docs 2026-07-06 (Graph API v25.0, same pin as
meta_api.py):
- POST /{dataset_id}/events with a `data` array; `test_event_code` rides
  top-level ONLY for testing (events surface in Events Manager's Test
  Events tool instead of production).
- Dedup against the browser Pixel is (event_name, event_id) within 48h —
  the Pixel fires with `eventID`, we send the identical `event_id` here.
- event_time is unix seconds, max 7 days old for action_source=website.
- EMQ: GET /dataset_quality?dataset_id=…&fields=web{event_match_quality,
  event_name} — composite_score is the 0–10 score Events Manager shows.

Hashing/normalization lives in pii.py; user_data built here only decides
WHICH keys go in. fbc/fbp/client_ip_address/client_user_agent are never
hashed.
"""

import datetime as dt
import json
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import pii
from .meta_api import _base, _check


def _utc(when: dt.datetime) -> dt.datetime:
    # SQLite hands naive datetimes back; all our timestamps are stored UTC.
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def format_fbc(fbclid: str, landing_time: dt.datetime) -> str:
    """Meta's _fbc cookie format, derived from a captured fbclid:
    fb.{subdomain_index}.{creation_time_ms}.{fbclid}. subdomain_index 1
    matches how the Pixel itself sets the cookie on a normal domain."""
    return f"fb.1.{int(_utc(landing_time).timestamp() * 1000)}.{fbclid}"


def event_time(when: dt.datetime) -> int:
    """Unix seconds; must be within 7 days for action_source=website."""
    return int(_utc(when).timestamp())


def build_user_data(
    lead: Dict[str, Any],
    fbc: Optional[str] = None,
    fbp: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Assemble the CAPI user_data block from raw lead fields, returning
    (user_data, match_keys) — match_keys is the PII-free list of which
    identifiers were included, for the dispatch log."""
    user_data: Dict[str, Any] = {}

    hashed = {
        "em": pii.meta_email(lead.get("email")),
        "ph": pii.meta_phone(lead.get("phone")),
        "fn": pii.meta_name(lead.get("first_name")),
        "ln": pii.meta_name(lead.get("last_name")),
        "ct": pii.meta_city(lead.get("city")),
        "st": pii.meta_state(lead.get("state")),
        "zp": pii.meta_zip(lead.get("zip"))
        or pii.meta_zip(lead.get("postal_code")),
        "country": pii.meta_country(lead.get("country")),
        "external_id": pii.meta_external_id(lead.get("external_id")),
    }
    for key, value in hashed.items():
        if value:
            # Meta accepts arrays for multi-value keys; single values are
            # sent as one-element arrays for em/ph per their examples.
            user_data[key] = [value] if key in ("em", "ph") else value

    # Unhashed by spec — hashing these breaks matching entirely.
    if fbc:
        user_data["fbc"] = fbc
    if fbp:
        user_data["fbp"] = fbp
    if lead.get("client_ip_address"):
        user_data["client_ip_address"] = lead["client_ip_address"]
    if lead.get("client_user_agent"):
        user_data["client_user_agent"] = lead["client_user_agent"]

    return user_data, sorted(user_data.keys())


def send_events(
    token: str,
    dataset_id: str,
    events: List[Dict[str, Any]],
    test_event_code: Optional[str] = None,
) -> Dict[str, Any]:
    """POST server events to the dataset. Each event must already carry
    event_name, event_time, event_id, action_source, and user_data."""
    payload: Dict[str, Any] = {"data": json.dumps(events)}
    if test_event_code:
        payload["test_event_code"] = test_event_code
    return _check(
        httpx.post(
            f"{_base()}/{dataset_id}/events",
            data={"access_token": token, **payload},
            timeout=30,
        )
    )


def fetch_event_match_quality(token: str, dataset_id: str) -> List[Dict[str, Any]]:
    """Per-event EMQ for a dataset — [{event_name, composite_score,
    match_keys: [{identifier, coverage_pct}]}]."""
    data = _check(
        httpx.get(
            f"{_base()}/dataset_quality",
            params={
                "access_token": token,
                "dataset_id": dataset_id,
                "fields": "web{event_match_quality,event_name}",
            },
            timeout=30,
        )
    )
    out = []
    for row in data.get("web", []):
        emq = row.get("event_match_quality") or {}
        out.append(
            {
                "event_name": row.get("event_name"),
                "composite_score": emq.get("composite_score"),
                "match_keys": [
                    {
                        "identifier": fb.get("identifier"),
                        "coverage_pct": (fb.get("coverage") or {}).get("percentage"),
                    }
                    for fb in emq.get("match_key_feedback", [])
                ],
            }
        )
    return out
