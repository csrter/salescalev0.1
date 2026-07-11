"""Phase 12 Part C — email verification.

Adapter pattern (task 9): a small provider interface with one reference
implementation against ZeroBounce's bulk validate endpoint. Deliberately NOT
hand-rolled SMTP handshakes — that gets Salescale's own IPs flagged; the
provider owns deliverability infrastructure.

`verify_contacts` is the single entry point every pipeline placement uses
(Lead Finder import chain, CSV import bulk action, manual selection): it
meters against the org's monthly quota, calls the provider once per batch,
stamps verification_status/verified_at on each contact, and writes one
EmailVerificationRecord per address (the quota counter + verdict history).

`sendable` / `EmailBlockedError` is the shared outreach gate (task 12): any
email-sending feature must consult it, so `invalid` exclusion and `risky`
warnings are inherited by the Outreach module for free instead of being
re-implemented per feature.
"""

import logging
from typing import Dict, Iterable, List, Optional, Protocol, Tuple

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Contact
from ..models.lead_finder import EmailVerificationRecord
from . import entitlements, integration_creds

log = logging.getLogger("salescale.email_verification")

STATUS_UNVERIFIED = "unverified"
STATUS_VALID = "valid"
STATUS_RISKY = "risky"
STATUS_INVALID = "invalid"
STATUS_UNKNOWN = "unknown"


class VerificationProvider(Protocol):
    id: str

    def verify(self, emails: List[str]) -> Dict[str, str]:
        """email -> one of valid|risky|invalid|unknown."""
        ...


class ZeroBounceProvider:
    """Reference adapter: ZeroBounce bulk validate (POST /v2/validatebatch,
    up to 200 addresses per call)."""

    id = "zerobounce"
    _URL = "https://bulkapi.zerobounce.net/v2/validatebatch"
    _BATCH = 200
    # ZeroBounce status -> our enum. catch-all mailboxes accept anything, so
    # a positive there proves nothing → risky; spamtrap/abuse/do_not_mail are
    # addresses that hurt sender reputation → risky, not invalid (they exist).
    _MAP = {
        "valid": STATUS_VALID,
        "invalid": STATUS_INVALID,
        "catch-all": STATUS_RISKY,
        "spamtrap": STATUS_RISKY,
        "abuse": STATUS_RISKY,
        "do_not_mail": STATUS_RISKY,
        "unknown": STATUS_UNKNOWN,
    }

    def __init__(self, api_key: str):
        self._key = api_key

    def verify(self, emails: List[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for i in range(0, len(emails), self._BATCH):
            chunk = emails[i : i + self._BATCH]
            try:
                resp = httpx.post(
                    self._URL,
                    json={
                        "api_key": self._key,
                        "email_batch": [{"email_address": e} for e in chunk],
                    },
                    timeout=60,
                )
                rows = resp.json().get("email_batch", []) if resp.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                rows = []
            got = {
                (r.get("address") or "").lower(): self._MAP.get(
                    r.get("status", "unknown"), STATUS_UNKNOWN
                )
                for r in rows
            }
            for e in chunk:
                out[e] = got.get(e, STATUS_UNKNOWN)
        return out


class NullProvider:
    """Dev fallback when no provider key is configured: everything comes back
    `unknown`, so the pipeline (statuses, metering, gates) stays exercisable
    without a paid key and nothing is ever wrongly marked valid/invalid."""

    id = "none"

    def verify(self, emails: List[str]) -> Dict[str, str]:
        return {e: STATUS_UNKNOWN for e in emails}


def resolve_provider(db: Session, org_id: str) -> VerificationProvider:
    key = integration_creds.resolve_key(db, org_id, "zerobounce")
    return ZeroBounceProvider(key) if key else NullProvider()


def reset_status(contact: Contact) -> None:
    """A changed email invalidates any previous verdict — call whenever
    contact.email is written outside this module."""
    contact.verification_status = STATUS_UNVERIFIED
    contact.verified_at = None


def verify_contacts(
    db: Session,
    org: Organization,
    contacts: Iterable[Contact],
    *,
    user_id: Optional[str] = None,
    provider: Optional[VerificationProvider] = None,
    enforce_quota: bool = True,
) -> Dict[str, str]:
    """Verify every contact in the set that has an email. Returns
    {contact_id: status}. Meters BEFORE calling the provider (batch-aware,
    402 if the batch doesn't fit the monthly quota) — background callers
    catch that HTTPException and leave contacts unverified rather than
    bypassing the meter, so a burst import can't blow past billing."""
    todo = [c for c in contacts if c.email]
    if not todo:
        return {}
    if enforce_quota:
        entitlements.enforce_can_verify_emails(db, org, count=len(todo))
    prov = provider or resolve_provider(db, org.id)
    emails = sorted({c.email.lower() for c in todo})
    verdicts = prov.verify(emails)
    now = utcnow()
    results: Dict[str, str] = {}
    for c in todo:
        status = verdicts.get(c.email.lower(), STATUS_UNKNOWN)
        c.verification_status = status
        c.verified_at = now
        db.add(
            EmailVerificationRecord(
                organization_id=org.id,
                user_id=user_id,
                contact_id=c.id,
                email=c.email.lower(),
                result=status,
                provider=prov.id,
            )
        )
        results[c.id] = status
    db.flush()
    return results


# --- shared outreach gate (task 12) ---


class EmailBlockedError(Exception):
    """Raised when a send path tries to email an `invalid` address."""

    def __init__(self, contact_id: str):
        self.contact_id = contact_id
        super().__init__("Contact email is verified invalid — sending blocked")


def sendable(contacts: Iterable[Contact]) -> Tuple[List[Contact], List[Contact], List[Contact]]:
    """Partition a would-be email audience: (ok, excluded_invalid, risky).
    `risky` contacts are in `ok` too — they may be sent, but the caller must
    surface a warning. Every email-sending feature filters through this ONE
    function; do not re-implement the rule per feature."""
    ok: List[Contact] = []
    invalid: List[Contact] = []
    risky: List[Contact] = []
    for c in contacts:
        if c.verification_status == STATUS_INVALID:
            invalid.append(c)
            continue
        if c.verification_status == STATUS_RISKY:
            risky.append(c)
        ok.append(c)
    return ok, invalid, risky


def assert_can_email(contact: Contact) -> None:
    """Single-recipient form of the gate, for direct-send paths."""
    if contact.verification_status == STATUS_INVALID:
        raise EmailBlockedError(contact.id)
