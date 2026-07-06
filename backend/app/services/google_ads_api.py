"""Google Ads API access via the official google-ads client library (API v24).

OAuth token exchange is plain HTTP; everything after that goes through
GoogleAdsClient + GAQL. google-ads imports are kept inside functions so the
app (and tests) can run without the heavy grpc stack loaded.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from ..config import get_settings

GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleAuthError(Exception):
    """Refresh token invalid/revoked — mark the connection disconnected."""


class GoogleApiError(Exception):
    pass


def build_oauth_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_ADS_SCOPE,
        "access_type": "offline",  # we need a refresh token
        "prompt": "consent",  # force refresh token even on re-auth
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    settings = get_settings()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
            "code": code,
        },
        timeout=30,
    )
    data = resp.json()
    if resp.status_code >= 400:
        raise GoogleApiError(data.get("error_description", str(data)))
    return data


def _client(refresh_token: str):
    from google.ads.googleads.client import GoogleAdsClient

    settings = get_settings()
    config = {
        "developer_token": settings.google_developer_token,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "refresh_token": refresh_token,
        "use_proto_plus": True,
    }
    if settings.google_login_customer_id:
        config["login_customer_id"] = settings.google_login_customer_id
    return GoogleAdsClient.load_from_dict(config)


def _wrap_auth_errors(fn):
    from google.ads.googleads.errors import GoogleAdsException
    from google.auth.exceptions import RefreshError

    try:
        return fn()
    except RefreshError as e:
        raise GoogleAuthError(str(e))
    except GoogleAdsException as e:
        for err in e.failure.errors:
            if err.error_code.authentication_error or err.error_code.authorization_error:
                raise GoogleAuthError(err.message)
        raise GoogleApiError(e.failure.errors[0].message if e.failure.errors else str(e))


def list_accessible_customers(refresh_token: str) -> List[str]:
    def run():
        client = _client(refresh_token)
        svc = client.get_service("CustomerService")
        resp = svc.list_accessible_customers()
        # resource_names look like "customers/1234567890"
        return [rn.split("/")[-1] for rn in resp.resource_names]

    return _wrap_auth_errors(run)


def _search(refresh_token: str, customer_id: str, query: str) -> List[Any]:
    def run():
        client = _client(refresh_token)
        svc = client.get_service("GoogleAdsService")
        return list(svc.search(customer_id=customer_id, query=query))

    return _wrap_auth_errors(run)


def fetch_customer_details(refresh_token: str, customer_id: str) -> Dict[str, Any]:
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
        "customer.time_zone, customer.status, customer.manager FROM customer",
    )
    if not rows:
        raise GoogleApiError(f"No customer row for {customer_id}")
    c = rows[0].customer
    return {
        "external_id": str(c.id),
        "name": c.descriptive_name or str(c.id),
        "currency": c.currency_code,
        "timezone": c.time_zone,
        "status": c.status.name if c.status else None,
        "is_manager": bool(c.manager),
    }


def fetch_campaigns(refresh_token: str, customer_id: str) -> List[Dict[str, Any]]:
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT campaign.id, campaign.name, campaign.status, "
        "campaign.advertising_channel_type, campaign_budget.amount_micros, "
        "campaign.start_date, campaign.end_date FROM campaign "
        "ORDER BY campaign.id",
    )
    out = []
    for row in rows:
        out.append(
            {
                "external_id": str(row.campaign.id),
                "name": row.campaign.name,
                "status": row.campaign.status.name,
                "objective": row.campaign.advertising_channel_type.name,
                "daily_budget_micros": row.campaign_budget.amount_micros or None,
            }
        )
    return out


def fetch_ad_groups(
    refresh_token: str, customer_id: str, campaign_external_id: str
) -> List[Dict[str, Any]]:
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT ad_group.id, ad_group.name, ad_group.status, campaign.id "
        f"FROM ad_group WHERE campaign.id = {int(campaign_external_id)}",
    )
    return [
        {
            "external_id": str(r.ad_group.id),
            "name": r.ad_group.name,
            "status": r.ad_group.status.name,
        }
        for r in rows
    ]


def fetch_ads(
    refresh_token: str, customer_id: str, ad_group_external_id: str
) -> List[Dict[str, Any]]:
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.status, "
        f"ad_group.id FROM ad_group_ad WHERE ad_group.id = {int(ad_group_external_id)}",
    )
    return [
        {
            "external_id": str(r.ad_group_ad.ad.id),
            "name": r.ad_group_ad.ad.name or f"Ad {r.ad_group_ad.ad.id}",
            "status": r.ad_group_ad.status.name,
        }
        for r in rows
    ]
