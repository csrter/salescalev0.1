"""Executes a confirmed PendingChange against the owning platform.

This module is the ONLY place platform write functions are called from.
The manage router stages changes and this executor applies them after the
explicit confirm step — keeping every spend-affecting mutation behind the
same guardrail and audit path.

Unified conventions at this boundary:
- money is always micros (the executor converts to Meta minor units),
- pause/resume are unified actions mapped to each platform's status enum
  (Meta: PAUSED/ACTIVE, Google: PAUSED/ENABLED),
- results report the platform-assigned external id for creates so the
  caller can insert the local cache row.
"""

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..models.ads import Ad, AdGroup, Campaign, Creative
from ..models.audit import PendingChange
from ..models.base import utcnow
from ..models.core import PLATFORM_META, AdAccount, PlatformConnection
from . import connections as conn_svc
from . import google_ads_api, meta_api


class UnsupportedChange(Exception):
    """The (platform, entity_type, action) combination is not implemented —
    a staging-time validation bug, not a runtime platform error."""


def _meta_status(action: str) -> str:
    return "PAUSED" if action == "pause" else "ACTIVE"


def _google_status(action: str) -> str:
    return "PAUSED" if action == "pause" else "ENABLED"


def _meta_budget_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if payload.get("daily_budget_micros") is not None:
        out["daily_budget"] = meta_api.micros_to_meta_budget(
            payload["daily_budget_micros"]
        )
    if payload.get("lifetime_budget_micros") is not None:
        out["lifetime_budget"] = meta_api.micros_to_meta_budget(
            payload["lifetime_budget_micros"]
        )
    return out


def execute(
    db: Session,
    change: PendingChange,
    account: AdAccount,
    conn: PlatformConnection,
) -> Dict[str, Any]:
    """Perform the platform write. Raises Meta/Google Api/Auth errors upward;
    the router owns audit logging and state transitions."""
    if account.platform == PLATFORM_META:
        return _execute_meta(db, change, account, conn)
    return _execute_google(db, change, account, conn)


# --- Meta ---


def _execute_meta(
    db: Session, change: PendingChange, account: AdAccount, conn: PlatformConnection
) -> Dict[str, Any]:
    token = conn_svc.get_access_token(conn)
    payload = dict(change.payload)
    kind = (change.entity_type, change.action)

    if kind == ("campaign", "create"):
        fields = {
            "name": payload["name"],
            "objective": payload.get("objective", "OUTCOME_LEADS"),
            "status": payload.get("status", "PAUSED"),
            "special_ad_categories": payload.get("special_ad_categories", []),
            **_meta_budget_fields(payload),
        }
        resp = meta_api.create_campaign(token, account.external_id, fields)
        return _cache_create(
            db, Campaign, change, account,
            external_id=resp["id"],
            extra={
                "ad_account_id": account.id,
                "name": payload["name"],
                "status": fields["status"],
                "objective": fields["objective"],
                "daily_budget_micros": payload.get("daily_budget_micros"),
                "lifetime_budget_micros": payload.get("lifetime_budget_micros"),
            },
        )

    if kind == ("campaign", "update"):
        fields = {"name": payload.get("name"), **_meta_budget_fields(payload)}
        meta_api.update_entity(token, change.entity_external_id, fields)
        return _cache_update(db, Campaign, change, payload)

    if change.entity_type in ("campaign", "ad_group", "ad") and change.action in (
        "pause",
        "resume",
    ):
        status = _meta_status(change.action)
        meta_api.update_entity(token, change.entity_external_id, {"status": status})
        model = {"campaign": Campaign, "ad_group": AdGroup, "ad": Ad}[
            change.entity_type
        ]
        return _cache_update(db, model, change, {"status": status})

    if kind == ("ad_group", "create"):
        campaign = db.get(Campaign, payload["campaign_id"])
        fields = {
            "name": payload["name"],
            "campaign_id": campaign.external_id,
            "status": payload.get("status", "PAUSED"),
            "optimization_goal": payload.get("optimization_goal", "LEAD_GENERATION"),
            **_meta_budget_fields(payload),
        }
        if payload.get("bid_micros") is not None:
            fields["bid_amount"] = meta_api.micros_to_meta_budget(payload["bid_micros"])
        resp = meta_api.create_ad_set(token, account.external_id, fields)
        return _cache_create(
            db, AdGroup, change, account,
            external_id=resp["id"],
            extra={
                "campaign_id": campaign.id,
                "name": payload["name"],
                "status": fields["status"],
            },
        )

    if kind == ("ad_group", "update"):
        fields = {"name": payload.get("name"), **_meta_budget_fields(payload)}
        if payload.get("bid_micros") is not None:
            fields["bid_amount"] = meta_api.micros_to_meta_budget(payload["bid_micros"])
        meta_api.update_entity(token, change.entity_external_id, fields)
        return _cache_update(db, AdGroup, change, {"name": payload.get("name")})

    if kind == ("ad", "create"):
        ad_group = db.get(AdGroup, payload["ad_group_id"])
        creative = db.get(Creative, payload["creative_id"])
        resp = meta_api.create_ad(
            token,
            account.external_id,
            {
                "name": payload["name"],
                "adset_id": ad_group.external_id,
                "creative": {"creative_id": creative.external_id},
                "status": payload.get("status", "PAUSED"),
            },
        )
        return _cache_create(
            db, Ad, change, account,
            external_id=resp["id"],
            extra={
                "ad_group_id": ad_group.id,
                "name": payload["name"],
                "status": payload.get("status", "PAUSED"),
                "creative_id": creative.id,
            },
        )

    if kind == ("ad", "update"):
        meta_api.update_entity(
            token, change.entity_external_id, {"name": payload.get("name")}
        )
        return _cache_update(db, Ad, change, {"name": payload.get("name")})

    raise UnsupportedChange(f"meta/{change.entity_type}/{change.action}")


# --- Google ---


def _execute_google(
    db: Session, change: PendingChange, account: AdAccount, conn: PlatformConnection
) -> Dict[str, Any]:
    refresh_token = conn_svc.get_refresh_token(conn)
    customer_id = account.external_id
    payload = dict(change.payload)
    kind = (change.entity_type, change.action)

    if kind == ("campaign", "create"):
        resp = google_ads_api.create_campaign(
            refresh_token,
            customer_id,
            name=payload["name"],
            daily_budget_micros=payload["daily_budget_micros"],
            status=payload.get("status", "PAUSED"),
        )
        return _cache_create(
            db, Campaign, change, account,
            external_id=resp["external_id"],
            extra={
                "ad_account_id": account.id,
                "name": payload["name"],
                "status": payload.get("status", "PAUSED"),
                "objective": "SEARCH",
                "daily_budget_micros": payload["daily_budget_micros"],
            },
        )

    if kind == ("campaign", "update"):
        fields = {}
        if payload.get("name") is not None:
            fields["name"] = payload["name"]
        if fields:
            google_ads_api.update_campaign(
                refresh_token, customer_id, change.entity_external_id, fields
            )
        if payload.get("daily_budget_micros") is not None:
            google_ads_api.update_campaign_budget(
                refresh_token,
                customer_id,
                change.entity_external_id,
                payload["daily_budget_micros"],
            )
        return _cache_update(db, Campaign, change, payload)

    if kind[0] == "campaign" and change.action in ("pause", "resume"):
        status = _google_status(change.action)
        google_ads_api.update_campaign(
            refresh_token, customer_id, change.entity_external_id, {"status": status}
        )
        return _cache_update(db, Campaign, change, {"status": status})

    if kind == ("ad_group", "create"):
        campaign = db.get(Campaign, payload["campaign_id"])
        resp = google_ads_api.create_ad_group(
            refresh_token,
            customer_id,
            campaign.external_id,
            name=payload["name"],
            cpc_bid_micros=payload.get("bid_micros"),
            status=payload.get("status", "PAUSED"),
        )
        return _cache_create(
            db, AdGroup, change, account,
            external_id=resp["external_id"],
            extra={
                "campaign_id": campaign.id,
                "name": payload["name"],
                "status": payload.get("status", "PAUSED"),
            },
        )

    if kind == ("ad_group", "update"):
        fields = {}
        if payload.get("name") is not None:
            fields["name"] = payload["name"]
        if payload.get("bid_micros") is not None:
            fields["cpc_bid_micros"] = payload["bid_micros"]
        google_ads_api.update_ad_group(
            refresh_token, customer_id, change.entity_external_id, fields
        )
        return _cache_update(db, AdGroup, change, {"name": payload.get("name")})

    if kind[0] == "ad_group" and change.action in ("pause", "resume"):
        status = _google_status(change.action)
        google_ads_api.update_ad_group(
            refresh_token, customer_id, change.entity_external_id, {"status": status}
        )
        return _cache_update(db, AdGroup, change, {"status": status})

    if kind == ("ad", "create"):
        ad_group = db.get(AdGroup, payload["ad_group_id"])
        resp = google_ads_api.create_responsive_search_ad(
            refresh_token,
            customer_id,
            ad_group.external_id,
            headlines=payload["headlines"],
            descriptions=payload["descriptions"],
            final_url=payload["final_url"],
            status=payload.get("status", "PAUSED"),
        )
        return _cache_create(
            db, Ad, change, account,
            external_id=resp["external_id"],
            extra={
                "ad_group_id": ad_group.id,
                "name": payload.get("name") or f"RSA {resp['external_id']}",
                "status": payload.get("status", "PAUSED"),
            },
        )

    if kind[0] == "ad" and change.action in ("pause", "resume"):
        ad = db.get(Ad, change.entity_id)
        ad_group = db.get(AdGroup, ad.ad_group_id)
        status = _google_status(change.action)
        google_ads_api.update_ad_status(
            refresh_token,
            customer_id,
            ad_group.external_id,
            change.entity_external_id,
            status,
        )
        return _cache_update(db, Ad, change, {"status": status})

    if kind == ("keyword", "add"):
        ad_group = db.get(AdGroup, payload["ad_group_id"])
        return google_ads_api.add_keyword(
            refresh_token,
            customer_id,
            ad_group.external_id,
            text=payload["text"],
            match_type=payload["match_type"],
            negative=payload.get("negative", False),
            cpc_bid_micros=payload.get("cpc_bid_micros"),
        )

    if kind == ("keyword", "remove"):
        ad_group = db.get(AdGroup, payload["ad_group_id"])
        google_ads_api.remove_keyword(
            refresh_token, customer_id, ad_group.external_id, payload["criterion_id"]
        )
        return {}

    if kind == ("campaign_negative", "add"):
        campaign = db.get(Campaign, payload["campaign_id"])
        return google_ads_api.add_campaign_negative_keyword(
            refresh_token,
            customer_id,
            campaign.external_id,
            text=payload["text"],
            match_type=payload["match_type"],
        )

    if kind[0] == "asset_group" and change.action in ("pause", "resume"):
        google_ads_api.update_asset_group_status(
            refresh_token,
            customer_id,
            change.entity_external_id,
            _google_status(change.action),
        )
        return {}

    raise UnsupportedChange(f"google/{change.entity_type}/{change.action}")


# --- local cache maintenance ---


def _cache_create(
    db: Session,
    model,
    change: PendingChange,
    account: AdAccount,
    external_id: str,
    extra: Dict[str, Any],
) -> Dict[str, Any]:
    row = model(
        client_id=account.client_id,
        platform=account.platform,
        external_id=external_id,
        synced_at=utcnow(),
        **extra,
    )
    db.add(row)
    db.flush()
    return {"external_id": external_id, "local_id": row.id}


def _cache_update(
    db: Session, model, change: PendingChange, fields: Dict[str, Any]
) -> Dict[str, Any]:
    row: Optional[Any] = db.get(model, change.entity_id) if change.entity_id else None
    if row is not None:
        for key, value in fields.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        row.synced_at = utcnow()
    return {"external_id": change.entity_external_id}
