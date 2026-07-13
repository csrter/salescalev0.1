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

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.attribution import LandingEvent
from ..models.base import utcnow
from ..models.conversions import ConversionEvent
from ..models.core import Client, Organization
from ..models.crm import (
    Activity,
    Company,
    Contact,
    ContactListMember,
    ContactTag,
    CrmTask,
    Deal,
    Pipeline,
    PipelineStage,
)
from ..models.lead_finder import EmailVerificationRecord
from ..models.outreach import (
    OutreachConversation,
    OutreachEnrollment,
    OutreachProspect,
)
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
    # first() not one-or-none: nothing constrains defaults to a single row
    # (direct imports/backfills can add another), and "get or create" should
    # pick the earliest deterministically rather than 500.
    pipeline = (
        db.execute(
            select(Pipeline)
            .where(
                Pipeline.organization_id == client.organization_id,
                Pipeline.client_id == client.id,
                Pipeline.is_default.is_(True),
            )
            .order_by(Pipeline.created_at)
            .limit(1)
        )
        .scalars()
        .first()
    )
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


def get_or_create_house_client(db: Session, organization_id: str) -> Client:
    """The agency's own prospect pipeline — one synthetic Client row per org
    (flagged is_house). Mirrors the get-or-create in api/orgs.get_house_client
    (GET /api/orgs/me/house-client) so callers outside that route — e.g. the
    iMessage/SMS inbound webhook's new-lead fallback — can resolve/create the
    same row without duplicating the race-safe logic."""
    # first() not one-or-none, same rigor as get_or_create_pipeline above: the
    # partial unique index caps it at one per org, but "get or create" picks
    # the earliest deterministically rather than assuming exactly one row.
    client = (
        db.execute(
            select(Client)
            .where(
                Client.organization_id == organization_id,
                Client.is_house.is_(True),
            )
            .order_by(Client.created_at)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if client is not None:
        return client
    client = Client(
        organization_id=organization_id,
        name="House",
        status="active",
        is_house=True,
    )
    db.add(client)
    try:
        db.commit()
    except IntegrityError:
        # Two callers raced to create the house client at once — the partial
        # unique index let exactly one create through, so read that winner
        # back instead of surfacing a 500.
        db.rollback()
        client = (
            db.execute(
                select(Client)
                .where(
                    Client.organization_id == organization_id,
                    Client.is_house.is_(True),
                )
                .order_by(Client.created_at)
                .limit(1)
            )
            .scalars()
            .first()
        )
        if client is None:  # pragma: no cover — index fired, row must exist
            raise HTTPException(500, "House client creation raced")
    return client


def stages_for(db: Session, pipeline: Pipeline) -> List[PipelineStage]:
    return list(
        db.execute(
            select(PipelineStage)
            .where(PipelineStage.pipeline_id == pipeline.id)
            .order_by(PipelineStage.position)
        ).scalars()
    )


def get_or_create_company(
    db: Session, organization_id: str, client_id: str, name: str
) -> Optional[str]:
    """Resolve a company name to a company id, get-or-create scoped to
    (organization_id, client_id). Case-insensitive exact-name match, so a batch
    of rows naming the same company links to one row. Returns None for a blank
    name."""
    name = (name or "").strip()
    if not name:
        return None
    existing = (
        db.execute(
            select(Company)
            .where(
                Company.organization_id == organization_id,
                Company.client_id == client_id,
                func.lower(Company.name) == name.lower(),
            )
            .order_by(Company.created_at)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing.id
    company = Company(
        organization_id=organization_id, client_id=client_id, name=name
    )
    db.add(company)
    db.flush()
    return company.id


def delete_contact(db: Session, contact: Contact) -> None:
    """Delete a contact and everything that references it, so no row is left
    orphaned or dangling on a not-null FK. Owned CRM children (deals, notes,
    tasks, tag links) are deleted; looser historical/ledger references
    (attribution, conversions, verification records, outreach) are detached to
    keep metrics and metering counts intact."""
    cid = contact.id
    deal_ids = [
        r[0] for r in db.execute(select(Deal.id).where(Deal.contact_id == cid))
    ]
    if deal_ids:
        db.execute(
            update(Activity)
            .where(Activity.deal_id.in_(deal_ids))
            .values(deal_id=None)
        )
        db.execute(
            update(CrmTask)
            .where(CrmTask.deal_id.in_(deal_ids))
            .values(deal_id=None)
        )
    db.execute(delete(Activity).where(Activity.contact_id == cid))
    db.execute(delete(CrmTask).where(CrmTask.contact_id == cid))
    db.execute(delete(ContactTag).where(ContactTag.contact_id == cid))
    db.execute(delete(ContactListMember).where(ContactListMember.contact_id == cid))
    db.execute(delete(Deal).where(Deal.contact_id == cid))
    for model in (
        LandingEvent,
        ConversionEvent,
        EmailVerificationRecord,
        OutreachConversation,
        OutreachEnrollment,
        OutreachProspect,
    ):
        db.execute(
            update(model).where(model.contact_id == cid).values(contact_id=None)
        )
    db.delete(contact)


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
