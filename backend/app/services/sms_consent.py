"""THE SMS consent gate — the one shared check every SMS-send feature routes
through (mirrors services/email_verification.sendable/assert_can_email).

TCPA marketing texts require prior express written consent, and the burden of
proof is on the sender: sms_opt_in alone is never enough without the where/
when record (sms_opt_in_at / sms_opt_in_source). Salescale only sends to
contacts whose opt-in was captured on the Organization's own surfaces
(website form → lead webhook), attested at CSV import, or recorded manually —
and a STOP suppression on the number always beats consent.

Nothing else may re-implement this check.
"""

import datetime as dt
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Contact
from ..models.sms_outreach import SmsSuppression

ORG_DEFAULT_SOURCE = "org_default:pre_opted_funnel"


class SmsBlockedError(Exception):
    """Raised by assert_can_sms — message is safe to surface to the team."""


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Best-effort E.164 normalization (US-biased default, matching the
    product's current market): '+' prefix preserved, 10 digits → +1, 11
    digits starting with 1 → +1. Returns None when there's no usable number.
    The suppression ledger and all gateway comparisons key on this form so
    '(480) 555-0100' and '+14805550100' can never diverge."""
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    if raw.strip().startswith("+"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}"


def contact_sms_number(contact: Contact) -> Optional[str]:
    """The number outreach texts: mobile first (a person's direct line beats
    the office main line), falling back to phone."""
    return normalize_phone(contact.mobile_phone) or normalize_phone(contact.phone)


def is_suppressed(db: Session, organization_id: str, phone_e164: str) -> bool:
    return (
        db.execute(
            select(SmsSuppression.id).where(
                SmsSuppression.organization_id == organization_id,
                SmsSuppression.phone_e164 == phone_e164,
            )
        ).scalar_one_or_none()
        is not None
    )


def sendable(db: Session, contact: Contact) -> Tuple[bool, str]:
    """(ok, reason). Reasons are stable strings the enroll receipt buckets
    on: no_number | no_consent | suppressed | ok."""
    number = contact_sms_number(contact)
    if not number:
        return False, "no_number"
    if not contact.sms_opt_in:
        return False, "no_consent"
    if is_suppressed(db, contact.organization_id, number):
        return False, "suppressed"
    return True, "ok"


def assert_can_sms(db: Session, contact: Contact) -> str:
    """Returns the normalized send number or raises SmsBlockedError."""
    ok, reason = sendable(db, contact)
    if ok:
        return contact_sms_number(contact)  # type: ignore[return-value]
    detail = {
        "no_number": "Contact has no phone number.",
        "no_consent": "Contact has no recorded SMS opt-in — texting them "
        "would violate TCPA consent requirements.",
        "suppressed": "This number sent STOP (or was manually suppressed) — "
        "it can never be texted again through any path.",
    }[reason]
    raise SmsBlockedError(detail)


def record_opt_in(
    contact: Contact,
    source: str,
    at: Optional[dt.datetime] = None,
) -> None:
    """Stamp the consent record. `source` is the compliance breadcrumb —
    'website_form', 'csv_import', 'manual' — kept verbatim for audit."""
    contact.sms_opt_in = True
    contact.sms_opt_in_at = at or utcnow()
    contact.sms_opt_in_source = source[:100]


def apply_org_default(org: Optional[Organization], contact: Contact) -> None:
    """Stamp the org's standing consent attestation on a newly created
    contact. Never overwrites an existing opt-in record."""
    if org and org.sms_opt_in_default and not contact.sms_opt_in:
        record_opt_in(contact, source=ORG_DEFAULT_SOURCE)


def record_opt_out(
    db: Session,
    organization_id: str,
    phone_e164: str,
    reason: str,
    detail: Optional[str] = None,
) -> bool:
    """Idempotently add a suppression row (returns True when newly added) and
    clear sms_opt_in on every org contact carrying that number — STOP revokes
    consent for the person, not just the campaign (guardrail #9 posture)."""
    added = False
    if not is_suppressed(db, organization_id, phone_e164):
        db.add(
            SmsSuppression(
                organization_id=organization_id,
                phone_e164=phone_e164,
                reason=reason,
                detail=detail,
            )
        )
        added = True
    for contact in db.execute(
        select(Contact).where(Contact.organization_id == organization_id)
    ).scalars():
        if contact.sms_opt_in and contact_sms_number(contact) == phone_e164:
            contact.sms_opt_in = False
    return added
