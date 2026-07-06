"""Lead-quality source abstraction for the lead-quality-adjusted CPL metric.

Salescale CRM is the native source of truth: a lead (Contact) counts as
QUALIFIED when it has a Deal that either sits in a pipeline stage flagged
`is_qualified_stage` or has already been won. Each Organization defines its
own qualified stage(s) on its pipelines (Phase 6 builds the workflow UI);
nothing here assumes any particular Organization's criteria.

Some clients keep nurture automation in an external CRM during a transition
(CLAUDE.md: optional per-client external sync). For those,
`Client.lead_quality_source == "external"` routes through a provider
registered in EXTERNAL_PROVIDERS — an interface, deliberately not hardcoded
to any one CRM's API shape. A provider only ever answers one question:
"which of this client's contact ids are qualified?" Everything downstream
(metrics math, reporting) is source-agnostic.
"""

from typing import Callable, Dict, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.core import Client
from ..models.crm import Contact, Deal, PipelineStage

# provider name -> fn(db, client) -> set of qualified contact ids.
# External adapters (e.g. a GHL sync in Phase 6+) register here; the name is
# selected per client via metric_settings["external_crm"]["provider"].
EXTERNAL_PROVIDERS: Dict[str, Callable[[Session, Client], Set[str]]] = {}


def salescale_qualified_contact_ids(db: Session, client: Client) -> Set[str]:
    """Native source: contacts explicitly marked qualified (Phase 6 workflow
    sets Contact.qualified_at — the single status flag the checklist, the
    kanban qualified-stage move, and external sync all write), plus contacts
    with a deal in a qualified stage or won (belt-and-braces: a deal that
    reached those states is a qualified lead even if nobody touched the
    checklist)."""
    marked = db.execute(
        select(Contact.id).where(
            Contact.client_id == client.id,
            Contact.organization_id == client.organization_id,
            Contact.qualified_at.is_not(None),
        )
    ).all()
    rows = db.execute(
        select(Deal.contact_id)
        .join(PipelineStage, Deal.stage_id == PipelineStage.id)
        .where(
            Deal.client_id == client.id,
            Deal.organization_id == client.organization_id,
            (PipelineStage.is_qualified_stage == True)  # noqa: E712
            | (Deal.status == "won"),
        )
    ).all()
    return {r[0] for r in marked} | {r[0] for r in rows}


def qualified_contact_ids(db: Session, client: Client) -> Set[str]:
    """Route to the client's configured quality source. Unknown/missing
    external provider degrades to the native source rather than erroring —
    a misconfigured client should see conservative numbers, not a 500."""
    if client.lead_quality_source == "external":
        provider_name = ((client.metric_settings or {}).get("external_crm") or {}).get(
            "provider"
        )
        provider = EXTERNAL_PROVIDERS.get(provider_name or "")
        if provider is not None:
            return provider(db, client)
    return salescale_qualified_contact_ids(db, client)
