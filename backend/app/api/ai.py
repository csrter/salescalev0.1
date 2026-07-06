"""Phase 9 AI insights endpoints.

Access model: explanations and summaries follow the metrics endpoints —
TenantScope resolves the client, so a team user reaches any of their
Organization's clients and a client-role user only their own account
(cross-tenant is a 404 before any grounding is computed, which is the
architectural isolation guarantee — see services/ai_insights.py). Usage
reporting is team-only.

These POSTs are mutating only in the bookkeeping sense (an ai_usage row);
they never touch a platform — hence their entry in test_manage_flow's
mutating-route allowlist.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_team
from ..models.core import Client, Organization, User
from ..schemas import AiExplainIn, AiSummaryIn
from ..services import ai_insights, entitlements

router = APIRouter(prefix="/api/ai", tags=["ai"])


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
    if not platforms or platforms == "all":
        return None
    parsed = {p.strip().lower() for p in platforms.split(",") if p.strip()}
    unknown = parsed - {"meta", "google"}
    if unknown:
        raise HTTPException(400, f"Unknown platform(s): {', '.join(sorted(unknown))}")
    return parsed or None


def _handle(call):
    try:
        return call()
    except ai_insights.AiNotEntitled as e:
        raise HTTPException(403, str(e))
    except ai_insights.AiLimitExceeded as e:
        raise HTTPException(429, str(e))
    except ai_insights.AiNotConfigured as e:
        raise HTTPException(503, str(e))


@router.post("/explain")
def explain(
    body: AiExplainIn,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    if body.metric not in ai_insights.EXPLAINABLE_METRICS:
        raise HTTPException(
            400,
            "metric must be one of "
            + ", ".join(sorted(ai_insights.EXPLAINABLE_METRICS)),
        )
    client = _client_for(db, scope, body.client_id)
    org = db.get(Organization, scope.organization_id)
    since, until = _range(body.since, body.until)
    return _handle(
        lambda: ai_insights.explain_metric(
            db,
            org,
            client,
            scope.user,
            body.metric,
            body.question,
            since,
            until,
            _platform_set(body.platforms),
        )
    )


@router.post("/summary")
def summary(
    body: AiSummaryIn,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, body.client_id)
    org = db.get(Organization, scope.organization_id)
    since, until = _range(body.since, body.until)
    return _handle(
        lambda: ai_insights.report_summary(
            db, org, client, scope.user, since, until, _platform_set(body.platforms)
        )
    )


@router.get("/usage")
def usage(user: User = Depends(require_team), db: Session = Depends(get_db)):
    """This Organization's AI usage this month vs. its limit — team-only
    (cost is agency-internal, not something a client account sees)."""
    org = db.get(Organization, user.organization_id)
    used = ai_insights.month_usage(db, org.id)
    limit = entitlements.ai_monthly_query_limit(org)
    return {
        "month_queries": used["queries"],
        "month_cost_micro_usd": used["cost_micro_usd"],
        "limit": limit,
        "remaining": max(limit - used["queries"], 0),
        "enabled": entitlements.can_use_ai_insights(org),
    }
