"""Custom CRM field definitions — management API (Phase 14, Part C task 10).

Definitions are per-Organization (entity_type='contact' today), so these
endpoints are org-scoped, not client-scoped — one set of fields applies across
every client's contacts. Admin+ gated: field design is CRM setup, not
day-to-day deal work (mirrors the pipeline-stage editor).

Values on contacts flow through api/crm.py using the same data-access layer
(services/custom_fields), so validation is identical on every surface.
"""

from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_scope, require_admin, TenantScope
from ..models.core import Organization, User
from ..models.crm import CUSTOM_FIELD_OPTION_TYPES, CustomFieldDefinition
from ..models.base import utcnow
from ..schemas import (
    CustomFieldDefinitionCreate,
    CustomFieldDefinitionOut,
    CustomFieldDefinitionUpdate,
    CustomFieldReorderIn,
)
from ..services import custom_fields as cf
from ..services import entitlements

router = APIRouter(prefix="/api/crm/custom-fields", tags=["crm"])


def _get_def(db: Session, org_id: str, def_id: str) -> CustomFieldDefinition:
    d = db.get(CustomFieldDefinition, def_id)
    if d is None or d.organization_id != org_id:
        raise HTTPException(404, "Not found")
    return d


@router.get("", response_model=List[CustomFieldDefinitionOut])
def list_definitions(
    include_archived: bool = False,
    entity_type: str = "contact",
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """List this org's field definitions. Readable by any role in the org so the
    contact views can label/render values; the values themselves are still
    client-visibility-filtered where they render."""
    return cf.list_definitions(
        db, scope.organization_id, entity_type, include_archived=include_archived
    )


@router.get("/usage")
def usage(
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Self-service "X of Y used" for the active-definition cap (guardrail 5)."""
    org = db.get(Organization, scope.organization_id)
    return entitlements.custom_field_usage(db, org)


@router.post("", status_code=201, response_model=CustomFieldDefinitionOut)
def create_definition(
    body: CustomFieldDefinitionCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    org = db.get(Organization, user.organization_id)
    entitlements.enforce_can_add_custom_field(db, org)
    try:
        options = cf.normalize_options(
            body.field_type,
            [o.model_dump() for o in body.options] if body.options else None,
        )
    except cf.CustomFieldError as e:
        raise HTTPException(400, str(e))
    key = cf.generate_key(db, org.id, body.entity_type, body.label)
    # New definition sorts last by default.
    sort_order = len(
        cf.list_definitions(db, org.id, body.entity_type, include_archived=True)
    )
    definition = CustomFieldDefinition(
        organization_id=org.id,
        entity_type=body.entity_type,
        label=body.label.strip(),
        key=key,
        field_type=body.field_type,
        options=options,
        required=body.required,
        visible_to_clients=body.visible_to_clients,
        sort_order=sort_order,
    )
    db.add(definition)
    db.commit()
    return definition


@router.patch("/{def_id}", response_model=CustomFieldDefinitionOut)
def update_definition(
    def_id: str,
    body: CustomFieldDefinitionUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Rename (label only — key is immutable), toggle required/visibility, and
    edit select options. Removing an in-use option without a remap decision is
    blocked with 409 listing the affected option keys, so the UI can prompt
    remap-or-keep (task 7) instead of silently dropping data."""
    d = _get_def(db, user.organization_id, def_id)

    if body.label is not None:
        d.label = body.label.strip()
    if body.required is not None:
        d.required = body.required
    if body.visible_to_clients is not None:
        d.visible_to_clients = body.visible_to_clients

    if body.options is not None:
        if d.field_type not in CUSTOM_FIELD_OPTION_TYPES:
            raise HTTPException(400, "This field type has no options")
        try:
            new_options = cf.normalize_options(
                d.field_type, [o.model_dump() for o in body.options]
            )
        except cf.CustomFieldError as e:
            raise HTTPException(400, str(e))
        old_keys = {o["key"] for o in (d.options or [])}
        new_keys = {o["key"] for o in (new_options or [])}
        removed = old_keys - new_keys
        remap = {
            k: v for k, v in (body.option_remap or {}).items() if k in removed
        }
        # Remap targets must be valid new option keys.
        bad_targets = set(remap.values()) - new_keys
        if bad_targets:
            raise HTTPException(
                400, f"remap targets not in options: {', '.join(sorted(bad_targets))}"
            )
        undecided = cf.option_key_usage(db, d.organization_id, d, removed - set(remap))
        if undecided:
            # In-use options removed with no keep/remap decision — let the UI ask.
            raise HTTPException(
                409,
                {
                    "message": "Some removed options are in use — choose remap or keep.",
                    "in_use": sorted(undecided),
                },
            )
        if remap:
            cf.remap_option_values(db, d.organization_id, d, remap)
        d.options = new_options

    cf.touch(d)
    db.commit()
    return d


@router.post("/reorder", response_model=List[CustomFieldDefinitionOut])
def reorder_definitions(
    body: CustomFieldReorderIn,
    entity_type: str = "contact",
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Drag-sort: set sort_order from the given id order. Ids not belonging to
    the org are rejected."""
    defs = {
        d.id: d
        for d in cf.list_definitions(
            db, user.organization_id, entity_type, include_archived=True
        )
    }
    unknown = [i for i in body.ids if i not in defs]
    if unknown:
        raise HTTPException(400, "Unknown definition id(s)")
    for order, def_id in enumerate(body.ids):
        defs[def_id].sort_order = order
    db.commit()
    return cf.list_definitions(
        db, user.organization_id, entity_type, include_archived=True
    )


@router.post("/{def_id}/archive", response_model=CustomFieldDefinitionOut)
def archive_definition(
    def_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Archive (task 6): hide from forms/default views, keep stored values, stay
    filterable under the archived toggle. Reversible."""
    d = _get_def(db, user.organization_id, def_id)
    if d.archived_at is None:
        d.archived_at = utcnow()
        cf.touch(d)
        db.commit()
    return d


@router.post("/{def_id}/unarchive", response_model=CustomFieldDefinitionOut)
def unarchive_definition(
    def_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    d = _get_def(db, user.organization_id, def_id)
    if d.archived_at is not None:
        org = db.get(Organization, user.organization_id)
        # Un-archiving re-consumes an active slot — re-check the cap.
        entitlements.enforce_can_add_custom_field(db, org)
        d.archived_at = None
        cf.touch(d)
        db.commit()
    return d


@router.delete("/{def_id}")
def delete_definition(
    def_id: str,
    background: BackgroundTasks,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Hard delete (task 6): the explicit second step after archive. Removes the
    definition and scrubs its key from every contact's JSONB in a background
    job. The caller has already been shown that stored values will be lost."""
    d = _get_def(db, user.organization_id, def_id)
    org_id, key = d.organization_id, d.key
    db.delete(d)
    db.commit()
    background.add_task(cf.scrub_key, org_id, key)
    return {"deleted": True, "key": key, "scrub": "scheduled"}
