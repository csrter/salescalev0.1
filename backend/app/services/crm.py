"""Salescale CRM workflow logic (Phase 6).

The qualified-lead rule, in one place: Contact.qualified_at is the single
status flag. It gets set (or cleared) by exactly one function here —
set_qualified — no matter which surface triggered it: the qualification
checklist, dragging a deal into a qualified stage on the kanban, winning a
deal, or an inbound external-CRM sync. Everything downstream (the
lead-quality-adjusted CPL metric, the guarantee tracker, the client-facing
pipeline view) reads that flag through services/lead_quality.py — one
status change, many places it shows up, zero places to update by hand.

What "qualified" means is Organization data, not product code: the
Organization's structured checklist lives on
Organization.qualified_lead_criteria, and a contact with every criterion
checked is qualified. An Organization with no criteria configured uses a
plain qualified yes/no.
"""

from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Client, Organization
from ..models.crm import Contact, Deal, Pipeline, PipelineStage
from .external_sync import push_contact_update

# Sensible generic starting point — renamed/replaced per client the moment
# an Organization edits the pipeline. Nothing downstream depends on these
# names; qualified-ness rides on the is_qualified_stage flag, not the label.
DEFAULT_STAGES: List[Tuple[str, bool]] = [
    ("New", False),
    ("Contacted", False),
    ("Qualified", True),
    ("Negotiation", False),
]


def get_or_create_pipeline(db: Session, client: Client) -> Pipeline:
    """Every client gets one default pipeline on first CRM touch. Stages are
    then customizable per client (PUT /api/crm/pipelines/{id}/stages)."""
    pipeline = db.execute(
        select(Pipeline).where(
            Pipeline.organization_id == client.organization_id,
            Pipeline.client_id == client.id,
            Pipeline.is_default.is_(True),
        )
    ).scalar_one_or_none()
    if pipeline is not None:
        return pipeline
    pipeline = Pipeline(
        organization_id=client.organization_id,
        client_id=client.id,
        name="Sales pipeline",
        is_default=True,
    )
    db.add(pipeline)
    db.flush()
    for position, (name, is_qualified) in enumerate(DEFAULT_STAGES):
        db.add(
            PipelineStage(
                organization_id=client.organization_id,
                pipeline_id=pipeline.id,
                name=name,
                position=position,
                is_qualified_stage=is_qualified,
            )
        )
    db.flush()
    return pipeline


def stages_for(db: Session, pipeline: Pipeline) -> List[PipelineStage]:
    return list(
        db.execute(
            select(PipelineStage)
            .where(PipelineStage.pipeline_id == pipeline.id)
            .order_by(PipelineStage.position)
        ).scalars()
    )


def set_qualified(
    db: Session, client: Client, contact: Contact, qualified: bool
) -> Optional[str]:
    """The single write point for the qualified flag. Returns the transition
    ("qualified" / "unqualified") when the status actually changed, and
    notifies the client's external CRM sync (if opted in)."""
    if qualified and contact.qualified_at is None:
        contact.qualified_at = utcnow()
        push_contact_update(db, client, contact, event="lead.qualified")
        return "qualified"
    if not qualified and contact.qualified_at is not None:
        contact.qualified_at = None
        push_contact_update(db, client, contact, event="lead.unqualified")
        return "unqualified"
    return None


def apply_qualification(
    db: Session,
    org: Organization,
    client: Client,
    contact: Contact,
    checklist: Optional[Dict[str, bool]],
    qualified: Optional[bool],
) -> dict:
    """Apply a qualification update against the Organization's own criteria.

    With criteria configured, the checklist is the input and qualified is
    derived (every criterion true) — a structured definition, not a vibe.
    Without criteria, the explicit `qualified` boolean is the input.
    """
    criteria = org.qualified_lead_criteria or []
    if criteria:
        known = {c["key"] for c in criteria}
        current = dict(contact.qualification or {})
        if checklist:
            unknown = set(checklist) - known
            if unknown:
                raise ValueError(
                    f"unknown criteria: {', '.join(sorted(unknown))}"
                )
            current.update(checklist)
        # Reassign (not mutate) so SQLAlchemy sees the JSON change.
        contact.qualification = {k: bool(current.get(k)) for k in known}
        now_qualified = all(contact.qualification.values())
    else:
        if qualified is None:
            raise ValueError(
                "this organization has no qualified-lead criteria configured; "
                "pass `qualified` explicitly"
            )
        now_qualified = bool(qualified)
    transition = set_qualified(db, client, contact, now_qualified)
    return {
        "qualified": contact.qualified_at is not None,
        "qualified_at": contact.qualified_at,
        "qualification": contact.qualification,
        "transition": transition,
    }


def move_deal_stage(
    db: Session, client: Client, deal: Deal, stage: PipelineStage
) -> None:
    """Kanban drag: move a deal to another stage of its own pipeline.
    Entering a qualified stage marks the contact qualified — the same event
    the checklist fires, so metrics/guarantee update either way."""
    if stage.pipeline_id != deal.pipeline_id:
        raise ValueError("stage belongs to a different pipeline")
    if stage.id == deal.stage_id:
        return
    old_stage = db.get(PipelineStage, deal.stage_id)
    deal.stage_id = stage.id
    contact = db.get(Contact, deal.contact_id)
    if stage.is_qualified_stage:
        set_qualified(db, client, contact, True)
    push_contact_update(
        db,
        client,
        contact,
        event="deal.stage_changed",
        extra={
            "deal_id": deal.id,
            "stage": stage.name,
            "previous_stage": old_stage.name if old_stage else None,
        },
    )


def close_deal(db: Session, client: Client, deal: Deal, status: str) -> None:
    """Won/lost. Winning implies the lead was qualified (lead_quality already
    counts won deals as qualified — keep the flag consistent with that)."""
    if status not in ("won", "lost"):
        raise ValueError("status must be won or lost")
    deal.status = status
    deal.closed_at = utcnow()
    contact = db.get(Contact, deal.contact_id)
    if status == "won":
        set_qualified(db, client, contact, True)
    push_contact_update(
        db,
        client,
        contact,
        event="deal.status_changed",
        extra={"deal_id": deal.id, "status": status},
    )


def reopen_deal(db: Session, client: Client, deal: Deal) -> None:
    deal.status = "open"
    deal.closed_at = None
