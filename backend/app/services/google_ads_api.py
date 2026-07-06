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


# --- write operations (Phase 2) ---
# Only ever invoked from the pending-change executor after an explicit user
# confirmation; never call these directly from a route. All statuses are the
# GAQL enum names (ENABLED / PAUSED); all money is micros (Google-native).


def _run(refresh_token: str, fn):
    """fn(client) -> result, with auth errors normalized."""

    def call():
        return fn(_client(refresh_token))

    return _wrap_auth_errors(call)


def _apply_update(client, entity, fields: Dict[str, Any], enum_attr: Optional[str]):
    """Set plain fields on a mutate-update entity and return its field mask."""
    from google.api_core import protobuf_helpers

    for key, value in fields.items():
        if key == "status" and enum_attr is not None:
            setattr(entity, "status", getattr(client.enums, enum_attr)[value])
        else:
            setattr(entity, key, value)
    return protobuf_helpers.field_mask(None, entity._pb)


def create_campaign(
    refresh_token: str,
    customer_id: str,
    name: str,
    daily_budget_micros: int,
    status: str = "PAUSED",
) -> Dict[str, Any]:
    """Search campaign with its own (non-shared) budget and Manual CPC —
    the conservative default for a new HVAC lead-gen campaign."""

    def fn(client):
        budget_svc = client.get_service("CampaignBudgetService")
        budget_op = client.get_type("CampaignBudgetOperation")
        budget = budget_op.create
        budget.name = f"Budget — {name}"
        budget.amount_micros = daily_budget_micros
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget.explicitly_shared = False
        budget_resp = budget_svc.mutate_campaign_budgets(
            customer_id=customer_id, operations=[budget_op]
        )
        budget_rn = budget_resp.results[0].resource_name

        campaign_svc = client.get_service("CampaignService")
        campaign_op = client.get_type("CampaignOperation")
        campaign = campaign_op.create
        campaign.name = name
        campaign.campaign_budget = budget_rn
        campaign.status = client.enums.CampaignStatusEnum[status]
        campaign.advertising_channel_type = (
            client.enums.AdvertisingChannelTypeEnum.SEARCH
        )
        campaign.manual_cpc.enhanced_cpc_enabled = False
        campaign.contains_eu_political_advertising = (
            client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        )
        resp = campaign_svc.mutate_campaigns(
            customer_id=customer_id, operations=[campaign_op]
        )
        rn = resp.results[0].resource_name  # customers/X/campaigns/ID
        return {"external_id": rn.split("/")[-1], "resource_name": rn}

    return _run(refresh_token, fn)


def update_campaign(
    refresh_token: str, customer_id: str, campaign_external_id: str, fields: Dict[str, Any]
) -> None:
    """fields: name and/or status (ENABLED|PAUSED)."""

    def fn(client):
        svc = client.get_service("CampaignService")
        op = client.get_type("CampaignOperation")
        campaign = op.update
        campaign.resource_name = svc.campaign_path(customer_id, campaign_external_id)
        mask = _apply_update(client, campaign, fields, "CampaignStatusEnum")
        client.copy_from(op.update_mask, mask)
        svc.mutate_campaigns(customer_id=customer_id, operations=[op])

    return _run(refresh_token, fn)


def update_campaign_budget(
    refresh_token: str, customer_id: str, campaign_external_id: str, daily_budget_micros: int
) -> None:
    def fn(client):
        ga_svc = client.get_service("GoogleAdsService")
        rows = list(
            ga_svc.search(
                customer_id=customer_id,
                query="SELECT campaign_budget.resource_name, campaign.id "
                f"FROM campaign WHERE campaign.id = {int(campaign_external_id)}",
            )
        )
        if not rows:
            raise GoogleApiError(f"Campaign {campaign_external_id} not found")
        budget_rn = rows[0].campaign_budget.resource_name

        from google.api_core import protobuf_helpers

        svc = client.get_service("CampaignBudgetService")
        op = client.get_type("CampaignBudgetOperation")
        budget = op.update
        budget.resource_name = budget_rn
        budget.amount_micros = daily_budget_micros
        client.copy_from(op.update_mask, protobuf_helpers.field_mask(None, budget._pb))
        svc.mutate_campaign_budgets(customer_id=customer_id, operations=[op])

    return _run(refresh_token, fn)


def create_ad_group(
    refresh_token: str,
    customer_id: str,
    campaign_external_id: str,
    name: str,
    cpc_bid_micros: Optional[int] = None,
    status: str = "PAUSED",
) -> Dict[str, Any]:
    def fn(client):
        svc = client.get_service("AdGroupService")
        op = client.get_type("AdGroupOperation")
        ad_group = op.create
        ad_group.name = name
        ad_group.campaign = client.get_service("CampaignService").campaign_path(
            customer_id, campaign_external_id
        )
        ad_group.status = client.enums.AdGroupStatusEnum[status]
        ad_group.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
        if cpc_bid_micros:
            ad_group.cpc_bid_micros = cpc_bid_micros
        resp = svc.mutate_ad_groups(customer_id=customer_id, operations=[op])
        rn = resp.results[0].resource_name
        return {"external_id": rn.split("/")[-1], "resource_name": rn}

    return _run(refresh_token, fn)


def update_ad_group(
    refresh_token: str, customer_id: str, ad_group_external_id: str, fields: Dict[str, Any]
) -> None:
    """fields: name, status (ENABLED|PAUSED), and/or cpc_bid_micros."""

    def fn(client):
        svc = client.get_service("AdGroupService")
        op = client.get_type("AdGroupOperation")
        ad_group = op.update
        ad_group.resource_name = svc.ad_group_path(customer_id, ad_group_external_id)
        mask = _apply_update(client, ad_group, fields, "AdGroupStatusEnum")
        client.copy_from(op.update_mask, mask)
        svc.mutate_ad_groups(customer_id=customer_id, operations=[op])

    return _run(refresh_token, fn)


def create_responsive_search_ad(
    refresh_token: str,
    customer_id: str,
    ad_group_external_id: str,
    headlines: List[str],
    descriptions: List[str],
    final_url: str,
    status: str = "PAUSED",
) -> Dict[str, Any]:
    def fn(client):
        svc = client.get_service("AdGroupAdService")
        op = client.get_type("AdGroupAdOperation")
        ad_group_ad = op.create
        ad_group_ad.ad_group = client.get_service("AdGroupService").ad_group_path(
            customer_id, ad_group_external_id
        )
        ad_group_ad.status = client.enums.AdGroupAdStatusEnum[status]
        ad = ad_group_ad.ad
        ad.final_urls.append(final_url)
        for text in headlines:
            asset = client.get_type("AdTextAsset")
            asset.text = text
            ad.responsive_search_ad.headlines.append(asset)
        for text in descriptions:
            asset = client.get_type("AdTextAsset")
            asset.text = text
            ad.responsive_search_ad.descriptions.append(asset)
        resp = svc.mutate_ad_group_ads(customer_id=customer_id, operations=[op])
        rn = resp.results[0].resource_name  # customers/X/adGroupAds/GROUP~AD
        return {"external_id": rn.split("~")[-1], "resource_name": rn}

    return _run(refresh_token, fn)


def update_ad_status(
    refresh_token: str,
    customer_id: str,
    ad_group_external_id: str,
    ad_external_id: str,
    status: str,
) -> None:
    def fn(client):
        from google.api_core import protobuf_helpers

        svc = client.get_service("AdGroupAdService")
        op = client.get_type("AdGroupAdOperation")
        ad_group_ad = op.update
        ad_group_ad.resource_name = svc.ad_group_ad_path(
            customer_id, ad_group_external_id, ad_external_id
        )
        ad_group_ad.status = client.enums.AdGroupAdStatusEnum[status]
        client.copy_from(
            op.update_mask, protobuf_helpers.field_mask(None, ad_group_ad._pb)
        )
        svc.mutate_ad_group_ads(customer_id=customer_id, operations=[op])

    return _run(refresh_token, fn)


# --- Google-only management surface: keywords, search terms, PMax ---

KEYWORD_MATCH_TYPES = ["EXACT", "PHRASE", "BROAD"]


def fetch_keywords(
    refresh_token: str, customer_id: str, ad_group_external_id: str
) -> List[Dict[str, Any]]:
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, "
        "ad_group_criterion.keyword.match_type, ad_group_criterion.status, "
        "ad_group_criterion.negative, ad_group.id FROM ad_group_criterion "
        f"WHERE ad_group.id = {int(ad_group_external_id)} "
        "AND ad_group_criterion.type = KEYWORD",
    )
    return [
        {
            "criterion_id": str(r.ad_group_criterion.criterion_id),
            "text": r.ad_group_criterion.keyword.text,
            "match_type": r.ad_group_criterion.keyword.match_type.name,
            "status": r.ad_group_criterion.status.name,
            "negative": bool(r.ad_group_criterion.negative),
        }
        for r in rows
    ]


def add_keyword(
    refresh_token: str,
    customer_id: str,
    ad_group_external_id: str,
    text: str,
    match_type: str,
    negative: bool = False,
    cpc_bid_micros: Optional[int] = None,
) -> Dict[str, Any]:
    def fn(client):
        svc = client.get_service("AdGroupCriterionService")
        op = client.get_type("AdGroupCriterionOperation")
        criterion = op.create
        criterion.ad_group = client.get_service("AdGroupService").ad_group_path(
            customer_id, ad_group_external_id
        )
        criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
        criterion.keyword.text = text
        criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum[match_type]
        criterion.negative = negative
        if cpc_bid_micros and not negative:
            criterion.cpc_bid_micros = cpc_bid_micros
        resp = svc.mutate_ad_group_criteria(
            customer_id=customer_id, operations=[op]
        )
        rn = resp.results[0].resource_name
        return {"criterion_id": rn.split("~")[-1], "resource_name": rn}

    return _run(refresh_token, fn)


def remove_keyword(
    refresh_token: str, customer_id: str, ad_group_external_id: str, criterion_id: str
) -> None:
    def fn(client):
        svc = client.get_service("AdGroupCriterionService")
        op = client.get_type("AdGroupCriterionOperation")
        op.remove = svc.ad_group_criterion_path(
            customer_id, ad_group_external_id, criterion_id
        )
        svc.mutate_ad_group_criteria(customer_id=customer_id, operations=[op])

    return _run(refresh_token, fn)


def fetch_campaign_negative_keywords(
    refresh_token: str, customer_id: str, campaign_external_id: str
) -> List[Dict[str, Any]]:
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT campaign_criterion.criterion_id, campaign_criterion.keyword.text, "
        "campaign_criterion.keyword.match_type, campaign.id "
        "FROM campaign_criterion "
        f"WHERE campaign.id = {int(campaign_external_id)} "
        "AND campaign_criterion.type = KEYWORD "
        "AND campaign_criterion.negative = TRUE",
    )
    return [
        {
            "criterion_id": str(r.campaign_criterion.criterion_id),
            "text": r.campaign_criterion.keyword.text,
            "match_type": r.campaign_criterion.keyword.match_type.name,
            "negative": True,
        }
        for r in rows
    ]


def add_campaign_negative_keyword(
    refresh_token: str,
    customer_id: str,
    campaign_external_id: str,
    text: str,
    match_type: str,
) -> Dict[str, Any]:
    def fn(client):
        svc = client.get_service("CampaignCriterionService")
        op = client.get_type("CampaignCriterionOperation")
        criterion = op.create
        criterion.campaign = client.get_service("CampaignService").campaign_path(
            customer_id, campaign_external_id
        )
        criterion.negative = True
        criterion.keyword.text = text
        criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum[match_type]
        resp = svc.mutate_campaign_criteria(customer_id=customer_id, operations=[op])
        rn = resp.results[0].resource_name
        return {"criterion_id": rn.split("~")[-1], "resource_name": rn}

    return _run(refresh_token, fn)


def fetch_search_terms(
    refresh_token: str,
    customer_id: str,
    campaign_external_id: Optional[str] = None,
    ad_group_external_id: Optional[str] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """Search terms report — what people actually typed. The review workflow
    is: scan for irrelevant terms, add them as negatives."""
    where = [f"segments.date DURING LAST_{days}_DAYS" if days in (7, 14, 30) else
             "segments.date DURING LAST_30_DAYS"]
    if campaign_external_id:
        where.append(f"campaign.id = {int(campaign_external_id)}")
    if ad_group_external_id:
        where.append(f"ad_group.id = {int(ad_group_external_id)}")
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT search_term_view.search_term, search_term_view.status, "
        "metrics.impressions, metrics.clicks, metrics.cost_micros, "
        "metrics.conversions, ad_group.id, campaign.id FROM search_term_view "
        f"WHERE {' AND '.join(where)} ORDER BY metrics.impressions DESC",
    )
    return [
        {
            "search_term": r.search_term_view.search_term,
            "status": r.search_term_view.status.name,
            "impressions": r.metrics.impressions,
            "clicks": r.metrics.clicks,
            "cost_micros": r.metrics.cost_micros,
            "conversions": r.metrics.conversions,
            "ad_group_external_id": str(r.ad_group.id),
            "campaign_external_id": str(r.campaign.id),
        }
        for r in rows
    ]


def fetch_asset_groups(
    refresh_token: str, customer_id: str, campaign_external_id: str
) -> List[Dict[str, Any]]:
    """Performance Max asset groups — PMax has no ad groups/ads; asset groups
    are its management unit."""
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT asset_group.id, asset_group.name, asset_group.status, "
        "asset_group.ad_strength, asset_group.final_urls, campaign.id "
        f"FROM asset_group WHERE campaign.id = {int(campaign_external_id)}",
    )
    return [
        {
            "external_id": str(r.asset_group.id),
            "name": r.asset_group.name,
            "status": r.asset_group.status.name,
            "ad_strength": r.asset_group.ad_strength.name,
            "final_urls": list(r.asset_group.final_urls),
        }
        for r in rows
    ]


def update_asset_group_status(
    refresh_token: str, customer_id: str, asset_group_external_id: str, status: str
) -> None:
    def fn(client):
        from google.api_core import protobuf_helpers

        svc = client.get_service("AssetGroupService")
        op = client.get_type("AssetGroupOperation")
        asset_group = op.update
        asset_group.resource_name = svc.asset_group_path(
            customer_id, asset_group_external_id
        )
        asset_group.status = client.enums.AssetGroupStatusEnum[status]
        client.copy_from(
            op.update_mask, protobuf_helpers.field_mask(None, asset_group._pb)
        )
        svc.mutate_asset_groups(customer_id=customer_id, operations=[op])

    return _run(refresh_token, fn)


# --- insights + quality signals (Phase 3) ---


def fetch_insights(
    refresh_token: str, customer_id: str, since: str, until: str
) -> List[Dict[str, Any]]:
    """Ad-group-level daily insights, normalized to InsightDaily's shape.

    Google's `metrics.conversions` is a float (fractional credit under
    data-driven attribution); we round half-up per day. `cost_micros` is
    already micros. since/until are YYYY-MM-DD (inclusive). Campaign id
    rides along in raw for upward aggregation.
    """
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT segments.date, ad_group.id, ad_group.name, campaign.id, "
        "metrics.impressions, metrics.clicks, metrics.cost_micros, "
        "metrics.conversions FROM ad_group "
        f"WHERE segments.date BETWEEN '{since}' AND '{until}'",
    )
    out = []
    for r in rows:
        out.append(
            {
                "entity_type": "ad_group",
                "entity_external_id": str(r.ad_group.id),
                "date": r.segments.date,
                "impressions": r.metrics.impressions,
                "clicks": r.metrics.clicks,
                "spend_micros": r.metrics.cost_micros,
                "conversions": int(r.metrics.conversions + 0.5),
                "raw": {
                    "campaign_id": str(r.campaign.id),
                    "ad_group_name": r.ad_group.name,
                },
            }
        )
    return out


def fetch_keyword_quality_scores(
    refresh_token: str, customer_id: str
) -> List[Dict[str, Any]]:
    """Current Quality Score (1–10) per enabled keyword. Google only exposes
    the point-in-time value — the caller snapshots it daily so trends can be
    computed (quality_snapshots table)."""
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, "
        "ad_group_criterion.quality_info.quality_score, ad_group.id "
        "FROM keyword_view WHERE ad_group_criterion.status = 'ENABLED'",
    )
    out = []
    for r in rows:
        score = r.ad_group_criterion.quality_info.quality_score
        if not score:
            continue  # not enough traffic for Google to score it
        out.append(
            {
                "entity_type": "keyword",
                "entity_external_id": (
                    f"{r.ad_group.id}~{r.ad_group_criterion.criterion_id}"
                ),
                "entity_name": r.ad_group_criterion.keyword.text,
                "value": int(score),
            }
        )
    return out


# Ordinal scale shared with metrics.py so ad-strength trends are comparable.
AD_STRENGTH_ORDINAL = {
    "PENDING": None,
    "NO_ADS": None,
    "POOR": 1,
    "AVERAGE": 2,
    "GOOD": 3,
    "EXCELLENT": 4,
}


def fetch_ad_strength(
    refresh_token: str, customer_id: str
) -> List[Dict[str, Any]]:
    """Current ad strength for responsive search ads and PMax asset groups,
    mapped onto AD_STRENGTH_ORDINAL for trend math."""
    out = []
    rsa_rows = _search(
        refresh_token,
        customer_id,
        "SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, "
        "ad_group_ad.ad_strength, ad_group.id FROM ad_group_ad "
        "WHERE ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD' "
        "AND ad_group_ad.status != 'REMOVED'",
    )
    for r in rsa_rows:
        label = r.ad_group_ad.ad_strength.name
        if AD_STRENGTH_ORDINAL.get(label) is None:
            continue
        out.append(
            {
                "entity_type": "ad",
                "entity_external_id": str(r.ad_group_ad.ad.id),
                "entity_name": r.ad_group_ad.ad.name or str(r.ad_group_ad.ad.id),
                "value": AD_STRENGTH_ORDINAL[label],
                "value_label": label,
            }
        )
    pmax_rows = _search(
        refresh_token,
        customer_id,
        "SELECT asset_group.id, asset_group.name, asset_group.ad_strength "
        "FROM asset_group WHERE asset_group.status != 'REMOVED'",
    )
    for r in pmax_rows:
        label = r.asset_group.ad_strength.name
        if AD_STRENGTH_ORDINAL.get(label) is None:
            continue
        out.append(
            {
                "entity_type": "asset_group",
                "entity_external_id": str(r.asset_group.id),
                "entity_name": r.asset_group.name,
                "value": AD_STRENGTH_ORDINAL[label],
                "value_label": label,
            }
        )
    return out
