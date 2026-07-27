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
from typing import Any, Callable, Dict, List, Tuple, Type

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
            # Org filter is load-bearing: without it, a second Organization
            # syncing the same external account would find (and overwrite)
            # another tenant's row. Matches uq_insight_entity_day.
            InsightDaily.organization_id == account.organization_id,
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
                account_external_id=account.external_id,
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
        existing.account_external_id = account.external_id


def _upsert_snapshot(
    db: Session, account: AdAccount, row: Dict[str, Any], metric: str, date: dt.date
) -> None:
    existing = db.execute(
        select(QualitySnapshot).where(
            QualitySnapshot.organization_id == account.organization_id,
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


def _sync_meta(
    db: Session, account: AdAccount, conn: PlatformConnection, since: dt.date, until: dt.date
) -> int:
    token = conn_svc.get_access_token(conn)
    rows = meta_api.fetch_insights(
        token, account.external_id, since.isoformat(), until.isoformat()
    )
    count = 0
    for row in rows:
        _upsert_insight(db, account, row)
        count += 1
    return count


def _sync_google(
    db: Session, account: AdAccount, conn: PlatformConnection, since: dt.date, until: dt.date
) -> int:
    refresh_token = conn_svc.get_refresh_token(conn)
    rows = google_ads_api.fetch_insights(
        refresh_token, account.external_id, since.isoformat(), until.isoformat()
    )
    count = 0
    for row in rows:
        _upsert_insight(db, account, row)
        count += 1
    # Point-in-time quality signals, snapshotted under today's date.
    for row in google_ads_api.fetch_keyword_quality_scores(
        refresh_token, account.external_id
    ):
        _upsert_snapshot(db, account, row, "quality_score", until)
        count += 1
    for row in google_ads_api.fetch_ad_strength(refresh_token, account.external_id):
        _upsert_snapshot(db, account, row, "ad_strength", until)
        count += 1
    return count


# Adapter seam: a new platform registers its insights fetcher here (same
# (db, account, conn, since, until) -> count signature). Accounts for a
# platform with no fetcher are simply skipped (returns 0).
InsightsFetcher = Callable[[Session, AdAccount, PlatformConnection, dt.date, dt.date], int]
INSIGHTS_FETCHERS: Dict[str, InsightsFetcher] = {
    PLATFORM_META: _sync_meta,
    PLATFORM_GOOGLE: _sync_google,
}

# Auth-error classes that mean "the connection was revoked/expired" — caught so
# the connection flips to disconnected instead of surfacing as a generic error.
# A new adapter adds its own *AuthError class here.
PLATFORM_AUTH_ERRORS: Tuple[Type[Exception], ...] = (
    meta_api.MetaAuthError,
    google_ads_api.GoogleAuthError,
)


def _sync_account(db: Session, account: AdAccount, conn: PlatformConnection, days: int) -> int:
    until = dt.date.today()
    since = until - dt.timedelta(days=days)
    fetcher = INSIGHTS_FETCHERS.get(account.platform)
    if fetcher is None:
        return 0  # no insights adapter for this platform's accounts yet
    count = fetcher(db, account, conn, since, until)
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
        # Manual sync moves the auto-poll cursor too — the freshness cue
        # reflects it, and run_due won't immediately re-pull the same days.
        from ..models.base import utcnow

        conn.last_insights_sync_at = utcnow()
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
        except PLATFORM_AUTH_ERRORS as e:
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


# --- background auto-sync (run_due) ------------------------------------------

# Restatement window for the automatic poll: platforms restate the last few
# days (late conversions, spend corrections), so each pass re-pulls a short
# tail rather than one day.
AUTO_SYNC_DAYS = 3
# Connections per tick — bounds one tick's wall-clock (a Google read can
# take up to its 45s per-RPC deadline).
AUTO_SYNC_MAX_CONNECTIONS = 3


def run_due(db: Session, limit: int = AUTO_SYNC_MAX_CONNECTIONS) -> int:
    """Automatically sync the active connections whose last poll is older
    than insights_sync_interval_seconds (or that never synced). Before this
    pass existed, dashboards only refreshed on the manual Sync button.

    The cursor is stamped at attempt START, so a connection whose platform
    is down retries on the interval instead of hot-looping every tick.
    Auth failures flip the connection to disconnected exactly like the
    manual sync path. Returns the number of connections attempted."""
    from ..config import get_settings
    from ..models.base import utcnow

    cutoff = utcnow() - dt.timedelta(
        seconds=get_settings().insights_sync_interval_seconds
    )
    conns = db.execute(
        select(PlatformConnection)
        .where(
            PlatformConnection.status == CONN_ACTIVE,
            (
                PlatformConnection.last_insights_sync_at.is_(None)
                | (PlatformConnection.last_insights_sync_at < cutoff)
            ),
        )
        .order_by(PlatformConnection.last_insights_sync_at.asc().nulls_first())
        .limit(limit)
    ).scalars().all()
    attempted = 0
    for conn in conns:
        conn.last_insights_sync_at = utcnow()
        db.commit()
        attempted += 1
        accounts = (
            db.execute(
                select(AdAccount).where(AdAccount.connection_id == conn.id)
            )
            .scalars()
            .all()
        )
        for account in accounts:
            try:
                _sync_account(db, account, conn, AUTO_SYNC_DAYS)
            except PLATFORM_AUTH_ERRORS as e:
                db.rollback()
                conn_svc.mark_disconnected(db, conn, str(e))
                db.commit()
                break  # revoked — no point trying its other accounts
            except Exception:  # rate limit/outage: isolate, retry next interval
                db.rollback()
        db.commit()
    return attempted
