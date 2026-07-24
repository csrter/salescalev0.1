"""Apify Google Maps scraper — an alternate Lead Finder search source.

BYO ONLY: the Organization connects its OWN Apify API token
(integration_creds provider "apify"); there is deliberately no operator
fallback, mirroring Hunter/Apollo — the scrape runs on the org's own Apify
account, under the org's own account terms, and Salescale never operates a
shared scraping key. Guardrail 6 holds: nothing here touches any Meta
surface. Runs cost the org's Apify credits, NOT the monthly Places search
quota — the ledger row for an Apify search is written with pages_fetched=0.

Runs are ASYNC by design: the actor typically takes one to several minutes
per search, far beyond the frontend api() timeout, so the API starts a run
and the UI polls (start_run -> check_run). The actor returns Google place
IDs, so results flow through the SAME import pipeline as Places results —
idempotent re-import and org-wide dedupe work unchanged.
"""

import os
from typing import List, Optional, Tuple

import httpx

from .places import PlaceResult

# Env-overridable for local stub verification only — never set in deployments
# (same pattern as places.PLACES_TEXT_SEARCH_URL).
APIFY_BASE_URL = os.environ.get("APIFY_BASE_URL", "https://api.apify.com")
# Apify's canonical Google Maps Scraper actor (compass/crawler-google-places).
ACTOR_ID = os.environ.get("APIFY_GMAPS_ACTOR", "compass~crawler-google-places")
# Ceiling per search — a runaway maxCrawledPlaces is pure spend on the org's
# Apify account and a giant, slow run; 200 is plenty for a prospecting pass.
MAX_RESULTS = 200

# Apify run statuses that mean "give up" (anything else non-SUCCEEDED = still
# working: READY, RUNNING, ...).
_TERMINAL_FAILURES = ("FAILED", "ABORTED", "ABORTING", "TIMED-OUT")


class ApifyError(Exception):
    pass


class ApifyNotConfigured(ApifyError):
    """No org token connected (there is no operator fallback by design)."""


def _require_token(token: Optional[str]) -> None:
    if not token:
        raise ApifyNotConfigured(
            "Apify is not connected — add your organization's Apify API token "
            "under Data providers to use the Google Maps scraper."
        )


def _request(method: str, url: str, token: str, **kwargs) -> httpx.Response:
    # Normalize network-level failures into ApifyError so an unreachable API
    # surfaces through existing 502 handling instead of a bare 500 (the same
    # posture as MetaApiError/GoogleApiError/PlacesError).
    try:
        resp = httpx.request(method, url, params={"token": token}, **kwargs)
    except httpx.HTTPError as e:
        raise ApifyError(f"could not reach Apify: {e}")
    if resp.status_code == 401:
        raise ApifyError("Apify rejected the API token — check it under Data providers")
    if resp.status_code == 404:
        raise ApifyError("Apify run not found")
    if resp.status_code >= 400:
        raise ApifyError(f"Apify returned HTTP {resp.status_code}")
    return resp


def start_run(
    query: str,
    location: Optional[str],
    token: Optional[str],
    max_results: int = 60,
) -> str:
    """Kick off one scraper run and return Apify's run id (polled via
    check_run). Never blocks on the scrape itself."""
    _require_token(token)
    run_input: dict = {
        "searchStringsArray": [query],
        "maxCrawledPlacesPerSearch": max(1, min(int(max_results), MAX_RESULTS)),
        "language": "en",
        "skipClosedPlaces": True,
    }
    if location:
        run_input["locationQuery"] = location
    resp = _request(
        "POST",
        f"{APIFY_BASE_URL}/v2/acts/{ACTOR_ID}/runs",
        token,
        json=run_input,
        timeout=30,
    )
    run_id = (resp.json().get("data") or {}).get("id")
    if not run_id:
        raise ApifyError("Apify did not return a run id")
    return str(run_id)


def check_run(run_id: str, token: Optional[str]) -> Tuple[str, List[PlaceResult]]:
    """One poll: (status, results). Results are non-empty only once the run
    has SUCCEEDED; a terminal failure raises ApifyError; anything else means
    keep polling."""
    _require_token(token)
    resp = _request(
        "GET", f"{APIFY_BASE_URL}/v2/actor-runs/{run_id}", token, timeout=30
    )
    data = resp.json().get("data") or {}
    status = str(data.get("status") or "UNKNOWN")
    if status in _TERMINAL_FAILURES:
        raise ApifyError(f"Apify run {status.lower()}")
    if status != "SUCCEEDED":
        return status, []
    dataset_id = data.get("defaultDatasetId")
    if not dataset_id:
        raise ApifyError("Apify run finished without a dataset")
    items_resp = _request(
        "GET",
        f"{APIFY_BASE_URL}/v2/datasets/{dataset_id}/items",
        token,
        timeout=60,
    )
    items = items_resp.json()
    if not isinstance(items, list):
        raise ApifyError("unexpected Apify dataset shape")
    results = [
        _to_place(item)
        for item in items
        if isinstance(item, dict) and (item.get("title") or item.get("name"))
    ]
    return "SUCCEEDED", results


def _to_place(item: dict) -> PlaceResult:
    """Map one actor dataset item onto the shared PlaceResult shape. The
    actor emits Google's own placeId, which keeps import idempotency and
    CRM dedupe identical to the Places path; the rare item without one gets
    a stable synthetic id so re-imports of the same business still dedupe."""
    place_id = item.get("placeId") or item.get("place_id")
    if not place_id:
        fallback = item.get("cid") or item.get("fid") or item.get("title") or ""
        place_id = f"apify:{fallback}"
    categories = item.get("categories")
    if not isinstance(categories, list):
        categories = [item["categoryName"]] if item.get("categoryName") else []
    rating = item.get("totalScore")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None
    return PlaceResult(
        place_id=str(place_id)[:300],
        name=str(item.get("title") or item.get("name")).strip(),
        address=item.get("address") or None,
        phone=item.get("phone") or item.get("phoneUnformatted") or None,
        website=item.get("website") or None,
        rating=rating,
        types=[c for c in categories if isinstance(c, str)][:10],
    )
