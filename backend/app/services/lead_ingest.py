"""Lead ingestion shared by every capture path (Phase 6 task 1).

Three ways a lead reaches Salescale — a landing-page submission
(api/leads.py), a Meta Instant Form webhook, or a Google Lead Form webhook
(api/lead_webhooks.py) — and all of them land here so the create-or-update
rule is one piece of code: a returning lead updates the contact it already
has instead of duplicating it, and attribution is attached at creation
time, never reconciled later.

Match order for "already exists", most to least authoritative:
  1. source_external_id — the platform's own lead id (webhook retries and
     re-deliveries are exact re-sends, so this is a hard idempotency key);
  2. email (case-insensitive);
  3. phone (compared on bare digits — "(555) 010-2030" == "+15550102030"
     for the trailing-10-digit US form).
Field updates fill gaps only — an existing non-null value is never
overwritten by a later submission, matching the landing-event backfill rule
in api/leads.py.
"""

import re
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.core import Client
from ..models.crm import Contact

_CONTACT_FIELDS = ("first_name", "last_name", "email", "phone")


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _find_by_phone(db: Session, client: Client, phone: str) -> Optional[Contact]:
    """Digit-normalized phone match. Candidate set is the client's contacts
    with a phone — per-client volumes make a Python-side comparison fine, and
    it keeps the normalization rule in one testable place."""
    target = _digits(phone)
    if len(target) < 7:  # too short to be a meaningful match key
        return None
    rows = (
        db.execute(
            select(Contact).where(
                Contact.organization_id == client.organization_id,
                Contact.client_id == client.id,
                Contact.phone.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    for c in rows:
        got = _digits(c.phone or "")
        if got and (got == target or got[-10:] == target[-10:]):
            return c
    return None


def upsert_contact(
    db: Session,
    client: Client,
    *,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    source: str,
    source_external_id: Optional[str] = None,
    source_detail: Optional[dict] = None,
) -> Tuple[Contact, bool]:
    """Create the contact, or update the one this lead already is.
    Returns (contact, created). Flushes so the contact has an id."""
    email = email.lower().strip() if email else None

    contact: Optional[Contact] = None
    if source_external_id:
        contact = db.execute(
            select(Contact).where(
                Contact.organization_id == client.organization_id,
                Contact.client_id == client.id,
                Contact.source_external_id == source_external_id,
            )
        ).scalar_one_or_none()
    if contact is None and email:
        contact = db.execute(
            select(Contact).where(
                Contact.organization_id == client.organization_id,
                Contact.client_id == client.id,
                func.lower(Contact.email) == email,
            )
        ).scalar_one_or_none()
    if contact is None and phone:
        contact = _find_by_phone(db, client, phone)

    incoming = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone,
    }
    if contact is None:
        contact = Contact(
            organization_id=client.organization_id,
            client_id=client.id,
            source=source,
            source_external_id=source_external_id,
            source_detail=source_detail,
            **incoming,
        )
        db.add(contact)
        db.flush()
        return contact, True

    for field in _CONTACT_FIELDS:
        if getattr(contact, field) is None and incoming[field]:
            setattr(contact, field, incoming[field])
    if contact.source_external_id is None and source_external_id:
        contact.source_external_id = source_external_id
    if contact.source_detail is None and source_detail:
        contact.source_detail = source_detail
    db.flush()
    return contact, False
