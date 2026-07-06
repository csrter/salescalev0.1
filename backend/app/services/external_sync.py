"""Optional per-client two-way external CRM sync (Phase 6 task 5).

Some clients' nurture automation (e.g. GHL SMS sequences) still lives in an
external CRM during a transition. Salescale stays the source of truth for
reporting and qualified-lead status; this module keeps the external system
informed (outbound) and accepts its status changes (inbound) so existing
automation keeps firing — without requiring any of it for clients who
don't need it.

Config is per-client, opt-in, admin-managed (PUT /api/clients/{id}/
external-sync), stored in client.metric_settings["external_sync"]:
    {"enabled": bool, "url": <their webhook>, "secret": <shared secret>}

Outbound: POST one JSON body per status change to the configured URL,
signed with "sha256=<hex HMAC of raw body, keyed by secret>" in
X-Salescale-Signature-256 (same scheme Meta uses toward us — easy for the
other side to verify). Failures are recorded on the return value and never
raised: a downed external CRM must not block CRM writes, mirroring the
per-platform isolation rule everywhere else in this codebase.

Inbound: the external system POSTs to /api/crm/external-sync/{client_id}
with the shared secret; matching is by external_contact_id → our stored
mapping, then salescale_contact_id, then email/phone (lead_ingest's upsert
rules) — updates in place, so no duplicates on either side. Inbound applies
changes directly (no outbound push back), which is the echo-loop guard.
"""

import hashlib
import hmac
import json
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Client
from ..models.crm import Contact, Deal, PipelineStage
from . import lead_ingest

OUTBOUND_TIMEOUT_S = 5


def get_config(client: Client) -> Optional[Dict[str, Any]]:
    config = (client.metric_settings or {}).get("external_sync")
    if config and config.get("enabled") and config.get("url"):
        return config
    return None


def _contact_payload(contact: Contact) -> Dict[str, Any]:
    return {
        "salescale_contact_id": contact.id,
        "external_contact_id": contact.external_crm_id,
        "email": contact.email,
        "phone": contact.phone,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "qualified": contact.qualified_at is not None,
        "qualified_at": contact.qualified_at.isoformat()
        if contact.qualified_at
        else None,
    }


def push_contact_update(
    db: Session,
    client: Client,
    contact: Contact,
    event: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Send one status change out, if this client opted in. Returns a small
    result dict for the caller's response, or None when sync isn't
    configured. Never raises."""
    config = get_config(client)
    if config is None:
        return None
    body = {
        "event": event,  # lead.created | lead.qualified | lead.unqualified |
        #                  deal.stage_changed | deal.status_changed
        "sent_at": utcnow().isoformat(),
        "contact": _contact_payload(contact),
        **(extra or {}),
    }
    raw = json.dumps(body, separators=(",", ":")).encode()
    signature = hmac.new(
        str(config.get("secret") or "").encode(), raw, hashlib.sha256
    ).hexdigest()
    try:
        resp = httpx.post(
            config["url"],
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Salescale-Signature-256": f"sha256={signature}",
            },
            timeout=OUTBOUND_TIMEOUT_S,
        )
        return {"event": event, "ok": resp.status_code < 300, "status_code": resp.status_code}
    except Exception as e:
        return {"event": event, "ok": False, "error": str(e)}


def verify_inbound_secret(client: Client, provided: Optional[str]) -> bool:
    config = get_config(client)
    if config is None or not config.get("secret"):
        return False
    return hmac.compare_digest(str(config["secret"]), provided or "")


def apply_inbound(db: Session, client: Client, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one external status change. Match order: our stored external id
    mapping, their copy of our contact id, then email/phone upsert (which
    creates the contact if the lead only ever existed externally)."""
    external_id = payload.get("external_contact_id")
    contact: Optional[Contact] = None
    if external_id:
        contact = db.execute(
            select(Contact).where(
                Contact.organization_id == client.organization_id,
                Contact.client_id == client.id,
                Contact.external_crm_id == str(external_id),
            )
        ).scalar_one_or_none()
    if contact is None and payload.get("salescale_contact_id"):
        candidate = db.get(Contact, payload["salescale_contact_id"])
        if (
            candidate is not None
            and candidate.organization_id == client.organization_id
            and candidate.client_id == client.id
        ):
            contact = candidate
    created = False
    if contact is None:
        contact, created = lead_ingest.upsert_contact(
            db,
            client,
            email=payload.get("email"),
            phone=payload.get("phone"),
            first_name=payload.get("first_name"),
            last_name=payload.get("last_name"),
            source="external_crm",
        )
    if external_id and contact.external_crm_id is None:
        contact.external_crm_id = str(external_id)

    changed = []
    if "qualified" in payload and payload["qualified"] is not None:
        want = bool(payload["qualified"])
        if want and contact.qualified_at is None:
            contact.qualified_at = utcnow()
            changed.append("qualified")
        elif not want and contact.qualified_at is not None:
            contact.qualified_at = None
            changed.append("unqualified")

    if payload.get("stage"):
        moved = _move_open_deal_to_stage(db, client, contact, str(payload["stage"]))
        if moved:
            changed.append("stage")

    db.commit()
    return {
        "contact_id": contact.id,
        "created": created,
        "applied": changed,
    }


def _move_open_deal_to_stage(
    db: Session, client: Client, contact: Contact, stage_name: str
) -> bool:
    deal = db.execute(
        select(Deal)
        .where(
            Deal.organization_id == client.organization_id,
            Deal.client_id == client.id,
            Deal.contact_id == contact.id,
            Deal.status == "open",
        )
        .order_by(Deal.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if deal is None:
        return False
    stage = db.execute(
        select(PipelineStage).where(
            PipelineStage.organization_id == client.organization_id,
            PipelineStage.pipeline_id == deal.pipeline_id,
            PipelineStage.name.ilike(stage_name),
        )
    ).scalar_one_or_none()
    if stage is None or stage.id == deal.stage_id:
        return False
    deal.stage_id = stage.id
    if stage.is_qualified_stage and contact.qualified_at is None:
        contact.qualified_at = utcnow()
    return True
