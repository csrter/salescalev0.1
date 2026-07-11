"""Custom CRM fields — the data-access layer for Phase 14.

This module is the single place custom-field values are validated, coerced,
and merged onto a contact, and the single place definitions are created/
renamed/archived/deleted. The API path (UI and programmatic) and the CSV
import path both go through here, so "never trust the client to send clean
shapes" (task 4) is enforced once, not per surface.

Design fixed by the phase:
- values live in Contact.custom_fields JSONB, keyed by definition `key`;
- `key` is generated once from the label and never regenerated on rename;
- generated keys never collide with system contact fields
  (RESERVED_CONTACT_FIELD_KEYS) or other custom keys in the org;
- archived fields keep their values and stay filterable; hard delete scrubs
  the key from every contact's JSONB;
- client-role reads only ever see fields flagged visible_to_clients.
"""

import datetime as dt
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.crm import (
    CUSTOM_FIELD_OPTION_TYPES,
    CUSTOM_FIELD_TYPES,
    Contact,
    CustomFieldDefinition,
    RESERVED_CONTACT_FIELD_KEYS,
)

DEFAULT_ENTITY = "contact"
# Hard ceiling on active definitions per org, independent of tier — bounds
# query and UI complexity even on the top plan (task 9). The tier stub may set
# a lower cap; this is the absolute maximum.
MAX_ACTIVE_DEFINITIONS = 100


class CustomFieldError(ValueError):
    """Validation error carrying the offending field's label for the UI/import
    error report. Raised at the data-access layer, surfaced as 400 by the API
    and as a per-row failure by the CSV importer."""


# --- key generation & collision protection (task 8) ---


def slugify_key(label: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")[:60]
    return base or "field"


def _reserved_for(entity_type: str) -> frozenset[str]:
    # Only 'contact' exists today; when deal/company land, give each its own
    # reserved set here (task 1 designed the column for that).
    return RESERVED_CONTACT_FIELD_KEYS if entity_type == DEFAULT_ENTITY else frozenset()


def existing_keys(db: Session, org_id: str, entity_type: str) -> set[str]:
    rows = db.execute(
        select(CustomFieldDefinition.key).where(
            CustomFieldDefinition.organization_id == org_id,
            CustomFieldDefinition.entity_type == entity_type,
        )
    ).scalars()
    return set(rows)


def generate_key(
    db: Session, org_id: str, entity_type: str, label: str
) -> str:
    """Derive a stable machine key from the label, disambiguating against both
    the reserved system-field names and every custom key already in the org."""
    taken = _reserved_for(entity_type) | existing_keys(db, org_id, entity_type)
    base = slugify_key(label)
    candidate = base
    suffix = 2
    while candidate in taken:
        tail = f"_{suffix}"
        candidate = f"{base[: 60 - len(tail)]}{tail}"
        suffix += 1
    return candidate


# --- definition queries ---


def list_definitions(
    db: Session,
    org_id: str,
    entity_type: str = DEFAULT_ENTITY,
    *,
    include_archived: bool = False,
) -> List[CustomFieldDefinition]:
    stmt = select(CustomFieldDefinition).where(
        CustomFieldDefinition.organization_id == org_id,
        CustomFieldDefinition.entity_type == entity_type,
    )
    if not include_archived:
        stmt = stmt.where(CustomFieldDefinition.archived_at.is_(None))
    stmt = stmt.order_by(
        CustomFieldDefinition.sort_order, CustomFieldDefinition.created_at
    )
    return list(db.execute(stmt).scalars())


def definitions_by_key(
    db: Session, org_id: str, entity_type: str = DEFAULT_ENTITY
) -> Dict[str, CustomFieldDefinition]:
    return {
        d.key: d
        for d in list_definitions(db, org_id, entity_type, include_archived=True)
    }


def active_count(db: Session, org_id: str, entity_type: str = DEFAULT_ENTITY) -> int:
    return len(list_definitions(db, org_id, entity_type, include_archived=False))


# --- options helpers ---


def normalize_options(field_type: str, options: Optional[list]) -> Optional[list]:
    """Coerce an incoming options list into [{key,label}] with generated keys,
    or None for non-option field types. Rejects empty option sets for select
    types."""
    if field_type not in CUSTOM_FIELD_OPTION_TYPES:
        return None
    if not options:
        raise CustomFieldError("Select fields need at least one option")
    out: List[dict] = []
    seen: set[str] = set()
    for opt in options:
        if isinstance(opt, dict):
            label = str(opt.get("label") or opt.get("key") or "").strip()
            key = str(opt.get("key") or "").strip() or slugify_key(label)
        else:
            label = str(opt).strip()
            key = slugify_key(label)
        if not label:
            continue
        key = slugify_key(key)
        # Disambiguate duplicate option keys within the field.
        base, n = key, 2
        while key in seen:
            key = f"{base}_{n}"
            n += 1
        seen.add(key)
        out.append({"key": key, "label": label})
    if not out:
        raise CustomFieldError("Select fields need at least one option")
    return out


def _option_keys(definition: CustomFieldDefinition) -> set[str]:
    return {o["key"] for o in (definition.options or [])}


# --- value coercion & validation (task 4) ---


def coerce_value(definition: CustomFieldDefinition, raw: Any) -> Any:
    """Coerce one raw value to its stored JSON shape, or raise CustomFieldError.
    Returns None for "cleared". Numbers are stored as numbers, dates as ISO-8601
    strings, select values must exist in the field's options."""
    ftype = definition.field_type
    label = definition.label

    # Empty / cleared — same for every type.
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip() == "":
        return None
    if ftype == "multi_select" and isinstance(raw, list) and len(raw) == 0:
        return None

    if ftype == "text" or ftype == "url":
        val = str(raw).strip()
        if ftype == "url":
            if "://" not in val:
                val = "https://" + val
            if not re.match(r"^https?://[^\s]+\.[^\s]+", val):
                raise CustomFieldError(f"'{label}' must be a valid URL")
        return val

    if ftype == "number":
        if isinstance(raw, bool):  # bool is a subclass of int — reject
            raise CustomFieldError(f"'{label}' must be a number")
        try:
            num = float(raw)
        except (TypeError, ValueError):
            raise CustomFieldError(f"'{label}' must be a number")
        # Preserve integers as ints so the JSON stays clean (5, not 5.0).
        return int(num) if num.is_integer() else num

    if ftype == "boolean":
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("true", "1", "yes", "y"):
            return True
        if s in ("false", "0", "no", "n"):
            return False
        raise CustomFieldError(f"'{label}' must be true or false")

    if ftype == "date":
        s = str(raw).strip()
        try:
            # Accept a date or a full datetime; store the date part, ISO-8601.
            parsed = dt.date.fromisoformat(s[:10])
        except ValueError:
            raise CustomFieldError(f"'{label}' must be a date (YYYY-MM-DD)")
        return parsed.isoformat()

    if ftype == "select":
        val = str(raw).strip()
        keys = _option_keys(definition)
        if val not in keys:
            # Allow matching by label too (CSV import friendliness).
            by_label = {o["label"]: o["key"] for o in (definition.options or [])}
            if val in by_label:
                return by_label[val]
            raise CustomFieldError(
                f"'{label}': '{val}' is not one of its options"
            )
        return val

    if ftype == "multi_select":
        if isinstance(raw, str):
            items = [p.strip() for p in raw.split(",") if p.strip()]
        elif isinstance(raw, list):
            items = [str(p).strip() for p in raw if str(p).strip()]
        else:
            raise CustomFieldError(f"'{label}' must be a list of options")
        keys = _option_keys(definition)
        by_label = {o["label"]: o["key"] for o in (definition.options or [])}
        out: List[str] = []
        for item in items:
            if item in keys:
                resolved = item
            elif item in by_label:
                resolved = by_label[item]
            else:
                raise CustomFieldError(
                    f"'{label}': '{item}' is not one of its options"
                )
            if resolved not in out:
                out.append(resolved)
        return out or None

    raise CustomFieldError(f"'{label}' has an unsupported type")


def validate_and_merge(
    db: Session,
    org_id: str,
    contact: Contact,
    incoming: Optional[Dict[str, Any]],
    *,
    enforce_required: bool,
    entity_type: str = DEFAULT_ENTITY,
) -> Dict[str, Any]:
    """Validate `incoming` (key -> raw value) against this org's definitions and
    merge onto contact.custom_fields. Unknown keys are rejected. A provided
    None/empty clears that key. Required active fields are enforced when
    `enforce_required` (contact create). Returns the merged bag.

    Reassigns contact.custom_fields (never mutates in place) so SQLAlchemy sees
    the JSON change.
    """
    defs = definitions_by_key(db, org_id, entity_type)
    current: Dict[str, Any] = dict(contact.custom_fields or {})

    if incoming:
        unknown = set(incoming) - set(defs)
        if unknown:
            raise CustomFieldError(
                f"unknown custom field key(s): {', '.join(sorted(unknown))}"
            )
        for key, raw in incoming.items():
            value = coerce_value(defs[key], raw)
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value

    if enforce_required:
        for d in defs.values():
            if d.required and d.archived_at is None and current.get(d.key) in (
                None,
                "",
                [],
            ):
                raise CustomFieldError(f"'{d.label}' is required")

    contact.custom_fields = current or None
    return current


# --- serialization (task 15: client visibility) ---


def visible_values(
    db: Session,
    org_id: str,
    contact: Contact,
    *,
    is_team: bool,
    entity_type: str = DEFAULT_ENTITY,
) -> Dict[str, Any]:
    """The custom-field values to expose for this contact. Team roles see every
    stored value; client-role reads are filtered to visible_to_clients fields
    at the data layer, so a hidden field never reaches a client response."""
    stored = contact.custom_fields or {}
    if is_team:
        return dict(stored)
    allowed = {
        d.key
        for d in list_definitions(db, org_id, entity_type, include_archived=True)
        if d.visible_to_clients
    }
    return {k: v for k, v in stored.items() if k in allowed}


# --- lifecycle: archive / delete / option remap ---


def scrub_key(org_id: str, key: str) -> int:
    """Remove `key` from every contact's custom_fields in the org. Opens its own
    session so it is safe to run as a background job after hard delete (task 6).
    Returns the number of contacts touched."""
    from ..db import SessionLocal

    db = SessionLocal()
    touched = 0
    try:
        contacts = (
            db.execute(
                select(Contact).where(
                    Contact.organization_id == org_id,
                    Contact.custom_fields.is_not(None),
                )
            )
            .scalars()
            .all()
        )
        for c in contacts:
            cf = c.custom_fields or {}
            if key in cf:
                new = {k: v for k, v in cf.items() if k != key}
                c.custom_fields = new or None
                touched += 1
        if touched:
            db.commit()
        return touched
    finally:
        db.close()


def option_key_usage(
    db: Session, org_id: str, definition: CustomFieldDefinition, option_keys: set[str]
) -> set[str]:
    """Which of `option_keys` are actually stored on some contact for this
    field — the set that would lose data if removed silently (task 7)."""
    if not option_keys:
        return set()
    contacts = (
        db.execute(
            select(Contact.custom_fields).where(
                Contact.organization_id == org_id,
                Contact.custom_fields.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    in_use: set[str] = set()
    for cf in contacts:
        val = (cf or {}).get(definition.key)
        if val is None:
            continue
        vals = val if isinstance(val, list) else [val]
        for v in vals:
            if v in option_keys:
                in_use.add(v)
    return in_use


def remap_option_values(
    db: Session, org_id: str, definition: CustomFieldDefinition, mapping: Dict[str, str]
) -> int:
    """Rewrite stored values for this field from old option key -> new option
    key across all contacts (task 7 "remap"). Returns contacts touched. Values
    for old keys with no mapping are left as-is ("keep": they render as
    "(removed option)" because the key no longer appears in options)."""
    if not mapping:
        return 0
    contacts = (
        db.execute(
            select(Contact).where(
                Contact.organization_id == org_id,
                Contact.custom_fields.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    touched = 0
    for c in contacts:
        cf = dict(c.custom_fields or {})
        val = cf.get(definition.key)
        if val is None:
            continue
        if isinstance(val, list):
            new_list, changed = [], False
            for v in val:
                nv = mapping.get(v, v)
                if nv != v:
                    changed = True
                if nv not in new_list:
                    new_list.append(nv)
            if changed:
                cf[definition.key] = new_list
                c.custom_fields = cf or None
                touched += 1
        else:
            if val in mapping:
                cf[definition.key] = mapping[val]
                c.custom_fields = cf or None
                touched += 1
    if touched:
        db.flush()
    return touched


def touch(definition: CustomFieldDefinition) -> None:
    definition.updated_at = utcnow()


# --- list-view filtering & sorting (task 12) ---


def _accessor(key: str, field_type: str):
    """A dialect-portable JSON accessor for one custom-field value. Uses
    SQLAlchemy's generic JSON element access, which compiles to json_extract on
    SQLite and the ->/->> operators on Postgres — so the same filter code runs
    in dev and prod, and Postgres can use the GIN index."""
    element = Contact.custom_fields[key]
    if field_type == "number":
        return element.as_float()
    if field_type == "boolean":
        return element.as_boolean()
    return element.as_string()


def build_filter_clauses(
    definitions: Dict[str, CustomFieldDefinition], filters: List[dict]
) -> list:
    """Translate a list of custom-field filter specs into SQLAlchemy where
    clauses. Each spec: {key, op, value}. Unknown keys/ops are ignored rather
    than erroring, so a stale saved view degrades gracefully."""
    clauses = []
    for f in filters or []:
        key = f.get("key")
        op = f.get("op")
        d = definitions.get(key)
        if d is None:
            continue
        col = _accessor(key, d.field_type)
        if d.field_type == "number":
            if op == "gte" and f.get("value") is not None:
                clauses.append(col >= float(f["value"]))
            elif op == "lte" and f.get("value") is not None:
                clauses.append(col <= float(f["value"]))
            elif op == "eq" and f.get("value") is not None:
                clauses.append(col == float(f["value"]))
        elif d.field_type == "boolean":
            if op == "eq" and f.get("value") is not None:
                truthy = str(f["value"]).lower() in ("true", "1", "yes")
                clauses.append(col.is_(truthy))
        elif d.field_type == "date":
            # ISO-8601 dates sort/compare correctly as strings.
            if op == "gte" and f.get("value"):
                clauses.append(col >= str(f["value"]))
            elif op == "lte" and f.get("value"):
                clauses.append(col <= str(f["value"]))
        elif d.field_type == "select":
            vals = f.get("value")
            vals = vals if isinstance(vals, list) else [vals]
            vals = [str(v) for v in vals if v not in (None, "")]
            if vals and op in ("is", "any_of", "eq"):
                clauses.append(col.in_(vals))
        elif d.field_type == "multi_select":
            vals = f.get("value")
            vals = vals if isinstance(vals, list) else [vals]
            vals = [str(v) for v in vals if v not in (None, "")]
            if vals:
                # Stored as a JSON array of controlled option-key slugs; match
                # each requested key as a quoted token in the serialized array.
                # Portable across SQLite and Postgres (keys never contain quotes).
                raw = Contact.custom_fields[key].as_string()
                clauses.append(or_(*[raw.like(f'%"{v}"%') for v in vals]))
        else:  # text / url
            if op in ("contains", "eq") and f.get("value"):
                clauses.append(col.ilike(f"%{f['value']}%"))
    return clauses


def build_sort(
    definitions: Dict[str, CustomFieldDefinition], sort_key: Optional[str], desc: bool
):
    """Return an order_by expression for a custom-field sort, or None if the key
    isn't a (sortable) custom field. Sorting rides the same query path as system
    fields."""
    if not sort_key:
        return None
    d = definitions.get(sort_key)
    if d is None:
        return None
    col = _accessor(sort_key, d.field_type)
    return col.desc() if desc else col.asc()
