"""Phase 3 metrics endpoints. Read/compute-only — the single write here
(insights sync) writes only to local time-series tables, never to a
platform.

Access: metric endpoints use TenantScope, so a client-role user sees their
own account's metrics (CLAUDE.md gives the Client role read access to its
own performance). The vertical benchmark is team-only: its peer aggregates
are Organization-internal even in aggregate form.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_team
from ..models.attribution import LandingEvent
from ..models.core import Client, User
from ..services import insights_sync, metrics, utm

router = APIRouter(prefix="/api", tags=["metrics"])


def _client_for(db: Session, scope: TenantScope, client_id: str) -> Client:
    scope.check_client_id(client_id)
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Not found")
    scope.check_organization_id(client.organization_id)
    return client


def _range(
    since: Optional[dt.date], until: Optional[dt.date]
) -> tuple[dt.date, dt.date]:
    until = until or dt.date.today()
    since = since or until - dt.timedelta(days=30)
    if since > until:
        raise HTTPException(400, "since must be <= until")
    return since, until


def _platform_set(platforms: Optional[str]) -> Optional[set[str]]:
    """Parse the Phase 4 dashboard filter (?platforms=meta,google).
    None / empty / "all" means no filter."""
    if not platforms or platforms == "all":
        return None
    parsed = {p.strip().lower() for p in platforms.split(",") if p.strip()}
    unknown = parsed - {"meta", "google"}
    if unknown:
        raise HTTPException(400, f"Unknown platform(s): {', '.join(sorted(unknown))}")
    return parsed or None


@router.post("/insights/sync")
def sync_insights(
    client_id: str,
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Pull insights + quality snapshots for every active connection of one
    client. Per-platform isolation: the response lists each platform's
    outcome; one platform failing never blocks the others."""
    client = _client_for(db, scope, client_id)
    return {"results": insights_sync.sync_client(db, client, days)}


@router.get("/metrics/blended")
def blended(
    client_id: str,
    since: Optional[dt.date] = None,
    until: Optional[dt.date] = None,
    platforms: Optional[str] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    s, u = _range(since, until)
    return metrics.blended_and_mix(db, client, s, u, _platform_set(platforms))


@router.get("/metrics/spend-daily")
def spend_daily(
    client_id: str,
    since: Optional[dt.date] = None,
    until: Optional[dt.date] = None,
    platforms: Optional[str] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    s, u = _range(since, until)
    return metrics.spend_daily(db, client, s, u, _platform_set(platforms))


@router.get("/metrics/guarantee")
def guarantee(
    client_id: str,
    platforms: Optional[str] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Progress against the client's Organization-configured performance
    guarantee. Client-role visible — it's their own goal being tracked."""
    client = _client_for(db, scope, client_id)
    return metrics.guarantee_progress(
        db, client, dt.date.today(), _platform_set(platforms)
    )


@router.get("/metrics/funnel-tiers")
def funnel_tiers(
    client_id: str,
    since: Optional[dt.date] = None,
    until: Optional[dt.date] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    s, u = _range(since, until)
    return metrics.funnel_tiers(db, client, s, u)


@router.get("/metrics/creative-fatigue")
def creative_fatigue(
    client_id: str,
    until: Optional[dt.date] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    return metrics.creative_fatigue(db, client, until or dt.date.today())


@router.get("/metrics/quality-trends")
def quality_trends(
    client_id: str,
    until: Optional[dt.date] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    return metrics.quality_trends(db, client, until or dt.date.today())


@router.get("/metrics/lead-quality-adjusted-cpl")
def lead_quality_adjusted_cpl(
    client_id: str,
    since: Optional[dt.date] = None,
    until: Optional[dt.date] = None,
    platforms: Optional[str] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    s, u = _range(since, until)
    return metrics.lead_quality_adjusted_cpl(
        db, client, s, u, _platform_set(platforms)
    )


@router.get("/metrics/benchmark")
def benchmark(
    client_id: str,
    since: Optional[dt.date] = None,
    until: Optional[dt.date] = None,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Team-only: peer medians are Organization-internal, even aggregated."""
    client = _client_for(db, scope, client_id)
    s, u = _range(since, until)
    return metrics.vertical_benchmark(db, client, s, u)


@router.get("/metrics/reconciliation")
def reconciliation(
    client_id: str,
    since: Optional[dt.date] = None,
    until: Optional[dt.date] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    s, u = _range(since, until)
    return metrics.reconciliation(db, client, s, u)


@router.get("/utm/build")
def utm_build(
    client_id: str,
    platform: str,
    campaign_name: str,
    content: Optional[str] = None,
    term: Optional[str] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    params = utm.build(client, platform, campaign_name, content, term)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return {"params": params, "query_string": query}


@router.get("/utm/violations")
def utm_violations(
    client_id: str,
    days: int = Query(30, ge=1, le=365),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Landing events from the window whose UTM values break the naming
    convention — the drift this tool exists to catch."""
    client = _client_for(db, scope, client_id)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    events = (
        db.execute(
            select(LandingEvent)
            .where(
                LandingEvent.organization_id == client.organization_id,
                LandingEvent.client_id == client.id,
                LandingEvent.created_at >= cutoff,
            )
            .order_by(LandingEvent.occurred_at.desc())
            .limit(1000)
        )
        .scalars()
        .all()
    )
    out = []
    for e in events:
        problems = utm.violations_for_event(client, e)
        if problems:
            out.append(
                {
                    "landing_event_id": e.id,
                    "occurred_at": e.occurred_at.isoformat(),
                    "utm_campaign": e.utm_campaign,
                    "utm_source": e.utm_source,
                    "problems": problems,
                }
            )
    return {"checked": len(events), "violations": out}
