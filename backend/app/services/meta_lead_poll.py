"""Meta Instant Form lead POLLING fallback.

Webhook delivery for leadgen depends on external state Salescale doesn't
control: the Meta app must be PUBLISHED, the app-level callback registered,
and (for non-role users) leads_retrieval granted Advanced Access. Any of
those lapsing silently stops lead flow — which is exactly how a Paganelli
lead was missed on 2026-07-20. This poller makes lead arrival independent
of webhook health: every few minutes it pulls each configured page's recent
leads straight from the Graph API and pushes them through the SAME ingest
path the webhook uses (_ingest_meta_lead → upsert → notify → auto-enroll).

Idempotency: upsert_contact dedupes by email/phone within the client and
notify/auto-enroll fire only on CREATED — so a lead seen by both the
webhook and the poller (or by two overlapping polls) lands once and texts
once. The cursor (LeadFormConfig.last_polled_at) is re-read with a 15-minute
overlap each pass so a poll racing lead creation can't skip anything.

Best-effort like every scheduler service: a Graph failure (including the
dev-mode "Cannot call API on behalf of user" state) logs and retries next
interval, never raises out of the tick.
"""

import datetime as dt
import json
import logging
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import CONN_ACTIVE, PlatformConnection
from ..models.crm import LeadFormConfig
from . import connections as conn_svc

log = logging.getLogger("salescale.meta_lead_poll")

GRAPH_BASE = "https://graph.facebook.com/v21.0"
POLL_INTERVAL_SECONDS = 300
# Re-scan window overlapping the previous poll — dedupe soaks up the repeats.
OVERLAP = dt.timedelta(minutes=15)
# First poll for a page looks back this far (catches recently missed leads).
FIRST_LOOKBACK = dt.timedelta(days=7)
MAX_LEADS_PER_POLL = 200


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _get(url: str, params: Optional[dict]) -> dict:
    resp = httpx.get(url, params=params, timeout=20)
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data["error"].get("message") or "Graph error")[:200])
    if not isinstance(data, dict):
        raise RuntimeError("unexpected Graph response shape")
    return data


def run_due(db: Session) -> int:
    """One scheduler tick: poll every enabled meta page whose cursor is
    older than POLL_INTERVAL_SECONDS. Returns leads created this pass."""
    now = utcnow()
    configs = (
        db.execute(
            select(LeadFormConfig).where(
                LeadFormConfig.platform == "meta",
                LeadFormConfig.enabled.is_(True),
            )
        )
        .scalars()
        .all()
    )
    created = 0
    for cfg in configs:
        if (
            cfg.last_polled_at is not None
            and (now - _aware(cfg.last_polled_at)).total_seconds()
            < POLL_INTERVAL_SECONDS
        ):
            continue
        since = (
            now - FIRST_LOOKBACK
            if cfg.last_polled_at is None
            else _aware(cfg.last_polled_at) - OVERLAP
        )
        try:
            created += _poll_page(db, cfg, since)
        except Exception as e:
            # Includes the app-unpublished/no-role Graph refusal — retry next
            # interval; the webhook path (if alive) is unaffected.
            log.warning(
                "meta lead poll failed for page %s: %s", cfg.external_key, e
            )
        # Stamp even on failure so a broken page retries on the interval
        # instead of hot-looping every tick.
        cfg.last_polled_at = now
        db.commit()
    return created


def _poll_page(db: Session, cfg: LeadFormConfig, since: dt.datetime) -> int:
    # Lazy import: api.lead_webhooks imports other services; importing it at
    # module scope from a service would invite an import cycle.
    from ..api.lead_webhooks import _ingest_meta_lead

    conn = db.execute(
        select(PlatformConnection).where(
            PlatformConnection.client_id == cfg.client_id,
            PlatformConnection.platform == "meta",
            PlatformConnection.status == CONN_ACTIVE,
        )
    ).scalar_one_or_none()
    if conn is None:
        return 0
    user_token = conn_svc.get_access_token(conn)
    pages = _get(
        f"{GRAPH_BASE}/me/accounts",
        {"access_token": user_token, "fields": "id,access_token", "limit": 100},
    )
    page_token = next(
        (
            p.get("access_token")
            for p in pages.get("data", [])
            if str(p.get("id")) == cfg.external_key
        ),
        None,
    )
    if not page_token:
        raise RuntimeError("page not visible to the connected user")

    filtering = json.dumps(
        [
            {
                "field": "time_created",
                "operator": "GREATER_THAN",
                "value": int(since.timestamp()),
            }
        ]
    )
    created = 0
    forms = _get(
        f"{GRAPH_BASE}/{cfg.external_key}/leadgen_forms",
        {"access_token": page_token, "fields": "id,status", "limit": 50},
    )
    for form in forms.get("data", []):
        url: Optional[str] = f"{GRAPH_BASE}/{form['id']}/leads"
        params: Optional[dict] = {
            "access_token": page_token,
            "fields": "id,created_time",
            "limit": 50,
            "filtering": filtering,
        }
        while url and created < MAX_LEADS_PER_POLL:
            data = _get(url, params)
            for lead in data.get("data", []):
                result = _ingest_meta_lead(
                    db,
                    {
                        "leadgen_id": lead.get("id"),
                        "page_id": cfg.external_key,
                        "form_id": form.get("id"),
                    },
                )
                # Per-lead commit: notify + auto-enroll rows persist even if
                # a later lead in the batch fails (webhook parity — it
                # commits per request).
                db.commit()
                if result.get("status") == "created":
                    created += 1
            next_url = (data.get("paging") or {}).get("next")
            url, params = (next_url, None) if next_url else (None, None)
    if created:
        log.info(
            "meta lead poll: %s new lead(s) for page %s", created, cfg.external_key
        )
    return created
