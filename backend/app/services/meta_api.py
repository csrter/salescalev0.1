"""Meta Marketing API access (Graph API, pinned via META_API_VERSION).

Deliberately uses direct Graph HTTP calls instead of facebook_business: the
SDK hard-pins an API version per release and lags Meta's cadence, and the
read/OAuth surface Phase 1 needs is a handful of stable endpoints. Phase 2's
write operations can revisit this choice.
"""

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from ..config import get_settings

GRAPH = "https://graph.facebook.com"

# Scopes required for reading + (Phase 2) managing client ad accounts.
META_SCOPES = "ads_management,ads_read,business_management"


class MetaAuthError(Exception):
    """Token invalid/expired/revoked — connection should be marked
    disconnected, never silently retried."""


class MetaApiError(Exception):
    pass


def _base(version: Optional[str] = None) -> str:
    return f"{GRAPH}/{version or get_settings().meta_api_version}"


def _check(resp: httpx.Response) -> Dict[str, Any]:
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        # code 190 = invalid/expired/revoked OAuth token
        if err.get("code") == 190 or err.get("type") == "OAuthException":
            raise MetaAuthError(err.get("message", "OAuth error"))
        # error_user_msg is Meta's human-readable validation detail
        detail = err.get("error_user_msg") or err.get("message")
        raise MetaApiError(detail or f"HTTP {resp.status_code}")
    return data


def _get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return _check(httpx.get(url, params=params, timeout=30))


def _post(url: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return _check(httpx.post(url, data=data, timeout=60))


def build_oauth_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": settings.meta_redirect_uri,
        "state": state,
        "scope": META_SCOPES,
    }
    return (
        f"https://www.facebook.com/{settings.meta_api_version}/dialog/oauth?"
        + urlencode(params)
    )


def exchange_code_for_token(code: str) -> Dict[str, Any]:
    settings = get_settings()
    return _get(
        f"{_base()}/oauth/access_token",
        {
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "redirect_uri": settings.meta_redirect_uri,
            "code": code,
        },
    )


def exchange_for_long_lived_token(short_token: str) -> Dict[str, Any]:
    settings = get_settings()
    return _get(
        f"{_base()}/oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "fb_exchange_token": short_token,
        },
    )


def _paginate(url: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    while True:
        data = _get(url, params)
        out.extend(data.get("data", []))
        next_url = data.get("paging", {}).get("next")
        if not next_url:
            return out
        url, params = next_url, {}


def fetch_me(token: str) -> Dict[str, Any]:
    return _get(f"{_base()}/me", {"access_token": token, "fields": "id,name"})


def fetch_ad_accounts(token: str) -> List[Dict[str, Any]]:
    return _paginate(
        f"{_base()}/me/adaccounts",
        {
            "access_token": token,
            "fields": "id,account_id,name,currency,timezone_name,account_status",
            "limit": 100,
        },
    )


def fetch_campaigns(token: str, account_external_id: str) -> List[Dict[str, Any]]:
    # account_external_id is the act_-prefixed id from /me/adaccounts.
    return _paginate(
        f"{_base()}/{account_external_id}/campaigns",
        {
            "access_token": token,
            "fields": "id,name,status,objective,daily_budget,lifetime_budget,"
            "start_time,stop_time",
            "limit": 100,
        },
    )


def fetch_ad_sets(token: str, campaign_external_id: str) -> List[Dict[str, Any]]:
    return _paginate(
        f"{_base()}/{campaign_external_id}/adsets",
        {"access_token": token, "fields": "id,name,status", "limit": 100},
    )


def fetch_ads(token: str, ad_set_external_id: str) -> List[Dict[str, Any]]:
    return _paginate(
        f"{_base()}/{ad_set_external_id}/ads",
        {"access_token": token, "fields": "id,name,status", "limit": 100},
    )


def meta_budget_to_micros(value: Optional[str]) -> Optional[int]:
    # Meta returns budgets as strings in the account currency's minor units
    # (e.g. cents); normalize to micros to match Google.
    if value in (None, ""):
        return None
    return int(value) * 10_000


def micros_to_meta_budget(micros: int) -> int:
    return micros // 10_000


# --- write operations (Phase 2) ---
# All of these are only ever invoked from the pending-change executor after
# an explicit user confirmation; never call them directly from a route.


def _encode_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    # Graph API takes form-encoded posts; nested structures go as JSON strings.
    return {
        k: json.dumps(v) if isinstance(v, (dict, list)) else v
        for k, v in fields.items()
        if v is not None
    }


def create_campaign(
    token: str, account_external_id: str, fields: Dict[str, Any]
) -> Dict[str, Any]:
    """fields: name, objective (ODAX e.g. OUTCOME_LEADS), status,
    special_ad_categories (list — required by Meta even when empty),
    daily_budget/lifetime_budget in minor units."""
    fields.setdefault("special_ad_categories", [])
    return _post(
        f"{_base()}/{account_external_id}/campaigns",
        {"access_token": token, **_encode_fields(fields)},
    )


def update_entity(token: str, external_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Campaigns, ad sets, and ads all update the same way: POST /{id}.
    Pause/resume is an update with status=PAUSED/ACTIVE."""
    return _post(
        f"{_base()}/{external_id}",
        {"access_token": token, **_encode_fields(fields)},
    )


def create_ad_set(
    token: str, account_external_id: str, fields: Dict[str, Any]
) -> Dict[str, Any]:
    """fields: name, campaign_id, daily_budget (minor units), optimization_goal,
    billing_event, bid_amount (minor units, optional), targeting, status."""
    fields.setdefault("billing_event", "IMPRESSIONS")
    fields.setdefault("targeting", {"geo_locations": {"countries": ["US"]}})
    return _post(
        f"{_base()}/{account_external_id}/adsets",
        {"access_token": token, **_encode_fields(fields)},
    )


def create_ad(
    token: str, account_external_id: str, fields: Dict[str, Any]
) -> Dict[str, Any]:
    """fields: name, adset_id, creative ({"creative_id": ...}), status."""
    return _post(
        f"{_base()}/{account_external_id}/ads",
        {"access_token": token, **_encode_fields(fields)},
    )


def upload_ad_image(
    token: str, account_external_id: str, image_bytes_b64: str, name: str
) -> Dict[str, Any]:
    """Returns Meta's response: {"images": {<name>: {"hash": ..., "url": ...}}}."""
    return _post(
        f"{_base()}/{account_external_id}/adimages",
        {"access_token": token, "bytes": image_bytes_b64, "name": name},
    )


def create_creative(
    token: str, account_external_id: str, fields: Dict[str, Any]
) -> Dict[str, Any]:
    """fields: name, object_story_spec ({page_id, link_data: {message, link,
    name, description, image_hash, call_to_action}})."""
    return _post(
        f"{_base()}/{account_external_id}/adcreatives",
        {"access_token": token, **_encode_fields(fields)},
    )


# Placement formats the preview UI offers — each renders in Meta's real
# template via the previews edge, which is what makes previews accurate.
META_PREVIEW_FORMATS = [
    "DESKTOP_FEED_STANDARD",
    "MOBILE_FEED_STANDARD",
    "INSTAGRAM_STANDARD",
    "INSTAGRAM_STORY",
    "FACEBOOK_STORY_MOBILE",
    "RIGHT_COLUMN_STANDARD",
]


def fetch_previews(token: str, external_id: str, ad_format: str) -> List[Dict[str, Any]]:
    """external_id is an ad id or creative id; returns [{"body": "<iframe …>"}]."""
    data = _get(
        f"{_base()}/{external_id}/previews",
        {"access_token": token, "ad_format": ad_format},
    )
    return data.get("data", [])


def fetch_pages(token: str) -> List[Dict[str, Any]]:
    """Pages the user can run ads for — needed for creative object_story_spec."""
    return _paginate(
        f"{_base()}/me/accounts",
        {"access_token": token, "fields": "id,name", "limit": 100},
    )
