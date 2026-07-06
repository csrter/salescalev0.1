"""Pulls daily insights (and quality signals) from every active platform
connection into the local time-series tables the metrics layer reads.

Per-platform isolation (CLAUDE.md architecture rule): each connection syncs
independently inside its own try/except — one platform being down, rate
limited, or de-authorized never blocks the others. The caller gets a
per-platform result list, never an all-or-nothing failure.

Invoked on demand via POST /api/insights/sync today; the same function is
the unit a background scheduler will call per-connection when recurring
polling lands (tracked for a later phase).
"""

import datetime as dt
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.ads import InsightDaily, QualitySnapshot
from ..models.core import (
    CONN_ACTIVE,
    PLATFORM_GOOGLE,
    PLATFORM_META,
    AdAccount,
    Client,
    PlatformConnection,
)
from . import connections as conn_svc
from . import google_ads_api, meta_api


def _upsert_insight(db: Session, account: AdAccount, row: Dict[str, Any]) -> None:
    date = (
        dt.date.fromisoformat(row["date"])
        if isinstance(row["date"], str)
        else row["date"]
    )
    existing = db.execute(
        select(InsightDaily).where(
            InsightDaily.platform == account.platform,
            InsightDaily.entity_type == row["entity_type"],
            InsightDaily.entity_external_id == row["entity_external_id"],
            InsightDaily.date == date,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            InsightDaily(
                organization_id=account.organization_id,
                client_id=account.client_id,
                platform=account.platform,
                entity_type=row["entity_type"],
                entity_external_id=row["entity_external_id"],
                date=date,
                impressions=row["impressions"],
                clicks=row["clicks"],
                spend_micros=row["spend_micros"],
                conversions=row["conversions"],
                raw=row.get("raw"),
            )
        )
    else:
        # Platforms restate recent days (late conversions, spend corrections);
        # the newest pull wins.
        existing.impressions = row["impressions"]
        existing.clicks = row["clicks"]
        existing.spend_micros = row["spend_micros"]
        existing.conversions = row["conversions"]
        existing.raw = row.get("raw")


def _upsert_snapshot(
    db: Session, account: AdAccount, row: Dict[str, Any], metric: str, date: dt.date
) -> None:
    existing = db.execute(
        select(QualitySnapshot).where(
            QualitySnapshot.platform == account.platform,
            QualitySnapshot.entity_type == row["entity_type"],
            QualitySnapshot.entity_external_id == row["entity_external_id"],
            QualitySnapshot.metric == metric,
            QualitySnapshot.date == date,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            QualitySnapshot(
                organization_id=account.organization_id,
                client_id=account.client_id,
                platform=account.platform,
                entity_type=row["entity_type"],
                entity_external_id=row["entity_external_id"],
                entity_name=row.get("entity_name"),
                metric=metric,
                value=row["value"],
                value_label=row.get("value_label"),
                date=date,
            )
        )
    else:
        existing.value = row["value"]
        existing.value_label = row.get("value_label")


def _sync_account(db: Session, account: AdAccount, conn: PlatformConnection, days: int) -> int:
    until = dt.date.today()
    since = until - dt.timedelta(days=days)
    count = 0
    if account.platform == PLATFORM_META:
        token = conn_svc.get_access_token(conn)
        rows = meta_api.fetch_insights(
            token, account.external_id, since.isoformat(), until.isoformat()
        )
        for row in rows:
            _upsert_insight(db, account, row)
            count += 1
    elif account.platform == PLATFORM_GOOGLE:
        refresh_token = conn_svc.get_refresh_token(conn)
        rows = google_ads_api.fetch_insights(
            refresh_token, account.external_id, since.isoformat(), until.isoformat()
        )
        for row in rows:
            _upsert_insight(db, account, row)
            count += 1
        # Point-in-time quality signals, snapshotted under today's date.
        for row in google_ads_api.fetch_keyword_quality_scores(
            refresh_token, account.external_id
        ):
            _upsert_snapshot(db, account, row, "quality_score", until)
            count += 1
        for row in google_ads_api.fetch_ad_strength(
            refresh_token, account.external_id
        ):
            _upsert_snapshot(db, account, row, "ad_strength", until)
            count += 1
    db.commit()
    return count


def sync_client(db: Session, client: Client, days: int = 30) -> List[Dict[str, Any]]:
    """Sync every active connection for one client. Returns one result dict
    per (platform, account) — {"platform", "account", "ok", "rows" | "error"}.
    A failure on one platform is reported, committed around, and never
    propagates to the others."""
    results: List[Dict[str, Any]] = []
    accounts = (
        db.execute(select(AdAccount).where(AdAccount.client_id == client.id))
        .scalars()
        .all()
    )
    for account in accounts:
        conn = db.get(PlatformConnection, account.connection_id)
        if conn is None or conn.status != CONN_ACTIVE:
            results.append(
                {
                    "platform": account.platform,
                    "account": account.external_id,
                    "ok": False,
                    "error": "connection not active",
                }
            )
            continue
        try:
            rows = _sync_account(db, account, conn, days)
            results.append(
                {
                    "platform": account.platform,
                    "account": account.external_id,
                    "ok": True,
                    "rows": rows,
                }
            )
        except (meta_api.MetaAuthError, google_ads_api.GoogleAuthError) as e:
            db.rollback()
            conn_svc.mark_disconnected(db, conn, str(e))
            results.append(
                {
                    "platform": account.platform,
                    "account": account.external_id,
                    "ok": False,
                    "error": f"auth failed: {e}",
                }
            )
        except Exception as e:  # rate limits, outages — isolate per platform
            db.rollback()
            results.append(
                {
                    "platform": account.platform,
                    "account": account.external_id,
                    "ok": False,
                    "error": str(e),
                }
            )
    return results
