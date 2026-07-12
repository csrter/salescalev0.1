"""Google Places API (New) — Text Search for the Lead Finder (Phase 12).

Same thin-httpx style as meta_api.py. Uses the current Places API (New)
REST surface (places.googleapis.com/v1) with an explicit FieldMask: Google
bills Text Search by the highest SKU tier any requested field belongs to,
so the mask below requests exactly what the Lead Finder displays/stores and
nothing else (phone/website/rating put us in the Enterprise SKU either way;
adding e.g. reviews or opening hours would not change the price, but the
mask keeps the payload honest and the billing intent auditable).

Caching policy (checked against Google's Places policies page, 2026):
place IDs may be stored indefinitely; every other field must not be cached
server-side. So: search results are returned to the caller for display and
never persisted — only the query text + result count land in the
lead_finder_searches ledger, and place data is stored only when the user
imports a business into their CRM.

Key resolution is BYO-first: the Organization's own key (IntegrationCredential
provider="google_places"), falling back to the operator's global
GOOGLE_PLACES_API_KEY.
"""

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx

# Overridable for local verification against a stub server only — never set
# in any deployed environment.
_TEXT_SEARCH_URL = os.environ.get(
    "PLACES_TEXT_SEARCH_URL", "https://places.googleapis.com/v1/places:searchText"
)

# Exactly the fields the feature displays/stores (task 2): name, category,
# phone, website, rating, address — plus the indefinitely-cacheable place id
# and the pagination token (nextPageToken sits in the IDs-Only SKU tier, so
# requesting it never changes the billed tier).
_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.types",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "nextPageToken",
    ]
)

MAX_RESULTS = 20  # Text Search (New) page size ceiling
MAX_TOTAL_RESULTS = 60  # documented ceiling across all pages (3 pages of 20)


class PlacesError(Exception):
    pass


class PlacesNotConfigured(PlacesError):
    """No API key available (neither org BYO nor operator global)."""


@dataclass
class PlaceResult:
    place_id: str
    name: str
    address: Optional[str]
    phone: Optional[str]
    website: Optional[str]
    rating: Optional[float]
    types: List[str]


def search_text(
    query: str,
    location: Optional[str],
    api_key: str,
    *,
    min_rating: Optional[float] = None,
    open_now: bool = False,
    page_token: Optional[str] = None,
) -> Tuple[List[PlaceResult], Optional[str]]:
    """One Text Search (New) request — a single page of up to 20 results
    plus the token for the next page (None when there isn't one; Google caps
    the whole result set at MAX_TOTAL_RESULTS).

    `location` is folded into the text query ("HVAC contractors in
    Scottsdale AZ") — the API's server-side geocoding of the combined query
    is the documented pattern for city-level search and avoids a separate
    Geocoding call per search.

    Filters are page-invariant: a pageToken request must carry the exact
    same textQuery/minRating/openNow as the request that issued the token,
    so callers must pass identical arguments when paging."""
    if not api_key:
        raise PlacesNotConfigured(
            "Google Places is not configured — connect your organization's "
            "API key or set GOOGLE_PLACES_API_KEY."
        )
    text = f"{query} in {location}" if location else query
    payload: dict = {"textQuery": text, "pageSize": MAX_RESULTS}
    if min_rating:
        # The API accepts 0–5 at a 0.5 cadence; snap rather than 400.
        payload["minRating"] = min(5.0, max(0.0, round(min_rating * 2) / 2))
    if open_now:
        payload["openNow"] = True
    if page_token:
        payload["pageToken"] = page_token
    try:
        resp = httpx.post(
            _TEXT_SEARCH_URL,
            json=payload,
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": _FIELD_MASK,
            },
            timeout=15,
        )
    except httpx.HTTPError as e:
        # Network-level failure normalizes to PlacesError so the API's 502
        # handling covers an unreachable Places endpoint too.
        raise PlacesError(f"Places API is unreachable: {e}")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", {}).get("message")
        except Exception:
            detail = None
        raise PlacesError(detail or f"Places API HTTP {resp.status_code}")
    data = resp.json()
    out: List[PlaceResult] = []
    for p in data.get("places", []):
        out.append(
            PlaceResult(
                place_id=p.get("id", ""),
                name=(p.get("displayName") or {}).get("text", ""),
                address=p.get("formattedAddress"),
                phone=p.get("nationalPhoneNumber"),
                website=p.get("websiteUri"),
                rating=p.get("rating"),
                types=p.get("types", []),
            )
        )
    return [r for r in out if r.place_id and r.name], data.get("nextPageToken") or None
