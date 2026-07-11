"""Salescale CRM endpoints (Phase 6).

Access model, same two-level TenantScope as everywhere else:
- Team roles (owner/admin/member): full read/write on their Organization's
  CRM. Stage configuration is admin-gated (client setup, not day-to-day).
- Client role: read-only view of their own pipeline. Field-level filtering
  happens here on the backend — internal-only activities are excluded from
  the query itself, contacts serialize through ContactOutPublic (no
  checklist/external mapping/platform linkage), and tasks are team-only —
  so nothing internal exists in the response for the UI to "hide".
- The external-sync inbound webhook is public but secret-authenticated
  per client (services/external_sync.py) — no JWT, same trust model as
  /api/track/lead.

CRM writes never touch a live ad platform, so they are not staged changes
(see test_manage_flow's structural allowlist).
"""

import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin, require_team
from ..models.attribution import LandingEvent
from ..models.core import Client, Organization, User
from ..models.crm import (
    Activity,
    Contact,
    CrmTask,
    CustomFieldDefinition,
    Deal,
    Pipeline,
    PipelineStage,
)
from ..schemas import (
    ACTIVITY_TYPES,
    ActivityCreateIn,
    ActivityOut,
    ContactCreateIn,
    ContactOutPublic,
    ContactOutTeam,
    ContactUpdateIn,
    CrmTaskCreateIn,
    CrmTaskOut,
    CrmTaskUpdateIn,
    CsvImportIn,
    DealCreateIn,
    DealOut,
    DealUpdateIn,
    QualificationIn,
    StageOut,
    StagesUpdateIn,
)
from ..ratelimit import rate_limit
from ..services import crm as crm_svc
from ..services import custom_fields as custom_fields_svc
from ..services import entitlements, external_sync, metrics
from ..models.base import utcnow

router = APIRouter(prefix="/api/crm", tags=["crm"])

# Public, secret-authenticated inbound sync — tight per-IP cap so the per-client
# secret can't be brute-forced online (and to blunt DoS).
_sync_limit = rate_limit("external_sync", limit=30, window_seconds=60)


def _client_for(db: Session, scope: TenantScope, client_id: str) -> Client:
    scope.check_client_id(client_id)
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Not found")
    scope.check_organization_id(client.organization_id)
    return client


def _serialize_contact(db: Session, contact: Contact, scope: TenantScope) -> dict:
    """Serialize a contact for the caller's role, with custom-field values
    injected. Client-role reads are filtered to visible_to_clients fields at the
    data layer (visible_values), so a hidden field never appears in the payload."""
    model = ContactOutTeam if scope.is_team else ContactOutPublic
    out = model.model_validate(contact).model_dump()
    out["custom_fields"] = custom_fields_svc.visible_values(
        db, scope.organization_id, contact, is_team=scope.is_team
    )
    return out


def _attribution_for(
    db: Session, client: Client, contacts: List[Contact]
) -> Dict[str, dict]:
    """Per-contact attribution summary for list/board views: the platform
    the lead is attributed to (same rules as the metrics layer) plus the
    UTM/click-id evidence from its landing event."""
    platform_by_contact = metrics.contact_platforms(db, client, contacts)
    ids = [c.id for c in contacts]
    events = (
        db.execute(
            select(LandingEvent).where(
                LandingEvent.organization_id == client.organization_id,
                LandingEvent.contact_id.in_(ids) if ids else False,
            )
        )
        .scalars()
        .all()
    )
    event_by_contact: Dict[str, LandingEvent] = {}
    for e in events:
        event_by_contact.setdefault(e.contact_id, e)
    out: Dict[str, dict] = {}
    for c in contacts:
        e = event_by_contact.get(c.id)
        out[c.id] = {
            "platform": platform_by_contact.get(c.id),
            "utm_source": e.utm_source if e else None,
            "utm_campaign": e.utm_campaign if e else None,
            "has_click_id": bool(e and (e.fbclid or e.gclid)),
        }
    return out


# --- Pipeline board ---


@router.get("/board")
def get_board(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Everything the kanban needs in one call: the client's pipeline,
    ordered stages, open deals grouped by stage, and closed-deal counts.
    Client-role readable (their own pipeline, read-only)."""
    client = _client_for(db, scope, client_id)
    pipeline = crm_svc.get_or_create_pipeline(db, client)
    db.commit()  # persist the auto-created default before reading back
    stages = crm_svc.stages_for(db, pipeline)
    deals = (
        db.execute(
            select(Deal).where(
                Deal.organization_id == client.organization_id,
                Deal.client_id == client.id,
                Deal.pipeline_id == pipeline.id,
            )
        )
        .scalars()
        .all()
    )
    contact_ids = {d.contact_id for d in deals}
    contacts = (
        db.execute(
            select(Contact).where(
                Contact.organization_id == client.organization_id,
                Contact.client_id == client.id,
                Contact.id.in_(contact_ids) if contact_ids else False,
            )
        )
        .scalars()
        .all()
    )
    attribution = _attribution_for(db, client, contacts)
    contact_out = {
        c.id: {
            **_serialize_contact(db, c, scope),
            "attribution": attribution.get(c.id),
        }
        for c in contacts
    }
    open_by_stage: Dict[str, list] = {s.id: [] for s in stages}
    won, lost = [], []
    for d in deals:
        row = DealOut.model_validate(d).model_dump()
        if d.status == "won":
            won.append(row)
        elif d.status == "lost":
            lost.append(row)
        elif d.stage_id in open_by_stage:
            open_by_stage[d.stage_id].append(row)
    for rows in open_by_stage.values():
        rows.sort(key=lambda r: r["created_at"])
    return {
        "pipeline": {"id": pipeline.id, "name": pipeline.name},
        "stages": [StageOut.model_validate(s).model_dump() for s in stages],
        "deals_by_stage": open_by_stage,
        "won": won,
        "lost": lost,
        "contacts": contact_out,
        "read_only": not scope.is_team,
    }


@router.put("/pipelines/{pipeline_id}/stages")
def update_stages(
    pipeline_id: str,
    body: StagesUpdateIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replace the stage list (order = payload order). Stages referenced by
    id are renamed/reordered in place so their deals follow; omitted stages
    are deleted (blocked while they still hold open deals). Admin-gated:
    pipeline design is client setup, not day-to-day deal work."""
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None or pipeline.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    existing = {
        s.id: s
        for s in db.execute(
            select(PipelineStage).where(PipelineStage.pipeline_id == pipeline.id)
        ).scalars()
    }
    unknown = [s.id for s in body.stages if s.id and s.id not in existing]
    if unknown:
        raise HTTPException(400, "Unknown stage id(s) for this pipeline")

    keep_ids = {s.id for s in body.stages if s.id}
    for stage_id, stage in existing.items():
        if stage_id in keep_ids:
            continue
        holds = db.execute(
            select(Deal.id)
            .where(Deal.stage_id == stage_id, Deal.status == "open")
            .limit(1)
        ).first()
        if holds:
            raise HTTPException(
                400,
                f"Stage {stage.name!r} still has open deals — move them first",
            )
        db.delete(stage)
    # Two-pass positioning dodges the (pipeline_id, position) unique
    # constraint colliding mid-shuffle.
    for offset, item in enumerate(body.stages):
        if item.id:
            existing[item.id].position = 1000 + offset
    db.flush()
    for position, item in enumerate(body.stages):
        if item.id:
            stage = existing[item.id]
            stage.name = item.name
            stage.position = position
            stage.is_qualified_stage = item.is_qualified_stage
        else:
            db.add(
                PipelineStage(
                    organization_id=pipeline.organization_id,
                    pipeline_id=pipeline.id,
                    name=item.name,
                    position=position,
                    is_qualified_stage=item.is_qualified_stage,
                )
            )
    db.commit()
    return {
        "stages": [
            StageOut.model_validate(s).model_dump()
            for s in crm_svc.stages_for(db, pipeline)
        ]
    }


# --- Contacts / leads ---


@router.get("/contacts")
def list_contacts(
    client_id: str,
    limit: int = 200,
    sort: Optional[str] = None,
    sort_dir: str = "desc",
    cf_filter: Optional[str] = Query(
        default=None,
        description="JSON list of custom-field filters: [{key, op, value}]",
    ),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """List a client's contacts. Custom fields join the same query path as
    system fields for filtering (`cf_filter`) and sorting (`sort` = a custom
    field key), so Postgres can use the GIN index on custom_fields."""
    client = _client_for(db, scope, client_id)
    stmt = select(Contact).where(
        Contact.organization_id == client.organization_id,
        Contact.client_id == client.id,
    )

    definitions = custom_fields_svc.definitions_by_key(db, scope.organization_id)
    if cf_filter:
        try:
            filters = json.loads(cf_filter)
        except (ValueError, TypeError):
            raise HTTPException(400, "cf_filter must be valid JSON")
        if not scope.is_team:
            # A client user can only filter on fields they're allowed to see.
            filters = [
                f
                for f in filters
                if definitions.get(f.get("key"))
                and definitions[f["key"]].visible_to_clients
            ]
        for clause in custom_fields_svc.build_filter_clauses(definitions, filters):
            stmt = stmt.where(clause)

    order = None
    if sort:
        allowed = scope.is_team or (
            definitions.get(sort) and definitions[sort].visible_to_clients
        )
        if allowed:
            order = custom_fields_svc.build_sort(
                definitions, sort, desc=sort_dir != "asc"
            )
    stmt = stmt.order_by(order if order is not None else Contact.created_at.desc())

    contacts = list(db.execute(stmt.limit(min(limit, 500))).scalars())
    attribution = _attribution_for(db, client, contacts)
    return [
        {
            **_serialize_contact(db, c, scope),
            "attribution": attribution.get(c.id),
        }
        for c in contacts
    ]


@router.post("/contacts", status_code=201)
def create_contact(
    body: ContactCreateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, body.client_id)
    if not (body.email or body.phone or body.first_name or body.last_name):
        raise HTTPException(400, "Provide at least one contact field")
    contact = Contact(
        organization_id=client.organization_id,
        client_id=client.id,
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email.lower() if body.email else None,
        phone=body.phone,
        source="manual",
    )
    try:
        custom_fields_svc.validate_and_merge(
            db,
            scope.organization_id,
            contact,
            body.custom_fields,
            enforce_required=True,
        )
    except custom_fields_svc.CustomFieldError as e:
        raise HTTPException(400, str(e))
    db.add(contact)
    db.commit()
    return _serialize_contact(db, contact, scope)


@router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: str,
    body: ContactUpdateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Edit a contact's identity fields and/or custom-field values. Custom-field
    writes go through the same validation as create; only the keys present are
    changed (a null clears one). Required is enforced on create, not on partial
    edits."""
    contact = scope.get_or_404(db, Contact, contact_id)
    if body.first_name is not None:
        contact.first_name = body.first_name
    if body.last_name is not None:
        contact.last_name = body.last_name
    if body.email is not None:
        contact.email = body.email.lower() if body.email else None
    if body.phone is not None:
        contact.phone = body.phone
    if body.custom_fields is not None:
        try:
            custom_fields_svc.validate_and_merge(
                db,
                scope.organization_id,
                contact,
                body.custom_fields,
                enforce_required=False,
            )
        except custom_fields_svc.CustomFieldError as e:
            raise HTTPException(400, str(e))
    db.commit()
    return _serialize_contact(db, contact, scope)


@router.get("/contacts/{contact_id}")
def get_contact(
    contact_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    contact = scope.get_or_404(db, Contact, contact_id)
    client = db.get(Client, contact.client_id)
    activity_stmt = (
        select(Activity)
        .where(
            Activity.organization_id == scope.organization_id,
            Activity.contact_id == contact.id,
        )
        .order_by(Activity.occurred_at.desc())
    )
    if not scope.is_team:
        # Field-level filtering at the query, not the serializer: an
        # internal-only entry never reaches a client-role response at all.
        activity_stmt = activity_stmt.where(Activity.is_internal.is_(False))
    activities = db.execute(activity_stmt).scalars().all()
    deals = (
        db.execute(
            select(Deal).where(
                Deal.organization_id == scope.organization_id,
                Deal.contact_id == contact.id,
            )
        )
        .scalars()
        .all()
    )
    attribution = _attribution_for(db, client, [contact])
    out = {
        **_serialize_contact(db, contact, scope),
        "attribution": attribution.get(contact.id),
        "activities": [
            ActivityOut.model_validate(a).model_dump() for a in activities
        ],
        "deals": [DealOut.model_validate(d).model_dump() for d in deals],
    }
    if scope.is_team:
        tasks = (
            db.execute(
                select(CrmTask)
                .where(
                    CrmTask.organization_id == scope.organization_id,
                    CrmTask.contact_id == contact.id,
                )
                .order_by(CrmTask.due_at.is_(None), CrmTask.due_at)
            )
            .scalars()
            .all()
        )
        out["tasks"] = [CrmTaskOut.model_validate(t).model_dump() for t in tasks]
    return out


@router.put("/contacts/{contact_id}/qualification")
def set_qualification(
    contact_id: str,
    body: QualificationIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """THE qualified-lead status change. Feeding LQA-CPL and the guarantee
    tracker needs nothing beyond this call — they read the same flag."""
    contact = scope.get_or_404(db, Contact, contact_id)
    client = db.get(Client, contact.client_id)
    org = db.get(Organization, scope.organization_id)
    try:
        result = crm_svc.apply_qualification(
            db, org, client, contact, body.checklist, body.qualified
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return result


# System contact fields a CSV column may map to directly (everything else maps
# to a custom field or is skipped).
_CSV_SYSTEM_TARGETS = {"first_name", "last_name", "email", "phone"}


@router.post("/contacts/import")
def import_contacts(
    body: CsvImportIn,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """CSV import with column mapping (task 13). New fields can be created inline
    during mapping (type inferred client-side, confirmed by the user). Each
    row's values validate through the same data-access layer; a bad value lands
    in the per-row error report rather than aborting the whole file.

    Admin-gated (field creation is admin work). Body carries already-parsed rows
    keyed by CSV header, the header->target mapping, and any new-field defs.
    """
    client = _client_for(db, scope, body.client_id)
    org = db.get(Organization, scope.organization_id)

    # 1) Create inline-defined fields first, so their columns can map to them.
    created_fields: List[dict] = []
    column_to_new_key: Dict[str, str] = {}
    for nf in body.new_fields or []:
        entitlements.enforce_can_add_custom_field(db, org)
        try:
            options = custom_fields_svc.normalize_options(
                nf.field_type,
                [o.model_dump() for o in nf.options] if nf.options else None,
            )
        except custom_fields_svc.CustomFieldError as e:
            raise HTTPException(400, f"{nf.label}: {e}")
        key = custom_fields_svc.generate_key(db, org.id, "contact", nf.label)
        sort_order = len(
            custom_fields_svc.list_definitions(
                db, org.id, "contact", include_archived=True
            )
        )
        definition = CustomFieldDefinition(
            organization_id=org.id,
            entity_type="contact",
            label=nf.label.strip(),
            key=key,
            field_type=nf.field_type,
            options=options,
            sort_order=sort_order,
        )
        db.add(definition)
        db.flush()  # so generate_key sees it for the next new field
        column_to_new_key[nf.column] = key
        created_fields.append({"column": nf.column, "key": key, "label": nf.label})

    # 2) Resolve each mapped column to a concrete target.
    def _resolve(column: str, target: str) -> Optional[tuple]:
        if target in _CSV_SYSTEM_TARGETS:
            return ("system", target)
        if target == "new":
            key = column_to_new_key.get(column)
            return ("custom", key) if key else None
        if target.startswith("custom:"):
            return ("custom", target.split(":", 1)[1])
        return None  # "skip" or unknown

    resolved = {col: _resolve(col, tgt) for col, tgt in body.mapping.items()}

    imported = 0
    failed: List[dict] = []
    for idx, row in enumerate(body.rows):
        identity: Dict[str, Optional[str]] = {}
        custom: Dict[str, object] = {}
        for column, target in resolved.items():
            if target is None:
                continue
            kind, name = target
            raw = row.get(column)
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                continue
            if kind == "system":
                identity[name] = str(raw).strip()
            else:
                custom[name] = raw
        if not any(identity.get(k) for k in _CSV_SYSTEM_TARGETS):
            failed.append(
                {"row": idx, "error": "no identity field (name/email/phone) mapped"}
            )
            continue
        contact = Contact(
            organization_id=client.organization_id,
            client_id=client.id,
            first_name=identity.get("first_name"),
            last_name=identity.get("last_name"),
            email=(identity["email"].lower() if identity.get("email") else None),
            phone=identity.get("phone"),
            source="csv_import",
        )
        try:
            custom_fields_svc.validate_and_merge(
                db, scope.organization_id, contact, custom, enforce_required=True
            )
        except custom_fields_svc.CustomFieldError as e:
            failed.append({"row": idx, "error": str(e)})
            continue
        db.add(contact)
        imported += 1

    db.commit()
    return {
        "imported": imported,
        "failed": failed,
        "created_fields": created_fields,
    }


# --- Deals ---


@router.post("/deals", status_code=201, response_model=DealOut)
def create_deal(
    body: DealCreateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, body.client_id)
    contact = scope.get_or_404(db, Contact, body.contact_id)
    if contact.client_id != client.id:
        raise HTTPException(400, "Contact belongs to a different client")
    pipeline = crm_svc.get_or_create_pipeline(db, client)
    stages = crm_svc.stages_for(db, pipeline)
    if body.stage_id:
        stage = next((s for s in stages if s.id == body.stage_id), None)
        if stage is None:
            raise HTTPException(400, "Unknown stage for this client's pipeline")
    else:
        stage = stages[0]
    name = body.name or " ".join(
        p for p in (contact.first_name, contact.last_name) if p
    ) or contact.email or "New deal"
    deal = Deal(
        organization_id=client.organization_id,
        client_id=client.id,
        contact_id=contact.id,
        pipeline_id=pipeline.id,
        stage_id=stage.id,
        name=name,
        value_cents=body.value_cents,
    )
    db.add(deal)
    if stage.is_qualified_stage:
        # Created straight into a qualified stage — same event as a drag.
        crm_svc.set_qualified(db, client, contact, True)
    db.commit()
    return DealOut.model_validate(deal)


@router.patch("/deals/{deal_id}", response_model=DealOut)
def update_deal(
    deal_id: str,
    body: DealUpdateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    deal = scope.get_or_404(db, Deal, deal_id)
    client = db.get(Client, deal.client_id)
    if body.name is not None:
        deal.name = body.name
    if body.value_cents is not None:
        deal.value_cents = body.value_cents
    if body.stage_id is not None:
        stage = db.get(PipelineStage, body.stage_id)
        if stage is None or stage.organization_id != scope.organization_id:
            raise HTTPException(404, "Not found")
        try:
            crm_svc.move_deal_stage(db, client, deal, stage)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if body.status is not None and body.status != deal.status:
        if body.status == "open":
            crm_svc.reopen_deal(db, client, deal)
        else:
            try:
                crm_svc.close_deal(db, client, deal, body.status)
            except ValueError as e:
                raise HTTPException(400, str(e))
    if body.stage_id is not None or (
        body.status is not None and deal.status != "open"
    ):
        # Outreach CRM-sync rule: a pipeline move or a won/lost close exits
        # any active outreach sequence for this contact — no manual cleanup.
        from ..services import outreach_sequences

        outreach_sequences.exit_for_contact(db, deal.contact_id)
    db.commit()
    return DealOut.model_validate(deal)


# --- Activities ---


@router.get("/activities", response_model=List[ActivityOut])
def list_activities(
    client_id: str,
    contact_id: Optional[str] = None,
    limit: int = 100,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    stmt = (
        select(Activity)
        .where(
            Activity.organization_id == client.organization_id,
            Activity.client_id == client.id,
        )
        .order_by(Activity.occurred_at.desc())
        .limit(min(limit, 500))
    )
    if contact_id:
        stmt = stmt.where(Activity.contact_id == contact_id)
    if not scope.is_team:
        stmt = stmt.where(Activity.is_internal.is_(False))
    return db.execute(stmt).scalars().all()


@router.post("/activities", status_code=201, response_model=ActivityOut)
def create_activity(
    body: ActivityCreateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    if body.type not in ACTIVITY_TYPES:
        raise HTTPException(
            400, f"type must be one of {', '.join(sorted(ACTIVITY_TYPES))}"
        )
    contact = scope.get_or_404(db, Contact, body.contact_id)
    activity = Activity(
        organization_id=contact.organization_id,
        client_id=contact.client_id,
        contact_id=contact.id,
        type=body.type,
        body=body.body,
        is_internal=body.is_internal,
        occurred_at=body.occurred_at or utcnow(),
        created_by_user_id=user.id,
    )
    db.add(activity)
    db.commit()
    return activity


# --- Tasks / follow-ups (team-only: internal work management) ---


@router.get("/tasks", response_model=List[CrmTaskOut])
def list_tasks(
    client_id: str,
    contact_id: Optional[str] = None,
    open_only: bool = True,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    stmt = (
        select(CrmTask)
        .where(
            CrmTask.organization_id == client.organization_id,
            CrmTask.client_id == client.id,
        )
        .order_by(CrmTask.due_at.is_(None), CrmTask.due_at)
    )
    if contact_id:
        stmt = stmt.where(CrmTask.contact_id == contact_id)
    if open_only:
        stmt = stmt.where(CrmTask.completed_at.is_(None))
    return db.execute(stmt.limit(500)).scalars().all()


@router.post("/tasks", status_code=201, response_model=CrmTaskOut)
def create_task(
    body: CrmTaskCreateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, body.client_id)
    if body.contact_id:
        contact = scope.get_or_404(db, Contact, body.contact_id)
        if contact.client_id != client.id:
            raise HTTPException(400, "Contact belongs to a different client")
    if body.assigned_to_user_id:
        assignee = db.get(User, body.assigned_to_user_id)
        if (
            assignee is None
            or assignee.organization_id != scope.organization_id
            or assignee.role == "client"
        ):
            raise HTTPException(400, "Assignee must be a team member")
    task = CrmTask(
        organization_id=client.organization_id,
        client_id=client.id,
        contact_id=body.contact_id,
        deal_id=body.deal_id,
        title=body.title,
        due_at=body.due_at,
        assigned_to_user_id=body.assigned_to_user_id or user.id,
    )
    db.add(task)
    db.commit()
    return task


@router.patch("/tasks/{task_id}", response_model=CrmTaskOut)
def update_task(
    task_id: str,
    body: CrmTaskUpdateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    task = scope.get_or_404(db, CrmTask, task_id)
    if body.title is not None:
        task.title = body.title
    if body.due_at is not None:
        task.due_at = body.due_at
    if body.assigned_to_user_id is not None:
        assignee = db.get(User, body.assigned_to_user_id)
        if (
            assignee is None
            or assignee.organization_id != scope.organization_id
            or assignee.role == "client"
        ):
            raise HTTPException(400, "Assignee must be a team member")
        task.assigned_to_user_id = body.assigned_to_user_id
    if body.completed is not None:
        task.completed_at = utcnow() if body.completed else None
    db.commit()
    return task


# --- External CRM sync: inbound webhook (public + per-client secret) ---


@router.post("/external-sync/{client_id}")
def external_sync_inbound(
    client_id: str,
    payload: dict,
    x_salescale_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
    _: None = _sync_limit,
):
    """The external CRM pushes status changes here. Auth = the per-client
    shared secret; one 403 shape for unknown client / sync-not-enabled /
    wrong secret so the endpoint doesn't confirm which clients exist."""
    client = db.get(Client, client_id)
    if client is None or not external_sync.verify_inbound_secret(
        client, x_salescale_secret
    ):
        raise HTTPException(403, "Invalid secret")
    return external_sync.apply_inbound(db, client, payload)
