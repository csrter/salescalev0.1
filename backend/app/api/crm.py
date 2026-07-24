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

import datetime as dt
import json
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
)
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin, require_team
from ..models.attribution import LandingEvent
from ..models.audit import AUDIT_SUCCESS, AuditLogEntry
from ..models.core import Client, Organization, User
from ..models.crm import (
    Activity,
    Company,
    Contact,
    ContactList,
    ContactListMember,
    CrmTask,
    CustomFieldDefinition,
    Deal,
    Pipeline,
    PipelineStage,
    ResearchFieldDef,
)
from ..models.lead_finder import VERIFICATION_STATUSES, EnrichmentJob
from ..schemas import (
    ACTIVITY_TYPES,
    ActivityCreateIn,
    ActivityOut,
    ContactBulkDeleteIn,
    ContactBulkUpdateIn,
    ContactCreateIn,
    ContactListCreateIn,
    ContactListMembersIn,
    ContactListOut,
    ContactListRenameIn,
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
    ResearchFieldIn,
    ResearchFieldOut,
    ResearchFieldPatch,
    ResearchRunIn,
    StageOut,
    StagesUpdateIn,
    VerifyContactsIn,
)
from ..ratelimit import rate_limit
from ..services import crm as crm_svc
from ..services import custom_fields as custom_fields_svc
from ..services import email_verification
from ..services import entitlements, external_sync, metrics
from ..services import lead_finder as lead_finder_svc
from ..services import research as research_svc
from ..services import sms_consent
from ..models.base import utcnow

router = APIRouter(prefix="/api/crm", tags=["crm"])
log = logging.getLogger("salescale.crm")

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


def _company_names(db: Session, contacts: List[Contact]) -> Dict[str, dict]:
    """Batch-resolve company_id -> {name + enrichment firmographics} for a set
    of contacts, so list views don't fire one query per row."""
    ids = {c.company_id for c in contacts if c.company_id}
    if not ids:
        return {}
    rows = db.execute(
        select(
            Company.id,
            Company.name,
            Company.description,
            Company.estimated_revenue,
            Company.employee_count,
        ).where(Company.id.in_(ids))
    ).all()
    return {
        cid: {
            "name": name,
            "description": description,
            "estimated_revenue": revenue,
            "employee_count": employees,
        }
        for cid, name, description, revenue, employees in rows
    }


def _serialize_contact(
    db: Session,
    contact: Contact,
    scope: TenantScope,
    company_names: Optional[Dict[str, Optional[str]]] = None,
) -> dict:
    """Serialize a contact for the caller's role, with custom-field values
    injected. Client-role reads are filtered to visible_to_clients fields at the
    data layer (visible_values), so a hidden field never appears in the payload."""
    model = ContactOutTeam if scope.is_team else ContactOutPublic
    out = model.model_validate(contact).model_dump()
    out["custom_fields"] = custom_fields_svc.visible_values(
        db, scope.organization_id, contact, is_team=scope.is_team
    )
    info: Optional[dict] = None
    if company_names is not None:
        info = company_names.get(contact.company_id)
    elif contact.company_id:
        company = db.get(Company, contact.company_id)
        if company is not None:
            info = {
                "name": company.name,
                "description": company.description,
                "estimated_revenue": company.estimated_revenue,
                "employee_count": company.employee_count,
            }
    out["company_name"] = info["name"] if info else None
    if scope.is_team:
        # Firmographics are agency workflow data (ContactOutTeam-only keys).
        out["company_description"] = info["description"] if info else None
        out["company_estimated_revenue"] = info["estimated_revenue"] if info else None
        out["company_employee_count"] = info["employee_count"] if info else None
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
    company_names = _company_names(db, contacts)
    contact_out = {
        c.id: {
            **_serialize_contact(db, c, scope, company_names),
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
    limit: int = 1000,
    sort: Optional[str] = None,
    sort_dir: str = "desc",
    cf_filter: Optional[str] = Query(
        default=None,
        description="JSON list of custom-field filters: [{key, op, value}]",
    ),
    verification: Optional[str] = Query(
        default=None, description="Filter by verification_status (Phase 12)"
    ),
    list_id: Optional[str] = Query(
        default=None, description="Filter to members of a contact list"
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
    if verification:
        if verification not in VERIFICATION_STATUSES:
            raise HTTPException(400, "Unknown verification status")
        stmt = stmt.where(Contact.verification_status == verification)
    if list_id:
        scope.get_or_404(db, ContactList, list_id)
        stmt = stmt.where(
            select(ContactListMember.id)
            .where(
                ContactListMember.list_id == list_id,
                ContactListMember.contact_id == Contact.id,
            )
            .exists()
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

    contacts = list(db.execute(stmt.limit(min(limit, 5000))).scalars())
    attribution = _attribution_for(db, client, contacts)
    company_names = _company_names(db, contacts)
    return [
        {
            **_serialize_contact(db, c, scope, company_names),
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
        mobile_phone=body.mobile_phone,
        job_title=body.job_title,
        city=body.city,
        state=body.state,
        zip=body.zip,
        source="manual",
    )
    if body.company_name and body.company_name.strip():
        contact.company_id = crm_svc.get_or_create_company(
            db, client.organization_id, client.id, body.company_name
        )
    if body.sms_opt_in:
        sms_consent.record_opt_in(contact, "manual")
    else:
        sms_consent.apply_org_default(
            db.get(Organization, client.organization_id), contact
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


def _apply_contact_update(
    db: Session, scope: TenantScope, contact: Contact, body: ContactUpdateIn
) -> None:
    """Apply a ContactUpdateIn's field-application rules to one contact.
    Shared by the single PATCH and the bulk-update endpoint — behavior is
    identical either way. Raises custom_fields_svc.CustomFieldError on a bad
    custom-field value; caller decides how to surface it."""
    if body.first_name is not None:
        contact.first_name = body.first_name
    if body.last_name is not None:
        contact.last_name = body.last_name
    if body.email is not None:
        new_email = body.email.lower() if body.email else None
        if new_email != contact.email:
            # A different address means the old verdict says nothing about
            # the new one (Phase 12).
            email_verification.reset_status(contact)
        contact.email = new_email
    if body.phone is not None:
        contact.phone = body.phone
    if body.mobile_phone is not None:
        contact.mobile_phone = body.mobile_phone or None
    if body.job_title is not None:
        contact.job_title = body.job_title or None
    if body.city is not None:
        contact.city = body.city
    if body.state is not None:
        contact.state = body.state
    if body.zip is not None:
        contact.zip = body.zip
    if "company_name" in body.model_fields_set:
        name = (body.company_name or "").strip()
        contact.company_id = (
            crm_svc.get_or_create_company(
                db, contact.organization_id, contact.client_id, name
            )
            if name
            else None
        )
    if body.sms_opt_in is not None:
        if body.sms_opt_in and not contact.sms_opt_in:
            sms_consent.record_opt_in(contact, "manual")
        elif not body.sms_opt_in:
            # Revoke: opt_in cleared; at/source kept as the audit trail of
            # the original consent event.
            contact.sms_opt_in = False
    if body.custom_fields is not None:
        custom_fields_svc.validate_and_merge(
            db,
            scope.organization_id,
            contact,
            body.custom_fields,
            enforce_required=False,
        )


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
    try:
        _apply_contact_update(db, scope, contact, body)
    except custom_fields_svc.CustomFieldError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _serialize_contact(db, contact, scope)


@router.post("/contacts/bulk-update")
def bulk_update_contacts(
    body: ContactBulkUpdateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Apply the same field change(s) to many contacts at once. Cross-org/
    unknown ids are silently skipped (same convention as bulk-delete). A bad
    custom-field value aborts the whole batch before commit — all-or-nothing,
    never a half-applied bulk edit."""
    contacts = list(
        db.execute(
            select(Contact).where(
                Contact.organization_id == scope.organization_id,
                Contact.id.in_(body.contact_ids),
            )
        ).scalars()
    )
    try:
        for contact in contacts:
            _apply_contact_update(db, scope, contact, body.fields)
    except custom_fields_svc.CustomFieldError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"updated": len(contacts), "skipped": len(body.contact_ids) - len(contacts)}


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
# to a custom field or is skipped). "company" get-or-creates a linked Company;
# "full_name" splits into first/last (explicit first/last columns win).
_CSV_SYSTEM_TARGETS = {
    "first_name",
    "last_name",
    "email",
    "phone",
    "mobile_phone",
    "job_title",
    "city",
    "state",
    "zip",
    "company",
    "full_name",
    "sms_opt_in",
    "website",  # -> Company.domain (needs a company column on the same row)
    "notes",  # -> an internal Activity(note), not a Contact field
}

# Cell values that count as an opt-in when a column maps to sms_opt_in.
_CSV_TRUTHY = {"1", "true", "yes", "y", "x", "opted in", "opt-in", "opt in"}
# The subset that makes a row a real contact — a row mapping only a company or
# a city isn't one.
_CSV_IDENTITY_TARGETS = ("first_name", "last_name", "email", "phone")

# Postgres column caps checked BEFORE the DB (SQLite ignores them, so an
# over-cap cell only 500s on prod). Errors name the source CSV column.
_CSV_LENGTH_CAPS = {
    "first_name": 150,
    "last_name": 150,
    "job_title": 150,
    "phone": 50,
    "mobile_phone": 50,
    "city": 120,
    "state": 64,
    "zip": 20,
    "email": 320,
}

# Full US state / DC name -> 2-letter code (keys lowercased at lookup).
_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

_EMAIL_ADDR_RE = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


def _normalize_phone_cell(v: str) -> str:
    """Store the E.164 form when the cell is a real number (>=7 digits and
    normalize_phone resolves it), else the stripped raw — never lose data."""
    digits = sum(ch.isdigit() for ch in v)
    norm = sms_consent.normalize_phone(v)
    if norm and digits >= 7:
        return norm
    return v.strip()


def _norm_field_label(label: str) -> str:
    """Alnum-lowercase key for matching a would-be new field against an
    existing definition's label (mirrors the frontend's header normalization),
    so re-importing a file doesn't recreate the same custom field."""
    return re.sub(r"[^a-z0-9]", "", (label or "").lower())


def _normalize_state_cell(v: str) -> str:
    s = v.strip()
    if len(s) == 2:
        return s.upper()
    return _US_STATES.get(s.lower(), s)


def _parse_website_host(v: str) -> Optional[str]:
    """A website cell -> a bare host (no scheme/path/www) for Company.domain."""
    s = (v or "").strip()
    if not s:
        return None
    parsed = urlparse(s if "//" in s else f"//{s}")
    host = (parsed.netloc or parsed.path).split("/")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _split_email_cell(cell: Optional[str]):
    """A CSV email cell can hold several addresses ("a@x.com, b@x.com" — two
    guesses at one person's address). Return (primary, all_lowercased) where
    primary is the first syntactically-valid address so the contact is
    deliverable; the alternates are kept as candidate_emails rather than
    stored as one un-sendable comma-joined string (which SMTP 501s)."""
    found = [m.group(0).lower() for m in _EMAIL_ADDR_RE.finditer(cell or "")]
    return (found[0] if found else None), found


# Contact fields a matched-row update may fill (email/company handled apart).
_CSV_FILL_FIELDS = (
    "first_name", "last_name", "phone", "mobile_phone",
    "job_title", "city", "state", "zip",
)


def _apply_fill_blanks(
    db: Session,
    org_id: str,
    contact: Contact,
    identity: Dict[str, Optional[str]],
    primary_email: Optional[str],
    email_candidates: List[str],
    custom: Dict[str, object],
    company_name: Optional[str],
    resolve_company,
) -> bool:
    """Fill-blanks update of a matched contact: a system field is set only when
    the existing value is empty; company only when unlinked; custom fields drop
    keys already set; extra emails merge into candidate_emails. Returns whether
    any field was filled (drives the created/updated/unchanged tally)."""
    changed = False
    for field in _CSV_FILL_FIELDS:
        newv = identity.get(field)
        if newv and not getattr(contact, field):
            setattr(contact, field, newv)
            changed = True
    if primary_email and not contact.email:
        contact.email = primary_email
        changed = True
    if len(email_candidates) > 1:
        existing = list(contact.candidate_emails or [])
        have = {x.get("email") for x in existing}
        for e in email_candidates:
            if e not in have:
                existing.append({"email": e, "source": "csv_import"})
                have.add(e)
                changed = True
        contact.candidate_emails = existing
    if company_name and contact.company_id is None:
        cid = resolve_company(company_name)
        if cid:
            contact.company_id = cid
            changed = True
    if custom:
        existing_cf = contact.custom_fields or {}
        to_apply = {k: v for k, v in custom.items() if not existing_cf.get(k)}
        if to_apply:
            custom_fields_svc.validate_and_merge(
                db, org_id, contact, to_apply, enforce_required=False
            )
            changed = True
    return changed


@router.post("/contacts/import")
def import_contacts(
    body: CsvImportIn,
    background: BackgroundTasks,
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

    # 0) Resolve the target list (import-into-list): an explicit list must
    # belong to this client; a new_list_name reuses an existing same-named
    # list (batched requests / re-imports converge on one list) or creates it.
    target_list: Optional[ContactList] = None
    if body.new_list_name and body.new_list_name.strip():
        list_name = body.new_list_name.strip()
        target_list = db.execute(
            select(ContactList).where(
                ContactList.client_id == client.id, ContactList.name == list_name
            )
        ).scalar_one_or_none()
        if target_list is None:
            target_list = ContactList(
                organization_id=client.organization_id,
                client_id=client.id,
                name=list_name,
            )
            db.add(target_list)
            db.flush()
    elif body.list_id:
        target_list = scope.get_or_404(db, ContactList, body.list_id)
        if target_list.client_id != client.id:
            raise HTTPException(400, "That list belongs to a different client")

    # 1) Create inline-defined fields first, so their columns can map to them.
    # Hitting the custom-field cap soft-skips that column (recorded in
    # skipped_fields) instead of aborting the whole import with a 402.
    #
    # Re-import idempotency: if an ACTIVE definition already carries the same
    # (normalized) label, reuse it instead of minting a suffixed duplicate.
    # A stale client that re-sends a column as "new" — e.g. importing the same
    # file twice before the field list refetches — must not spawn
    # lead_score_2/lead_score_3… on every pass.
    created_fields: List[dict] = []
    skipped_fields: List[dict] = []
    column_to_new_key: Dict[str, str] = {}
    _existing_by_label = {
        _norm_field_label(d.label): d
        for d in custom_fields_svc.list_definitions(
            db, org.id, "contact", include_archived=False
        )
    }
    for nf in body.new_fields or []:
        existing = _existing_by_label.get(_norm_field_label(nf.label))
        if existing is not None:
            column_to_new_key[nf.column] = existing.key
            continue
        try:
            entitlements.enforce_can_add_custom_field(db, org)
        except HTTPException as exc:
            skipped_fields.append({"column": nf.column, "reason": str(exc.detail)})
            continue
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
        _existing_by_label[_norm_field_label(nf.label)] = definition
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

    # 3) Prefetch this client's existing contacts ONCE for dedupe/upsert. Match
    # order per row is email, then phone, then mobile — first writer into each
    # map wins (a duplicate existing contact never shadows the first).
    email_map: Dict[str, Contact] = {}
    phone_map: Dict[str, Contact] = {}

    def _index(contact: Contact) -> None:
        if contact.email:
            email_map.setdefault(contact.email.lower(), contact)
        for p in (contact.phone, contact.mobile_phone):
            np = sms_consent.normalize_phone(p)
            if np:
                phone_map.setdefault(np, contact)

    if body.mode != "create":
        for c in db.execute(
            select(Contact).where(Contact.client_id == client.id)
        ).scalars():
            _index(c)

    def _match(primary_email, phone_val, mobile_val) -> Optional[Contact]:
        if primary_email:
            m = email_map.get(primary_email.lower())
            if m:
                return m
        for v in (phone_val, mobile_val):
            np = sms_consent.normalize_phone(v)
            if np:
                m = phone_map.get(np)
                if m:
                    return m
        return None

    created = 0
    updated = 0
    unchanged = 0
    skipped = 0
    failed: List[dict] = []
    created_contacts: List[Contact] = []
    touched_ids: List[str] = []  # created + matched rows -> list membership
    company_cache: Dict[str, Optional[str]] = {}

    def _resolve_company(company_name: str) -> Optional[str]:
        cache_key = company_name.lower()
        cid = company_cache.get(cache_key)
        if cid is None:
            cid = crm_svc.get_or_create_company(
                db, client.organization_id, client.id, company_name
            )
            if cid:
                company_cache[cache_key] = cid
        return cid

    def _apply_website(company_id: str, website_val: str) -> None:
        # Fill-blanks: only stamp Company.domain when it's empty; a row with a
        # website but no company has nowhere to attach it, so it's ignored.
        host = _parse_website_host(website_val)
        if not host:
            return
        company = db.get(Company, company_id)
        if company is not None and not company.domain:
            company.domain = host[:300]

    def _add_note(contact: Contact, note: str) -> None:
        db.add(
            Activity(
                organization_id=client.organization_id,
                client_id=client.id,
                contact_id=contact.id,
                type="note",
                body=note,
                is_internal=True,
                occurred_at=dt.datetime.now(dt.timezone.utc),
            )
        )

    for idx, row in enumerate(body.rows):
        identity: Dict[str, Optional[str]] = {}
        identity_col: Dict[str, str] = {}  # system field -> source column (errors)
        custom: Dict[str, object] = {}
        website_val: Optional[str] = None
        note_val: Optional[str] = None
        for column, target in resolved.items():
            if target is None:
                continue
            kind, name = target
            raw = row.get(column)
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                continue
            if kind == "system":
                if name == "website":
                    website_val = str(raw).strip()
                elif name == "notes":
                    note_val = str(raw).strip()
                else:
                    identity[name] = str(raw).strip()
                    identity_col[name] = column
            else:
                custom[name] = raw
        full_col = identity_col.pop("full_name", None)
        full_name = identity.pop("full_name", None)
        if full_name:
            parts = full_name.split(None, 1)
            if "first_name" not in identity:
                identity["first_name"] = parts[0]
                identity_col["first_name"] = full_col
            if len(parts) > 1 and "last_name" not in identity:
                identity["last_name"] = parts[1]
                identity_col["last_name"] = full_col
        company_name = identity.pop("company", None)
        identity_col.pop("company", None)
        opt_in_cell = identity.pop("sms_opt_in", None)
        identity_col.pop("sms_opt_in", None)
        row_opted_in = body.sms_opt_in_all or (
            opt_in_cell is not None and opt_in_cell.strip().lower() in _CSV_TRUTHY
        )
        # Normalize phone/mobile to E.164 and expand state names -> 2-letter.
        for pf in ("phone", "mobile_phone"):
            if identity.get(pf) is not None:
                identity[pf] = _normalize_phone_cell(identity[pf])
        if identity.get("state") is not None:
            identity["state"] = _normalize_state_cell(identity["state"])
        if not any(identity.get(k) for k in _CSV_IDENTITY_TARGETS):
            failed.append(
                {"row": idx, "error": "no identity field (name/email/phone) mapped"}
            )
            continue
        primary_email, email_candidates = _split_email_cell(identity.get("email"))

        # Length pre-check BEFORE the DB — a per-row failure naming the CSV
        # COLUMN, so the user knows which column to trim.
        cap_error = None
        for name, cap in _CSV_LENGTH_CAPS.items():
            val = primary_email if name == "email" else identity.get(name)
            if val is not None and len(val) > cap:
                col = identity_col.get(name, name)
                cap_error = f"'{col}' is too long (max {cap} characters)"
                break
        if cap_error:
            failed.append({"row": idx, "error": cap_error})
            continue

        match = _match(primary_email, identity.get("phone"), identity.get("mobile_phone"))
        if body.mode == "update" and match is None:
            skipped += 1
            continue

        # Everything that touches the DB for this row runs inside a SAVEPOINT.
        # A single bad row — a value longer than a Postgres column cap, an
        # integrity violation, any driver error — then rolls back only that row
        # and lands in `failed`, instead of aborting the whole import.
        try:
            with db.begin_nested():
                if match is None:
                    contact = Contact(
                        organization_id=client.organization_id,
                        client_id=client.id,
                        first_name=identity.get("first_name"),
                        last_name=identity.get("last_name"),
                        email=primary_email,
                        phone=identity.get("phone"),
                        mobile_phone=identity.get("mobile_phone"),
                        job_title=identity.get("job_title"),
                        city=identity.get("city"),
                        state=identity.get("state"),
                        zip=identity.get("zip"),
                        source="csv_import",
                        source_detail=(
                            {"import_file": body.file_name} if body.file_name else None
                        ),
                    )
                    # Alternate addresses (a multi-address cell) kept as
                    # candidates so nothing is lost, without an un-sendable email.
                    if len(email_candidates) > 1:
                        contact.candidate_emails = [
                            {"email": e, "source": "csv_import"}
                            for e in email_candidates
                        ]
                    if row_opted_in:
                        sms_consent.record_opt_in(
                            contact, "csv_import:website_attested"
                        )
                    else:
                        sms_consent.apply_org_default(org, contact)
                    if company_name:
                        contact.company_id = _resolve_company(company_name)
                    custom_fields_svc.validate_and_merge(
                        db, scope.organization_id, contact, custom,
                        enforce_required=False,
                    )
                    db.add(contact)
                    db.flush()
                    if company_name and contact.company_id and website_val:
                        _apply_website(contact.company_id, website_val)
                    if note_val:
                        _add_note(contact, note_val)
                    created += 1
                    created_contacts.append(contact)
                    # In-file duplicate rows now UPDATE this contact instead of
                    # double-inserting — except in create mode, which always
                    # inserts (back-compat: no dedupe at all).
                    if body.mode != "create":
                        _index(contact)
                else:
                    contact = match
                    changed = _apply_fill_blanks(
                        db,
                        scope.organization_id,
                        contact,
                        identity,
                        primary_email,
                        email_candidates,
                        custom,
                        company_name,
                        _resolve_company,
                    )
                    if row_opted_in:
                        sms_consent.record_opt_in(
                            contact, "csv_import:website_attested"
                        )
                    if company_name and contact.company_id and website_val:
                        _apply_website(contact.company_id, website_val)
                    if note_val:
                        _add_note(contact, note_val)  # a log, always appended
                    db.flush()
                    if changed:
                        updated += 1
                    else:
                        unchanged += 1
            # Row committed to its savepoint — it belongs in the target list
            # (matched-but-unchanged rows included: being in the file is the
            # membership signal, not whether any field changed).
            touched_ids.append(contact.id)
        except custom_fields_svc.CustomFieldError as e:
            failed.append({"row": idx, "error": str(e)})
            continue
        except Exception as e:  # DataError, IntegrityError, anything else
            log.warning("csv import row %s failed", idx, exc_info=True)
            failed.append(
                {"row": idx, "error": f"could not import this row ({type(e).__name__})"}
            )
            continue

    # 4) List membership for every touched row — idempotent (existing members
    # skipped), so re-importing a file into the same list never duplicates.
    added_to_list = 0
    if target_list is not None and touched_ids:
        unique_ids = list(dict.fromkeys(touched_ids))
        existing_members = set(
            db.execute(
                select(ContactListMember.contact_id).where(
                    ContactListMember.list_id == target_list.id,
                    ContactListMember.contact_id.in_(unique_ids),
                )
            ).scalars()
        )
        for cid in unique_ids:
            if cid in existing_members:
                continue
            db.add(
                ContactListMember(
                    organization_id=client.organization_id,
                    list_id=target_list.id,
                    contact_id=cid,
                )
            )
            added_to_list += 1

    # One audit entry per import run (guardrail 8), same pattern as bulk-delete.
    db.add(
        AuditLogEntry(
            organization_id=client.organization_id,
            client_id=client.id,
            user_id=user.id,
            user_email=user.email,
            user_name=user.full_name,
            platform="crm",
            entity_type="contact",
            entity_name=body.file_name,
            action="contacts.imported",
            # AuditEntryOut serializes diff as [{field, before, after}] rows
            # (DiffRowOut), so the run summary rides in that shape.
            diff=[
                {"field": "created", "after": created},
                {"field": "updated", "after": updated},
                {"field": "failed", "after": len(failed)},
                {"field": "file", "after": body.file_name},
                {
                    "field": "list",
                    "after": target_list.name if target_list else None,
                },
            ],
            status=AUDIT_SUCCESS,
        )
    )
    db.commit()
    # Phase 12 bulk action: verify the imported addresses after the response.
    # Quota-checked inside the pipeline — an over-quota import still imports,
    # it just leaves contacts unverified instead of part-verifying the file.
    if body.verify and created_contacts:
        background.add_task(
            lead_finder_svc.enrich_and_verify,
            scope.organization_id,
            [c.id for c in created_contacts],
        )
    return {
        "imported": created + updated,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "failed": failed,
        "created_fields": created_fields,
        "skipped_fields": skipped_fields,
        "verification_queued": bool(body.verify and created_contacts),
        "list": (
            {
                "id": target_list.id,
                "name": target_list.name,
                "added": added_to_list,
            }
            if target_list
            else None
        ),
    }


@router.post("/contacts/verify")
def verify_contacts_bulk(
    body: VerifyContactsIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Phase 12 task 11: verify any manually selected contact set. Synchronous
    (the caller wants the badges to update), metered against the org's monthly
    quota as one batch — 402 if it doesn't fit, nothing part-verified."""
    if not body.contact_ids:
        raise HTTPException(400, "No contacts selected")
    if len(body.contact_ids) > 500:
        raise HTTPException(400, "At most 500 contacts per request")
    org = db.get(Organization, scope.organization_id)
    contacts = [scope.get_or_404(db, Contact, cid) for cid in body.contact_ids]
    with_email = [c for c in contacts if c.email]
    email_verification.verify_contacts(db, org, with_email, user_id=user.id)
    db.commit()
    return {
        "verified": {
            c.id: {
                "verification_status": c.verification_status,
                "verified_at": c.verified_at,
            }
            for c in with_email
        },
        "skipped_no_email": [c.id for c in contacts if not c.email],
        "usage": entitlements.email_verification_usage(db, org),
    }


@router.post("/contacts/enrich")
def enrich_contacts_bulk(
    body: VerifyContactsIn,
    background: BackgroundTasks,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Re-run the full enrichment pipeline (own-site discovery → licensed
    profile provider for owner name/title/mobile/firmographics → email
    verification) on an existing contact set. The import-time run is the
    only other trigger, so leads imported BEFORE the org connected its
    profile-provider key (Apollo) had no way to backfill owner contact
    info. Fill-blanks-only throughout — a human edit always wins. Runs in
    the background (provider calls are slow); the caller re-fetches."""
    contacts = [scope.get_or_404(db, Contact, cid) for cid in body.contact_ids]
    background.add_task(
        lead_finder_svc.enrich_and_verify,
        scope.organization_id,
        [c.id for c in contacts],
    )
    return {"queued": len(contacts)}


# Heartbeat threshold for declaring a running job interrupted: one contact's
# enrichment worst-cases around 30s of network timeouts, so a heartbeat this
# old means the process died mid-run (e.g. a deploy restarted the backend).
_ENRICH_STALE_SECONDS = 180


@router.get("/enrich/jobs")
def enrichment_job_status(
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Recent enrichment runs for the status card: live progress, a
    pace-based ETA while running, and history. The pipeline heartbeats
    `processed`/`updated_at` per contact (services/lead_finder.py)."""

    def _aware(v: Optional[dt.datetime]) -> Optional[dt.datetime]:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=dt.timezone.utc)
        return v

    now = dt.datetime.now(dt.timezone.utc)
    jobs = (
        db.execute(
            select(EnrichmentJob)
            .where(EnrichmentJob.organization_id == scope.organization_id)
            .order_by(EnrichmentJob.created_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    out = []
    for j in jobs:
        started = _aware(j.created_at)
        heartbeat = _aware(j.updated_at) or started
        end = _aware(j.finished_at) or now
        elapsed = max(0.0, (end - started).total_seconds())
        stale = (
            j.status == "running"
            and (now - heartbeat).total_seconds() > _ENRICH_STALE_SECONDS
        )
        eta = None
        if j.status == "running" and not stale and 0 < j.processed < j.total:
            eta = round(elapsed / j.processed * (j.total - j.processed))
        out.append(
            {
                "id": j.id,
                "status": "interrupted" if stale else j.status,
                "phase": j.phase,
                "total": j.total,
                "processed": j.processed,
                "error": j.error,
                "created_at": started.isoformat(),
                "finished_at": _aware(j.finished_at).isoformat() if j.finished_at else None,
                "elapsed_seconds": round(elapsed),
                "eta_seconds": eta,
            }
        )
    return {"jobs": out, "processing": any(job["status"] == "running" for job in out)}


def _write_contact_delete_audit(db: Session, contact: Contact, user: User) -> None:
    """Per-deleted-contact trail in the standard audit pattern (guardrail 8):
    actor, target contact, org, timestamp — written before the cascade."""
    name = " ".join(filter(None, [contact.first_name, contact.last_name]))
    db.add(
        AuditLogEntry(
            organization_id=contact.organization_id,
            client_id=contact.client_id,
            user_id=user.id,
            user_email=user.email,
            user_name=user.full_name,
            platform="crm",
            entity_type="contact",
            entity_external_id=contact.id,
            entity_name=name or contact.email or contact.phone,
            action="contact.deleted",
            diff=[],
            status=AUDIT_SUCCESS,
        )
    )


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(
    contact_id: str,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Delete a contact and cascade to everything referencing it (guardrail 9).
    Admin-gated, org-scoped (cross-tenant ids 404)."""
    contact = scope.get_or_404(db, Contact, contact_id)
    _write_contact_delete_audit(db, contact, user)
    crm_svc.delete_contact(db, contact)
    db.commit()
    return Response(status_code=204)


@router.post("/contacts/bulk-delete")
def bulk_delete_contacts(
    body: ContactBulkDeleteIn,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Delete many contacts at once. Ids not in the caller's org are silently
    skipped — never a signal that a contact exists elsewhere."""
    contacts = list(
        db.execute(
            select(Contact).where(
                Contact.organization_id == scope.organization_id,
                Contact.id.in_(body.contact_ids),
            )
        ).scalars()
    )
    for contact in contacts:
        _write_contact_delete_audit(db, contact, user)
        crm_svc.delete_contact(db, contact)
    db.commit()
    return {"deleted": len(contacts)}


# --- Contact lists ---
# Named, client-scoped audiences (managed like Tags) used to target outreach
# enrollment. Same TenantScope conventions as everywhere else in this file.


def _list_for(db: Session, scope: TenantScope, list_id: str) -> ContactList:
    return scope.get_or_404(db, ContactList, list_id)


def _list_out(db: Session, contact_list: ContactList) -> dict:
    member_count = db.execute(
        select(func.count(ContactListMember.id)).where(
            ContactListMember.list_id == contact_list.id
        )
    ).scalar_one()
    return {
        "id": contact_list.id,
        "name": contact_list.name,
        "client_id": contact_list.client_id,
        "member_count": member_count,
    }


@router.get("/lists")
def list_contact_lists(
    client_id: str,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, client_id)
    lists = (
        db.execute(
            select(ContactList)
            .where(
                ContactList.organization_id == client.organization_id,
                ContactList.client_id == client.id,
            )
            .order_by(ContactList.name)
        )
        .scalars()
        .all()
    )
    return [_list_out(db, l) for l in lists]


@router.post("/lists", status_code=201)
def create_contact_list(
    body: ContactListCreateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _client_for(db, scope, body.client_id)
    existing = db.execute(
        select(ContactList.id).where(
            ContactList.client_id == client.id, ContactList.name == body.name
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "A list with this name already exists")
    contact_list = ContactList(
        organization_id=client.organization_id, client_id=client.id, name=body.name
    )
    db.add(contact_list)
    db.commit()
    return _list_out(db, contact_list)


@router.patch("/lists/{list_id}")
def rename_contact_list(
    list_id: str,
    body: ContactListRenameIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    contact_list = _list_for(db, scope, list_id)
    existing = db.execute(
        select(ContactList.id).where(
            ContactList.client_id == contact_list.client_id,
            ContactList.name == body.name,
            ContactList.id != contact_list.id,
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "A list with this name already exists")
    contact_list.name = body.name
    db.commit()
    return _list_out(db, contact_list)


@router.delete("/lists/{list_id}", status_code=204)
def delete_contact_list(
    list_id: str,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Deletes the list and its membership rows only — contacts are untouched."""
    contact_list = _list_for(db, scope, list_id)
    db.execute(
        delete(ContactListMember).where(ContactListMember.list_id == contact_list.id)
    )
    db.delete(contact_list)
    db.commit()
    return Response(status_code=204)


@router.post("/lists/{list_id}/contacts")
def add_contacts_to_list(
    list_id: str,
    body: ContactListMembersIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Bulk-add contacts to a list. Cross-org/wrong-client ids are silently
    skipped (same convention as bulk-delete); duplicates are idempotent."""
    contact_list = _list_for(db, scope, list_id)
    contacts = list(
        db.execute(
            select(Contact.id).where(
                Contact.organization_id == contact_list.organization_id,
                Contact.client_id == contact_list.client_id,
                Contact.id.in_(body.contact_ids),
            )
        ).scalars()
    )
    existing_ids = set(
        db.execute(
            select(ContactListMember.contact_id).where(
                ContactListMember.list_id == contact_list.id,
                ContactListMember.contact_id.in_(contacts),
            )
        ).scalars()
    )
    added = 0
    for cid in contacts:
        if cid in existing_ids:
            continue
        db.add(
            ContactListMember(
                organization_id=contact_list.organization_id,
                list_id=contact_list.id,
                contact_id=cid,
            )
        )
        added += 1
    db.commit()
    return {"added": added, "skipped": len(body.contact_ids) - added}


@router.post("/lists/{list_id}/contacts/remove")
def remove_contacts_from_list(
    list_id: str,
    body: ContactListMembersIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    contact_list = _list_for(db, scope, list_id)
    result = db.execute(
        delete(ContactListMember).where(
            ContactListMember.list_id == contact_list.id,
            ContactListMember.contact_id.in_(body.contact_ids),
        )
    )
    db.commit()
    return {"removed": result.rowcount}


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


# --- AI research fields ("Claygent-lite") -----------------------------------


def _research_def_key(db: Session, org_id: str, label: str) -> str:
    """Slugify + disambiguate within research_field_defs only — a separate
    namespace from Phase 14 custom fields (task per CLAY_HANDOFF Feature A),
    so collision with RESERVED_CONTACT_FIELD_KEYS or an existing custom-field
    key is deliberately not checked here."""
    taken = set(
        db.execute(
            select(ResearchFieldDef.key).where(
                ResearchFieldDef.organization_id == org_id
            )
        ).scalars()
    )
    base = custom_fields_svc.slugify_key(label)
    candidate = base
    suffix = 2
    while candidate in taken:
        tail = f"_{suffix}"
        candidate = f"{base[: 60 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def _research_def_or_404(db: Session, org_id: str, def_id: str) -> ResearchFieldDef:
    d = db.get(ResearchFieldDef, def_id)
    if d is None or d.organization_id != org_id:
        raise HTTPException(404, "Not found")
    return d


@router.get("/research-fields", response_model=List[ResearchFieldOut])
def list_research_fields(
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
):
    return list(
        db.execute(
            select(ResearchFieldDef)
            .where(ResearchFieldDef.organization_id == scope.organization_id)
            .order_by(ResearchFieldDef.created_at)
        ).scalars()
    )


@router.post("/research-fields", status_code=201, response_model=ResearchFieldOut)
def create_research_field(
    body: ResearchFieldIn,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    org = db.get(Organization, scope.organization_id)
    entitlements.enforce_can_add_research_field(db, org)
    key = (
        custom_fields_svc.slugify_key(body.key)
        if body.key
        else _research_def_key(db, org.id, body.label)
    )
    dupe = db.execute(
        select(ResearchFieldDef.id).where(
            ResearchFieldDef.organization_id == org.id,
            ResearchFieldDef.key == key,
        )
    ).scalar_one_or_none()
    if dupe is not None:
        raise HTTPException(409, f"A research field with key '{key}' already exists")
    definition = ResearchFieldDef(
        organization_id=org.id,
        key=key,
        label=body.label.strip(),
        prompt=body.prompt.strip(),
        max_words=body.max_words,
    )
    db.add(definition)
    db.commit()
    return definition


@router.patch("/research-fields/{def_id}", response_model=ResearchFieldOut)
def update_research_field(
    def_id: str,
    body: ResearchFieldPatch,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Rename is label-only — key is immutable (same rule as custom fields)."""
    d = _research_def_or_404(db, scope.organization_id, def_id)
    if body.label is not None:
        d.label = body.label.strip()
    if body.prompt is not None:
        d.prompt = body.prompt.strip()
    if body.max_words is not None:
        d.max_words = body.max_words
    if body.archived is not None:
        if body.archived is False and d.archived is True:
            org = db.get(Organization, scope.organization_id)
            entitlements.enforce_can_add_research_field(db, org)
        d.archived = body.archived
    db.commit()
    return d


@router.delete("/research-fields/{def_id}")
def delete_research_field(
    def_id: str,
    background: BackgroundTasks,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Hard delete: removes the definition and scrubs its key from every
    contact's research JSON in a background job (copies the custom-fields
    delete-scrub pattern)."""
    d = _research_def_or_404(db, scope.organization_id, def_id)
    org_id, key = d.organization_id, d.key
    db.delete(d)
    db.commit()
    background.add_task(_scrub_research_key, org_id, key)
    return {"deleted": True, "key": key, "scrub": "scheduled"}


def _scrub_research_key(org_id: str, key: str) -> int:
    """Remove `key` from every contact's research JSON in the org. Opens its
    own session so it's safe to run as a background job after hard delete
    (mirrors services.custom_fields.scrub_key)."""
    from ..db import SessionLocal

    db = SessionLocal()
    touched = 0
    try:
        contacts = (
            db.execute(
                select(Contact).where(
                    Contact.organization_id == org_id,
                    Contact.research.is_not(None),
                )
            )
            .scalars()
            .all()
        )
        for c in contacts:
            data = c.research or {}
            if key in data:
                new = {k: v for k, v in data.items() if k != key}
                c.research = new or None
                touched += 1
        if touched:
            db.commit()
        return touched
    finally:
        db.close()


@router.post("/research/run")
def run_research(
    body: ResearchRunIn,
    background: BackgroundTasks,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
    user: User = Depends(require_team),
):
    """Queue AI research for up to 200 contacts. Cross-org ids are silently
    skipped by run_for_contacts; this endpoint returns a 202-style receipt
    immediately and runs the batch in a FastAPI BackgroundTask."""
    background.add_task(
        _run_research_task,
        scope.organization_id,
        body.contact_ids,
        body.field_keys,
        body.force,
    )
    return {"queued": len(body.contact_ids)}


def _run_research_task(
    org_id: str,
    contact_ids: List[str],
    field_keys: Optional[List[str]],
    force: bool,
) -> dict:
    """Background entry: opens its own session (the request's session closes
    before this runs)."""
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        org = db.get(Organization, org_id)
        if org is None:
            return {"processed": 0, "filled": 0, "skipped_cached": 0, "failed": 0}
        return research_svc.run_for_contacts(
            db, org, contact_ids, field_keys=field_keys, force=force
        )
    finally:
        db.close()


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
