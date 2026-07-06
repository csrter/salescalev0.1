"""Unified account → campaign → ad set/group → ad browser.

Reads pull live from the platform API (per phase requirement), upsert into
the local cache tables, and return the unified shape. Platform auth failures
flip the connection to disconnected and surface as 502s — never a silent
empty list.
"""

import datetime as dt
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope
from ..models.ads import Ad, AdGroup, Campaign
from ..models.base import utcnow
from ..models.core import AdAccount, PLATFORM_GOOGLE, PLATFORM_META, PlatformConnection
from ..schemas import AdAccountOut, AdGroupOut, AdOut, CampaignOut
from ..services import connections as conn_svc
from ..services import google_ads_api, meta_api

router = APIRouter(prefix="/api", tags=["browser"])


def _connection_for(db: Session, account: AdAccount) -> PlatformConnection:
    conn = db.get(PlatformConnection, account.connection_id)
    if conn is None or conn.status != "active":
        raise HTTPException(
            409,
            f"The {account.platform} connection for this account is not active"
            + (f": {conn.error_detail}" if conn and conn.error_detail else ""),
        )
    return conn


def _auth_failed(db: Session, conn: PlatformConnection, exc: Exception):
    conn_svc.mark_disconnected(db, conn, str(exc))
    return HTTPException(
        502,
        f"{conn.platform} rejected the stored credentials — the connection "
        "has been marked disconnected and needs to be re-authorized",
    )


@router.get("/ad-accounts", response_model=List[AdAccountOut])
def list_ad_accounts(
    client_id: Optional[str] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    stmt = select(AdAccount)
    stmt = scope.filter(stmt, AdAccount)
    if client_id is not None:
        scope.check_client_id(client_id)
        stmt = stmt.where(AdAccount.client_id == client_id)
    return db.execute(stmt).scalars().all()


@router.get("/ad-accounts/{account_id}/campaigns", response_model=List[CampaignOut])
def list_campaigns(
    account_id: str,
    refresh: bool = True,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account = scope.get_or_404(db, AdAccount, account_id)
    if refresh:
        _refresh_campaigns(db, account)
    stmt = (
        select(Campaign)
        .where(Campaign.ad_account_id == account.id)
        .order_by(Campaign.name)
    )
    return db.execute(scope.filter(stmt, Campaign)).scalars().all()


@router.get("/campaigns/{campaign_id}/ad-groups", response_model=List[AdGroupOut])
def list_ad_groups(
    campaign_id: str,
    refresh: bool = True,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    campaign = scope.get_or_404(db, Campaign, campaign_id)
    if refresh:
        _refresh_ad_groups(db, campaign)
    stmt = (
        select(AdGroup)
        .where(AdGroup.campaign_id == campaign.id)
        .order_by(AdGroup.name)
    )
    return db.execute(scope.filter(stmt, AdGroup)).scalars().all()


@router.get("/ad-groups/{ad_group_id}/ads", response_model=List[AdOut])
def list_ads(
    ad_group_id: str,
    refresh: bool = True,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    ad_group = scope.get_or_404(db, AdGroup, ad_group_id)
    if refresh:
        _refresh_ads(db, ad_group)
    stmt = select(Ad).where(Ad.ad_group_id == ad_group.id).order_by(Ad.name)
    return db.execute(scope.filter(stmt, Ad)).scalars().all()


# --- live-pull + upsert helpers ---


def _upsert(db: Session, model, account_or_parent_fields: dict, items: List[dict]):
    now = utcnow()
    for item in items:
        existing = db.execute(
            select(model).where(
                model.platform == account_or_parent_fields["platform"],
                model.external_id == item["external_id"],
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                model(
                    **account_or_parent_fields,
                    **item,
                    synced_at=now,
                )
            )
        else:
            for k, v in item.items():
                setattr(existing, k, v)
            existing.synced_at = now
    db.commit()


def _parse_meta_time(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _refresh_campaigns(db: Session, account: AdAccount) -> None:
    conn = _connection_for(db, account)
    base = {
        "platform": account.platform,
        "organization_id": account.organization_id,
        "client_id": account.client_id,
        "ad_account_id": account.id,
    }
    if account.platform == PLATFORM_META:
        token = conn_svc.get_access_token(conn)
        try:
            rows = meta_api.fetch_campaigns(token, account.external_id)
        except meta_api.MetaAuthError as e:
            raise _auth_failed(db, conn, e)
        items = [
            {
                "external_id": r["id"],
                "name": r.get("name") or r["id"],
                "status": r.get("status"),
                "objective": r.get("objective"),
                "daily_budget_micros": meta_api.meta_budget_to_micros(
                    r.get("daily_budget")
                ),
                "lifetime_budget_micros": meta_api.meta_budget_to_micros(
                    r.get("lifetime_budget")
                ),
                "start_time": _parse_meta_time(r.get("start_time")),
                "end_time": _parse_meta_time(r.get("stop_time")),
                "raw": r,
            }
            for r in rows
        ]
    elif account.platform == PLATFORM_GOOGLE:
        refresh_token = conn_svc.get_refresh_token(conn)
        try:
            rows = google_ads_api.fetch_campaigns(refresh_token, account.external_id)
        except google_ads_api.GoogleAuthError as e:
            raise _auth_failed(db, conn, e)
        items = [{**r, "raw": None} for r in rows]
    else:
        raise HTTPException(400, f"Unknown platform {account.platform}")
    _upsert(db, Campaign, base, items)


def _refresh_ad_groups(db: Session, campaign: Campaign) -> None:
    account = db.get(AdAccount, campaign.ad_account_id)
    conn = _connection_for(db, account)
    base = {
        "platform": campaign.platform,
        "organization_id": campaign.organization_id,
        "client_id": campaign.client_id,
        "campaign_id": campaign.id,
    }
    if campaign.platform == PLATFORM_META:
        token = conn_svc.get_access_token(conn)
        try:
            rows = meta_api.fetch_ad_sets(token, campaign.external_id)
        except meta_api.MetaAuthError as e:
            raise _auth_failed(db, conn, e)
        items = [
            {
                "external_id": r["id"],
                "name": r.get("name") or r["id"],
                "status": r.get("status"),
                "raw": r,
            }
            for r in rows
        ]
    else:
        refresh_token = conn_svc.get_refresh_token(conn)
        try:
            rows = google_ads_api.fetch_ad_groups(
                refresh_token, account.external_id, campaign.external_id
            )
        except google_ads_api.GoogleAuthError as e:
            raise _auth_failed(db, conn, e)
        items = [{**r, "raw": None} for r in rows]
    _upsert(db, AdGroup, base, items)


def _refresh_ads(db: Session, ad_group: AdGroup) -> None:
    campaign = db.get(Campaign, ad_group.campaign_id)
    account = db.get(AdAccount, campaign.ad_account_id)
    conn = _connection_for(db, account)
    base = {
        "platform": ad_group.platform,
        "organization_id": ad_group.organization_id,
        "client_id": ad_group.client_id,
        "ad_group_id": ad_group.id,
    }
    if ad_group.platform == PLATFORM_META:
        token = conn_svc.get_access_token(conn)
        try:
            rows = meta_api.fetch_ads(token, ad_group.external_id)
        except meta_api.MetaAuthError as e:
            raise _auth_failed(db, conn, e)
        items = [
            {
                "external_id": r["id"],
                "name": r.get("name") or r["id"],
                "status": r.get("status"),
                "raw": r,
            }
            for r in rows
        ]
    else:
        refresh_token = conn_svc.get_refresh_token(conn)
        try:
            rows = google_ads_api.fetch_ads(
                refresh_token, account.external_id, ad_group.external_id
            )
        except google_ads_api.GoogleAuthError as e:
            raise _auth_failed(db, conn, e)
        items = [{**r, "raw": None} for r in rows]
    _upsert(db, Ad, base, items)
