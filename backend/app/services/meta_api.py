"""Meta Marketing API access (Graph API, pinned via META_API_VERSION).

Deliberately uses direct Graph HTTP calls instead of facebook_business: the
SDK hard-pins an API version per release and lags Meta's cadence, and the
read/OAuth surface Phase 1 needs is a handful of stable endpoints. Phase 2's
write operations can revisit this choice.
"""

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


def _get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    resp = httpx.get(url, params=params, timeout=30)
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        # code 190 = invalid/expired/revoked OAuth token
        if err.get("code") == 190 or err.get("type") == "OAuthException":
            raise MetaAuthError(err.get("message", "OAuth error"))
        raise MetaApiError(err.get("message", f"HTTP {resp.status_code}"))
    return data


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
